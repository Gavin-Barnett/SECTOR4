from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.entities import (
    Alert,
    Filing,
    Insider,
    InsiderCompensation,
    Issuer,
    SignalWindow,
    Transaction,
)
from app.schemas.signals import (
    SignalAiSummary,
    SignalAlertRecord,
    SignalDetail,
    SignalFilters,
    SignalRecomputeResponse,
    SignalSummary,
)
from app.services.alerts import AlertService, SignalAlertSnapshot
from app.services.outcomes import SignalOutcomeTracker
from sector4_ai_summary import (
    SignalSummaryGenerator,
    SignalSummaryResult,
    get_signal_summary_generator,
    summarize_fact_payload,
)
from sector4_core.config import Settings, get_settings
from sector4_core.enrichment import (
    IssuerEnrichmentProvider,
    IssuerEnrichmentRequest,
    IssuerEnrichmentSnapshot,
    get_issuer_enrichment_provider,
)
from sector4_core.observability import get_metrics_registry
from sector4_scoring import CandidateBuy, ComputedSignal, compute_signal_windows

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ExistingSignalState:
    signal_score: Decimal
    total_purchase_usd: Decimal
    unique_buyers: int
    summary_status: str
    summary_input_hash: str | None
    summary_payload: dict[str, Any] | None


