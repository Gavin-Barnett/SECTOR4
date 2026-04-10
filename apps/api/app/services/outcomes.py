from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import SignalOutcomeCheckpoint, SignalWindow
from sector4_core.config import Settings, get_settings
from sector4_core.observability import get_metrics_registry
from sector4_core.price_history import (
    HistoricalPricePoint,
    PriceHistoryProvider,
    get_price_history_provider,
)

_CHECKPOINT_SCHEDULE: tuple[tuple[str, int], ...] = (
    ("first_seen", 0),
    ("week_1", 7),
    ("week_2", 14),
    ("week_4", 28),
)


class SignalOutcomeTracker:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        price_history_provider: PriceHistoryProvider | None = None,
        today_provider: Callable[[], date] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._price_history_provider = price_history_provider or get_price_history_provider(
            self.settings
        )
        self._today = today_provider or date.today
        self.metrics = get_metrics_registry()

    def refresh_signals(self, signals: list[SignalWindow]) -> None:
        self.metrics.increment("outcomes.refresh_runs")
        for signal in signals:
            self.refresh_signal(signal)

    def refresh_signal(self, signal: SignalWindow) -> None:
        if signal.id is None:
            return
        anchor_date = _signal_anchor_date(signal)
        today = self._today()
        existing = {
            checkpoint.checkpoint_label: checkpoint
            for checkpoint in self.session.scalars(
                select(SignalOutcomeCheckpoint).where(
                    SignalOutcomeCheckpoint.signal_window_id == signal.id
                )
            ).all()
        }

        for label, offset_days in _CHECKPOINT_SCHEDULE:
            target_date = anchor_date + timedelta(days=offset_days)
            checkpoint = existing.get(label)
            if checkpoint is None:
                checkpoint = SignalOutcomeCheckpoint(
                    signal_window_id=signal.id,
                    checkpoint_label=label,
                    target_date=target_date,
                    status="pending",
                    details_json={"reason": "target_date_not_reached"},
                )
                self.session.add(checkpoint)
                existing[label] = checkpoint
            else:
                checkpoint.target_date = target_date

            if checkpoint.price_value is not None and checkpoint.price_date is not None:
                continue
            if target_date > today:
                checkpoint.status = "pending"
                checkpoint.source = None
                checkpoint.price_date = None
                checkpoint.price_value = None
                checkpoint.details_json = {"reason": "target_date_not_reached"}
                continue
            self._capture_checkpoint(signal, checkpoint)

    def close(self) -> None:
        self._price_history_provider.close()

    def _capture_checkpoint(
        self, signal: SignalWindow, checkpoint: SignalOutcomeCheckpoint
    ) -> None:
        lookup = self._price_history_provider.lookup_price(
            signal.issuer.ticker, checkpoint.target_date
        )
        self._apply_lookup(checkpoint, lookup)

    def _apply_lookup(
        self, checkpoint: SignalOutcomeCheckpoint, lookup: HistoricalPricePoint
    ) -> None:
        checkpoint.status = lookup.status
        checkpoint.source = lookup.source
        checkpoint.price_date = lookup.price_date
        checkpoint.price_value = lookup.price_value
        checkpoint.details_json = dict(lookup.details)
        if lookup.price_value is not None:
            self.metrics.increment("outcomes.checkpoints.captured")
        elif lookup.status == "pending":
            self.metrics.increment("outcomes.checkpoints.pending")
        else:
            self.metrics.increment("outcomes.checkpoints.unavailable")


def checkpoint_schedule() -> tuple[tuple[str, int], ...]:
    return _CHECKPOINT_SCHEDULE


def _signal_anchor_date(signal: SignalWindow) -> date:
    created_at = signal.created_at
    if created_at is None:
        return datetime.now(UTC).date()
    return created_at.date()
