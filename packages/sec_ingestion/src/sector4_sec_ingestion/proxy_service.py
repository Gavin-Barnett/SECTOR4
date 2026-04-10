from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Insider, InsiderCompensation, Issuer
from sector4_core.config import Settings, get_settings
from sector4_core.observability import get_metrics_registry
from sector4_sec_ingestion.client import SecClient, normalize_cik
from sector4_sec_ingestion.proxy_parser import (
    ProxyCompensationParseError,
    parse_proxy_compensation_html,
)
from sector4_sec_ingestion.types import (
    ParsedCompensationRecord,
    ParsedProxyCompensationDocument,
    ProxyFilingMetadata,
)

logger = logging.getLogger(__name__)

_PROXY_FORMS = {"DEF 14A", "DEFA14A"}
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]+")


@dataclass(slots=True)
class ProxyIngestResult:
    accession_number: str
    status: str
    record_count: int
    matched_insider_count: int


class ProxyCompensationService:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        client: SecClient | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.client = client
        self._owns_client = False
        self.metrics = get_metrics_registry()

    def ingest_html(self, metadata: ProxyFilingMetadata, html_text: str) -> ProxyIngestResult:
        try:
            parsed = parse_proxy_compensation_html(html_text, metadata)
        except ProxyCompensationParseError:
            self.metrics.increment("proxy.parse.failures")
            raise
        except Exception:
            self.metrics.increment("proxy.parse.failures")
            raise
        self.metrics.increment("proxy.parse.successes")
        return self.ingest_document(parsed, html_text)

    def ingest_document(
        self,
        parsed: ParsedProxyCompensationDocument,
        raw_html: str,
    ) -> ProxyIngestResult:
        raw_html_path = self._persist_raw_html(parsed.metadata.accession_number, raw_html)
        issuer = self._upsert_issuer(parsed.metadata)
        created_count = 0
        updated_count = 0
        matched_count = 0

        for record in parsed.records:
            insider, matched_existing = self._match_or_create_insider(record)
            if matched_existing:
                matched_count += 1
            compensation = self.session.scalar(
                select(InsiderCompensation).where(
                    InsiderCompensation.issuer_id == issuer.id,
                    InsiderCompensation.insider_name == record.insider_name,
                    InsiderCompensation.fiscal_year == record.fiscal_year,
                    InsiderCompensation.source_accession_number == parsed.metadata.accession_number,
                )
            )
            if compensation is None:
                compensation = InsiderCompensation(
                    issuer=issuer,
                    insider=insider,
                    insider_name=record.insider_name,
                    fiscal_year=record.fiscal_year,
                    source_accession_number=parsed.metadata.accession_number,
                    source_url=parsed.metadata.source_url,
                    filed_at=parsed.metadata.filed_at,
                )
                self.session.add(compensation)
                created_count += 1
            else:
                compensation.insider = insider
                updated_count += 1

            compensation.title = record.title
            compensation.insider_name = record.insider_name
            compensation.fiscal_year = record.fiscal_year
            compensation.salary_usd = record.salary_usd
            compensation.bonus_usd = record.bonus_usd
            compensation.stock_awards_usd = record.stock_awards_usd
            compensation.option_awards_usd = record.option_awards_usd
            compensation.non_equity_incentive_usd = record.non_equity_incentive_usd
            compensation.all_other_comp_usd = record.all_other_comp_usd
            compensation.total_compensation_usd = record.total_compensation_usd
            compensation.source_url = parsed.metadata.source_url
            compensation.filed_at = parsed.metadata.filed_at
            compensation.raw_payload = {
                **record.raw_payload,
                "document_url": parsed.metadata.document_url,
                "issuer_cik": parsed.metadata.issuer_cik,
                "issuer_name": parsed.metadata.issuer_name,
                "raw_html_path": str(raw_html_path),
            }

        self.session.commit()
        status = "created"
        if created_count == 0 and updated_count > 0:
            status = "updated"
        elif created_count > 0 and updated_count > 0:
            status = "updated"
        self.metrics.increment(f"proxy.records.{status}", max(created_count + updated_count, 1))
        logger.info(
            "proxy compensation processed",
            extra={
                "accession_number": parsed.metadata.accession_number,
                "issuer_cik": parsed.metadata.issuer_cik,
                "status": status,
                "record_count": len(parsed.records),
                "matched_insider_count": matched_count,
            },
        )
        return ProxyIngestResult(
            accession_number=parsed.metadata.accession_number,
            status=status,
            record_count=len(parsed.records),
            matched_insider_count=matched_count,
        )

    def sync_latest_proxy_for_issuer(
        self,
        cik: str,
        *,
        issuer_name: str | None = None,
        fiscal_year: int | None = None,
    ) -> ProxyIngestResult | None:
        client = self._get_client()
        submissions = client.fetch_submissions(cik)
        metadata = _latest_proxy_metadata_from_submissions(
            submissions,
            client,
            cik,
            issuer_name=issuer_name,
            fiscal_year=fiscal_year,
        )
        if metadata is None:
            return None
        return self.ingest_html(metadata, client.fetch_text(metadata.document_url))

    def close(self) -> None:
        if self._owns_client and self.client is not None:
            self.client.close()

    def _get_client(self) -> SecClient:
        if self.client is None:
            self.client = SecClient(self.settings)
            self._owns_client = True
        return self.client

    def _upsert_issuer(self, metadata: ProxyFilingMetadata) -> Issuer:
        issuer = self.session.scalar(
            select(Issuer).where(Issuer.cik == normalize_cik(metadata.issuer_cik))
        )
        if issuer is None:
            issuer = Issuer(cik=normalize_cik(metadata.issuer_cik), name=metadata.issuer_name)
            self.session.add(issuer)
        issuer.name = metadata.issuer_name or issuer.name
        self.session.flush()
        return issuer

    def _match_or_create_insider(self, record: ParsedCompensationRecord) -> tuple[Insider, bool]:
        insider = self.session.scalar(select(Insider).where(Insider.name == record.insider_name))
        matched_existing = insider is not None
        if insider is None:
            normalized_target = _normalize_person_name(record.insider_name)
            for existing in self.session.scalars(select(Insider)).all():
                if _normalize_person_name(existing.name) == normalized_target:
                    insider = existing
                    matched_existing = True
                    break
        if insider is None:
            insider = Insider(name=record.insider_name)
            self.session.add(insider)

        inferred_title = record.title
        if inferred_title:
            if insider.officer_title is None or len(inferred_title) > len(insider.officer_title):
                insider.officer_title = inferred_title
            insider.is_officer = insider.is_officer or _looks_like_officer(inferred_title)
            insider.is_director = insider.is_director or _looks_like_director(inferred_title)
        insider.name = record.insider_name
        self.session.flush()
        return insider, matched_existing

    def _persist_raw_html(self, accession_number: str, raw_html: str) -> Path:
        raw_dir = Path(self.settings.raw_filings_dir) / "proxy"
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / f"{accession_number}.html"
        path.write_text(raw_html, encoding="utf-8")
        return path


