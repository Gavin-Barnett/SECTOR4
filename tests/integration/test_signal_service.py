import json
from datetime import UTC, datetime
from decimal import Decimal

from app.models.entities import SignalWindow
from app.schemas.signals import SignalFilters
from app.services.alerts import AlertDispatchResult, AlertService, StaticAlertNotifier
from app.services.signals import SignalService
from sector4_ai_summary import SignalSummaryResult, StaticSignalSummaryGenerator
from sector4_core.config import Settings
from sector4_core.enrichment import (
    CompositeIssuerEnrichmentProvider,
    HealthSnapshot,
    IssuerEnrichmentSnapshot,
    NullIssuerEnrichmentProvider,
    PriceContextSnapshot,
    StaticIssuerEnrichmentProvider,
)
from sector4_core.market_data import AlphaVantagePriceEnrichmentProvider
from sector4_sec_ingestion.enrichment import SecCompanyfactsEnrichmentProvider
from sector4_sec_ingestion.fixtures import load_fixture_manifest
from sector4_sec_ingestion.service import IngestionService
from sector4_sec_ingestion.types import FilingMetadata


def test_recompute_creates_signal_from_seeded_sample(db_session, seed_sample_data) -> None:
    assert seed_sample_data.generated == 1
    assert seed_sample_data.alerts_created == 0
    assert seed_sample_data.summaries_generated == 0

    signal = db_session.query(SignalWindow).one()
    assert signal.unique_buyers == 2
    assert signal.total_purchase_usd == Decimal("259800.00")
    assert signal.average_purchase_usd == Decimal("129900.00")
    assert signal.signal_score == Decimal("79.38")
    assert signal.summary_status == "disabled"
    assert signal.rationale_json["transaction_count"] == 2
    assert signal.rationale_json["health_status"] == "unknown"
    assert signal.rationale_json["price_context_status"] == "unavailable"
    assert signal.rationale_json["includes_indirect"] is False
    assert signal.rationale_json["includes_amendment"] is True
    assert signal.rationale_json["compensation_coverage_count"] == 2
    assert signal.rationale_json["executive_buyer_count"] == 2


def test_signal_service_detail_uses_amended_transaction_not_original(
    db_session, seed_sample_data
) -> None:
    signal = db_session.query(SignalWindow).one()
    detail = SignalService(db_session).get_signal(signal.id)

    assert detail is not None
    accession_numbers = [
        transaction.accession_number for transaction in detail.qualifying_transactions
    ]
    assert detail.summary_status == "disabled"
    assert detail.ai_summary is None
    assert detail.alerts == []
    assert detail.trade_setup is not None
    assert detail.trade_setup["entry_zone_low"] == 11.9
    assert detail.trade_setup["entry_zone_high"] == 13.0
    assert detail.trade_setup["cluster_purchase_vwap"] == 12.37
    assert detail.includes_amendment is True
    assert detail.includes_indirect is False
    assert "0001234567-24-000002" in accession_numbers
    assert "0001234567-24-000001" not in accession_numbers


def test_list_signals_can_exclude_amendment_backed_windows(db_session, seed_sample_data) -> None:
    signals = SignalService(db_session).list_signals(SignalFilters(include_amendments=False))

    assert signals == []


