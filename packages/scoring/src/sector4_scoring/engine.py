from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from statistics import mean
from typing import Any

from sector4_core.config import Settings

ZERO = Decimal("0")
HUNDRED = Decimal("100")
CLUSTER_MAX = Decimal("30")
CONVICTION_MAX = Decimal("25")
PRICE_CONTEXT_MAX = Decimal("15")
HEALTH_MAX = Decimal("20")
EVENT_MAX = Decimal("10")
AVAILABLE_TOTAL = CLUSTER_MAX + CONVICTION_MAX
ROUTINE_HISTORY_LOOKBACK_YEARS = 3


@dataclass(slots=True)
class CandidateBuy:
    transaction_id: int
    filing_id: int
    accession_number: str
    source_url: str
    xml_url: str
    filed_at: datetime
    is_amendment: bool
    issuer_id: int
    issuer_cik: str
    issuer_name: str
    issuer_ticker: str | None
    insider_id: int
    insider_name: str
    insider_role: str | None
    transaction_date: date
    security_title: str | None
    shares: Decimal | None
    price_per_share: Decimal | None
    value_usd: Decimal
    shares_after: Decimal | None
    ownership_type: str | None
    transaction_code: str | None
    annual_compensation_usd: Decimal | None = None
    compensation_purchase_ratio: Decimal | None = None
    role_weight_multiplier: Decimal = Decimal("1.00")
    is_first_time_buyer: bool = False
    is_repeat_quarter_buyer: bool = False
    repeat_quarter_years: int = 0


@dataclass(slots=True)
class ComputedSignal:
    issuer_id: int
    issuer_cik: str
    issuer_name: str
    issuer_ticker: str | None
    window_start: date
    window_end: date
    unique_buyers: int
    total_purchase_usd: Decimal
    average_purchase_usd: Decimal
    signal_score: Decimal
    price_context_score: Decimal | None
    health_score: Decimal | None
    rationale_json: dict[str, Any]


def compute_signal_windows(
    candidates: list[CandidateBuy], settings: Settings
) -> list[ComputedSignal]:
    grouped: dict[int, list[CandidateBuy]] = defaultdict(list)
    for candidate in _annotate_candidate_history(_dedupe_candidates(candidates)):
        if candidate.is_repeat_quarter_buyer:
            continue
        grouped[candidate.issuer_id].append(candidate)

    signals: list[ComputedSignal] = []
    window_span = max(settings.default_cluster_window_days - 1, 0)
    for issuer_candidates in grouped.values():
        ordered = sorted(
            issuer_candidates,
            key=lambda item: (item.transaction_date, item.filed_at, item.transaction_id),
        )
        for end_index, end_candidate in enumerate(ordered):
            window_start = end_candidate.transaction_date - timedelta(days=window_span)
            window_candidates = [
                candidate
                for candidate in ordered[: end_index + 1]
                if window_start <= candidate.transaction_date <= end_candidate.transaction_date
            ]
            signal = _build_signal(window_candidates, settings)
            if signal is None:
                continue
            signals.append(signal)

    signals = _collapse_duplicate_windows(signals)
    signals.sort(key=lambda item: (item.signal_score, item.window_end), reverse=True)
    return signals


def _collapse_duplicate_windows(signals: list[ComputedSignal]) -> list[ComputedSignal]:
    collapsed: dict[tuple[int, date, date], ComputedSignal] = {}
    for signal in signals:
        key = (signal.issuer_id, signal.window_start, signal.window_end)
        existing = collapsed.get(key)
        if existing is None or _signal_rank(signal) > _signal_rank(existing):
            collapsed[key] = signal
    return list(collapsed.values())


def _signal_rank(signal: ComputedSignal) -> tuple[Decimal, Decimal, int, int, int]:
    rationale = signal.rationale_json or {}
    return (
        signal.signal_score,
        signal.total_purchase_usd,
        int(rationale.get("transaction_count", 0)),
        signal.unique_buyers,
        int(bool(rationale.get("includes_indirect", False))),
    )


def _annotate_candidate_history(candidates: list[CandidateBuy]) -> list[CandidateBuy]:
    prior_history: dict[tuple[int, int], list[date]] = defaultdict(list)
    for candidate in sorted(
        candidates,
        key=lambda item: (item.transaction_date, item.filed_at, item.transaction_id),
    ):
        history_key = (candidate.issuer_id, candidate.insider_id)
        prior_dates = prior_history[history_key]
        candidate.is_first_time_buyer = len(prior_dates) == 0
        candidate.repeat_quarter_years = len(
            {
                prior_date.year
                for prior_date in prior_dates
                if _is_repeat_calendar_quarter(prior_date, candidate.transaction_date)
            }
        )
        candidate.is_repeat_quarter_buyer = candidate.repeat_quarter_years > 0
        prior_dates.append(candidate.transaction_date)
    return candidates


