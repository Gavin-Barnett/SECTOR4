from decimal import Decimal

from app.models.entities import Issuer, SignalWindow
from app.services.alerts import evaluate_alert_event
from sector4_core.config import Settings


def test_evaluate_alert_event_returns_new_signal_for_threshold_cross() -> None:
    signal = SignalWindow(
        id=1,
        issuer_id=1,
        issuer=Issuer(id=1, cik="0001234567", ticker="ACME", name="Acme Robotics, Inc."),
        window_start=__import__("datetime").date(2024, 2, 14),
        window_end=__import__("datetime").date(2024, 2, 18),
        unique_buyers=2,
        total_purchase_usd=Decimal("259800.00"),
        average_purchase_usd=Decimal("129900.00"),
        signal_score=Decimal("77.30"),
        health_score=None,
        price_context_score=None,
        summary_status="disabled",
        is_active=True,
        rationale_json={},
    )
    settings = Settings(alert_min_signal_score=Decimal("75"))

    decision = evaluate_alert_event(signal, None, settings)

    assert decision is not None
    assert decision.event_type == "new_signal"
    assert "77.30" in decision.reason


def test_evaluate_alert_event_returns_strengthening_for_material_delta() -> None:
    signal = SignalWindow(
        id=1,
        issuer_id=1,
        issuer=Issuer(id=1, cik="0001234567", ticker="ACME", name="Acme Robotics, Inc."),
        window_start=__import__("datetime").date(2024, 2, 14),
        window_end=__import__("datetime").date(2024, 2, 18),
        unique_buyers=3,
        total_purchase_usd=Decimal("359800.00"),
        average_purchase_usd=Decimal("119933.33"),
        signal_score=Decimal("84.00"),
        health_score=None,
        price_context_score=None,
        summary_status="generated",
        is_active=True,
        rationale_json={},
    )
    settings = Settings(
        alert_min_signal_score=Decimal("75"),
        alert_min_score_delta=Decimal("5"),
        alert_min_total_purchase_delta_usd=Decimal("50000"),
    )

    decision = evaluate_alert_event(
        signal,
        previous=type(
            "Snapshot",
            (),
            {
                "signal_score": Decimal("77.30"),
                "total_purchase_usd": Decimal("259800.00"),
                "unique_buyers": 2,
            },
        )(),
        settings=settings,
    )

    assert decision is not None
    assert decision.event_type == "material_strengthening"
    assert "score +6.70" in decision.reason
