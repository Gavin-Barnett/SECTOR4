from datetime import date
from decimal import Decimal

from sector4_core.config import Settings
from sector4_core.price_history import AlphaVantageDailyPriceHistoryProvider


def test_alpha_vantage_daily_price_history_selects_first_trading_day_on_or_after_target() -> None:
    payload = {
        "Time Series (Daily)": {
            "2024-02-20": {"5. adjusted close": "11.2500"},
            "2024-02-16": {"5. adjusted close": "10.0000"},
            "2024-02-15": {"5. adjusted close": "9.7500"},
        }
    }
    provider = AlphaVantageDailyPriceHistoryProvider(
        Settings(market_data_provider="alpha_vantage", market_data_api_key="demo-key"),
        loader=lambda ticker: payload,
    )

    point = provider.lookup_price("ACME", date(2024, 2, 17))

    assert point.status == "captured"
    assert point.price_date == date(2024, 2, 20)
    assert point.price_value == Decimal("11.2500")
    assert point.details["days_after_target"] == 3


def test_alpha_vantage_daily_price_history_prefers_exact_trading_day_when_present() -> None:
    payload = {
        "Time Series (Daily)": {
            "2024-02-20": {"5. adjusted close": "11.2500"},
            "2024-02-16": {"5. adjusted close": "10.0000"},
            "2024-02-15": {"5. adjusted close": "9.7500"},
        }
    }
    provider = AlphaVantageDailyPriceHistoryProvider(
        Settings(market_data_provider="alpha_vantage", market_data_api_key="demo-key"),
        loader=lambda ticker: payload,
    )

    point = provider.lookup_price("ACME", date(2024, 2, 15))

    assert point.status == "captured"
    assert point.price_date == date(2024, 2, 15)
    assert point.price_value == Decimal("9.7500")
    assert point.details["days_after_target"] == 0


def test_alpha_vantage_daily_price_history_marks_old_targets_out_of_range() -> None:
    payload = {
        "Time Series (Daily)": {
            "2024-02-20": {"5. adjusted close": "11.2500"},
            "2024-02-16": {"5. adjusted close": "10.0000"},
        }
    }
    provider = AlphaVantageDailyPriceHistoryProvider(
        Settings(market_data_provider="alpha_vantage", market_data_api_key="demo-key"),
        loader=lambda ticker: payload,
    )

    point = provider.lookup_price("ACME", date(2023, 9, 1))

    assert point.status == "unavailable"
    assert point.details["reason"] == "historical_price_out_of_range"
    assert point.details["earliest_available_date"] == "2024-02-16"


def test_alpha_vantage_daily_price_history_surfaces_rate_limit_or_note_payload() -> None:
    provider = AlphaVantageDailyPriceHistoryProvider(
        Settings(market_data_provider="alpha_vantage", market_data_api_key="demo-key"),
        loader=lambda ticker: {"Note": "daily limit exceeded"},
    )

    point = provider.lookup_price("ACME", date(2024, 2, 17))

    assert point.status == "unavailable"
    assert point.details["reason"] == "market_data_fetch_failed"
    assert "daily limit exceeded" in point.details["error"]


def test_alpha_vantage_daily_price_history_redacts_api_key_in_provider_errors() -> None:
    provider = AlphaVantageDailyPriceHistoryProvider(
        Settings(market_data_provider="alpha_vantage", market_data_api_key="secret-key"),
        loader=lambda ticker: {"Note": "API key secret-key exceeded the daily rate limit"},
    )

    point = provider.lookup_price("ACME", date(2024, 2, 17))

    assert point.status == "unavailable"
    assert point.details["reason"] == "market_data_fetch_failed"
    assert "secret-key" not in point.details["error"]
    assert "[redacted]" in point.details["error"]