class SignalService:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        enrichment_provider: IssuerEnrichmentProvider | None = None,
        summary_generator: SignalSummaryGenerator | None = None,
        alert_service: AlertService | None = None,
        outcome_tracker: SignalOutcomeTracker | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._enrichment_provider = enrichment_provider
        self._summary_generator = summary_generator
        self._alert_service = alert_service
        self._outcome_tracker = outcome_tracker
        self.metrics = get_metrics_registry()

    def _get_enrichment_provider(self) -> IssuerEnrichmentProvider:
        if self._enrichment_provider is None:
            self._enrichment_provider = get_issuer_enrichment_provider(self.settings)
        return self._enrichment_provider

    def _get_summary_generator(self) -> SignalSummaryGenerator:
        if self._summary_generator is None:
            self._summary_generator = get_signal_summary_generator(self.settings)
        return self._summary_generator

    def _get_alert_service(self) -> AlertService:
        if self._alert_service is None:
            self._alert_service = AlertService(self.settings)
        return self._alert_service

    def _get_outcome_tracker(self) -> SignalOutcomeTracker:
        if self._outcome_tracker is None:
            self._outcome_tracker = SignalOutcomeTracker(self.session, self.settings)
        return self._outcome_tracker

    def _close_resources(self) -> None:
        if self._enrichment_provider is not None:
            self._enrichment_provider.close()
        if self._summary_generator is not None:
            self._summary_generator.close()
        if self._alert_service is not None:
            self._alert_service.close()
        if self._outcome_tracker is not None:
            self._outcome_tracker.close()

    def recompute(self) -> SignalRecomputeResponse:
        try:
            self.metrics.increment("signals.recompute_runs")
            candidates = self._load_candidate_buys()
            computed_signals = compute_signal_windows(candidates, self.settings)
            existing_records = self.session.scalars(
                select(SignalWindow).options(
                    selectinload(SignalWindow.issuer), selectinload(SignalWindow.alerts)
                )
            ).all()
            existing_by_key = {
                _signal_key(record.issuer_id, record.window_start, record.window_end): record
                for record in existing_records
            }

            persisted_ids: list[int] = []
            current_keys: set[tuple[int, date, date]] = set()
            alerts_created = 0
            summaries_generated = 0
            summaries_reused = 0

            for signal in computed_signals:
                issuer = self.session.get(Issuer, signal.issuer_id)
                if issuer is not None:
                    snapshot = self._get_enrichment_provider().enrich(
                        IssuerEnrichmentRequest(
                            cik=issuer.cik,
                            ticker=issuer.ticker,
                            name=issuer.name,
                            market_cap=issuer.market_cap,
                            latest_price=issuer.latest_price,
                            market_cap_price_hint=_as_decimal(
                                signal.rationale_json.get("market_cap_price_hint")
                            ),
                            event_anchor_date=signal.window_end,
                        )
                    )
                    self._apply_enrichment(issuer, signal, snapshot)

                signal_key = _signal_key(signal.issuer_id, signal.window_start, signal.window_end)
                record = existing_by_key.get(signal_key)
                previous_state = _capture_existing_state(record)
                if record is None:
                    record = SignalWindow(
                        issuer_id=signal.issuer_id,
                        issuer=issuer,
                        window_start=signal.window_start,
                        window_end=signal.window_end,
                        unique_buyers=signal.unique_buyers,
                        total_purchase_usd=signal.total_purchase_usd,
                        average_purchase_usd=signal.average_purchase_usd,
                        signal_score=signal.signal_score,
                        health_score=signal.health_score,
                        price_context_score=signal.price_context_score,
                        summary_status="pending",
                        is_active=True,
                        rationale_json=signal.rationale_json,
                    )
                    self.session.add(record)
                    self.session.flush()
                    existing_by_key[signal_key] = record
                else:
                    self._update_record(record, signal)
                    record.issuer = issuer or record.issuer
                    self.session.flush()

                persisted_ids.append(record.id)
                current_keys.add(signal_key)

                qualifying_transactions = self._load_qualifying_transactions(
                    record.rationale_json.get("qualifying_transaction_ids", [])
                )
                summary_result = self._apply_summary(
                    record, qualifying_transactions, previous_state
                )
                if summary_result.reused:
                    summaries_reused += 1
                    self.metrics.increment("summaries.reused")
                elif summary_result.status == "generated":
                    summaries_generated += 1
                    self.metrics.increment("summaries.generated")
                elif summary_result.status == "failed":
                    self.metrics.increment("summaries.failed")
                elif summary_result.status == "disabled":
                    self.metrics.increment("summaries.disabled")

                alert = self._get_alert_service().maybe_dispatch(
                    record,
                    None
                    if previous_state is None
                    else SignalAlertSnapshot(
                        signal_score=previous_state.signal_score,
                        total_purchase_usd=previous_state.total_purchase_usd,
                        unique_buyers=previous_state.unique_buyers,
                    ),
                )
                if alert is not None:
                    self.session.add(alert)
                    self.session.flush()
                    alerts_created += 1

            for signal_key, record in existing_by_key.items():
                record.is_active = signal_key in current_keys

            self._get_outcome_tracker().refresh_signals(list(existing_by_key.values()))

            self.session.commit()
            self.metrics.increment("signals.generated_total", len(persisted_ids))
            self.metrics.increment("alerts.created_total", alerts_created)
            logger.info(
                "signals recomputed",
                extra={
                    "generated": len(persisted_ids),
                    "alerts_created": alerts_created,
                    "summaries_generated": summaries_generated,
                    "summaries_reused": summaries_reused,
                },
            )
            return SignalRecomputeResponse(
                generated=len(persisted_ids),
                signal_ids=persisted_ids,
                alerts_created=alerts_created,
                summaries_generated=summaries_generated,
                summaries_reused=summaries_reused,
            )
        finally:
            self._close_resources()

    def list_signals(self, filters: SignalFilters) -> list[SignalSummary]:
        statement = (
            select(SignalWindow)
            .options(selectinload(SignalWindow.issuer))
            .join(SignalWindow.issuer)
            .where(SignalWindow.is_active.is_(True))
            .order_by(SignalWindow.signal_score.desc(), SignalWindow.window_end.desc())
        )
        if filters.ticker:
            statement = statement.where(Issuer.ticker == filters.ticker.upper())
        if filters.cik:
            statement = statement.where(Issuer.cik == filters.cik)
        if filters.date_from:
            statement = statement.where(SignalWindow.window_end >= filters.date_from)
        if filters.date_to:
            statement = statement.where(SignalWindow.window_end <= filters.date_to)
        if filters.market_cap_max is not None:
            statement = statement.where(
                or_(Issuer.market_cap.is_(None), Issuer.market_cap <= filters.market_cap_max)
            )
        if filters.minimum_score is not None:
            statement = statement.where(SignalWindow.signal_score >= filters.minimum_score)
        if filters.minimum_unique_buyers is not None:
            statement = statement.where(SignalWindow.unique_buyers >= filters.minimum_unique_buyers)
        if not filters.include_unknown_health:
            statement = statement.where(SignalWindow.health_score.is_not(None))

        signals = self.session.scalars(statement).all()
        filtered = [signal for signal in signals if self._matches_summary_filters(signal, filters)]
        return [self._to_summary(signal) for signal in filtered]

    def latest_signals(self, limit: int = 10) -> list[SignalSummary]:
        statement = (
            select(SignalWindow)
            .options(selectinload(SignalWindow.issuer))
            .where(SignalWindow.is_active.is_(True))
            .order_by(SignalWindow.window_end.desc(), SignalWindow.signal_score.desc())
            .limit(limit)
        )
        return [self._to_summary(signal) for signal in self.session.scalars(statement).all()]

    def get_signal(self, signal_id: int) -> SignalDetail | None:
        signal = self.session.scalar(
            select(SignalWindow)
            .options(selectinload(SignalWindow.issuer), selectinload(SignalWindow.alerts))
            .where(SignalWindow.id == signal_id)
        )
        if signal is None:
            return None

        qualifying_transactions = self._load_qualifying_transactions(
            signal.rationale_json.get("qualifying_transaction_ids", [])
        )
        summary = self._to_summary(signal)
        return SignalDetail(
            **summary.model_dump(),
            ai_summary=_summary_from_rationale(signal.rationale_json or {}),
            alerts=_alerts_from_records(signal.alerts),
            trade_setup=_build_trade_setup(signal, qualifying_transactions),
            qualifying_transactions=qualifying_transactions,
        )

    def _matches_summary_filters(self, signal: SignalWindow, filters: SignalFilters) -> bool:
        rationale = signal.rationale_json or {}
        includes_indirect = bool(rationale.get("includes_indirect", False))
        includes_amendment = bool(rationale.get("includes_amendment", False))
        if not filters.include_indirect and includes_indirect:
            return False
        if not filters.include_amendments and includes_amendment:
            return False
        return True

    def _load_candidate_buys(self) -> list[CandidateBuy]:
        rows = self.session.execute(
            select(Transaction, Filing, Issuer, Insider)
            .join(Transaction.filing)
            .join(Filing.issuer)
            .join(Transaction.insider)
            .where(Transaction.is_candidate_buy.is_(True))
            .where(Transaction.transaction_date.is_not(None))
            .where(Transaction.value_usd.is_not(None))
        ).all()
        compensation_index = _build_compensation_index(
            self.session,
            {issuer.id for _, _, issuer, _ in rows},
        )
        candidates: list[CandidateBuy] = []
        for transaction, filing, issuer, insider in rows:
            if transaction.transaction_date is None or transaction.value_usd is None:
                continue
            compensation = _latest_compensation_for_candidate(
                compensation_index.get(issuer.id, []),
                insider,
                filing.filed_at,
            )
            annual_compensation = (
                compensation.total_compensation_usd if compensation is not None else None
            )
            compensation_ratio = None
            if annual_compensation is not None and annual_compensation > Decimal("0"):
                compensation_ratio = _quantize(transaction.value_usd / annual_compensation)
            insider_role = _insider_role(
                insider,
                compensation.title if compensation is not None else None,
            )
            candidates.append(
                CandidateBuy(
                    transaction_id=transaction.id,
                    filing_id=filing.id,
                    accession_number=filing.accession_number,
                    source_url=filing.source_url,
                    xml_url=filing.xml_url,
                    filed_at=filing.filed_at,
                    is_amendment=filing.is_amendment,
                    issuer_id=issuer.id,
                    issuer_cik=issuer.cik,
                    issuer_name=issuer.name,
                    issuer_ticker=issuer.ticker,
                    insider_id=insider.id,
                    insider_name=insider.name,
                    insider_role=insider_role,
                    transaction_date=transaction.transaction_date,
                    security_title=transaction.security_title,
                    shares=transaction.shares,
                    price_per_share=transaction.price_per_share,
                    value_usd=transaction.value_usd,
                    shares_after=transaction.shares_after,
                    ownership_type=transaction.ownership_type,
                    transaction_code=transaction.transaction_code,
                    annual_compensation_usd=annual_compensation,
                    compensation_purchase_ratio=compensation_ratio,
                    role_weight_multiplier=_role_weight_multiplier(insider_role),
                )
            )
        return candidates

    def _load_qualifying_transactions(self, transaction_ids: list[int]) -> list[dict[str, object]]:
        if not transaction_ids:
            return []
        rows = self.session.execute(
            select(Transaction, Filing, Insider)
            .join(Transaction.filing)
            .join(Transaction.insider)
            .where(Transaction.id.in_(transaction_ids))
        ).all()
        row_map = {
            transaction.id: (transaction, filing, insider) for transaction, filing, insider in rows
        }
        qualifying_transactions: list[dict[str, object]] = []
        for transaction_id in transaction_ids:
            row = row_map.get(transaction_id)
            if row is None:
                continue
            transaction, filing, insider = row
            qualifying_transactions.append(
                {
                    "transaction_id": transaction.id,
                    "accession_number": filing.accession_number,
                    "filing_url": filing.source_url,
                    "xml_url": filing.xml_url,
                    "insider_id": insider.id,
                    "insider_name": insider.name,
                    "insider_role": _insider_role(insider),
                    "transaction_date": transaction.transaction_date,
                    "security_title": transaction.security_title,
                    "shares": transaction.shares,
                    "price_per_share": transaction.price_per_share,
                    "value_usd": transaction.value_usd,
                    "ownership_type": transaction.ownership_type,
                }
            )
        return qualifying_transactions

    def _apply_enrichment(
        self,
        issuer: Issuer,
        signal: ComputedSignal,
        snapshot: IssuerEnrichmentSnapshot,
    ) -> None:
        if snapshot.market_cap is not None:
            issuer.market_cap = snapshot.market_cap
        if snapshot.latest_price is not None:
            issuer.latest_price = snapshot.latest_price
        if snapshot.exchange is not None:
            issuer.exchange = snapshot.exchange
        if snapshot.sic is not None:
            issuer.sic = snapshot.sic
        if snapshot.state_of_incorp is not None:
            issuer.state_of_incorp = snapshot.state_of_incorp

        component_breakdown = dict(signal.rationale_json.get("component_breakdown", {}))
        previous_total_max = Decimal("0")
        for component in component_breakdown.values():
            raw_score = _as_decimal(component.get("raw_score"))
            max_score = _as_decimal(component.get("max_score"))
            if raw_score is None or max_score is None:
                continue
            previous_total_max += max_score

        total_max = previous_total_max
        additional_total_raw = Decimal("0")
        has_additional_component = False
        if snapshot.price_context.score is not None:
            total_max += Decimal("15")
            additional_total_raw += snapshot.price_context.score
            has_additional_component = True
        if snapshot.health.score is not None:
            total_max += Decimal("20")
            additional_total_raw += snapshot.health.score
            has_additional_component = True
        if snapshot.event_context.score is not None:
            total_max += Decimal("10")
            additional_total_raw += snapshot.event_context.score
            has_additional_component = True

        if total_max and has_additional_component:
            previous_total_raw = (signal.signal_score / Decimal("100")) * previous_total_max
            signal.signal_score = _quantize(
                ((previous_total_raw + additional_total_raw) / total_max) * Decimal("100")
            )
        signal.price_context_score = snapshot.price_context.score
        signal.health_score = snapshot.health.score

        component_breakdown["price_context"] = {
            "status": snapshot.price_context.status,
            "raw_score": _as_float(snapshot.price_context.score),
            "max_score": 15.0,
            "reweighted_score": _reweighted(snapshot.price_context.score, total_max),
            "details": snapshot.price_context.details,
        }
        component_breakdown["health"] = {
            "status": snapshot.health.status,
            "raw_score": _as_float(snapshot.health.score),
            "max_score": 20.0,
            "reweighted_score": _reweighted(snapshot.health.score, total_max),
            "details": snapshot.health.details,
        }
        component_breakdown["event_context"] = {
            "status": snapshot.event_context.status,
            "raw_score": _as_float(snapshot.event_context.score),
            "max_score": 10.0,
            "reweighted_score": _reweighted(snapshot.event_context.score, total_max),
            "details": snapshot.event_context.details,
        }
        if has_additional_component:
            for name, component in component_breakdown.items():
                if name in {"price_context", "health", "event_context"}:
                    continue
                raw_score = _as_decimal(component.get("raw_score"))
                component_breakdown[name] = {
                    **component,
                    "reweighted_score": _reweighted(raw_score, total_max),
                }
        signal.rationale_json = {
            **signal.rationale_json,
            "component_breakdown": component_breakdown,
            "health_status": snapshot.health.status,
            "price_context_status": snapshot.price_context.status,
            "event_context_status": snapshot.event_context.status,
        }

    def _apply_summary(
        self,
        signal: SignalWindow,
        qualifying_transactions: list[dict[str, object]],
        previous: ExistingSignalState | None,
    ) -> SignalSummaryResult:
        request = summarize_fact_payload(
            signal.id, self._build_summary_facts(signal, qualifying_transactions)
        )
        if (
            previous is not None
            and previous.summary_status == "generated"
            and previous.summary_input_hash == request.input_hash
            and previous.summary_payload is not None
            and previous.summary_payload.get("text")
        ):
            signal.summary_status = "generated"
            signal.rationale_json = {
                **signal.rationale_json,
                "ai_summary": previous.summary_payload,
            }
            return SignalSummaryResult(
                status="generated",
                summary_text=str(previous.summary_payload.get("text")),
                highlights=[str(item) for item in previous.summary_payload.get("highlights", [])],
                warnings=[str(item) for item in previous.summary_payload.get("warnings", [])],
                provider=str(previous.summary_payload.get("provider", "stored")),
                model=(
                    str(previous.summary_payload.get("model"))
                    if previous.summary_payload.get("model") is not None
                    else None
                ),
                input_hash=request.input_hash,
                reused=True,
                generated_at=_maybe_datetime(previous.summary_payload.get("generated_at")),
            )

        result = self._get_summary_generator().generate(request)
        signal.summary_status = result.status
        rationale = dict(signal.rationale_json or {})
        if result.summary_text:
            rationale["ai_summary"] = {
                "text": result.summary_text,
                "highlights": result.highlights,
                "warnings": result.warnings,
                "provider": result.provider,
                "model": result.model,
                "generated_at": result.generated_at.isoformat() if result.generated_at else None,
                "input_hash": result.input_hash,
            }
        else:
            rationale.pop("ai_summary", None)
        rationale["summary_meta"] = {
            "status": result.status,
            "provider": result.provider,
            "model": result.model,
            "input_hash": result.input_hash,
            "error": result.error,
        }
        signal.rationale_json = rationale
        return result

    def _build_summary_facts(
        self, signal: SignalWindow, qualifying_transactions: list[dict[str, object]]
    ) -> dict[str, object]:
        rationale = signal.rationale_json or {}
        return {
            "signal_id": signal.id,
            "issuer_cik": signal.issuer.cik,
            "ticker": signal.issuer.ticker,
            "issuer_name": signal.issuer.name,
            "window_start": signal.window_start.isoformat(),
            "window_end": signal.window_end.isoformat(),
            "signal_score": f"{signal.signal_score:.2f}",
            "market_cap": f"{signal.issuer.market_cap:.2f}" if signal.issuer.market_cap else None,
            "unique_buyers": signal.unique_buyers,
            "total_purchase_usd": f"{signal.total_purchase_usd:.2f}",
            "average_purchase_usd": f"{signal.average_purchase_usd:.2f}",
            "latest_transaction_date": rationale.get("latest_transaction_date"),
            "transaction_count": int(rationale.get("transaction_count", 0)),
            "first_time_buyer_count": int(rationale.get("first_time_buyer_count", 0)),
            "first_time_buyer_names": list(rationale.get("first_time_buyer_names", [])),
            "compensation_coverage_count": int(rationale.get("compensation_coverage_count", 0)),
            "compensation_covered_buyer_names": list(
                rationale.get("compensation_covered_buyer_names", [])
            ),
            "max_purchase_vs_compensation_ratio": rationale.get(
                "max_purchase_vs_compensation_ratio"
            ),
            "executive_buyer_count": int(rationale.get("executive_buyer_count", 0)),
            "executive_buyer_names": list(rationale.get("executive_buyer_names", [])),
            "includes_indirect": bool(rationale.get("includes_indirect", False)),
            "includes_amendment": bool(rationale.get("includes_amendment", False)),
            "health_status": str(rationale.get("health_status", "unknown")),
            "price_context_status": str(rationale.get("price_context_status", "unavailable")),
            "event_context_status": str(rationale.get("event_context_status", "not_implemented")),
            "explanation": str(rationale.get("explanation", "")),
            "component_breakdown": rationale.get("component_breakdown", {}),
            "qualifying_transactions": qualifying_transactions,
            "disclaimer": {
                "uses_public_sec_filings_only": True,
                "investment_advice": False,
                "review_original_filings": True,
            },
        }

    def _update_record(self, record: SignalWindow, signal: ComputedSignal) -> None:
        record.window_start = signal.window_start
        record.window_end = signal.window_end
        record.unique_buyers = signal.unique_buyers
        record.total_purchase_usd = signal.total_purchase_usd
        record.average_purchase_usd = signal.average_purchase_usd
        record.signal_score = signal.signal_score
        record.health_score = signal.health_score
        record.price_context_score = signal.price_context_score
        record.is_active = True
        record.rationale_json = signal.rationale_json

    def _to_summary(self, signal: SignalWindow) -> SignalSummary:
        rationale = signal.rationale_json or {}
        issuer = signal.issuer
        return SignalSummary(
            id=signal.id,
            issuer_cik=issuer.cik,
            ticker=issuer.ticker,
            issuer_name=issuer.name,
            window_start=signal.window_start,
            window_end=signal.window_end,
            unique_buyers=signal.unique_buyers,
            total_purchase_usd=signal.total_purchase_usd,
            average_purchase_usd=signal.average_purchase_usd,
            signal_score=signal.signal_score,
            latest_transaction_date=_maybe_date(rationale.get("latest_transaction_date")),
            transaction_count=int(rationale.get("transaction_count", 0)),
            first_time_buyer_count=int(rationale.get("first_time_buyer_count", 0)),
            includes_indirect=bool(rationale.get("includes_indirect", False)),
            includes_amendment=bool(rationale.get("includes_amendment", False)),
            health_status=str(rationale.get("health_status", "unknown")),
            price_context_status=str(rationale.get("price_context_status", "unavailable")),
            summary_status=signal.summary_status,
            explanation=str(rationale.get("explanation", "")),
            component_breakdown=rationale.get("component_breakdown", {}),
        )