def test_list_signals_can_include_or_exclude_indirect_windows(
    db_session, fixture_dir, tmp_path
) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        raw_filings_dir=str(tmp_path / "mixed_indirect_raw"),
        fixture_manifest_path=str(fixture_dir / "manifest.json"),
    )
    ingestion_service = IngestionService(db_session, settings)

    first_metadata = FilingMetadata(
        accession_number="0001234567-24-000001",
        form_type="4",
        filed_at=datetime(2024, 2, 15, 14, 30, tzinfo=UTC),
        source_url="https://www.sec.gov/Archives/edgar/data/1234567/000123456724000001.txt",
        xml_url="https://www.sec.gov/Archives/edgar/data/1234567/000123456724000001/ownership.xml",
    )
    second_metadata = FilingMetadata(
        accession_number="0001234567-24-000010",
        form_type="4",
        filed_at=datetime(2024, 2, 19, 18, 45, tzinfo=UTC),
        source_url="https://www.sec.gov/Archives/edgar/data/1234567/000123456724000010.txt",
        xml_url="https://www.sec.gov/Archives/edgar/data/1234567/000123456724000010/ownership.xml",
    )

    ingestion_service.ingest_xml(
        first_metadata,
        (fixture_dir / "form4_open_market_purchase.xml").read_text(encoding="utf-8-sig"),
    )
    ingestion_service.ingest_xml(
        second_metadata,
        (fixture_dir / "form4_mixed_direct_indirect_purchase.xml").read_text(encoding="utf-8-sig"),
    )

    SignalService(db_session, settings).recompute()

    direct_only = SignalService(db_session).list_signals(SignalFilters(include_indirect=False))
    assert direct_only == []
    inclusive = SignalService(db_session).list_signals(SignalFilters(include_indirect=True))
    assert len(inclusive) == 1
    assert inclusive[0].includes_indirect is True
    assert inclusive[0].includes_amendment is False


def test_static_enrichment_provider_updates_signal_and_issuer(
    db_session, fixture_dir, tmp_path
) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        raw_filings_dir=str(tmp_path / "enriched_raw"),
        fixture_manifest_path=str(fixture_dir / "manifest.json"),
    )
    ingestion_service = IngestionService(db_session, settings)
    for metadata, fixture_path in load_fixture_manifest(settings.fixture_manifest_path):
        ingestion_service.ingest_xml(metadata, fixture_path.read_text(encoding="utf-8-sig"))

    provider = StaticIssuerEnrichmentProvider(
        {
            "0001234567": IssuerEnrichmentSnapshot(
                market_cap=Decimal("250000000.00"),
                latest_price=Decimal("13.2500"),
                price_context=PriceContextSnapshot(
                    status="available",
                    score=Decimal("12.00"),
                    details={"reference": "mock_52_week_range"},
                ),
                health=HealthSnapshot(
                    status="caution",
                    score=Decimal("14.00"),
                    details={"reference": "mock_current_ratio"},
                ),
            )
        },
        fallback=NullIssuerEnrichmentProvider(settings),
    )

    SignalService(db_session, settings, provider).recompute()

    signal = db_session.query(SignalWindow).one()
    assert signal.price_context_score == Decimal("12.00")
    assert signal.health_score == Decimal("14.00")
    assert signal.signal_score == Decimal("77.40")
    assert signal.rationale_json["price_context_status"] == "available"
    assert signal.rationale_json["health_status"] == "caution"
    assert signal.rationale_json["component_breakdown"]["price_context"]["status"] == "available"

    issuer = signal.issuer
    assert issuer.market_cap == Decimal("250000000.00")
    assert issuer.latest_price == Decimal("13.2500")


def test_sec_companyfacts_provider_updates_signal_health_and_issuer_metadata(
    db_session, fixture_dir, tmp_path
) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        raw_filings_dir=str(tmp_path / "sec_companyfacts_raw"),
        fixture_manifest_path=str(fixture_dir / "manifest.json"),
    )
    ingestion_service = IngestionService(db_session, settings)
    for metadata, fixture_path in load_fixture_manifest(settings.fixture_manifest_path):
        ingestion_service.ingest_xml(metadata, fixture_path.read_text(encoding="utf-8-sig"))

    companyfacts = json.loads(
        (fixture_dir / "companyfacts_acme.json").read_text(encoding="utf-8-sig")
    )
    submissions = json.loads(
        (fixture_dir / "submissions_acme.json").read_text(encoding="utf-8-sig")
    )
    provider = SecCompanyfactsEnrichmentProvider(
        settings,
        companyfacts_loader=lambda cik: companyfacts,
        submissions_loader=lambda cik: submissions,
    )

    SignalService(db_session, settings, provider).recompute()

    signal = db_session.query(SignalWindow).one()
    assert signal.health_score == Decimal("20.00")
    assert signal.price_context_score is None
    assert signal.signal_score == Decimal("86.66")
    assert signal.rationale_json["health_status"] == "healthy"
    assert signal.rationale_json["price_context_status"] == "unavailable"
    assert signal.rationale_json["event_context_status"] == "available"
    assert (
        signal.rationale_json["component_breakdown"]["health"]["details"]["altman_z_score"] == 6.41
    )
    assert (
        signal.rationale_json["component_breakdown"]["health"]["details"][
            "market_value_equity_source"
        ]
        == "price_times_shares_outstanding"
    )
    assert signal.rationale_json["component_breakdown"]["event_context"]["status"] == "available"
    assert signal.rationale_json["component_breakdown"]["event_context"]["raw_score"] == 10.0
    assert (
        signal.rationale_json["component_breakdown"]["event_context"]["details"][
            "earnings_related_points"
        ]
        == 4.0
    )
    assert (
        signal.rationale_json["component_breakdown"]["event_context"]["details"][
            "corporate_event_points"
        ]
        == 3.0
    )

    assert (
        signal.rationale_json["component_breakdown"]["event_context"]["details"][
            "estimated_next_earnings_date"
        ]
        == "2024-05-12"
    )
    assert (
        signal.rationale_json["component_breakdown"]["event_context"]["details"][
            "earnings_window_points"
        ]
        == 0.0
    )

    issuer = signal.issuer
    assert issuer.market_cap == Decimal("309250000.00")
    assert issuer.exchange == "NASDAQ"
    assert issuer.sic == "3571"
    assert issuer.state_of_incorp == "DE"


