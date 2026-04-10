import json
from datetime import date
from decimal import Decimal

from sector4_core.config import Settings
from sector4_core.enrichment import IssuerEnrichmentRequest, get_issuer_enrichment_provider
from sector4_sec_ingestion.enrichment import SecCompanyfactsEnrichmentProvider


def test_sec_companyfacts_provider_builds_health_event_context_and_float_market_cap(
    fixture_dir,
) -> None:
    companyfacts = json.loads(
        (fixture_dir / "companyfacts_acme.json").read_text(encoding="utf-8-sig")
    )
    submissions = json.loads(
        (fixture_dir / "submissions_acme.json").read_text(encoding="utf-8-sig")
    )
    provider = SecCompanyfactsEnrichmentProvider(
        companyfacts_loader=lambda cik: companyfacts,
        submissions_loader=lambda cik: submissions,
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
    assert snapshot.sic == "3571"
    assert snapshot.state_of_incorp == "DE"
    assert snapshot.market_cap == Decimal("240000000.00")
    assert snapshot.price_context.status == "unavailable"
    assert snapshot.health.status == "healthy"
    assert snapshot.health.score == Decimal("20.00")
    assert snapshot.health.details["model"] == "altman_z_liquidity_debt_maturity"
    assert snapshot.health.details["current_ratio"] == 3.0
    assert snapshot.health.details["altman_z_score"] == 5.42
    assert snapshot.health.details["altman_zone"] == "safe"
    assert snapshot.health.details["market_value_equity_usd"] == 240000000.0
    assert snapshot.health.details["market_value_equity_source"] == "entity_public_float"
    assert snapshot.health.details["cash_and_equivalents_usd"] == 17000000.0
    assert snapshot.health.details["debt_due_within_year_usd"] == 4000000.0
    assert snapshot.health.details["total_debt_usd"] == 20000000.0
    assert snapshot.health.details["cash_to_near_term_debt_ratio"] == 4.25
    assert snapshot.health.details["interest_coverage_ratio"] == 6.67
    assert snapshot.health.details["altman_inputs_missing"] == []
    assert snapshot.health.details["distress_flags"] == []
    assert snapshot.health.details["health_component_points"] == {
        "altman_points": 10.0,
        "liquidity_points": 6.0,
        "solvency_points": 4.0,
        "debt_maturity_points": 2.0,
        "credit_proxy_points": 2.0,
    }
    assert snapshot.event_context.status == "available"
    assert snapshot.event_context.score == Decimal("10.00")
    assert snapshot.event_context.details["recent_filing_count"] == 4
    assert snapshot.event_context.details["earnings_related_points"] == 4.0
    assert snapshot.event_context.details["corporate_event_points"] == 3.0
    assert snapshot.event_context.details["recent_periodic_report_points"] == 3.0
    assert snapshot.event_context.details["official_earnings_date"] is None
    assert snapshot.event_context.details["official_earnings_points"] == 0.0
    assert snapshot.event_context.details["earnings_window_points"] == 0.0
    assert snapshot.event_context.details["estimated_next_earnings_date"] == "2024-05-12"
    assert snapshot.event_context.details["days_until_estimated_earnings"] == 84
    assert snapshot.event_context.details["estimated_earnings_source"] == "recent_earnings_release"
    assert snapshot.event_context.details["upcoming_earnings_source"] == "recent_earnings_release"
    assert snapshot.event_context.details["upcoming_earnings_window_detected"] is False
    assert snapshot.event_context.details["item_classification_enabled"] is True

    matched_filings = snapshot.event_context.details["matched_filings"]
    assert [filing["label"] for filing in matched_filings] == [
        "earnings_release",
        "corporate_update",
        "recent_periodic_report",
    ]
    assert [filing["form"] for filing in matched_filings] == ["8-K", "8-K", "10-Q"]
    assert matched_filings[0]["item_codes"] == ["2.02", "9.01"]
    assert matched_filings[1]["item_codes"] == ["1.01", "8.01"]


def test_sec_companyfacts_provider_prefers_price_times_shares_for_market_cap(fixture_dir) -> None:
    companyfacts = json.loads(
        (fixture_dir / "companyfacts_acme.json").read_text(encoding="utf-8-sig")
    )
    submissions = json.loads(
        (fixture_dir / "submissions_acme.json").read_text(encoding="utf-8-sig")
    )
    provider = SecCompanyfactsEnrichmentProvider(
        companyfacts_loader=lambda cik: companyfacts,
        submissions_loader=lambda cik: submissions,
    )

    snapshot = provider.enrich(
        IssuerEnrichmentRequest(
            cik="1234567",
            ticker="ACME",
            name="Acme Robotics, Inc.",
            market_cap=None,
            latest_price=Decimal("10.5000"),
            event_anchor_date=date(2024, 2, 18),
        )
    )

    assert snapshot.market_cap == Decimal("262500000.00")
    assert snapshot.health.details["market_value_equity_source"] == "price_times_shares_outstanding"
    assert snapshot.health.details["altman_z_score"] == 5.74


def test_sec_companyfacts_provider_uses_price_hint_when_no_market_quote_available() -> None:
    companyfacts = {
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {
                                "end": "2024-03-31",
                                "val": 25000000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                }
            }
        }
    }
    provider = SecCompanyfactsEnrichmentProvider(
        companyfacts_loader=lambda cik: companyfacts,
        submissions_loader=lambda cik: {},
    )

    snapshot = provider.enrich(
        IssuerEnrichmentRequest(
            cik="1234567",
            ticker="ACME",
            name="Acme Robotics, Inc.",
            market_cap=None,
            latest_price=None,
            market_cap_price_hint=Decimal("12.2500"),
        )
    )

    assert snapshot.market_cap == Decimal("306250000.00")
    assert snapshot.latest_price is None