def _build_trade_setup(
    signal: SignalWindow,
    qualifying_transactions: list[dict[str, object]],
) -> dict[str, object] | None:
    rationale = signal.rationale_json or {}
    component_breakdown = rationale.get("component_breakdown") or {}
    price_component = component_breakdown.get("price_context") or {}
    price_details = price_component.get("details") or {}

    cluster_prices = [
        Decimal(str(transaction["price_per_share"]))
        for transaction in qualifying_transactions
        if transaction.get("price_per_share") not in {None, ""}
    ]
    cluster_vwap = _weighted_cluster_price(qualifying_transactions)
    reference_price = (
        _as_decimal(price_details.get("latest_adjusted_close"))
        or signal.issuer.latest_price
        or cluster_vwap
    )
    entry_zone_low = min(cluster_prices) if cluster_prices else reference_price
    entry_zone_high = max(cluster_prices) if cluster_prices else reference_price
    swing_low_reference = _as_decimal(price_details.get("low_13w")) or entry_zone_low

    if (
        reference_price is None
        or entry_zone_low is None
        or entry_zone_high is None
        or swing_low_reference is None
    ):
        return None

    protective_stop = _quantize(max(Decimal("0.01"), swing_low_reference * Decimal("0.97")))
    risk_to_stop_pct = None
    if reference_price > Decimal("0") and protective_stop < reference_price:
        risk_to_stop_pct = _quantize(
            ((reference_price - protective_stop) / reference_price) * Decimal("100")
        )

    trigger_above = _quantize(max(reference_price, entry_zone_high))
    return {
        "setup_label": "review_only_pullback_plan",
        "entry_zone_low": float(_quantize(entry_zone_low)),
        "entry_zone_high": float(_quantize(entry_zone_high)),
        "cluster_purchase_vwap": (
            float(_quantize(cluster_vwap)) if cluster_vwap is not None else None
        ),
        "reference_price": float(_quantize(reference_price)),
        "swing_low_reference": float(_quantize(swing_low_reference)),
        "protective_stop": float(protective_stop),
        "trigger_above": float(trigger_above),
        "risk_to_stop_pct": float(risk_to_stop_pct) if risk_to_stop_pct is not None else None,
        "latest_transaction_date": rationale.get("latest_transaction_date"),
        "latest_price_source": price_details.get("provider") or "qualifying_transaction_prices",
        "disclaimer": (
            "Review setup only. Public filings can lag the actual transaction and this is not "
            "investment advice."
        ),
    }


