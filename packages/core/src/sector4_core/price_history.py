from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

import httpx

from sector4_core.config import Settings, get_settings

_ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"


@dataclass(slots=True)
class HistoricalPricePoint:
    status: str
    source: str | None = None
    price_value: Decimal | None = None
    price_date: date | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DailyAdjustedBar:
    trading_date: date
    adjusted_close: Decimal


class PriceHistoryProvider(Protocol):
    def lookup_price(self, ticker: str | None, target_date: date) -> HistoricalPricePoint: ...

    def close(self) -> None: ...


class NullPriceHistoryProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def lookup_price(self, ticker: str | None, target_date: date) -> HistoricalPricePoint:
        if not ticker:
            return HistoricalPricePoint(
                status="unavailable",
                details={"reason": "ticker_unavailable"},
            )
        reason = (
            "no_market_data_provider_configured"
            if not self.settings.market_data_provider
            else "historical_price_provider_not_implemented"
        )
        return HistoricalPricePoint(
            status="unavailable",
            details={"reason": reason, "target_date": target_date.isoformat()},
        )

    def close(self) -> None:
        return None


class AlphaVantageDailyPriceHistoryProvider:
    def __init__(
        self,
        settings: Settings | None = None,
        loader: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.Client | None = None
        self._owns_client = loader is None
        if self._owns_client:
            self._client = httpx.Client(
                headers={"User-Agent": self.settings.sec_user_agent},
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
            )
        self._loader = loader or self._fetch_daily
        self._cache: dict[str, list[DailyAdjustedBar]] = {}

    def lookup_price(self, ticker: str | None, target_date: date) -> HistoricalPricePoint:
        if not ticker:
            return HistoricalPricePoint(
                status="unavailable",
                details={"reason": "ticker_unavailable"},
            )
        if not self.settings.market_data_api_key:
            return HistoricalPricePoint(
                status="unavailable",
                source="alpha_vantage_daily",
                details={"reason": "market_data_api_key_missing"},
            )
        normalized_ticker = ticker.upper()
        try:
            bars = self._cache.get(normalized_ticker)
            if bars is None:
                bars = _parse_daily_adjusted_bars(self._loader(normalized_ticker))
                self._cache[normalized_ticker] = bars
        except (httpx.HTTPError, ValueError) as exc:
            return HistoricalPricePoint(
                status="unavailable",
                source="alpha_vantage_daily",
                details={
                    "reason": "market_data_fetch_failed",
                    "error": _sanitize_provider_error(
                        str(exc), self.settings.market_data_api_key
                    ),
                },
            )

        if not bars:
            return HistoricalPricePoint(
                status="unavailable",
                source="alpha_vantage_daily",
                details={"reason": "price_history_missing"},
            )

        earliest_bar = bars[0]
        latest_bar = bars[-1]

        if target_date < earliest_bar.trading_date:
            gap_days = (earliest_bar.trading_date - target_date).days
            if gap_days > 7:
                return HistoricalPricePoint(
                    status="unavailable",
                    source="alpha_vantage_daily",
                    details={
                        "reason": "historical_price_out_of_range",
                        "earliest_available_date": earliest_bar.trading_date.isoformat(),
                        "target_date": target_date.isoformat(),
                        "days_after_target": gap_days,
                    },
                )

        matching_bar = next((bar for bar in bars if bar.trading_date >= target_date), None)
        if matching_bar is None:
            return HistoricalPricePoint(
                status="pending",
                source="alpha_vantage_daily",
                details={
                    "reason": "historical_price_not_yet_available",
                    "latest_available_date": latest_bar.trading_date.isoformat(),
                    "target_date": target_date.isoformat(),
                },
            )

        return HistoricalPricePoint(
            status="captured",
            source="alpha_vantage_daily",
            price_date=matching_bar.trading_date,
            price_value=_quantize(matching_bar.adjusted_close),
            details={
                "target_date": target_date.isoformat(),
                "matched_trading_date": matching_bar.trading_date.isoformat(),
                "days_after_target": (matching_bar.trading_date - target_date).days,
            },
        )

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()

    def _fetch_daily(self, ticker: str) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Alpha Vantage HTTP client is not initialized")
        response = self._client.get(
            _ALPHA_VANTAGE_URL,
            params={
                "function": "TIME_SERIES_DAILY",
                "outputsize": "compact",
                "symbol": ticker,
                "apikey": self.settings.market_data_api_key,
            },
        )
        response.raise_for_status()
        return response.json()


def get_price_history_provider(settings: Settings) -> PriceHistoryProvider:
    provider_name = (settings.market_data_provider or "").strip().lower()
    if provider_name == "alpha_vantage":
        return AlphaVantageDailyPriceHistoryProvider(settings)
    return NullPriceHistoryProvider(settings)


def _parse_daily_adjusted_bars(payload: dict[str, Any]) -> list[DailyAdjustedBar]:
    if "Error Message" in payload:
        raise ValueError(str(payload["Error Message"]))
    if "Note" in payload:
        raise ValueError(str(payload["Note"]))
    if "Information" in payload:
        raise ValueError(str(payload["Information"]))

    series_payload = payload.get("Time Series (Daily)")
    if not isinstance(series_payload, dict) or not series_payload:
        raise ValueError("Time Series (Daily) missing from market data payload")

    bars: list[DailyAdjustedBar] = []
    for period, values in sorted(series_payload.items()):
        if not isinstance(values, dict):
            continue
        adjusted_close = values.get("5. adjusted close") or values.get("4. close")
        if adjusted_close in {None, ""}:
            continue
        try:
            trading_date = date.fromisoformat(str(period))
        except ValueError:
            continue
        bars.append(
            DailyAdjustedBar(
                trading_date=trading_date,
                adjusted_close=Decimal(str(adjusted_close)),
            )
        )
    return bars


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _sanitize_provider_error(message: str, api_key: str | None) -> str:
    if not api_key:
        return message
    return message.replace(api_key, "[redacted]")