def test_sec_companyfacts_provider_uses_official_earnings_date_when_forwarded(
    fixture_dir,
) -> None:
    companyfacts = json.loads(
        (fixture_dir / "companyfacts_acme.json").read_text(encoding="utf-8-sig")
    )
    submissions = json.loads(
        (fixture_dir / "submissions_acme.json").read_text(encoding="utf-8-sig")
    )
    provider = SecCompanyfactsEnrichmentProvider(
        companyfacts_loader=lambda cik: companyfacts,
        submissions_loader=lambda cik: submissions,
    )

    snapshot = provider.enrich(
        IssuerEnrichmentRequest(
            cik="1234567",
            ticker="ACME",
            name="Acme Robotics, Inc.",
            market_cap=None,
            latest_price=None,
            event_anchor_date=date(2024, 2, 18),
            upcoming_earnings_date=date(2024, 3, 28),
            earnings_date_source="alpha_vantage_earnings_calendar",
        )
    )

    assert snapshot.event_context.details["official_earnings_date"] == "2024-03-28"
    assert (
        snapshot.event_context.details["official_earnings_provider"]
        == "alpha_vantage_earnings_calendar"
    )
    assert snapshot.event_context.details["official_earnings_points"] == 4.0
    assert snapshot.event_context.details["upcoming_earnings_date"] == "2024-03-28"
    assert (
        snapshot.event_context.details["upcoming_earnings_source"]
        == "alpha_vantage_earnings_calendar"
    )
    assert snapshot.event_context.score == Decimal("10.00")


def test_sec_companyfacts_provider_detects_upcoming_estimated_earnings_window(
    fixture_dir,
) -> None:
    companyfacts = json.loads(
        (fixture_dir / "companyfacts_acme.json").read_text(encoding="utf-8-sig")
    )
    submissions = json.loads(
        (fixture_dir / "submissions_acme.json").read_text(encoding="utf-8-sig")
    )
    provider = SecCompanyfactsEnrichmentProvider(
        companyfacts_loader=lambda cik: companyfacts,
        submissions_loader=lambda cik: submissions,
    )

    snapshot = provider.enrich(
        IssuerEnrichmentRequest(
            cik="1234567",
            ticker="ACME",
            name="Acme Robotics, Inc.",
            market_cap=None,
            latest_price=None,
            event_anchor_date=date(2024, 5, 10),
        )
    )

    assert snapshot.event_context.status == "available"
    assert snapshot.event_context.score == Decimal("3.00")
    assert snapshot.event_context.details["earnings_related_points"] == 0.0
    assert snapshot.event_context.details["corporate_event_points"] == 0.0
    assert snapshot.event_context.details["recent_periodic_report_points"] == 0.0
    assert snapshot.event_context.details["earnings_window_points"] == 3.0
    assert snapshot.event_context.details["estimated_next_earnings_date"] == "2024-05-12"
    assert snapshot.event_context.details["days_until_estimated_earnings"] == 2
    assert snapshot.event_context.details["estimated_earnings_source"] == "recent_earnings_release"
    assert snapshot.event_context.details["upcoming_earnings_date"] == "2024-05-12"
    assert snapshot.event_context.details["upcoming_earnings_source"] == "recent_earnings_release"
    assert snapshot.event_context.details["upcoming_earnings_window_detected"] is True
    assert snapshot.event_context.details["matched_filings"] == []


