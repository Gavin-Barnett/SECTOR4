from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class FilingMetadata:
    accession_number: str
    form_type: str
    filed_at: datetime
    source_url: str
    xml_url: str


@dataclass(slots=True)
class ProxyFilingMetadata:
    accession_number: str
    form_type: str
    filed_at: datetime
    source_url: str
    document_url: str
    issuer_cik: str
    issuer_name: str
    fiscal_year: int | None = None


@dataclass(slots=True)
class ParsedIssuer:
    cik: str
    name: str
    ticker: str | None = None


@dataclass(slots=True)
class ParsedInsider:
    name: str
    reporting_owner_cik: str | None = None
    is_director: bool = False
    is_officer: bool = False
    is_ten_percent_owner: bool = False
    officer_title: str | None = None


@dataclass(slots=True)
class ParsedTransaction:
    security_title: str | None
    transaction_date: date | None
    deemed_execution_date: date | None
    transaction_code: str | None
    acquired_disposed: str | None
    shares: Decimal | None
    price_per_share: Decimal | None
    value_usd: Decimal | None
    shares_after: Decimal | None
    ownership_type: str | None
    is_derivative: bool
    footnote_text: str | None
    is_candidate_buy: bool
    is_likely_routine: bool
    routine_reason: str | None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedCompensationRecord:
    insider_name: str
    title: str | None
    fiscal_year: int | None
    salary_usd: Decimal | None
    bonus_usd: Decimal | None
    stock_awards_usd: Decimal | None
    option_awards_usd: Decimal | None
    non_equity_incentive_usd: Decimal | None
    all_other_comp_usd: Decimal | None
    total_compensation_usd: Decimal | None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedOwnershipDocument:
    metadata: FilingMetadata
    document_type: str
    period_of_report: date | None
    issuer: ParsedIssuer
    insiders: list[ParsedInsider]
    transactions: list[ParsedTransaction]
    footnotes: dict[str, str] = field(default_factory=dict)
    remarks: str | None = None
    extra_data: dict[str, Any] = field(default_factory=dict)

    @property
    def is_amendment(self) -> bool:
        return self.document_type.upper().endswith("/A")

    def fingerprint_payload(self) -> dict[str, Any]:
        return {
            "metadata": {
                "accession_number": self.metadata.accession_number,
                "form_type": self.document_type,
                "filed_at": self.metadata.filed_at.isoformat(),
            },
            "period_of_report": self.period_of_report.isoformat()
            if self.period_of_report
            else None,
            "issuer": asdict(self.issuer),
            "insiders": [asdict(insider) for insider in self.insiders],
            "transactions": [
                {
                    **asdict(transaction),
                    "transaction_date": (
                        transaction.transaction_date.isoformat()
                        if transaction.transaction_date
                        else None
                    ),
                    "deemed_execution_date": (
                        transaction.deemed_execution_date.isoformat()
                        if transaction.deemed_execution_date
                        else None
                    ),
                    "shares": str(transaction.shares) if transaction.shares is not None else None,
                    "price_per_share": (
                        str(transaction.price_per_share)
                        if transaction.price_per_share is not None
                        else None
                    ),
                    "value_usd": str(transaction.value_usd)
                    if transaction.value_usd is not None
                    else None,
                    "shares_after": (
                        str(transaction.shares_after)
                        if transaction.shares_after is not None
                        else None
                    ),
                }
                for transaction in self.transactions
            ],
            "footnotes": self.footnotes,
            "remarks": self.remarks,
            "extra_data": self.extra_data,
        }


@dataclass(slots=True)
class ParsedProxyCompensationDocument:
    metadata: ProxyFilingMetadata
    records: list[ParsedCompensationRecord]
    extra_data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SecIndexEntry:
    form_type: str
    company_name: str
    cik: str
    filed_date: date
    filename: str

    @property
    def accession_number(self) -> str:
        stem = self.filename.rsplit("/", 1)[-1].replace(".txt", "")
        if len(stem) == 18 and stem.isdigit():
            return f"{stem[:10]}-{stem[10:12]}-{stem[12:]}"
        return stem

    @property
    def directory_path(self) -> str:
        base_path = self.filename.removesuffix(".txt")
        prefix, _, stem = base_path.rpartition("/")
        if not stem:
            return base_path
        return f"{prefix}/{stem.replace('-', '')}" if prefix else stem.replace('-', '')

