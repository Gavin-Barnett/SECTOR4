from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class SignalComponent(BaseModel):
    status: str
    raw_score: float | None
    max_score: float
    reweighted_score: float | None
    details: dict[str, Any]


class SignalTransactionEvidence(BaseModel):
    transaction_id: int
    accession_number: str
    filing_url: str
    xml_url: str
    insider_id: int
    insider_name: str
    insider_role: str | None
    transaction_date: date
    security_title: str | None
    shares: Decimal | None
    price_per_share: Decimal | None
    value_usd: Decimal | None
    ownership_type: str | None


class SignalAiSummary(BaseModel):
    text: str
    highlights: list[str]
    warnings: list[str]
    provider: str
    model: str | None
    generated_at: datetime | None


class SignalAlertRecord(BaseModel):
    id: int
    channel: str
    status: str
    sent_at: datetime | None
    event_type: str
    reason: str
    score_at_send: Decimal
    total_purchase_usd_at_send: Decimal
    unique_buyers_at_send: int


class SignalSummary(BaseModel):
    id: int
    issuer_cik: str
    ticker: str | None
    issuer_name: str
    window_start: date
    window_end: date
    unique_buyers: int
    total_purchase_usd: Decimal
    average_purchase_usd: Decimal
    signal_score: Decimal
    latest_transaction_date: date | None
    transaction_count: int
    first_time_buyer_count: int
    includes_indirect: bool
    includes_amendment: bool
    health_status: str
    price_context_status: str
    summary_status: str
    explanation: str
    component_breakdown: dict[str, SignalComponent]


class SignalDetail(SignalSummary):
    ai_summary: SignalAiSummary | None
    alerts: list[SignalAlertRecord]
    trade_setup: dict[str, Any] | None = None
    qualifying_transactions: list[SignalTransactionEvidence]


class SignalRecomputeResponse(BaseModel):
    generated: int
    signal_ids: list[int]
    alerts_created: int = 0
    summaries_generated: int = 0
    summaries_reused: int = 0


@dataclass(slots=True)
class SignalFilters:
    ticker: str | None = None
    cik: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    market_cap_max: Decimal | None = None
    minimum_score: Decimal | None = None
    minimum_unique_buyers: int | None = None
    include_unknown_health: bool = True
    include_indirect: bool = False
    include_amendments: bool = True


class OpsRequestContext(BaseModel):
    model_config = ConfigDict(extra="ignore")