def _weighted_cluster_price(
    qualifying_transactions: list[dict[str, object]],
) -> Decimal | None:
    total_value = Decimal("0")
    total_shares = Decimal("0")
    for transaction in qualifying_transactions:
        shares = _as_decimal(transaction.get("shares"))
        value = _as_decimal(transaction.get("value_usd"))
        if shares is None or value is None or shares <= Decimal("0"):
            continue
        total_value += value
        total_shares += shares
    if total_shares <= Decimal("0"):
        return None
    return total_value / total_shares


def _capture_existing_state(signal: SignalWindow | None) -> ExistingSignalState | None:
    if signal is None:
        return None
    rationale = signal.rationale_json or {}
    ai_summary = rationale.get("ai_summary") or None
    summary_meta = rationale.get("summary_meta") or {}
    return ExistingSignalState(
        signal_score=signal.signal_score,
        total_purchase_usd=signal.total_purchase_usd,
        unique_buyers=signal.unique_buyers,
        summary_status=signal.summary_status,
        summary_input_hash=(
            str(summary_meta.get("input_hash"))
            if summary_meta.get("input_hash") is not None
            else (
                str(ai_summary.get("input_hash"))
                if isinstance(ai_summary, dict) and ai_summary.get("input_hash") is not None
                else None
            )
        ),
        summary_payload=ai_summary if isinstance(ai_summary, dict) else None,
    )


