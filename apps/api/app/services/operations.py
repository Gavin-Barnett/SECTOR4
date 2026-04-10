from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.schemas.signals import SignalRecomputeResponse
from app.services.signals import SignalService
from sector4_core.config import Settings, get_settings
from sector4_core.observability import get_metrics_registry
from sector4_sec_ingestion.client import SecClient
from sector4_sec_ingestion.proxy_service import ProxyCompensationService
from sector4_sec_ingestion.service import IngestionService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OperationFailure:
    target_date: date
    stage: str
    error: str
    accession_number: str | None = None


@dataclass(slots=True)
class IngestOperationResult:
    mode: str
    start_date: date
    end_date: date
    days_processed: int
    entries_discovered: int = 0
    created_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    failure_count: int = 0
    accession_numbers: list[str] = field(default_factory=list)
    failures: list[OperationFailure] = field(default_factory=list)
    recompute_result: SignalRecomputeResponse | None = None


class OperationsService:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        ingestion_service: IngestionService | None = None,
        sec_client_factory: Callable[[], SecClient] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.ingestion_service = ingestion_service or IngestionService(session, self.settings)
        self._sec_client_factory = sec_client_factory or (lambda: SecClient(self.settings))
        self.metrics = get_metrics_registry()

    def ingest_live(
        self,
        *,
        target_date: date | None = None,
        limit: int | None = None,
        recompute: bool = True,
    ) -> IngestOperationResult:
        resolved_date = target_date or date.today()
        resolved_limit = limit if limit is not None else self.settings.ops_live_ingest_limit
        return self._ingest_dates(
            mode="live",
            dates=[resolved_date],
            limit_per_day=resolved_limit,
            recompute=recompute,
        )

    def ingest_backfill(
        self,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        days: int | None = None,
        limit_per_day: int | None = None,
        recompute: bool = True,
    ) -> IngestOperationResult:
        resolved_start, resolved_end = self._resolve_backfill_range(
            start_date=start_date,
            end_date=end_date,
            days=days,
        )
        resolved_limit = (
            limit_per_day if limit_per_day is not None else self.settings.ops_live_ingest_limit
        )
        return self._ingest_dates(
            mode="backfill",
            dates=list(_date_range(resolved_start, resolved_end)),
            limit_per_day=resolved_limit,
            recompute=recompute,
        )

    def _ingest_dates(
        self,
        *,
        mode: str,
        dates: list[date],
        limit_per_day: int,
        recompute: bool,
    ) -> IngestOperationResult:
        if limit_per_day < 1:
            raise ValueError("limit_per_day must be at least 1")
        result = IngestOperationResult(
            mode=mode,
            start_date=min(dates),
            end_date=max(dates),
            days_processed=len(dates),
        )
        self.metrics.increment("ops.ingest.runs")
        client = self._sec_client_factory()
        proxy_service = (
            ProxyCompensationService(self.session, self.settings, client)
            if self.settings.sec_proxy_sync_enabled
            else None
        )
        seen_issuers: dict[str, str] = {}
        try:
            for target_date in dates:
                try:
                    entries = client.fetch_daily_index(target_date)[:limit_per_day]
                except Exception as exc:
                    self._record_failure(
                        result,
                        OperationFailure(
                            target_date=target_date,
                            stage="daily_index",
                            error=str(exc),
                        ),
                    )
                    self.metrics.increment("ops.ingest.daily_index_failures")
                    logger.warning(
                        "daily index fetch failed",
                        extra={"target_date": target_date.isoformat(), "error": str(exc)},
                    )
                    continue

                result.entries_discovered += len(entries)
                self.metrics.increment("ops.ingest.entries_discovered", len(entries))
                for entry in entries:
                    try:
                        metadata, xml_text = client.fetch_filing_metadata(entry)
                        ingest_result = self.ingestion_service.ingest_xml(metadata, xml_text)
                    except Exception as exc:
                        self.session.rollback()
                        self._record_failure(
                            result,
                            OperationFailure(
                                target_date=target_date,
                                accession_number=entry.accession_number,
                                stage="filing_fetch_or_parse",
                                error=str(exc),
                            ),
                        )
                        self.metrics.increment("ops.ingest.filing_failures")
                        logger.warning(
                            "filing processing failed",
                            extra={
                                "target_date": target_date.isoformat(),
                                "accession_number": entry.accession_number,
                                "error": str(exc),
                            },
                        )
                        continue

                    result.accession_numbers.append(ingest_result.accession_number)
                    if ingest_result.status == "created":
                        result.created_count += 1
                    elif ingest_result.status == "updated":
                        result.updated_count += 1
                    elif ingest_result.status == "skipped":
                        result.skipped_count += 1
                    seen_issuers.setdefault(entry.cik, entry.company_name)

            if proxy_service is not None:
                for cik, company_name in seen_issuers.items():
                    try:
                        synced = proxy_service.sync_latest_proxy_for_issuer(
                            cik,
                            issuer_name=company_name,
                        )
                    except Exception as exc:
                        self.session.rollback()
                        self.metrics.increment("proxy.sync.failures")
                        logger.warning(
                            "proxy sync failed",
                            extra={"cik": cik, "error": str(exc)},
                        )
                        continue
                    if synced is not None:
                        self.metrics.increment("proxy.sync.successes")

            if recompute:
                result.recompute_result = SignalService(self.session, self.settings).recompute()
            logger.info(
                "ops ingestion completed",
                extra={
                    "mode": mode,
                    "start_date": result.start_date.isoformat(),
                    "end_date": result.end_date.isoformat(),
                    "days_processed": result.days_processed,
                    "entries_discovered": result.entries_discovered,
                    "created_count": result.created_count,
                    "updated_count": result.updated_count,
                    "skipped_count": result.skipped_count,
                    "failure_count": result.failure_count,
                },
            )
            return result
        finally:
            if proxy_service is not None:
                proxy_service.close()
            client.close()

    def _resolve_backfill_range(
        self,
        *,
        start_date: date | None,
        end_date: date | None,
        days: int | None,
    ) -> tuple[date, date]:
        resolved_end = end_date or date.today()
        if start_date is not None and days is not None:
            raise ValueError("provide start_date or days, not both")
        if days is None:
            days = self.settings.ops_backfill_days
        if days < 1:
            raise ValueError("days must be at least 1")
        resolved_start = start_date or (resolved_end - timedelta(days=days - 1))
        if resolved_start > resolved_end:
            raise ValueError("start_date must be on or before end_date")
        return resolved_start, resolved_end

    def _record_failure(self, result: IngestOperationResult, failure: OperationFailure) -> None:
        result.failures.append(failure)
        result.failure_count += 1
        self.metrics.increment("ops.ingest.failures")


def _date_range(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)
