from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.entities import Issuer, SignalOutcomeCheckpoint, SignalWindow
from app.schemas.results import SignalOutcomeCheckpointRecord, SignalOutcomeSummary

_ZERO = Decimal("0")


class ResultsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_results(self, ticker: str | None = None) -> list[SignalOutcomeSummary]:
        statement = (
            select(SignalWindow)
            .options(
                selectinload(SignalWindow.issuer),
                selectinload(SignalWindow.outcome_checkpoints),
            )
            .order_by(SignalWindow.created_at.desc(), SignalWindow.signal_score.desc())
        )
        if ticker:
            statement = statement.join(SignalWindow.issuer).where(Issuer.ticker == ticker.upper())

        signals = self.session.scalars(statement).all()
        return [self._to_summary(signal) for signal in signals]

    def _to_summary(self, signal: SignalWindow) -> SignalOutcomeSummary:
        checkpoints = {
            checkpoint.checkpoint_label: checkpoint
            for checkpoint in sorted(
                signal.outcome_checkpoints,
                key=lambda item: (item.target_date, item.checkpoint_label),
            )
        }
        baseline = checkpoints.get("first_seen")

        checkpoint_records = [
            self._to_checkpoint_record(checkpoints.get(label), baseline)
            for label in ("first_seen", "week_1", "week_2", "week_4")
            if checkpoints.get(label) is not None
        ]

        week_1 = checkpoints.get("week_1")
        week_2 = checkpoints.get("week_2")
        week_4 = checkpoints.get("week_4")
        completed_records = [
            record for record in checkpoint_records if record.checkpoint_label != "first_seen"
        ]
        completed_returns = [
            record.return_pct for record in completed_records if record.return_pct is not None
        ]
        latest_completed = next(
            (
                record
                for record in reversed(completed_records)
                if record.return_pct is not None
            ),
            None,
        )

        return SignalOutcomeSummary(
            signal_id=signal.id,
            issuer_cik=signal.issuer.cik,
            ticker=signal.issuer.ticker,
            issuer_name=signal.issuer.name,
            first_seen_date=_signal_first_seen_date(signal),
            signal_score_at_mention=signal.signal_score,
            is_active=signal.is_active,
            first_seen_price=baseline.price_value if baseline is not None else None,
            first_seen_price_date=baseline.price_date if baseline is not None else None,
            first_seen_price_status=baseline.status if baseline is not None else "missing",
            week_1_return_pct=_return_pct(week_1, baseline),
            week_1_status=week_1.status if week_1 is not None else "missing",
            week_2_return_pct=_return_pct(week_2, baseline),
            week_2_status=week_2.status if week_2 is not None else "missing",
            week_4_return_pct=_return_pct(week_4, baseline),
            week_4_status=week_4.status if week_4 is not None else "missing",
            latest_completed_checkpoint=(
                latest_completed.checkpoint_label if latest_completed is not None else None
            ),
            latest_completed_return_pct=(
                latest_completed.return_pct if latest_completed is not None else None
            ),
            best_return_pct=max(completed_returns) if completed_returns else None,
            worst_return_pct=min(completed_returns) if completed_returns else None,
            checkpoints=checkpoint_records,
        )

    def _to_checkpoint_record(
        self,
        checkpoint: SignalOutcomeCheckpoint,
        baseline: SignalOutcomeCheckpoint | None,
    ) -> SignalOutcomeCheckpointRecord:
        return SignalOutcomeCheckpointRecord(
            checkpoint_label=checkpoint.checkpoint_label,
            target_date=checkpoint.target_date,
            status=checkpoint.status,
            source=checkpoint.source,
            price_date=checkpoint.price_date,
            price_value=checkpoint.price_value,
            return_pct=_return_pct(checkpoint, baseline),
            details=checkpoint.details_json or {},
        )


def _signal_first_seen_date(signal: SignalWindow) -> date:
    created_at = signal.created_at
    if created_at is not None:
        return created_at.date()
    return signal.window_end


def _return_pct(
    checkpoint: SignalOutcomeCheckpoint | None,
    baseline: SignalOutcomeCheckpoint | None,
) -> Decimal | None:
    if checkpoint is None or baseline is None:
        return None
    if (
        checkpoint.price_value is None
        or baseline.price_value is None
        or baseline.price_value <= _ZERO
    ):
        return None
    return _quantize(((checkpoint.price_value - baseline.price_value) / baseline.price_value) * 100)


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
