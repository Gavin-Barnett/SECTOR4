from __future__ import annotations

from collections.abc import Callable
from csv import DictReader
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from io import StringIO
from typing import Any

import httpx

from sector4_core.config import Settings, get_settings
from sector4_core.enrichment import (
    EventContextSnapshot,
    IssuerEnrichmentRequest,
    IssuerEnrichmentSnapshot,
    PriceContextSnapshot,
)

_ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
_ZERO = Decimal("0")
_LIVE_EARNINGS_LOOKBACK_DAYS = 45
_EARNINGS_CALENDAR_HORIZON = "12month"


@dataclass(slots=True)
class WeeklyPriceBar:
    date: str
    high: Decimal
    low: Decimal
    adjusted_close: Decimal


@dataclass(slots=True)
class EarningsCalendarEvent:
    symbol: str
    report_date: date
    fiscal_date_ending: str | None
    estimate: Decimal | None
    currency: str | None


class AlphaVantagePriceEnrichmentProvider:
    def __init__(
        self,
        settings: Settings | None = None,
        loader: Callable[[str], dict[str, Any]] | None = None,
        earnings_loader: Callable[[str], str | list[dict[str, Any]]] | None = None,
        today_provider: Callable[[], date] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.Client | None = None
        self._owns_client = loader is None or earnings_loader is None
        if self._owns_client:
            self._client = httpx.Client(
                headers={"User-Agent": self.settings.sec_user_agent},
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
            )
        self._loader = loader or self._fetch_weekly_adjusted
        self._earnings_loader = earnings_loader or self._fetch_earnings_calendar
        self._today = today_provider or date.today
        self._cache: dict[tuple[str, str], IssuerEnrichmentSnapshot] = {}

    def enrich(self, request: IssuerEnrichmentRequest) -> IssuerEnrichmentSnapshot:
        anchor_key = request.event_anchor_date.isoformat() if request.event_anchor_date else ""
        cache_key = ((request.ticker or request.cik).upper(), anchor_key)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if not request.ticker:
            snapshot = IssuerEnrichmentSnapshot(
                market_cap=request.market_cap,
                latest_price=request.latest_price,
                price_context=PriceContextSnapshot(
                    status="unavailable",
                    details={"reason": "ticker_unavailable", "provider": "alpha_vantage"},
                ),
                event_context=EventContextSnapshot(
                    status="unavailable",
                    details={"reason": "ticker_unavailable", "provider": "alpha_vantage"},
                ),
            )
            self._cache[cache_key] = snapshot
            return snapshot
        if not self.settings.market_data_api_key:
            snapshot = IssuerEnrichmentSnapshot(
                market_cap=request.market_cap,
                latest_price=request.latest_price,
                price_context=PriceContextSnapshot(
                    status="unavailable",
                    details={
                        "reason": "market_data_api_key_missing",
                        "provider": "alpha_vantage",
                    },
                ),
                event_context=EventContextSnapshot(
                    status="unavailable",
                    details={
                        "reason": "market_data_api_key_missing",
                        "provider": "alpha_vantage",
                    },
                ),
            )
            self._cache[cache_key] = snapshot
            return snapshot

        price_context = PriceContextSnapshot(
            status="unavailable",
            details={"reason": "market_data_fetch_not_attempted", "provider": "alpha_vantage"},
        )
        latest_price = request.latest_price
        try:
            payload = self._loader(request.ticker)
            price_snapshot = _snapshot_from_weekly_payload(
                payload,
                market_cap=request.market_cap,
                fallback_latest_price=request.latest_price,
            )
            price_context = price_snapshot.price_context
            latest_price = price_snapshot.latest_price
        except (httpx.HTTPError, ValueError) as exc:
            price_context = PriceContextSnapshot(
                status="unavailable",
                details={
                    "reason": "market_data_fetch_failed",
                    "provider": "alpha_vantage",
                    "error": str(exc),
                },
            )

        upcoming_earnings_date = None
        earnings_date_source = None
        event_context = EventContextSnapshot(
            status="unavailable",
            details={
                "provider": "alpha_vantage",
                "reason": "earnings_context_provided_via_sec_provider",
            },
        )
        if request.event_anchor_date is not None:
            if _should_query_live_earnings_calendar(request.event_anchor_date, self._today()):
                try:
                    earnings_payload = self._earnings_loader(request.ticker)
                    earnings_event = _next_earnings_event(
                        _parse_earnings_calendar_payload(earnings_payload),
                        request.event_anchor_date,
                        request.ticker,
                    )
                    if earnings_event is not None:
                        upcoming_earnings_date = earnings_event.report_date
                        earnings_date_source = "alpha_vantage_earnings_calendar"
                        event_context = EventContextSnapshot(
                            status="unavailable",
                            details={
                                "provider": "alpha_vantage",
                                "reason": "official_earnings_date_forwarded_to_sec_provider",
                                "official_earnings_date": earnings_event.report_date.isoformat(),
                                "fiscal_date_ending": earnings_event.fiscal_date_ending,
                                "currency": earnings_event.currency,
                                "estimate": (
                                    float(_quantize(earnings_event.estimate))
                                    if earnings_event.estimate is not None
                                    else None
                                ),
                            },
                        )
                    else:
                        event_context = EventContextSnapshot(
                            status="unavailable",
                            details={
                                "provider": "alpha_vantage",
                                "reason": "no_upcoming_earnings_event_found",
                            },
                        )
                except (httpx.HTTPError, ValueError) as exc:
                    event_context = EventContextSnapshot(
                        status="unavailable",
                        details={
                            "provider": "alpha_vantage",
                            "reason": "earnings_calendar_fetch_failed",
                            "error": str(exc),
                        },
                    )
            else:
                event_context = EventContextSnapshot(
                    status="unavailable",
                    details={
                        "provider": "alpha_vantage",
                        "reason": "historical_anchor_uses_sec_estimate",
                        "anchor_date": request.event_anchor_date.isoformat(),
                        "today": self._today().isoformat(),
                    },
                )

        snapshot = IssuerEnrichmentSnapshot(
            market_cap=request.market_cap,
            latest_price=latest_price,
            upcoming_earnings_date=upcoming_earnings_date,
            earnings_date_source=earnings_date_source,
            price_context=price_context,
            event_context=event_context,
        )
        self._cache[cache_key] = snapshot
        return snapshot

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def _fetch_weekly_adjusted(self, ticker: str) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Alpha Vantage HTTP client is not initialized")
        response = self._client.get(
            _ALPHA_VANTAGE_URL,
            params={
                "function": "TIME_SERIES_WEEKLY_ADJUSTED",
                "symbol": ticker,
                "apikey": self.settings.market_data_api_key,
            },
        )
        response.raise_for_status()
        return response.json()

    def _fetch_earnings_calendar(self, ticker: str) -> str:
        if self._client is None:
            raise RuntimeError("Alpha Vantage HTTP client is not initialized")
        response = self._client.get(
            _ALPHA_VANTAGE_URL,
            params={
                "function": "EARNINGS_CALENDAR",
                "symbol": ticker,
                "horizon": _EARNINGS_CALENDAR_HORIZON,
                "apikey": self.settings.market_data_api_key,
            },
        )
        response.raise_for_status()
        return response.text


def _snapshot_from_weekly_payload(
    payload: dict[str, Any],
    *,
    market_cap: Decimal | None,
    fallback_latest_price: Decimal | None,
) -> IssuerEnrichmentSnapshot:
    if "Error Message" in payload:
        raise ValueError(str(payload["Error Message"]))
    if "Note" in payload:
        raise ValueError(str(payload["Note"]))
    if "Information" in payload:
        raise ValueError(str(payload["Information"]))

    series_payload = payload.get("Weekly Adjusted Time Series")
    if not isinstance(series_payload, dict) or not series_payload:
        raise ValueError("Weekly Adjusted Time Series missing from market data payload")

    bars = _parse_weekly_bars(series_payload)
    if len(bars) < 26:
        latest_price = bars[0].adjusted_close if bars else fallback_latest_price
        return IssuerEnrichmentSnapshot(
            market_cap=market_cap,
            latest_price=latest_price,
            price_context=PriceContextSnapshot(
                status="unavailable",
                details={
                    "reason": "insufficient_price_history",
                    "provider": "alpha_vantage",
                    "weeks_sampled": len(bars),
                },
            ),
        )

    snapshot = _build_price_context_snapshot(bars)
    return IssuerEnrichmentSnapshot(
        market_cap=market_cap,
        latest_price=bars[0].adjusted_close,
        price_context=snapshot,
    )


def _build_price_context_snapshot(bars: list[WeeklyPriceBar]) -> PriceContextSnapshot:
    sampled = bars[:52]
    recent = bars[:13]
    latest = sampled[0].adjusted_close
    low_52 = min(bar.low for bar in sampled)
    high_52 = max(bar.high for bar in sampled)
    low_13 = min(bar.low for bar in recent)

    pct_above_52w_low = _ratio_over_base(latest - low_52, low_52)
    pct_above_13w_low = _ratio_over_base(latest - low_13, low_13)
    drawdown_from_52w_high = _ratio_over_base(high_52 - latest, high_52)

    low_52_points = _closeness_points(pct_above_52w_low, Decimal("0.25"), Decimal("7"))
    drawdown_points = _drawdown_points(drawdown_from_52w_high, Decimal("0.35"), Decimal("5"))
    local_low_points = _closeness_points(pct_above_13w_low, Decimal("0.15"), Decimal("3"))
    score = _quantize(low_52_points + drawdown_points + local_low_points)

    return PriceContextSnapshot(
        status="available",
        score=score,
        details={
            "provider": "alpha_vantage",
            "latest_adjusted_close": float(_quantize(latest)),
            "low_52w": float(_quantize(low_52)),
            "high_52w": float(_quantize(high_52)),
            "low_13w": float(_quantize(low_13)),
            "pct_above_52w_low": float(_quantize(pct_above_52w_low * Decimal("100"))),
            "pct_above_13w_low": float(_quantize(pct_above_13w_low * Decimal("100"))),
            "drawdown_from_52w_high": float(_quantize(drawdown_from_52w_high * Decimal("100"))),
            "weeks_sampled": len(sampled),
            "low_52w_points": float(_quantize(low_52_points)),
            "drawdown_points": float(_quantize(drawdown_points)),
            "local_low_points": float(_quantize(local_low_points)),
        },
    )


def _parse_weekly_bars(series_payload: dict[str, Any]) -> list[WeeklyPriceBar]:
    bars: list[WeeklyPriceBar] = []
    for period, values in sorted(series_payload.items(), reverse=True):
        if not isinstance(values, dict):
            continue
        adjusted_close = values.get("5. adjusted close") or values.get("4. close")
        high = values.get("2. high")
        low = values.get("3. low")
        if adjusted_close is None or high is None or low is None:
            continue
        bars.append(
            WeeklyPriceBar(
                date=str(period),
                high=Decimal(str(high)),
                low=Decimal(str(low)),
                adjusted_close=Decimal(str(adjusted_close)),
            )
        )
    return bars


def _parse_earnings_calendar_payload(
    payload: str | list[dict[str, Any]],
) -> list[EarningsCalendarEvent]:
    if isinstance(payload, list):
        rows = payload
    else:
        text = payload.strip()
        if not text:
            return []
        if text.startswith("{"):
            raise ValueError(text)
        rows = list(DictReader(StringIO(text)))

    events: list[EarningsCalendarEvent] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        report_date = row.get("reportDate") or row.get("report_date")
        if not report_date:
            continue
        try:
            parsed_report_date = date.fromisoformat(str(report_date))
        except ValueError:
            continue
        estimate = row.get("estimate")
        events.append(
            EarningsCalendarEvent(
                symbol=str(row.get("symbol", "")).upper(),
                report_date=parsed_report_date,
                fiscal_date_ending=(
                    str(row.get("fiscalDateEnding"))
                    if row.get("fiscalDateEnding") not in {None, ""}
                    else None
                ),
                estimate=(Decimal(str(estimate)) if estimate not in {None, ""} else None),
                currency=str(row.get("currency"))
                if row.get("currency") not in {None, ""}
                else None,
            )
        )
    return events


def _next_earnings_event(
    events: list[EarningsCalendarEvent],
    anchor_date: date,
    ticker: str,
) -> EarningsCalendarEvent | None:
    normalized_ticker = ticker.upper()
    upcoming = [
        event
        for event in events
        if event.symbol == normalized_ticker and event.report_date >= anchor_date
    ]
    if not upcoming:
        return None
    return min(upcoming, key=lambda event: event.report_date)


def _should_query_live_earnings_calendar(anchor_date: date, today: date) -> bool:
    return anchor_date >= today - timedelta(days=_LIVE_EARNINGS_LOOKBACK_DAYS)


def _ratio_over_base(value: Decimal, base: Decimal) -> Decimal:
    if base <= _ZERO:
        return _ZERO
    return value / base


def _closeness_points(distance: Decimal, zero_cutoff: Decimal, max_points: Decimal) -> Decimal:
    scaled = Decimal("1") - min(max(distance, _ZERO), zero_cutoff) / zero_cutoff
    return max(_ZERO, scaled * max_points)


def _drawdown_points(drawdown: Decimal, full_points_at: Decimal, max_points: Decimal) -> Decimal:
    return min(max(drawdown, _ZERO) / full_points_at, Decimal("1")) * max_points


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
