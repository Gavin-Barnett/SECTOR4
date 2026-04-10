from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class SignalOutcomeCheckpointRecord(BaseModel):
    checkpoint_label: str
    target_date: date
    status: str
    source: str | None
    price_date: date | None
    price_value: Decimal | None
    return_pct: Decimal | None
    details: dict[str, Any]


class SignalOutcomeSummary(BaseModel):
    signal_id: int
    issuer_cik: str
    ticker: str | None
    issuer_name: str
    first_seen_date: date
    signal_score_at_mention: Decimal
    is_active: bool
    first_seen_price: Decimal | None
    first_seen_price_date: date | None
    first_seen_price_status: str
    week_1_return_pct: Decimal | None
    week_1_status: str
    week_2_return_pct: Decimal | None
    week_2_status: str
    week_4_return_pct: Decimal | None
    week_4_status: str
    latest_completed_checkpoint: str | None
    latest_completed_return_pct: Decimal | None
    best_return_pct: Decimal | None
    worst_return_pct: Decimal | None
    checkpoints: list[SignalOutcomeCheckpointRecord]
