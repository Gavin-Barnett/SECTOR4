from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx

from sector4_core.config import Settings, get_settings
from sector4_core.enrichment import (
    EventContextSnapshot,
    HealthSnapshot,
    IssuerEnrichmentRequest,
    IssuerEnrichmentSnapshot,
    PriceContextSnapshot,
)
from sector4_sec_ingestion.client import SecClient, normalize_cik

_ALLOWED_FACT_FORMS = (
    "10-K",
    "10-K/A",
    "10-Q",
    "10-Q/A",
    "20-F",
    "20-F/A",
    "40-F",
    "40-F/A",
)
_SHARES_OUTSTANDING_CONCEPTS = (
    "EntityCommonStockSharesOutstanding",
    "CommonStockSharesOutstanding",
)
_PUBLIC_FLOAT_CONCEPTS = ("EntityPublicFloat",)
_RETAINED_EARNINGS_CONCEPTS = (
    "RetainedEarningsAccumulatedDeficit",
    "RetainedEarnings",
)
_OPERATING_INCOME_CONCEPTS = (
    "OperatingIncomeLoss",
    "IncomeBeforeTaxExpenseBenefit",
)
_REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "Revenues",
)
_CASH_CONCEPTS = (
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
)
_DEBT_DUE_WITHIN_YEAR_CONCEPTS = (
    "LongTermDebtCurrent",
    "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "CurrentPortionOfLongTermDebt",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
)
_DEBT_NONCURRENT_CONCEPTS = (
    "LongTermDebtNoncurrent",
    "LongTermDebtAndCapitalLeaseObligationsNoncurrent",
)
_TOTAL_DEBT_CONCEPTS = (
    "LongTermDebtAndCapitalLeaseObligations",
    "LongTermDebt",
)
_INTEREST_EXPENSE_CONCEPTS = (
    "InterestExpenseAndDebtExpense",
    "InterestExpense",
)
_EIGHT_K_EVENT_FORMS = {"8-K", "8-K/A"}
_PERIODIC_EVENT_FORMS = set(_ALLOWED_FACT_FORMS)
_EARNINGS_EVENT_ITEMS = {"2.02"}
_CORPORATE_EVENT_ITEMS = {"1.01", "1.03", "2.01", "2.03", "2.05", "5.02", "8.01"}
_EARNINGS_CADENCE_DAYS = 90
_ZERO = Decimal("0")
_EVENT_CONTEXT_MAX = Decimal("10")


@dataclass(slots=True)
class FactObservation:
    concept: str
    value: Decimal
    form: str | None
    filed: str | None
    end: str | None


@dataclass(slots=True)
class RecentFilingObservation:
    form: str
    filed_at: date
    accession_number: str | None
    primary_document: str | None
    items: str | None
    item_codes: tuple[str, ...]


@dataclass(slots=True)
class RecentFilingMatch:
    filing: RecentFilingObservation
    days_before_anchor: int
    points: Decimal


@dataclass(slots=True)
class UpcomingEarningsWindow:
    source_label: str
    event_date: date
    days_until_event: int
    points: Decimal


