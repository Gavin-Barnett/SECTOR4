import json
from datetime import date
from decimal import Decimal

from sector4_core.config import Settings
from sector4_core.enrichment import (
    CompositeIssuerEnrichmentProvider,
    IssuerEnrichmentRequest,
    get_issuer_enrichment_provider,
)
from sector4_core.market_data import AlphaVantagePriceEnrichmentProvider
from sector4_sec_ingestion.enrichment import SecCompanyfactsEnrichmentProvider


def test_alpha_vantage_price_provider_builds_price_context_and_official_earnings_context(
    fixture_dir,
) -> None:
    payload = json.loads(
        (fixture_dir / "alpha_vantage_weekly_acme.json").read_text(encoding="utf-8-sig")
    )
    earnings_payload = (fixture_dir / "alpha_vantage_earnings_calendar_acme.csv").read_text(
        encoding="utf-8-sig"
    )
    settings = Settings(
        market_data_provider="alpha_vantage",
        market_data_api_key="demo-key",
    )
    provider = AlphaVantagePriceEnrichmentProvider(
        settings,
        loader=lambda ticker: payload,
        earnings_loader=lambda ticker: earnings_payload,
        today_provider=lambda: date(2024, 3, 1),
    )

    snapshot = provider.enrich(
        IssuerEnrichmentRequest(
            cik="1234567",
            ticker="ACME",
            name="Acme Robotics, Inc.",
            market_cap=None,
            latest_price=None,
            event_anchor_date=date(2024, 3, 18),
        )
    )

    assert snapshot.latest_price == Decimal("10.5000")
    assert snapshot.market_cap is None
    assert snapshot.price_context.status == "available"
    assert snapshot.price_context.score == Decimal("12.60")
    assert snapshot.price_context.details["weeks_sampled"] == 52
    assert snapshot.price_context.details["provider"] == "alpha_vantage"
    assert snapshot.upcoming_earnings_date == date(2024, 3, 28)
    assert snapshot.earnings_date_source == "alpha_vantage_earnings_calendar"
    assert (
        snapshot.event_context.details["reason"]
        == "official_earnings_date_forwarded_to_sec_provider"
    )


def test_alpha_vantage_price_provider_handles_missing_api_key() -> None:
    settings = Settings(market_data_provider="alpha_vantage", market_data_api_key=None)
    provider = AlphaVantagePriceEnrichmentProvider(settings, loader=lambda ticker: {})

    snapshot = provider.enrich(
        IssuerEnrichmentRequest(
            cik="1234567",
            ticker="ACME",
            name="Acme Robotics, Inc.",
            market_cap=None,
            latest_price=None,
        )
    )

    assert snapshot.price_context.status == "unavailable"
    assert snapshot.price_context.details["reason"] == "market_data_api_key_missing"
    assert snapshot.event_context.details["reason"] == "market_data_api_key_missing"


def test_alpha_vantage_price_provider_skips_live_earnings_for_old_anchor(fixture_dir) -> None:
    payload = json.loads(
        (fixture_dir / "alpha_vantage_weekly_acme.json").read_text(encoding="utf-8-sig")
    )
    earnings_payload = (fixture_dir / "alpha_vantage_earnings_calendar_acme.csv").read_text(
        encoding="utf-8-sig"
    )
    settings = Settings(
        market_data_provider="alpha_vantage",
        market_data_api_key="demo-key",
    )
    provider = AlphaVantagePriceEnrichmentProvider(
        settings,
        loader=lambda ticker: payload,
        earnings_loader=lambda ticker: earnings_payload,
        today_provider=lambda: date(2024, 5, 30),
    )

    snapshot = provider.enrich(
        IssuerEnrichmentRequest(
            cik="1234567",
            ticker="ACME",
            name="Acme Robotics, Inc.",
            market_cap=None,
            latest_price=None,
            event_anchor_date=date(2024, 2, 18),
        )
    )

    assert snapshot.upcoming_earnings_date is None
    assert snapshot.event_context.details["reason"] == "historical_anchor_uses_sec_estimate"


def test_composite_provider_merges_sec_health_alpha_price_and_official_earnings(
    fixture_dir,
) -> None:
    companyfacts = json.loads(
        (fixture_dir / "companyfacts_acme.json").read_text(encoding="utf-8-sig")
    )
    submissions = json.loads(
        (fixture_dir / "submissions_acme.json").read_text(encoding="utf-8-sig")
    )
    prices = json.loads(
        (fixture_dir / "alpha_vantage_weekly_acme.json").read_text(encoding="utf-8-sig")
    )
    earnings_payload = (fixture_dir / "alpha_vantage_earnings_calendar_acme.csv").read_text(
        encoding="utf-8-sig"
    )
    settings = Settings(
        market_data_provider="alpha_vantage",
        market_data_api_key="demo-key",
    )
    provider = CompositeIssuerEnrichmentProvider(
        [
            AlphaVantagePriceEnrichmentProvider(
                settings,
                loader=lambda ticker: prices,
                earnings_loader=lambda ticker: earnings_payload,
                today_provider=lambda: date(2024, 2, 20),
            ),
            SecCompanyfactsEnrichmentProvider(
                settings,
                companyfacts_loader=lambda cik: companyfacts,
                submissions_loader=lambda cik: submissions,
            ),
        ]
    )

    snapshot = provider.enrich(
        IssuerEnrichmentRequest(
            cik="1234567",
            ticker="ACME",
            name="Acme Robotics, Inc.",
            market_cap=None,
            latest_price=None,
            event_anchor_date=date(2024, 2, 18),
        )
    )

    assert snapshot.exchange == "NASDAQ"
    assert snapshot.health.status == "healthy"
    assert snapshot.health.score == Decimal("20.00")
    assert snapshot.price_context.status == "available"
    assert snapshot.price_context.score == Decimal("12.60")
    assert snapshot.event_context.status == "available"
    assert snapshot.event_context.score == Decimal("10.00")
    assert snapshot.event_context.details["official_earnings_date"] == "2024-03-28"
    assert (
        snapshot.event_context.details["official_earnings_provider"]
        == "alpha_vantage_earnings_calendar"
    )
    assert snapshot.event_context.details["official_earnings_points"] == 4.0
    assert snapshot.latest_price == Decimal("10.5000")
    assert snapshot.market_cap == Decimal("262500000.00")


def test_get_issuer_enrichment_provider_selects_alpha_vantage_composite() -> None:
    settings = Settings(
        market_data_provider="alpha_vantage",
        market_data_api_key="demo-key",
    )

    provider = get_issuer_enrichment_provider(settings)
    try:
        assert isinstance(provider, CompositeIssuerEnrichmentProvider)
    finally:
        provider.close()
