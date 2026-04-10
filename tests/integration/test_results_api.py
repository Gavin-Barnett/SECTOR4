from datetime import date, timedelta
from decimal import Decimal

from app.models.entities import SignalOutcomeCheckpoint
from app.services.outcomes import SignalOutcomeTracker
from app.services.signals import SignalService
from sector4_core.config import Settings
from sector4_core.price_history import HistoricalPricePoint
from sector4_sec_ingestion.fixtures import load_fixture_manifest
from sector4_sec_ingestion.service import IngestionService


class SequencePriceHistoryProvider:
    def __init__(self, prices: list[str]) -> None:
        self._prices = [Decimal(price) for price in prices]
        self.calls: list[date] = []

    def lookup_price(self, ticker: str | None, target_date: date):
        self.calls.append(target_date)
        index = len(self.calls) - 1
        return HistoricalPricePoint(
            status="captured",
            source="static_daily",
            price_date=target_date,
            price_value=self._prices[index],
            details={"target_date": target_date.isoformat()},
        )

    def close(self) -> None:
        return None


def test_results_endpoint_exposes_first_seen_and_forward_returns(
    client, db_session, fixture_dir, tmp_path
) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        raw_filings_dir=str(tmp_path / "results_raw"),
        fixture_manifest_path=str(fixture_dir / "manifest.json"),
    )
    ingestion_service = IngestionService(db_session, settings)
    for metadata, fixture_path in load_fixture_manifest(settings.fixture_manifest_path):
        ingestion_service.ingest_xml(metadata, fixture_path.read_text(encoding="utf-8-sig"))

    tracker = SignalOutcomeTracker(
        db_session,
        settings,
        price_history_provider=SequencePriceHistoryProvider(["10.00", "11.00", "12.00", "14.00"]),
        today_provider=lambda: date.today() + timedelta(days=60),
    )

    SignalService(db_session, settings, outcome_tracker=tracker).recompute()

    checkpoints = db_session.query(SignalOutcomeCheckpoint).all()
    assert len(checkpoints) == 4

    response = client.get("/results")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["ticker"] == "ACME"
    assert payload[0]["first_seen_price"] == "10.0000"
    assert payload[0]["week_1_return_pct"] == "10.00"
    assert payload[0]["week_2_return_pct"] == "20.00"
    assert payload[0]["week_4_return_pct"] == "40.00"
    assert payload[0]["latest_completed_checkpoint"] == "week_4"
