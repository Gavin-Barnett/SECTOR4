from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Filing, Insider, Issuer, Transaction
from sector4_core.config import Settings, get_settings
from sector4_core.observability import get_metrics_registry
from sector4_sec_ingestion.parser import parse_ownership_xml
from sector4_sec_ingestion.types import FilingMetadata, ParsedInsider, ParsedOwnershipDocument

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IngestResult:
    accession_number: str
    status: str
    filing_id: int
    insider_count: int
    transaction_count: int
    candidate_buy_count: int


class IngestionService:
    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.metrics = get_metrics_registry()

    def ingest_xml(self, metadata: FilingMetadata, xml_text: str) -> IngestResult:
        try:
            parsed = parse_ownership_xml(xml_text, metadata)
        except Exception as exc:
            self.metrics.increment("parse.failures")
            logger.warning(
                "filing parse failed",
                extra={
                    "accession_number": metadata.accession_number,
                    "form_type": metadata.form_type,
                    "error": str(exc),
                },
            )
            raise
        self.metrics.increment("parse.successes")
        return self.ingest_document(parsed, xml_text)

    def ingest_document(self, parsed: ParsedOwnershipDocument, raw_xml: str) -> IngestResult:
        fingerprint = self._fingerprint(parsed)
        raw_xml_path = self._persist_raw_xml(parsed.metadata.accession_number, raw_xml)
        issuer = self._upsert_issuer(parsed)
        insiders = [self._upsert_insider(owner) for owner in parsed.insiders]

        filing = self.session.scalar(
            select(Filing).where(Filing.accession_number == parsed.metadata.accession_number)
        )
        if filing is not None and filing.fingerprint == fingerprint:
            self.metrics.increment("ingest.filings.skipped")
            logger.info(
                "filing skipped",
                extra={
                    "accession_number": parsed.metadata.accession_number,
                    "reason": "fingerprint_match",
                },
            )
            return IngestResult(
                accession_number=parsed.metadata.accession_number,
                status="skipped",
                filing_id=filing.id,
                insider_count=len(insiders),
                transaction_count=len(filing.transactions),
                candidate_buy_count=sum(1 for txn in filing.transactions if txn.is_candidate_buy),
            )

        status = "created"
        if filing is None:
            filing = Filing(accession_number=parsed.metadata.accession_number)
            self.session.add(filing)
        else:
            filing.transactions.clear()
            status = "updated"

        filing.form_type = parsed.document_type
        filing.issuer = issuer
        filing.filed_at = parsed.metadata.filed_at
        filing.source_url = parsed.metadata.source_url
        filing.xml_url = parsed.metadata.xml_url
        filing.is_amendment = parsed.is_amendment
        filing.raw_xml_path = str(raw_xml_path)
        filing.fingerprint = fingerprint
        filing.extra_data = {
            **parsed.extra_data,
            "period_of_report": parsed.period_of_report.isoformat()
            if parsed.period_of_report
            else None,
            "remarks": parsed.remarks,
            "footnotes": parsed.footnotes,
        }
        self.session.flush()

        owners = insiders or [self._upsert_insider(ParsedInsider(name="Unknown reporting owner"))]
        for insider in owners:
            for transaction in parsed.transactions:
                filing.transactions.append(
                    Transaction(
                        insider=insider,
                        transaction_date=transaction.transaction_date,
                        security_title=transaction.security_title,
                        is_derivative=transaction.is_derivative,
                        transaction_code=transaction.transaction_code,
                        acquired_disposed=transaction.acquired_disposed,
                        shares=transaction.shares,
                        price_per_share=transaction.price_per_share,
                        value_usd=transaction.value_usd,
                        shares_after=transaction.shares_after,
                        ownership_type=transaction.ownership_type,
                        deemed_execution_date=transaction.deemed_execution_date,
                        footnote_text=transaction.footnote_text,
                        is_candidate_buy=transaction.is_candidate_buy,
                        is_likely_routine=transaction.is_likely_routine,
                        routine_reason=transaction.routine_reason,
                        raw_payload=transaction.raw_payload,
                    )
                )

        self.session.commit()
        transaction_count = len(parsed.transactions) * len(owners)
        candidate_count = sum(1 for txn in parsed.transactions if txn.is_candidate_buy) * len(
            owners
        )
        self.metrics.increment(f"ingest.filings.{status}")
        self.metrics.increment("ingest.transactions.parsed", transaction_count)
        self.metrics.increment("ingest.transactions.candidate_buys", candidate_count)
        logger.info(
            "filing processed",
            extra={
                "accession_number": parsed.metadata.accession_number,
                "status": status,
                "parsed_transactions": len(parsed.transactions),
                "candidate_transactions": sum(
                    1 for txn in parsed.transactions if txn.is_candidate_buy
                ),
                "contributed_to_signal": candidate_count > 0,
                "exclusion_reasons": sorted(
                    {
                        txn.routine_reason
                        for txn in parsed.transactions
                        if not txn.is_candidate_buy and txn.routine_reason
                    }
                ),
            },
        )
        return IngestResult(
            accession_number=parsed.metadata.accession_number,
            status=status,
            filing_id=filing.id,
            insider_count=len(owners),
            transaction_count=transaction_count,
            candidate_buy_count=candidate_count,
        )

    def _upsert_issuer(self, parsed: ParsedOwnershipDocument) -> Issuer:
        issuer = self.session.scalar(select(Issuer).where(Issuer.cik == parsed.issuer.cik))
        if issuer is None:
            issuer = Issuer(cik=parsed.issuer.cik)
            self.session.add(issuer)
        issuer.name = parsed.issuer.name
        issuer.ticker = parsed.issuer.ticker
        self.session.flush()
        return issuer

    def _upsert_insider(self, parsed: ParsedInsider) -> Insider:
        insider = None
        if parsed.reporting_owner_cik:
            insider = self.session.scalar(
                select(Insider).where(Insider.reporting_owner_cik == parsed.reporting_owner_cik)
            )
        if insider is None:
            insider = self.session.scalar(select(Insider).where(Insider.name == parsed.name))
        if insider is None:
            insider = Insider(name=parsed.name)
            self.session.add(insider)
        insider.reporting_owner_cik = parsed.reporting_owner_cik
        insider.name = parsed.name
        insider.is_director = parsed.is_director
        insider.is_officer = parsed.is_officer
        insider.is_ten_percent_owner = parsed.is_ten_percent_owner
        insider.officer_title = parsed.officer_title
        self.session.flush()
        return insider

    def _persist_raw_xml(self, accession_number: str, raw_xml: str) -> Path:
        raw_dir = Path(self.settings.raw_filings_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)
        path = raw_dir / f"{accession_number}.xml"
        path.write_text(raw_xml, encoding="utf-8")
        return path

    def _fingerprint(self, parsed: ParsedOwnershipDocument) -> str:
        payload = json.dumps(parsed.fingerprint_payload(), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