def test_composite_price_and_health_enrichment_updates_signal(
    db_session, fixture_dir, tmp_path
) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        raw_filings_dir=str(tmp_path / "alpha_vantage_raw"),
        fixture_manifest_path=str(fixture_dir / "manifest.json"),
        market_data_provider="alpha_vantage",
        market_data_api_key="demo-key",
    )
    ingestion_service = IngestionService(db_session, settings)
    for metadata, fixture_path in load_fixture_manifest(settings.fixture_manifest_path):
        ingestion_service.ingest_xml(metadata, fixture_path.read_text(encoding="utf-8-sig"))

    companyfacts = json.loads(
        (fixture_dir / "companyfacts_acme.json").read_text(encoding="utf-8-sig")
    )
    submissions = json.loads(
        (fixture_dir / "submissions_acme.json").read_text(encoding="utf-8-sig")
    )
    prices = json.loads(
        (fixture_dir / "alpha_vantage_weekly_acme.json").read_text(encoding="utf-8-sig")
    )
    provider = CompositeIssuerEnrichmentProvider(
        [
            AlphaVantagePriceEnrichmentProvider(settings, loader=lambda ticker: prices),
            SecCompanyfactsEnrichmentProvider(
                settings,
                companyfacts_loader=lambda cik: companyfacts,
                submissions_loader=lambda cik: submissions,
            ),
        ]
    )

    SignalService(db_session, settings, provider).recompute()

    signal = db_session.query(SignalWindow).one()
    assert signal.health_score == Decimal("20.00")
    assert signal.price_context_score == Decimal("12.60")
    assert signal.signal_score == Decimal("86.26")
    assert signal.rationale_json["health_status"] == "healthy"
    assert signal.rationale_json["price_context_status"] == "available"
    assert signal.rationale_json["event_context_status"] == "available"
    assert (
        signal.rationale_json["component_breakdown"]["health"]["details"]["altman_z_score"] == 5.74
    )
    assert (
        signal.rationale_json["component_breakdown"]["health"]["details"][
            "market_value_equity_source"
        ]
        == "price_times_shares_outstanding"
    )
    assert signal.rationale_json["component_breakdown"]["event_context"]["raw_score"] == 10.0

    issuer = signal.issuer
    assert issuer.latest_price == Decimal("10.5000")
    assert issuer.market_cap == Decimal("262500000.00")
    assert issuer.exchange == "NASDAQ"
    assert issuer.sic == "3571"
    assert issuer.state_of_incorp == "DE"