class SecCompanyfactsEnrichmentProvider:
    def __init__(
        self,
        settings: Settings | None = None,
        client: SecClient | None = None,
        companyfacts_loader: Callable[[str], dict[str, Any]] | None = None,
        submissions_loader: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client
        self._owns_client = False
        if self.client is None and (companyfacts_loader is None or submissions_loader is None):
            self.client = SecClient(self.settings)
            self._owns_client = True
        self._companyfacts_loader = companyfacts_loader or self.client.fetch_companyfacts
        self._submissions_loader = submissions_loader or self.client.fetch_submissions
        self._snapshot_cache: dict[tuple[str, str, str, str, str], IssuerEnrichmentSnapshot] = {}
        self._submissions_cache: dict[str, tuple[dict[str, Any] | None, str | None]] = {}
        self._companyfacts_cache: dict[str, tuple[dict[str, Any] | None, str | None]] = {}

    def enrich(self, request: IssuerEnrichmentRequest) -> IssuerEnrichmentSnapshot:
        cik = normalize_cik(request.cik)
        anchor_key = request.event_anchor_date.isoformat() if request.event_anchor_date else ""
        price_key = str(request.latest_price) if request.latest_price is not None else ""
        hint_key = (
            str(request.market_cap_price_hint) if request.market_cap_price_hint is not None else ""
        )
        earnings_key = (
            request.upcoming_earnings_date.isoformat() if request.upcoming_earnings_date else ""
        )
        cache_key = (cik, anchor_key, price_key, hint_key, earnings_key)
        cached = self._snapshot_cache.get(cache_key)
        if cached is not None:
            return cached

        submissions, submissions_error = self._load_submissions(cik)
        companyfacts, companyfacts_error = self._load_companyfacts(cik)
        shares_outstanding = _latest_share_fact(companyfacts, list(_SHARES_OUTSTANDING_CONCEPTS))
        public_float = _latest_dei_usd_fact(companyfacts, list(_PUBLIC_FLOAT_CONCEPTS))

        price_context = PriceContextSnapshot(
            status="unavailable",
            details={
                "source": "sec_companyfacts",
                "reason": "sec_provider_has_no_market_prices",
            },
        )
        market_cap = request.market_cap
        market_cap_source = "request_market_cap" if request.market_cap is not None else None
        market_cap_input_price = request.latest_price
        if market_cap_input_price is None or market_cap_input_price <= _ZERO:
            market_cap_input_price = request.market_cap_price_hint
        if (
            market_cap is None
            and market_cap_input_price is not None
            and market_cap_input_price > _ZERO
            and shares_outstanding is not None
        ):
            market_cap = _quantize(market_cap_input_price * shares_outstanding.value)
            market_cap_source = "price_times_shares_outstanding"
        elif market_cap is None and public_float is not None and public_float.value > _ZERO:
            market_cap = _quantize(public_float.value)
            market_cap_source = "entity_public_float"

        health = self._build_health_snapshot(
            companyfacts,
            companyfacts_error,
            market_cap,
            market_cap_source,
        )
        event_context = self._build_event_context_snapshot(
            submissions,
            request.event_anchor_date,
            request.upcoming_earnings_date,
            request.earnings_date_source,
            submissions_error,
        )
        snapshot = IssuerEnrichmentSnapshot(
            market_cap=market_cap,
            latest_price=request.latest_price,
            exchange=_first_string(submissions, "exchanges"),
            sic=_string_or_none(submissions, "sic"),
            state_of_incorp=(
                _string_or_none(submissions, "stateOfIncorporation")
                or _string_or_none(submissions, "stateOfIncorporationDescription")
            ),
            upcoming_earnings_date=request.upcoming_earnings_date,
            earnings_date_source=request.earnings_date_source,
            price_context=price_context,
            health=health,
            event_context=event_context,
        )
        self._snapshot_cache[cache_key] = snapshot
        return snapshot

    def close(self) -> None:
        if self._owns_client and self.client is not None:
            self.client.close()

    def _load_submissions(self, cik: str) -> tuple[dict[str, Any] | None, str | None]:
        cached = self._submissions_cache.get(cik)
        if cached is not None:
            return cached
        try:
            submissions = self._submissions_loader(cik)
            result = (submissions, None)
        except (httpx.HTTPError, ValueError) as exc:
            result = (None, str(exc))
        self._submissions_cache[cik] = result
        return result

    def _load_companyfacts(self, cik: str) -> tuple[dict[str, Any] | None, str | None]:
        cached = self._companyfacts_cache.get(cik)
        if cached is not None:
            return cached
        try:
            companyfacts = self._companyfacts_loader(cik)
            result = (companyfacts, None)
        except (httpx.HTTPError, ValueError) as exc:
            result = (None, str(exc))
        self._companyfacts_cache[cik] = result
        return result

    def _build_health_snapshot(
        self,
        companyfacts: dict[str, Any] | None,
        companyfacts_error: str | None,
        market_value_equity: Decimal | None,
        market_value_equity_source: str | None,
    ) -> HealthSnapshot:
        if companyfacts is None:
            details: dict[str, Any] = {"source": "sec_companyfacts"}
            if companyfacts_error:
                details["reason"] = "companyfacts_fetch_failed"
                details["error"] = companyfacts_error
            else:
                details["reason"] = "companyfacts_unavailable"
            return HealthSnapshot(status="unknown", details=details)

        current_assets = _latest_usd_fact(companyfacts, ["AssetsCurrent"])
        current_liabilities = _latest_usd_fact(companyfacts, ["LiabilitiesCurrent"])
        total_assets = _latest_usd_fact(companyfacts, ["Assets"])
        total_liabilities = _latest_usd_fact(companyfacts, ["Liabilities"])
        stockholders_equity = _latest_usd_fact(
            companyfacts,
            [
                "StockholdersEquity",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            ],
        )
        retained_earnings = _latest_usd_fact(companyfacts, list(_RETAINED_EARNINGS_CONCEPTS))
        operating_income = _latest_usd_fact(companyfacts, list(_OPERATING_INCOME_CONCEPTS))
        revenue = _latest_usd_fact(companyfacts, list(_REVENUE_CONCEPTS))
        cash_and_equivalents = _latest_usd_fact(companyfacts, list(_CASH_CONCEPTS))
        debt_due_within_year = _latest_usd_fact(companyfacts, list(_DEBT_DUE_WITHIN_YEAR_CONCEPTS))
        total_debt = _latest_usd_fact(companyfacts, list(_TOTAL_DEBT_CONCEPTS))
        if total_debt is None:
            total_debt = _combined_fact_observation(
                debt_due_within_year,
                _latest_usd_fact(companyfacts, list(_DEBT_NONCURRENT_CONCEPTS)),
                "DerivedLongTermDebtAndCapitalLeaseObligations",
            )
        interest_expense = _latest_usd_fact(companyfacts, list(_INTEREST_EXPENSE_CONCEPTS))

        if current_assets is None or current_liabilities is None:
            return HealthSnapshot(
                status="unknown",
                details={
                    "source": "sec_companyfacts",
                    "reason": "missing_current_ratio_inputs",
                    "current_assets_present": current_assets is not None,
                    "current_liabilities_present": current_liabilities is not None,
                },
            )

        working_capital = current_assets.value - current_liabilities.value
        current_ratio = (
            Decimal("99")
            if current_liabilities.value == _ZERO
            else current_assets.value / current_liabilities.value
        )

        altman_missing_inputs = _altman_missing_inputs(
            total_assets,
            total_liabilities,
            retained_earnings,
            operating_income,
            revenue,
            market_value_equity,
        )
        altman_z_score = None
        altman_zone = "unavailable"
        if not altman_missing_inputs and market_value_equity is not None:
            altman_z_score = _compute_altman_z_score(
                working_capital=working_capital,
                total_assets=total_assets,
                retained_earnings=retained_earnings,
                operating_income=operating_income,
                market_value_equity=market_value_equity,
                total_liabilities=total_liabilities,
                revenue=revenue,
            )
            altman_zone = _altman_zone(altman_z_score)

        debt_due_ratio = _safe_ratio_observation(debt_due_within_year, total_debt)
        debt_cover_ratio = _safe_ratio_values(
            cash_and_equivalents.value if cash_and_equivalents is not None else None,
            debt_due_within_year.value if debt_due_within_year is not None else None,
        )
        interest_coverage_ratio = _interest_coverage_ratio(operating_income, interest_expense)

        distress_flags: list[str] = []
        if current_ratio < Decimal("1.0"):
            distress_flags.append("weak_liquidity")
        if (
            total_assets is not None
            and total_liabilities is not None
            and total_liabilities.value > total_assets.value
        ):
            distress_flags.append("liabilities_exceed_assets")
        if stockholders_equity is not None and stockholders_equity.value <= _ZERO:
            distress_flags.append("negative_equity")
        if altman_z_score is not None and altman_z_score < Decimal("1.81"):
            distress_flags.append("altman_distress_zone")
        if (
            debt_due_within_year is not None
            and debt_due_within_year.value > _ZERO
            and cash_and_equivalents is not None
            and cash_and_equivalents.value < debt_due_within_year.value
        ):
            distress_flags.append("near_term_debt_exceeds_cash")
        if debt_due_ratio is not None and debt_due_ratio >= Decimal("0.50"):
            distress_flags.append("heavy_near_term_debt_maturity")
        if interest_coverage_ratio is not None and interest_coverage_ratio < Decimal("1.50"):
            distress_flags.append("weak_interest_coverage")

        liquidity_points = _liquidity_points(current_ratio)
        solvency_points = _solvency_points(total_assets, total_liabilities, stockholders_equity)
        altman_points = _altman_points(altman_z_score)
        debt_maturity_points = _debt_maturity_points(
            debt_due_within_year, cash_and_equivalents, debt_due_ratio
        )
        credit_proxy_points = _credit_proxy_points(interest_coverage_ratio)
        score = _clamp_score(
            liquidity_points
            + solvency_points
            + altman_points
            + debt_maturity_points
            + credit_proxy_points
        )

        if altman_z_score is not None:
            if (
                altman_z_score < Decimal("1.81")
                or "negative_equity" in distress_flags
                or "weak_interest_coverage" in distress_flags
            ):
                status = "distressed"
            elif (
                altman_z_score < Decimal("3.0")
                or current_ratio < Decimal("1.25")
                or "near_term_debt_exceeds_cash" in distress_flags
                or "heavy_near_term_debt_maturity" in distress_flags
            ):
                status = "caution"
            else:
                status = "healthy"
        elif current_ratio < Decimal("1.0"):
            status = "distressed"
        elif (
            current_ratio < Decimal("1.5")
            or solvency_points < Decimal("3")
            or "near_term_debt_exceeds_cash" in distress_flags
            or "weak_interest_coverage" in distress_flags
        ):
            status = "caution"
        else:
            status = "healthy"

        return HealthSnapshot(
            status=status,
            score=_quantize(score),
            details={
                "source": "sec_companyfacts",
                "model": "altman_z_liquidity_debt_maturity",
                "current_ratio": float(_quantize(current_ratio)),
                "working_capital_usd": float(_quantize(working_capital)),
                "current_assets_usd": float(_quantize(current_assets.value)),
                "current_liabilities_usd": float(_quantize(current_liabilities.value)),
                "total_assets_usd": _fact_value(total_assets),
                "total_liabilities_usd": _fact_value(total_liabilities),
                "stockholders_equity_usd": _fact_value(stockholders_equity),
                "retained_earnings_usd": _fact_value(retained_earnings),
                "operating_income_usd": _fact_value(operating_income),
                "revenue_usd": _fact_value(revenue),
                "cash_and_equivalents_usd": _fact_value(cash_and_equivalents),
                "debt_due_within_year_usd": _fact_value(debt_due_within_year),
                "total_debt_usd": _fact_value(total_debt),
                "debt_due_within_year_ratio": (
                    float(_quantize(debt_due_ratio)) if debt_due_ratio is not None else None
                ),
                "cash_to_near_term_debt_ratio": (
                    float(_quantize(debt_cover_ratio)) if debt_cover_ratio is not None else None
                ),
                "interest_coverage_ratio": (
                    float(_quantize(interest_coverage_ratio))
                    if interest_coverage_ratio is not None
                    else None
                ),
                "market_value_equity_usd": (
                    float(_quantize(market_value_equity))
                    if market_value_equity is not None
                    else None
                ),
                "market_value_equity_source": market_value_equity_source,
                "altman_z_score": (
                    float(_quantize(altman_z_score)) if altman_z_score is not None else None
                ),
                "altman_zone": altman_zone,
                "altman_inputs_missing": altman_missing_inputs,
                "distress_flags": distress_flags,
                "health_component_points": {
                    "altman_points": float(_quantize(altman_points)),
                    "liquidity_points": float(_quantize(liquidity_points)),
                    "solvency_points": float(_quantize(solvency_points)),
                    "debt_maturity_points": float(_quantize(debt_maturity_points)),
                    "credit_proxy_points": float(_quantize(credit_proxy_points)),
                },
                "facts": {
                    "current_assets": _fact_reference(current_assets),
                    "current_liabilities": _fact_reference(current_liabilities),
                    "total_assets": _fact_reference(total_assets),
                    "total_liabilities": _fact_reference(total_liabilities),
                    "stockholders_equity": _fact_reference(stockholders_equity),
                    "retained_earnings": _fact_reference(retained_earnings),
                    "operating_income": _fact_reference(operating_income),
                    "revenue": _fact_reference(revenue),
                    "cash_and_equivalents": _fact_reference(cash_and_equivalents),
                    "debt_due_within_year": _fact_reference(debt_due_within_year),
                    "total_debt": _fact_reference(total_debt),
                    "interest_expense": _fact_reference(interest_expense),
                },
            },
        )

    def _build_event_context_snapshot(
        self,
        submissions: dict[str, Any] | None,
        event_anchor_date: date | None,
        official_earnings_date: date | None,
        official_earnings_source: str | None,
        submissions_error: str | None,
    ) -> EventContextSnapshot:
        details: dict[str, Any] = {
            "source": "sec_submissions",
            "anchor_date": event_anchor_date.isoformat() if event_anchor_date else None,
        }
        if event_anchor_date is None:
            details["reason"] = "event_anchor_date_missing"
            return EventContextSnapshot(status="unavailable", details=details)
        if submissions is None:
            details["reason"] = (
                "submissions_fetch_failed" if submissions_error else "submissions_unavailable"
            )
            if submissions_error:
                details["error"] = submissions_error
            return EventContextSnapshot(status="unavailable", details=details)

        recent_filings = _recent_filing_observations(submissions)
        if recent_filings is None:
            details["reason"] = "recent_filing_history_unavailable"
            return EventContextSnapshot(status="unavailable", details=details)

        earnings_match = _best_recent_item_match(
            recent_filings,
            event_anchor_date,
            _EARNINGS_EVENT_ITEMS,
            ((7, Decimal("4")), (14, Decimal("3")), (30, Decimal("1"))),
        )
        corporate_match = _best_recent_item_match(
            recent_filings,
            event_anchor_date,
            _CORPORATE_EVENT_ITEMS,
            ((14, Decimal("3")), (30, Decimal("2")), (60, Decimal("1"))),
        )
        periodic_match = _best_recent_filing_match(
            recent_filings,
            event_anchor_date,
            _PERIODIC_EVENT_FORMS,
            ((15, Decimal("3")), (45, Decimal("2")), (90, Decimal("1"))),
        )
        official_earnings_window = _official_earnings_window(
            official_earnings_date,
            official_earnings_source,
            event_anchor_date,
        )
        estimated_earnings_window = _estimate_next_earnings_window(
            recent_filings,
            event_anchor_date,
        )
        selected_earnings_window = official_earnings_window or estimated_earnings_window

        earnings_points = earnings_match.points if earnings_match is not None else _ZERO
        corporate_points = corporate_match.points if corporate_match is not None else _ZERO
        periodic_points = periodic_match.points if periodic_match is not None else _ZERO
        official_earnings_points = (
            official_earnings_window.points if official_earnings_window is not None else _ZERO
        )
        estimated_earnings_points = (
            estimated_earnings_window.points if estimated_earnings_window is not None else _ZERO
        )
        earnings_window_points = (
            selected_earnings_window.points if selected_earnings_window is not None else _ZERO
        )
        score = _quantize(
            min(
                earnings_points + corporate_points + periodic_points + earnings_window_points,
                _EVENT_CONTEXT_MAX,
            )
        )

        matched_filings: list[dict[str, Any]] = []
        if earnings_match is not None:
            matched_filings.append(_match_details(earnings_match, "earnings_release"))
        if corporate_match is not None:
            matched_filings.append(_match_details(corporate_match, "corporate_update"))
        if periodic_match is not None:
            matched_filings.append(_match_details(periodic_match, "recent_periodic_report"))

        details.update(
            {
                "recent_filing_count": len(recent_filings),
                "matched_filings": matched_filings,
                "earnings_related_points": float(_quantize(earnings_points)),
                "corporate_event_points": float(_quantize(corporate_points)),
                "recent_periodic_report_points": float(_quantize(periodic_points)),
                "official_earnings_date": (
                    official_earnings_window.event_date.isoformat()
                    if official_earnings_window is not None
                    else None
                ),
                "official_earnings_provider": (
                    official_earnings_window.source_label
                    if official_earnings_window is not None
                    else None
                ),
                "official_earnings_days_until": (
                    official_earnings_window.days_until_event
                    if official_earnings_window is not None
                    else None
                ),
                "official_earnings_points": float(_quantize(official_earnings_points)),
                "estimated_next_earnings_date": (
                    estimated_earnings_window.event_date.isoformat()
                    if estimated_earnings_window is not None
                    else None
                ),
                "days_until_estimated_earnings": (
                    estimated_earnings_window.days_until_event
                    if estimated_earnings_window is not None
                    else None
                ),
                "estimated_earnings_source": (
                    estimated_earnings_window.source_label
                    if estimated_earnings_window is not None
                    else None
                ),
                "estimated_earnings_points": float(_quantize(estimated_earnings_points)),
                "earnings_window_points": float(_quantize(earnings_window_points)),
                "upcoming_earnings_date": (
                    selected_earnings_window.event_date.isoformat()
                    if selected_earnings_window is not None
                    else None
                ),
                "days_until_upcoming_earnings": (
                    selected_earnings_window.days_until_event
                    if selected_earnings_window is not None
                    else None
                ),
                "upcoming_earnings_source": (
                    selected_earnings_window.source_label
                    if selected_earnings_window is not None
                    else None
                ),
                "upcoming_earnings_window_detected": (
                    selected_earnings_window is not None and selected_earnings_window.points > _ZERO
                ),
                "item_classification_enabled": True,
            }
        )
        return EventContextSnapshot(status="available", score=score, details=details)


def _latest_usd_fact(companyfacts: dict[str, Any], concepts: list[str]) -> FactObservation | None:
    gaap_facts = companyfacts.get("facts", {}).get("us-gaap", {})
    matches: list[FactObservation] = []
    for concept in concepts:
        concept_payload = gaap_facts.get(concept)
        if not isinstance(concept_payload, dict):
            continue
        facts = concept_payload.get("units", {}).get("USD", [])
        if not isinstance(facts, list):
            continue
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            form = str(fact.get("form", ""))
            if form and form not in _ALLOWED_FACT_FORMS:
                continue
            value = fact.get("val")
            if value is None:
                continue
            matches.append(
                FactObservation(
                    concept=concept,
                    value=Decimal(str(value)),
                    form=form or None,
                    filed=_string_or_none(fact, "filed"),
                    end=_string_or_none(fact, "end"),
                )
            )
    if not matches:
        return None
    return max(matches, key=lambda item: (item.filed or "", item.end or "", item.concept))


def _latest_dei_usd_fact(
    companyfacts: dict[str, Any] | None, concepts: list[str]
) -> FactObservation | None:
    if not companyfacts:
        return None
    dei_facts = companyfacts.get("facts", {}).get("dei", {})
    matches: list[FactObservation] = []
    for concept in concepts:
        concept_payload = dei_facts.get(concept)
        if not isinstance(concept_payload, dict):
            continue
        facts = concept_payload.get("units", {}).get("USD", [])
        if not isinstance(facts, list):
            continue
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            form = str(fact.get("form", ""))
            if form and form not in _ALLOWED_FACT_FORMS:
                continue
            value = fact.get("val")
            if value is None:
                continue
            matches.append(
                FactObservation(
                    concept=concept,
                    value=Decimal(str(value)),
                    form=form or None,
                    filed=_string_or_none(fact, "filed"),
                    end=_string_or_none(fact, "end"),
                )
            )
    if not matches:
        return None
    return max(matches, key=lambda item: (item.filed or "", item.end or "", item.concept))


def _latest_share_fact(
    companyfacts: dict[str, Any] | None, concepts: list[str]
) -> FactObservation | None:
    if not companyfacts:
        return None
    dei_facts = companyfacts.get("facts", {}).get("dei", {})
    matches: list[FactObservation] = []
    for concept in concepts:
        concept_payload = dei_facts.get(concept)
        if not isinstance(concept_payload, dict):
            continue
        facts = concept_payload.get("units", {}).get("shares", [])
        if not isinstance(facts, list):
            continue
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            form = str(fact.get("form", ""))
            if form and form not in _ALLOWED_FACT_FORMS:
                continue
            value = fact.get("val")
            if value is None:
                continue
            matches.append(
                FactObservation(
                    concept=concept,
                    value=Decimal(str(value)),
                    form=form or None,
                    filed=_string_or_none(fact, "filed"),
                    end=_string_or_none(fact, "end"),
                )
            )
    if not matches:
        return None
    return max(matches, key=lambda item: (item.filed or "", item.end or "", item.concept))


def _recent_filing_observations(
    submissions: dict[str, Any] | None,
) -> list[RecentFilingObservation] | None:
    if not submissions:
        return None
    recent = submissions.get("filings", {}).get("recent")
    if not isinstance(recent, dict):
        return None
    forms = recent.get("form")
    filing_dates = recent.get("filingDate")
    if not isinstance(forms, list) or not isinstance(filing_dates, list):
        return None

    observations: list[RecentFilingObservation] = []
    for index, raw_form in enumerate(forms):
        if index >= len(filing_dates):
            break
        raw_date = filing_dates[index]
        if raw_form in {None, ""} or raw_date in {None, ""}:
            continue
        try:
            filed_at = date.fromisoformat(str(raw_date))
        except ValueError:
            continue
        items = _indexed_string(recent.get("items"), index)
        observations.append(
            RecentFilingObservation(
                form=str(raw_form),
                filed_at=filed_at,
                accession_number=_indexed_string(recent.get("accessionNumber"), index),
                primary_document=_indexed_string(recent.get("primaryDocument"), index),
                items=items,
                item_codes=_parse_item_codes(items),
            )
        )
    return observations


def _best_recent_filing_match(
    filings: list[RecentFilingObservation],
    anchor_date: date,
    forms: set[str],
    thresholds: tuple[tuple[int, Decimal], ...],
) -> RecentFilingMatch | None:
    matches: list[RecentFilingMatch] = []
    for filing in filings:
        if filing.form not in forms:
            continue
        days_before_anchor = (anchor_date - filing.filed_at).days
        if days_before_anchor < 0:
            continue
        points = _recent_filing_points(days_before_anchor, thresholds)
        if points <= _ZERO:
            continue
        matches.append(
            RecentFilingMatch(
                filing=filing,
                days_before_anchor=days_before_anchor,
                points=points,
            )
        )
    if not matches:
        return None
    return max(
        matches,
        key=lambda item: (item.points, -item.days_before_anchor, item.filing.filed_at),
    )


def _best_recent_item_match(
    filings: list[RecentFilingObservation],
    anchor_date: date,
    item_codes: set[str],
    thresholds: tuple[tuple[int, Decimal], ...],
) -> RecentFilingMatch | None:
    matches: list[RecentFilingMatch] = []
    for filing in filings:
        if filing.form not in _EIGHT_K_EVENT_FORMS:
            continue
        if not set(filing.item_codes) & item_codes:
            continue
        days_before_anchor = (anchor_date - filing.filed_at).days
        if days_before_anchor < 0:
            continue
        points = _recent_filing_points(days_before_anchor, thresholds)
        if points <= _ZERO:
            continue
        matches.append(
            RecentFilingMatch(
                filing=filing,
                days_before_anchor=days_before_anchor,
                points=points,
            )
        )
    if not matches:
        return None
    return max(
        matches,
        key=lambda item: (item.points, -item.days_before_anchor, item.filing.filed_at),
    )


def _estimate_next_earnings_window(
    filings: list[RecentFilingObservation],
    anchor_date: date,
) -> UpcomingEarningsWindow | None:
    earnings_sources = [
        filing
        for filing in filings
        if filing.form in _EIGHT_K_EVENT_FORMS and set(filing.item_codes) & _EARNINGS_EVENT_ITEMS
    ]
    source_label = "recent_earnings_release"
    source_filing = (
        max(earnings_sources, key=lambda item: item.filed_at) if earnings_sources else None
    )
    if source_filing is None:
        periodic_sources = [filing for filing in filings if filing.form in _PERIODIC_EVENT_FORMS]
        if not periodic_sources:
            return None
        source_label = "recent_periodic_report"
        source_filing = max(periodic_sources, key=lambda item: item.filed_at)

    if anchor_date <= source_filing.filed_at:
        return None

    estimated_date = source_filing.filed_at + timedelta(days=_EARNINGS_CADENCE_DAYS)
    days_until_event = (estimated_date - anchor_date).days
    points = _upcoming_window_points(
        days_until_event,
        ((7, Decimal("3")), (14, Decimal("2")), (21, Decimal("1"))),
    )
    return UpcomingEarningsWindow(
        source_label=source_label,
        event_date=estimated_date,
        days_until_event=days_until_event,
        points=points,
    )


def _official_earnings_window(
    official_earnings_date: date | None,
    official_earnings_source: str | None,
    anchor_date: date,
) -> UpcomingEarningsWindow | None:
    if official_earnings_date is None or official_earnings_date < anchor_date:
        return None
    days_until_event = (official_earnings_date - anchor_date).days
    points = _upcoming_window_points(
        days_until_event,
        ((60, Decimal("4")), (120, Decimal("2")), (180, Decimal("1"))),
    )
    return UpcomingEarningsWindow(
        source_label=official_earnings_source or "official_earnings_calendar",
        event_date=official_earnings_date,
        days_until_event=days_until_event,
        points=points,
    )


def _recent_filing_points(
    days_before_anchor: int, thresholds: tuple[tuple[int, Decimal], ...]
) -> Decimal:
    for max_days, points in thresholds:
        if days_before_anchor <= max_days:
            return points
    return _ZERO


def _upcoming_window_points(
    days_until_event: int, thresholds: tuple[tuple[int, Decimal], ...]
) -> Decimal:
    if days_until_event < 0:
        return _ZERO
    for max_days, points in thresholds:
        if days_until_event <= max_days:
            return points
    return _ZERO


def _match_details(match: RecentFilingMatch, label: str) -> dict[str, Any]:
    return {
        "label": label,
        "form": match.filing.form,
        "filed_at": match.filing.filed_at.isoformat(),
        "days_before_anchor": match.days_before_anchor,
        "points": float(_quantize(match.points)),
        "accession_number": match.filing.accession_number,
        "primary_document": match.filing.primary_document,
        "items": match.filing.items,
        "item_codes": list(match.filing.item_codes),
    }


def _parse_item_codes(items: str | None) -> tuple[str, ...]:
    if not items:
        return ()
    normalized = items.replace(";", ",")
    parsed = [part.strip() for part in normalized.split(",") if part and part.strip()]
    return tuple(parsed)


def _indexed_string(values: Any, index: int) -> str | None:
    if not isinstance(values, list) or index >= len(values):
        return None
    value = values[index]
    if value in {None, ""}:
        return None
    return str(value)


def _string_or_none(payload: dict[str, Any] | None, key: str) -> str | None:
    if not payload:
        return None
    value = payload.get(key)
    if value in {None, ""}:
        return None
    return str(value)


def _first_string(payload: dict[str, Any] | None, key: str) -> str | None:
    if not payload:
        return None
    value = payload.get(key)
    if not isinstance(value, list) or not value:
        return None
    first = value[0]
    if first in {None, ""}:
        return None
    return str(first)


def _altman_missing_inputs(
    total_assets: FactObservation | None,
    total_liabilities: FactObservation | None,
    retained_earnings: FactObservation | None,
    operating_income: FactObservation | None,
    revenue: FactObservation | None,
    market_value_equity: Decimal | None,
) -> list[str]:
    missing: list[str] = []
    if total_assets is None:
        missing.append("total_assets")
    if total_liabilities is None:
        missing.append("total_liabilities")
    if retained_earnings is None:
        missing.append("retained_earnings")
    if operating_income is None:
        missing.append("operating_income")
    if revenue is None:
        missing.append("revenue")
    if market_value_equity is None:
        missing.append("market_value_equity")
    return missing


def _compute_altman_z_score(
    *,
    working_capital: Decimal,
    total_assets: FactObservation | None,
    retained_earnings: FactObservation | None,
    operating_income: FactObservation | None,
    market_value_equity: Decimal,
    total_liabilities: FactObservation | None,
    revenue: FactObservation | None,
) -> Decimal | None:
    if (
        total_assets is None
        or total_liabilities is None
        or retained_earnings is None
        or operating_income is None
        or revenue is None
        or total_assets.value <= _ZERO
        or total_liabilities.value <= _ZERO
        or market_value_equity <= _ZERO
    ):
        return None
    x1 = working_capital / total_assets.value
    x2 = retained_earnings.value / total_assets.value
    x3 = operating_income.value / total_assets.value
    x4 = market_value_equity / total_liabilities.value
    x5 = revenue.value / total_assets.value
    return _quantize(
        (Decimal("1.2") * x1)
        + (Decimal("1.4") * x2)
        + (Decimal("3.3") * x3)
        + (Decimal("0.6") * x4)
        + (Decimal("1.0") * x5)
    )


def _altman_zone(score: Decimal | None) -> str:
    if score is None:
        return "unavailable"
    if score < Decimal("1.81"):
        return "distress"
    if score < Decimal("3.0"):
        return "gray"
    return "safe"


def _altman_points(score: Decimal | None) -> Decimal:
    if score is None:
        return Decimal("0")
    if score >= Decimal("3.0"):
        return Decimal("10")
    if score >= Decimal("2.6"):
        return Decimal("8")
    if score >= Decimal("1.81"):
        return Decimal("5")
    if score >= Decimal("1.1"):
        return Decimal("2")
    return Decimal("0")


def _liquidity_points(current_ratio: Decimal) -> Decimal:
    if current_ratio >= Decimal("2.0"):
        return Decimal("6")
    if current_ratio >= Decimal("1.5"):
        return Decimal("5")
    if current_ratio >= Decimal("1.25"):
        return Decimal("4")
    if current_ratio >= Decimal("1.0"):
        return Decimal("3")
    if current_ratio >= Decimal("0.75"):
        return Decimal("2")
    return Decimal("1")


def _solvency_points(
    total_assets: FactObservation | None,
    total_liabilities: FactObservation | None,
    stockholders_equity: FactObservation | None,
) -> Decimal:
    if (
        total_assets is None
        or total_liabilities is None
        or total_assets.value <= _ZERO
        or stockholders_equity is None
        or stockholders_equity.value <= _ZERO
    ):
        return Decimal("0")
    leverage_ratio = total_liabilities.value / total_assets.value
    if leverage_ratio <= Decimal("0.50"):
        return Decimal("4")
    if leverage_ratio <= Decimal("0.65"):
        return Decimal("3")
    if leverage_ratio <= Decimal("0.80"):
        return Decimal("2")
    if leverage_ratio <= Decimal("1.00"):
        return Decimal("1")
    return Decimal("0")


def _debt_maturity_points(
    debt_due_within_year: FactObservation | None,
    cash_and_equivalents: FactObservation | None,
    debt_due_ratio: Decimal | None,
) -> Decimal:
    if debt_due_within_year is None or debt_due_within_year.value <= _ZERO:
        return Decimal("2")
    if cash_and_equivalents is None or cash_and_equivalents.value <= _ZERO:
        return Decimal("0")
    if cash_and_equivalents.value >= debt_due_within_year.value and (
        debt_due_ratio is None or debt_due_ratio <= Decimal("0.35")
    ):
        return Decimal("2")
    if cash_and_equivalents.value >= debt_due_within_year.value * Decimal("0.75"):
        return Decimal("1")
    return Decimal("0")


def _credit_proxy_points(interest_coverage_ratio: Decimal | None) -> Decimal:
    if interest_coverage_ratio is None:
        return Decimal("0")
    if interest_coverage_ratio >= Decimal("4.0"):
        return Decimal("2")
    if interest_coverage_ratio >= Decimal("2.0"):
        return Decimal("1")
    return Decimal("0")


def _interest_coverage_ratio(
    operating_income: FactObservation | None,
    interest_expense: FactObservation | None,
) -> Decimal | None:
    if operating_income is None or interest_expense is None or interest_expense.value <= _ZERO:
        return None
    return operating_income.value / interest_expense.value


def _safe_ratio_observation(
    numerator: FactObservation | None,
    denominator: FactObservation | None,
) -> Decimal | None:
    if numerator is None or denominator is None or denominator.value <= _ZERO:
        return None
    return numerator.value / denominator.value


def _safe_ratio_values(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator <= _ZERO:
        return None
    return numerator / denominator


def _combined_fact_observation(
    left: FactObservation | None,
    right: FactObservation | None,
    concept: str,
) -> FactObservation | None:
    if left is None or right is None:
        return None
    return FactObservation(
        concept=concept,
        value=left.value + right.value,
        form=left.form or right.form,
        filed=max(left.filed or "", right.filed or "") or None,
        end=max(left.end or "", right.end or "") or None,
    )


def _clamp_score(score: Decimal) -> Decimal:
    if score < _ZERO:
        return _ZERO
    if score > Decimal("20"):
        return Decimal("20")
    return score


def _fact_value(observation: FactObservation | None) -> float | None:
    if observation is None:
        return None
    return float(_quantize(observation.value))


def _fact_reference(observation: FactObservation | None) -> dict[str, str] | None:
    if observation is None:
        return None
    return {
        "concept": observation.concept,
        "form": observation.form or "",
        "filed": observation.filed or "",
        "end": observation.end or "",
    }


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