def _summary_from_rationale(rationale: dict[str, Any]) -> SignalAiSummary | None:
    payload = rationale.get("ai_summary")
    if not isinstance(payload, dict) or not payload.get("text"):
        return None
    return SignalAiSummary(
        text=str(payload.get("text")),
        highlights=[str(item) for item in payload.get("highlights", [])],
        warnings=[str(item) for item in payload.get("warnings", [])],
        provider=str(payload.get("provider", "unknown")),
        model=str(payload.get("model")) if payload.get("model") is not None else None,
        generated_at=_maybe_datetime(payload.get("generated_at")),
    )


def _alerts_from_records(alerts: list[Alert]) -> list[SignalAlertRecord]:
    ordered = sorted(alerts, key=lambda item: (item.sent_at or datetime.min, item.id), reverse=True)
    return [
        SignalAlertRecord(
            id=alert.id,
            channel=alert.channel,
            status=alert.status,
            sent_at=alert.sent_at,
            event_type=str((alert.payload_json or {}).get("event_type", "unknown")),
            reason=str((alert.payload_json or {}).get("reason", "")),
            score_at_send=Decimal(
                str(((alert.payload_json or {}).get("signal") or {}).get("signal_score", "0"))
            ),
            total_purchase_usd_at_send=Decimal(
                str(((alert.payload_json or {}).get("signal") or {}).get("total_purchase_usd", "0"))
            ),
            unique_buyers_at_send=int(
                ((alert.payload_json or {}).get("signal") or {}).get("unique_buyers", 0)
            ),
        )
        for alert in ordered
    ]