def _latest_proxy_metadata_from_submissions(
    submissions: dict,
    client: SecClient,
    cik: str,
    *,
    issuer_name: str | None,
    fiscal_year: int | None,
) -> ProxyFilingMetadata | None:
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form")
    filing_dates = recent.get("filingDate")
    accession_numbers = recent.get("accessionNumber")
    primary_documents = recent.get("primaryDocument")
    if not all(isinstance(values, list) for values in [forms, filing_dates, accession_numbers]):
        return None

    for index, raw_form in enumerate(forms):
        if raw_form not in _PROXY_FORMS:
            continue
        if index >= len(filing_dates) or index >= len(accession_numbers):
            continue
        primary_document = None
        if isinstance(primary_documents, list) and index < len(primary_documents):
            primary_document = primary_documents[index]
        if primary_document in {None, ""}:
            continue
        filed_at = datetime.fromisoformat(f"{filing_dates[index]}T00:00:00+00:00").astimezone(UTC)
        accession_number = str(accession_numbers[index])
        normalized_cik = normalize_cik(cik)
        resolved_issuer_name = issuer_name or str(submissions.get("name") or "Unknown issuer")
        return ProxyFilingMetadata(
            accession_number=accession_number,
            form_type=str(raw_form),
            filed_at=filed_at,
            source_url=(
                f"{client.settings.sec_base_url}/Archives/"
                f"edgar/data/{int(normalized_cik)}/{accession_number.replace('-', '')}.txt"
            ),
            document_url=client.build_archive_document_url(
                normalized_cik,
                accession_number,
                str(primary_document),
            ),
            issuer_cik=normalized_cik,
            issuer_name=resolved_issuer_name,
            fiscal_year=fiscal_year,
        )
    return None


def _normalize_person_name(value: str) -> str:
    return _NON_ALNUM_RE.sub("", value.upper())


def _looks_like_officer(title: str) -> bool:
    normalized = title.lower()
    return any(
        token in normalized
        for token in [
            "chief",
            "officer",
            "president",
            "cfo",
            "ceo",
            "coo",
            "cto",
            "treasurer",
            "secretary",
        ]
    )


def _looks_like_director(title: str) -> bool:
    normalized = title.lower()
    return "director" in normalized or "chair" in normalized