def _is_repeat_calendar_quarter(prior_date: date, current_date: date) -> bool:
    year_delta = current_date.year - prior_date.year
    if year_delta <= 0 or year_delta > ROUTINE_HISTORY_LOOKBACK_YEARS:
        return False
    return _quarter(prior_date) == _quarter(current_date)


def _quarter(value: date) -> int:
    return ((value.month - 1) // 3) + 1


def _dedupe_candidates(candidates: list[CandidateBuy]) -> list[CandidateBuy]:
    deduped: dict[tuple[Any, ...], CandidateBuy] = {}
    for candidate in sorted(
        candidates,
        key=lambda item: (item.transaction_date, item.filed_at, item.transaction_id),
    ):
        dedupe_key = (
            candidate.issuer_id,
            candidate.insider_id,
            candidate.transaction_date,
            candidate.security_title or "",
            candidate.ownership_type or "",
            candidate.transaction_code or "",
        )
        existing = deduped.get(dedupe_key)
        if existing is None:
            deduped[dedupe_key] = candidate
            continue
        if candidate.is_amendment and not existing.is_amendment:
            deduped[dedupe_key] = candidate
            continue
        if candidate.filed_at >= existing.filed_at:
            deduped[dedupe_key] = candidate
    return list(deduped.values())


def _build_signal(
    window_candidates: list[CandidateBuy], settings: Settings
) -> ComputedSignal | None:
    if not window_candidates:
        return None

    issuer = window_candidates[0]
    unique_buyers = len({candidate.insider_id for candidate in window_candidates})
    total_purchase_usd = sum((candidate.value_usd for candidate in window_candidates), start=ZERO)
    average_purchase_usd = total_purchase_usd / max(unique_buyers, 1)
    if unique_buyers < settings.default_min_unique_buyers:
        return None
    if total_purchase_usd < Decimal(str(settings.default_min_total_purchase_usd)):
        return None

    cluster, cluster_details = _cluster_component(window_candidates, settings)
    conviction, conviction_details = _conviction_component(window_candidates, settings)
    if cluster_details["total_value_points"] < 5 and conviction < Decimal("5"):
        return None

    signal_score = _quantize(((cluster + conviction) / AVAILABLE_TOTAL) * HUNDRED)
    component_breakdown = {
        "cluster_strength": {
            "status": "available",
            "raw_score": float(_quantize(cluster)),
            "max_score": float(CLUSTER_MAX),
            "reweighted_score": float(_quantize((cluster / AVAILABLE_TOTAL) * HUNDRED)),
            "details": cluster_details,
        },
        "conviction": {
            "status": "available",
            "raw_score": float(_quantize(conviction)),
            "max_score": float(CONVICTION_MAX),
            "reweighted_score": float(_quantize((conviction / AVAILABLE_TOTAL) * HUNDRED)),
            "details": conviction_details,
        },
        "price_context": {
            "status": "unavailable",
            "raw_score": None,
            "max_score": float(PRICE_CONTEXT_MAX),
            "reweighted_score": None,
            "details": {"reason": "no_market_data_provider_configured"},
        },
        "health": {
            "status": "unknown",
            "raw_score": None,
            "max_score": float(HEALTH_MAX),
            "reweighted_score": None,
            "details": {"reason": "fundamental_enrichment_not_implemented"},
        },
        "event_context": {
            "status": "not_implemented",
            "raw_score": None,
            "max_score": float(EVENT_MAX),
            "reweighted_score": None,
            "details": {"reason": "later_milestone"},
        },
    }
    latest_transaction_date = max(candidate.transaction_date for candidate in window_candidates)
    first_transaction_date = min(candidate.transaction_date for candidate in window_candidates)
    span_days = (latest_transaction_date - first_transaction_date).days + 1
    first_time_buyers = sorted(
        {candidate.insider_name for candidate in window_candidates if candidate.is_first_time_buyer}
    )
    compensation_covered_buyers = sorted(
        {
            candidate.insider_name
            for candidate in window_candidates
            if candidate.annual_compensation_usd is not None
            and candidate.annual_compensation_usd > ZERO
        }
    )
    executive_buyers = sorted(
        {
            candidate.insider_name
            for candidate in window_candidates
            if candidate.role_weight_multiplier > Decimal("1.00")
        }
    )
    compensation_ratios = [
        candidate.compensation_purchase_ratio
        for candidate in window_candidates
        if candidate.compensation_purchase_ratio is not None
    ]
    priced_candidates = [
        candidate
        for candidate in window_candidates
        if candidate.shares is not None
        and candidate.shares > ZERO
        and candidate.price_per_share is not None
        and candidate.price_per_share > ZERO
    ]
    market_cap_price_hint = None
    if priced_candidates:
        total_priced_shares = sum(
            (candidate.shares for candidate in priced_candidates if candidate.shares is not None),
            start=ZERO,
        )
        if total_priced_shares > ZERO:
            total_priced_value = sum(
                (candidate.value_usd for candidate in priced_candidates),
                start=ZERO,
            )
            market_cap_price_hint = _quantize(total_priced_value / total_priced_shares)
    explanation = (
        f"{unique_buyers} unique insiders bought ${_money(total_purchase_usd)} "
        f"of {issuer.issuer_ticker or issuer.issuer_name} over {span_days} days."
    )
    if compensation_ratios:
        average_ratio = Decimal(str(mean(compensation_ratios)))
        explanation = (
            f"{explanation} Covered insiders committed about "
            f"{_percent(average_ratio)} of annual compensation on average."
        )
    if first_time_buyers:
        explanation = (
            f"{explanation} {len(first_time_buyers)} buyer(s) were first-time open-market "
            "purchasers on record for this issuer."
        )
    includes_indirect = any(
        (candidate.ownership_type or "D").upper() != "D" for candidate in window_candidates
    )
    includes_amendment = any(candidate.is_amendment for candidate in window_candidates)
    return ComputedSignal(
        issuer_id=issuer.issuer_id,
        issuer_cik=issuer.issuer_cik,
        issuer_name=issuer.issuer_name,
        issuer_ticker=issuer.issuer_ticker,
        window_start=first_transaction_date,
        window_end=latest_transaction_date,
        unique_buyers=unique_buyers,
        total_purchase_usd=_quantize(total_purchase_usd),
        average_purchase_usd=_quantize(average_purchase_usd),
        signal_score=signal_score,
        price_context_score=None,
        health_score=None,
        rationale_json={
            "component_breakdown": component_breakdown,
            "qualifying_transaction_ids": [
                candidate.transaction_id for candidate in window_candidates
            ],
            "transaction_count": len(window_candidates),
            "latest_transaction_date": latest_transaction_date.isoformat(),
            "health_status": "unknown",
            "price_context_status": "unavailable",
            "first_time_buyer_count": len(first_time_buyers),
            "first_time_buyer_names": first_time_buyers,
            "compensation_coverage_count": len(compensation_covered_buyers),
            "compensation_covered_buyer_names": compensation_covered_buyers,
            "max_purchase_vs_compensation_ratio": (
                str(_quantize(max(compensation_ratios))) if compensation_ratios else None
            ),
            "executive_buyer_count": len(executive_buyers),
            "executive_buyer_names": executive_buyers,
            "market_cap_price_hint": (
                str(market_cap_price_hint) if market_cap_price_hint is not None else None
            ),
            "market_cap_price_hint_source": (
                "weighted_average_cluster_purchase_price"
                if market_cap_price_hint is not None
                else None
            ),
            "includes_indirect": includes_indirect,
            "includes_amendment": includes_amendment,
            "routine_history_filter_applied": True,
            "explanation": explanation,
        },
    )


def _cluster_component(
    window_candidates: list[CandidateBuy], settings: Settings
) -> tuple[Decimal, dict[str, float]]:
    unique_buyers = len({candidate.insider_id for candidate in window_candidates})
    transaction_count = len(window_candidates)
    total_purchase_usd = sum((candidate.value_usd for candidate in window_candidates), start=ZERO)
    buyers_points = min(Decimal(unique_buyers) / Decimal("4"), Decimal("1")) * Decimal("12")
    transaction_points = min(Decimal(transaction_count) / Decimal("4"), Decimal("1")) * Decimal("8")
    value_denominator = Decimal(str(settings.default_min_total_purchase_usd)) * Decimal("3")
    total_value_points = min(total_purchase_usd / value_denominator, Decimal("1")) * Decimal("10")
    score = buyers_points + transaction_points + total_value_points
    return score, {
        "buyers_points": float(_quantize(buyers_points)),
        "transaction_points": float(_quantize(transaction_points)),
        "total_value_points": float(_quantize(total_value_points)),
    }


def _conviction_component(
    window_candidates: list[CandidateBuy], settings: Settings
) -> tuple[Decimal, dict[str, float | int | bool | None]]:
    unique_buyers = max(len({candidate.insider_id for candidate in window_candidates}), 1)
    total_purchase_usd = sum((candidate.value_usd for candidate in window_candidates), start=ZERO)
    average_purchase_usd = total_purchase_usd / Decimal(unique_buyers)
    purchase_size_points = min(
        average_purchase_usd / Decimal(str(settings.default_min_total_purchase_usd)),
        Decimal("1"),
    ) * Decimal("10")

    holding_ratios: list[Decimal] = []
    for candidate in window_candidates:
        if (
            candidate.shares_after is None
            or candidate.shares is None
            or candidate.price_per_share is None
            or candidate.shares_after <= candidate.shares
            or candidate.price_per_share <= ZERO
        ):
            continue
        prior_value = (candidate.shares_after - candidate.shares) * candidate.price_per_share
        if prior_value > ZERO:
            holding_ratios.append(candidate.value_usd / prior_value)
    holding_points = (
        min(Decimal(str(mean(holding_ratios))) / Decimal("0.15"), Decimal("1")) * Decimal("10")
        if holding_ratios
        else Decimal("0")
    )

    dates = [candidate.transaction_date for candidate in window_candidates]
    span_days = (max(dates) - min(dates)).days if dates else 0
    timing_points = max(
        ZERO,
        Decimal("5") * (Decimal("1") - (Decimal(span_days) / Decimal("30"))),
    )

    first_time_buyer_count = len(
        {candidate.insider_id for candidate in window_candidates if candidate.is_first_time_buyer}
    )
    first_time_bonus_points = min(
        Decimal(first_time_buyer_count) / Decimal(unique_buyers), Decimal("1")
    ) * Decimal("2")

    compensation_ratios = [
        candidate.compensation_purchase_ratio
        for candidate in window_candidates
        if candidate.compensation_purchase_ratio is not None
    ]
    average_compensation_ratio = (
        Decimal(str(mean(compensation_ratios))) if compensation_ratios else None
    )
    compensation_purchase_points = (
        min(average_compensation_ratio / Decimal("0.25"), Decimal("1")) * Decimal("3")
        if average_compensation_ratio is not None and average_compensation_ratio > ZERO
        else Decimal("0")
    )

    average_role_weight_multiplier = (
        sum((candidate.role_weight_multiplier for candidate in window_candidates), start=ZERO)
        / Decimal(len(window_candidates))
        if window_candidates
        else Decimal("1")
    )
    executive_role_points = (
        min(
            (average_role_weight_multiplier - Decimal("1")) / Decimal("0.35"),
            Decimal("1"),
        )
        * Decimal("2")
        if average_role_weight_multiplier > Decimal("1")
        else Decimal("0")
    )

    raw_score = (
        purchase_size_points
        + holding_points
        + timing_points
        + first_time_bonus_points
        + compensation_purchase_points
        + executive_role_points
    )
    score = min(raw_score, CONVICTION_MAX)
    return score, {
        "purchase_size_points": float(_quantize(purchase_size_points)),
        "holding_ratio_points": float(_quantize(holding_points)),
        "timing_points": float(_quantize(timing_points)),
        "first_time_buyer_count": first_time_buyer_count,
        "first_time_bonus_points": float(_quantize(first_time_bonus_points)),
        "compensation_coverage_count": len(compensation_ratios),
        "average_compensation_purchase_ratio": (
            float(_quantize(average_compensation_ratio))
            if average_compensation_ratio is not None
            else None
        ),
        "compensation_purchase_ratio_points": float(_quantize(compensation_purchase_points)),
        "average_role_weight_multiplier": float(_quantize(average_role_weight_multiplier)),
        "executive_role_points": float(_quantize(executive_role_points)),
        "conviction_score_capped": raw_score > CONVICTION_MAX,
    }


def _money(value: Decimal) -> str:
    quantized = _quantize(value)
    return f"{quantized:,.2f}"


def _percent(value: Decimal) -> str:
    return f"{_quantize(value * Decimal('100'))}%"


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