def test_sec_companyfacts_provider_returns_unknown_when_current_ratio_inputs_missing() -> None:
    companyfacts = {
        "facts": {
            "us-gaap": {
                "AssetsCurrent": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": 1500000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                }
            }
        }
    }
    provider = SecCompanyfactsEnrichmentProvider(
        companyfacts_loader=lambda cik: companyfacts,
        submissions_loader=lambda cik: {},
    )

    snapshot = provider.enrich(
        IssuerEnrichmentRequest(
            cik="1234567",
            ticker="ACME",
            name="Acme Robotics, Inc.",
            market_cap=None,
            latest_price=None,
        )
    )

    assert snapshot.health.status == "unknown"
    assert snapshot.health.score is None
    assert snapshot.health.details["reason"] == "missing_current_ratio_inputs"


def test_sec_companyfacts_provider_marks_distress_when_altman_balance_sheet_and_debt_are_weak() -> (
    None
):
    companyfacts = {
        "facts": {
            "dei": {
                "EntityPublicFloat": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": 5000000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                }
            },
            "us-gaap": {
                "AssetsCurrent": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": 2000000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                },
                "LiabilitiesCurrent": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": 6000000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                },
                "Assets": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": 10000000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                },
                "Liabilities": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": 14000000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                },
                "StockholdersEquity": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": -4000000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                },
                "RetainedEarningsAccumulatedDeficit": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": -6000000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                },
                "OperatingIncomeLoss": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": -1000000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": 4000000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                },
                "CashAndCashEquivalentsAtCarryingValue": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": 1000000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                },
                "LongTermDebtCurrent": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": 7000000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                },
                "LongTermDebtNoncurrent": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": 2000000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                },
                "InterestExpenseAndDebtExpense": {
                    "units": {
                        "USD": [
                            {
                                "end": "2024-03-31",
                                "val": 2000000,
                                "form": "10-Q",
                                "filed": "2024-05-09",
                            }
                        ]
                    }
                },
            },
        }
    }
    provider = SecCompanyfactsEnrichmentProvider(
        companyfacts_loader=lambda cik: companyfacts,
        submissions_loader=lambda cik: {},
    )

    snapshot = provider.enrich(
        IssuerEnrichmentRequest(
            cik="1234567",
            ticker="ACME",
            name="Acme Robotics, Inc.",
            market_cap=None,
            latest_price=None,
        )
    )

    assert snapshot.health.status == "distressed"
    assert snapshot.health.score == Decimal("1.00")
    assert snapshot.health.details["altman_zone"] == "distress"
    assert snapshot.health.details["altman_z_score"] == -1.04
    assert snapshot.health.details["distress_flags"] == [
        "weak_liquidity",
        "liabilities_exceed_assets",
        "negative_equity",
        "altman_distress_zone",
        "near_term_debt_exceeds_cash",
        "heavy_near_term_debt_maturity",
        "weak_interest_coverage",
    ]


def test_get_issuer_enrichment_provider_selects_sec_companyfacts_provider() -> None:
    settings = Settings(market_data_provider="sec_companyfacts")

    provider = get_issuer_enrichment_provider(settings)
    try:
        assert isinstance(provider, SecCompanyfactsEnrichmentProvider)
    finally:
        provider.close()
