from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class IssuerReference(BaseModel):
    id: int
    cik: str
    ticker: str | None
    name: str


class InsiderReference(BaseModel):
    id: int
    reporting_owner_cik: str | None
    name: str
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    officer_title: str | None


class TransactionRecord(BaseModel):
    id: int
    filing_accession_number: str
    form_type: str
    filed_at: datetime
    source_url: str
    xml_url: str
    issuer: IssuerReference
    insider: InsiderReference
    transaction_date: date | None
    security_title: str | None
    is_derivative: bool
    transaction_code: str | None
    acquired_disposed: str | None
    shares: Decimal | None
    price_per_share: Decimal | None
    value_usd: Decimal | None
    shares_after: Decimal | None
    ownership_type: str | None
    deemed_execution_date: date | None
    footnote_text: str | None
    is_candidate_buy: bool
    is_likely_routine: bool
    routine_reason: str | None


class FilingDetail(BaseModel):
    accession_number: str
    form_type: str
    filed_at: datetime
    source_url: str
    xml_url: str
    is_amendment: bool
    raw_xml_path: str
    fingerprint: str
    issuer: IssuerReference
    extra_data: dict[str, Any]
    transactions: list[TransactionRecord]


class IssuerDetail(BaseModel):
    id: int
    cik: str
    ticker: str | None
    name: str
    exchange: str | None
    sic: str | None
    state_of_incorp: str | None
    market_cap: Decimal | None
    latest_price: Decimal | None
    filing_count: int
    transaction_count: int
    latest_signal_id: int | None
    latest_signal_score: Decimal | None
    latest_signal_window_end: date | None
    latest_signal_health_status: str | None
    latest_signal_price_context_status: str | None


class InsiderDetail(BaseModel):
    id: int
    reporting_owner_cik: str | None
    name: str
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    officer_title: str | None
    transaction_count: int
    recent_transactions: list[TransactionRecord]