def _signal_key(issuer_id: int, window_start: date, window_end: date) -> tuple[int, date, date]:
    return (issuer_id, window_start, window_end)


def _maybe_date(value: object):
    if not value:
        return None
    return date.fromisoformat(str(value))


def _maybe_datetime(value: object) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value))


def _build_compensation_index(
    session: Session,
    issuer_ids: set[int],
) -> dict[int, list[InsiderCompensation]]:
    if not issuer_ids:
        return {}
    rows = session.scalars(
        select(InsiderCompensation)
        .where(InsiderCompensation.issuer_id.in_(issuer_ids))
        .order_by(
            InsiderCompensation.issuer_id,
            InsiderCompensation.filed_at.desc(),
            InsiderCompensation.id.desc(),
        )
    ).all()
    grouped: dict[int, list[InsiderCompensation]] = {}
    for row in rows:
        grouped.setdefault(row.issuer_id, []).append(row)
    return grouped


def _latest_compensation_for_candidate(
    records: list[InsiderCompensation],
    insider: Insider,
    filed_at: datetime,
) -> InsiderCompensation | None:
    if not records:
        return None
    target_name = _normalize_person_name(insider.name)
    filing_date = filed_at.date()
    for record in records:
        if record.filed_at.date() > filing_date:
            continue
        if record.insider_id == insider.id:
            return record
    for record in records:
        if record.filed_at.date() > filing_date:
            continue
        if _normalize_person_name(record.insider_name) == target_name:
            return record
    return None


