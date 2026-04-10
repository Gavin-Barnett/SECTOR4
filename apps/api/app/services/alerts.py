from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

import httpx

from app.models.entities import Alert, SignalWindow
from sector4_core.config import Settings
from sector4_core.observability import get_metrics_registry

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SignalAlertSnapshot:
    signal_score: Decimal
    total_purchase_usd: Decimal
    unique_buyers: int


@dataclass(slots=True)
class AlertDecision:
    event_type: str
    reason: str
    dedupe_key: str


@dataclass(slots=True)
class AlertDispatchRequest:
    payload: dict[str, Any]


@dataclass(slots=True)
class AlertDispatchResult:
    status: str
    sent_at: datetime | None
    response_status_code: int | None = None
    error: str | None = None


class AlertNotifier(Protocol):
    def dispatch(self, request: AlertDispatchRequest) -> AlertDispatchResult: ...

    def close(self) -> None: ...


class WebhookAlertNotifier:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None
        if self._client is None and settings.alert_webhook_url:
            self._client = httpx.Client(timeout=10.0)

    def dispatch(self, request: AlertDispatchRequest) -> AlertDispatchResult:
        if not self.settings.alert_webhook_url:
            return AlertDispatchResult(
                status="disabled", sent_at=None, error="webhook_not_configured"
            )
        if self._client is None:
            return AlertDispatchResult(
                status="failed", sent_at=None, error="client_not_initialized"
            )
        try:
            response = self._client.post(self.settings.alert_webhook_url, json=request.payload)
            response.raise_for_status()
            return AlertDispatchResult(
                status="sent",
                sent_at=datetime.now(UTC),
                response_status_code=response.status_code,
            )
        except httpx.HTTPError as exc:
            return AlertDispatchResult(
                status="failed",
                sent_at=None,
                response_status_code=getattr(exc.response, "status_code", None),
                error=str(exc),
            )

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()


class StaticAlertNotifier:
    def __init__(
        self,
        factory: Callable[[AlertDispatchRequest], AlertDispatchResult] | None = None,
    ) -> None:
        self.factory = factory
        self.requests: list[AlertDispatchRequest] = []

    def dispatch(self, request: AlertDispatchRequest) -> AlertDispatchResult:
        self.requests.append(request)
        if self.factory is not None:
            return self.factory(request)
        return AlertDispatchResult(
            status="sent", sent_at=datetime.now(UTC), response_status_code=200
        )

    def close(self) -> None:
        return None


class AlertService:
    def __init__(
        self,
        settings: Settings,
        notifier: AlertNotifier | None = None,
    ) -> None:
        self.settings = settings
        self.notifier = notifier or WebhookAlertNotifier(settings)
        self.metrics = get_metrics_registry()

    def maybe_dispatch(
        self,
        signal: SignalWindow,
        previous: SignalAlertSnapshot | None,
    ) -> Alert | None:
        if not self.settings.alert_webhook_url:
            self.metrics.increment("alerts.disabled")
            return None

        decision = evaluate_alert_event(signal, previous, self.settings)
        if decision is None:
            self.metrics.increment("alerts.skipped")
            return None
        if any(
            (alert.payload_json or {}).get("dedupe_key") == decision.dedupe_key
            for alert in signal.alerts
        ):
            self.metrics.increment("alerts.deduped")
            logger.info(
                "signal alert deduped",
                extra={"signal_id": signal.id, "dedupe_key": decision.dedupe_key},
            )
            return None

        payload = build_alert_payload(signal, decision)
        self.metrics.increment("alerts.attempted")
        result = self.notifier.dispatch(AlertDispatchRequest(payload=payload))
        self.metrics.increment(f"alerts.{result.status}")
        alert = Alert(
            signal_window=signal,
            channel="webhook",
            status=result.status,
            sent_at=result.sent_at,
            payload_json={
                **payload,
                "response_status_code": result.response_status_code,
                "error": result.error,
            },
        )
        logger.info(
            "signal alert processed",
            extra={
                "signal_id": signal.id,
                "status": result.status,
                "event_type": decision.event_type,
            },
        )
        return alert

    def close(self) -> None:
        self.notifier.close()


def evaluate_alert_event(
    signal: SignalWindow,
    previous: SignalAlertSnapshot | None,
    settings: Settings,
) -> AlertDecision | None:
    threshold = settings.alert_min_signal_score
    if signal.signal_score < threshold:
        return None

    score_text = f"{signal.signal_score:.2f}"
    total_text = f"{signal.total_purchase_usd:.2f}"
    base_key = (
        f"{signal.issuer_id}:{signal.window_start.isoformat()}:{signal.window_end.isoformat()}"
    )

    if previous is None or previous.signal_score < threshold:
        return AlertDecision(
            event_type="new_signal",
            reason=(
                f"Signal reached {score_text} with {signal.unique_buyers} unique buyers and "
                f"${total_text} of purchases."
            ),
            dedupe_key=f"new_signal:{base_key}:{score_text}:{signal.unique_buyers}:{total_text}",
        )

    score_delta = signal.signal_score - previous.signal_score
    total_delta = signal.total_purchase_usd - previous.total_purchase_usd
    buyer_delta = signal.unique_buyers - previous.unique_buyers
    if (
        score_delta >= settings.alert_min_score_delta
        or total_delta >= settings.alert_min_total_purchase_delta_usd
        or buyer_delta > 0
    ):
        parts: list[str] = []
        if score_delta > Decimal("0"):
            parts.append(f"score +{score_delta:.2f}")
        if total_delta > Decimal("0"):
            parts.append(f"buy value +${total_delta:.2f}")
        if buyer_delta > 0:
            noun = "buyer" if buyer_delta == 1 else "buyers"
            parts.append(f"{buyer_delta} additional {noun}")
        reason = "Signal materially strengthened"
        if parts:
            reason = f"{reason}: {', '.join(parts)}."
        return AlertDecision(
            event_type="material_strengthening",
            reason=reason,
            dedupe_key=(
                f"material_strengthening:{base_key}:{score_text}:{signal.unique_buyers}:{total_text}"
            ),
        )
    return None


def build_alert_payload(signal: SignalWindow, decision: AlertDecision) -> dict[str, Any]:
    rationale = signal.rationale_json or {}
    ai_summary = rationale.get("ai_summary") or {}
    return {
        "dedupe_key": decision.dedupe_key,
        "event_type": decision.event_type,
        "reason": decision.reason,
        "signal": {
            "id": signal.id,
            "issuer_cik": signal.issuer.cik,
            "ticker": signal.issuer.ticker,
            "issuer_name": signal.issuer.name,
            "window_start": signal.window_start.isoformat(),
            "window_end": signal.window_end.isoformat(),
            "signal_score": f"{signal.signal_score:.2f}",
            "unique_buyers": signal.unique_buyers,
            "total_purchase_usd": f"{signal.total_purchase_usd:.2f}",
            "average_purchase_usd": f"{signal.average_purchase_usd:.2f}",
            "health_status": str(rationale.get("health_status", "unknown")),
            "price_context_status": str(rationale.get("price_context_status", "unavailable")),
            "explanation": str(rationale.get("explanation", "")),
            "summary_status": signal.summary_status,
            "ai_summary": ai_summary.get("text"),
        },
        "disclaimer": {
            "uses_public_sec_filings_only": True,
            "investment_advice": False,
            "note": "Review original SEC filings before acting.",
        },
    }