def test_recompute_generates_summaries_and_deduped_alerts(
    db_session, fixture_dir, tmp_path
) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        raw_filings_dir=str(tmp_path / "signal_ops_raw"),
        fixture_manifest_path=str(fixture_dir / "manifest.json"),
        alert_webhook_url="https://alerts.example.test/hook",
        market_data_api_key="demo-key",
        alert_min_signal_score=Decimal("75"),
        alert_min_score_delta=Decimal("5"),
        alert_min_total_purchase_delta_usd=Decimal("50000"),
    )
    ingestion_service = IngestionService(db_session, settings)
    for metadata, fixture_path in load_fixture_manifest(settings.fixture_manifest_path):
        ingestion_service.ingest_xml(metadata, fixture_path.read_text(encoding="utf-8-sig"))

    summary_calls: list[str] = []

    def summary_factory(request) -> SignalSummaryResult:
        summary_calls.append(request.input_hash)
        facts = request.facts
        return SignalSummaryResult(
            status="generated",
            summary_text=(
                f"Public filings show {facts['unique_buyers']} insiders buying "
                f"{facts['ticker']} in the current window."
            ),
            highlights=[
                f"Score {facts['signal_score']}",
                f"Buy value {facts['total_purchase_usd']}",
            ],
            warnings=["Public SEC filings only.", "Not investment advice."],
            provider="static",
            model="static-facts",
            input_hash=request.input_hash,
            generated_at=datetime(2026, 4, 8, 10, 0, tzinfo=UTC),
        )

    notifier = StaticAlertNotifier(
        lambda request: AlertDispatchResult(
            status="sent",
            sent_at=datetime(2026, 4, 8, 10, 5, tzinfo=UTC),
            response_status_code=202,
        )
    )
    summary_generator = StaticSignalSummaryGenerator(summary_factory)
    alert_service = AlertService(settings, notifier)

    base_service = SignalService(
        db_session,
        settings,
        summary_generator=summary_generator,
        alert_service=alert_service,
    )
    first = base_service.recompute()

    assert first.generated == 1
    assert first.alerts_created == 1
    assert first.summaries_generated == 1
    assert first.summaries_reused == 0
    assert len(summary_calls) == 1
    assert len(notifier.requests) == 1

    signal = db_session.query(SignalWindow).one()
    assert signal.summary_status == "generated"
    first_signal_id = signal.id

    second = SignalService(
        db_session,
        settings,
        summary_generator=summary_generator,
        alert_service=alert_service,
    ).recompute()

    assert second.signal_ids == [first_signal_id]
    assert second.alerts_created == 0
    assert second.summaries_generated == 0
    assert second.summaries_reused == 1
    assert len(summary_calls) == 1
    assert len(notifier.requests) == 1

    detail = SignalService(db_session).get_signal(first_signal_id)
    assert detail is not None
    assert detail.ai_summary is not None
    assert detail.ai_summary.text.startswith("Public filings show 2 insiders")
    assert len(detail.alerts) == 1
    assert detail.alerts[0].event_type == "new_signal"

    companyfacts = json.loads(
        (fixture_dir / "companyfacts_acme.json").read_text(encoding="utf-8-sig")
    )
    submissions = json.loads(
        (fixture_dir / "submissions_acme.json").read_text(encoding="utf-8-sig")
    )
    prices = json.loads(
        (fixture_dir / "alpha_vantage_weekly_acme.json").read_text(encoding="utf-8-sig")
    )
    enriched_provider = CompositeIssuerEnrichmentProvider(
        [
            AlphaVantagePriceEnrichmentProvider(settings, loader=lambda ticker: prices),
            SecCompanyfactsEnrichmentProvider(
                settings,
                companyfacts_loader=lambda cik: companyfacts,
                submissions_loader=lambda cik: submissions,
            ),
        ]
    )

    third = SignalService(
        db_session,
        settings,
        enrichment_provider=enriched_provider,
        summary_generator=summary_generator,
        alert_service=alert_service,
    ).recompute()

    assert third.signal_ids == [first_signal_id]
    assert third.alerts_created == 1
    assert third.summaries_generated == 1
    assert third.summaries_reused == 0
    assert len(summary_calls) == 2
    assert len(notifier.requests) == 2

    detail = SignalService(db_session).get_signal(first_signal_id)
    assert detail is not None
    assert detail.signal_score == Decimal("86.26")
    assert len(detail.alerts) == 2
    assert detail.alerts[0].event_type == "material_strengthening"
    assert detail.alerts[1].event_type == "new_signal"