def _insider_role(insider: Insider, fallback_title: str | None = None) -> str | None:
    roles: list[str] = []
    if insider.is_director:
        roles.append("Director")
    if insider.is_officer:
        roles.append("Officer")
    if insider.is_ten_percent_owner:
        roles.append("10% Owner")
    title = insider.officer_title or fallback_title
    if title:
        roles.append(title)
    if not roles:
        return None
    return ", ".join(dict.fromkeys(roles))


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _as_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(_quantize(value))


def _normalize_person_name(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def _role_weight_multiplier(role: str | None) -> Decimal:
    if not role:
        return Decimal("1.00")
    normalized = role.lower()
    if "chief executive officer" in normalized or " ceo" in normalized:
        return Decimal("1.35")
    if "chief financial officer" in normalized or " cfo" in normalized:
        return Decimal("1.30")
    if any(
        token in normalized for token in ["chief", " president", "coo", "cto", "chief operating"]
    ):
        return Decimal("1.20")
    if "officer" in normalized:
        return Decimal("1.15")
    if "director" in normalized or "chair" in normalized:
        return Decimal("1.10")
    return Decimal("1.00")


def _reweighted(raw_score: Decimal | None, total_max: Decimal) -> float | None:
    if raw_score is None or not total_max:
        return None
    return float(_quantize((raw_score / total_max) * Decimal("100")))


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
