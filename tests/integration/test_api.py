from __future__ import annotations

from datetime import date

from app.schemas.signals import SignalRecomputeResponse
from app.services.operations import IngestOperationResult, OperationFailure, OperationsService
from app.services.scheduler import PollScheduler
from sector4_core.config import Settings, get_settings
from sector4_sec_ingestion.fixtures import load_fixture_manifest
from sector4_sec_ingestion.service import IngestionService


def test_health_endpoint(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["uses_public_sec_filings_only"] is True
    assert body["scheduler_enabled"] is False


def test_ready_endpoint(client) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_health_endpoint_allows_dashboard_origin(client) -> None:
    response = client.get("/health", headers={"Origin": "http://localhost:5180"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5180"


def test_metrics_endpoint_reports_runtime_counters(client, seed_sample_data) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["scheduler"]["enabled"] is False
    assert payload["counters"]["parse.successes"] >= 1
    assert payload["counters"]["signals.recompute_runs"] == 1


def test_signal_endpoints_return_seeded_signal(client, seed_sample_data) -> None:
    response = client.get("/signals")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["ticker"] == "ACME"
    assert body[0]["unique_buyers"] == 2
    assert body[0]["total_purchase_usd"] == "259800.00"
    assert body[0]["signal_score"] == "79.38"
    assert body[0]["summary_status"] == "disabled"
    assert body[0]["includes_indirect"] is False
    assert body[0]["includes_amendment"] is True

    cik_filtered = client.get("/signals", params={"cik": "0001234567"})
    assert cik_filtered.status_code == 200
    assert len(cik_filtered.json()) == 1

    detail = client.get(f"/signals/{body[0]['id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert len(detail_body["qualifying_transactions"]) == 2
    assert detail_body["component_breakdown"]["price_context"]["status"] == "unavailable"
    assert detail_body["component_breakdown"]["event_context"]["status"] == "unavailable"
    assert detail_body["ai_summary"] is None
    assert detail_body["alerts"] == []

    latest = client.get("/signals/latest")
    assert latest.status_code == 200
    assert latest.json()[0]["ticker"] == "ACME"
    assert latest.json()[0]["summary_status"] == "disabled"


def test_recompute_ops_endpoint_is_available_in_development(
    client, db_session, fixture_dir, tmp_path
) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        raw_filings_dir=str(tmp_path / "ops_raw"),
        fixture_manifest_path=str(fixture_dir / "manifest.json"),
    )
    ingestion_service = IngestionService(db_session, settings)
    for metadata, fixture_path in load_fixture_manifest(settings.fixture_manifest_path):
        ingestion_service.ingest_xml(metadata, fixture_path.read_text(encoding="utf-8"))

    response = client.post("/ops/recompute-signals")

    assert response.status_code == 200
    payload = response.json()
    assert payload["generated"] == 1
    assert payload["alerts_created"] == 0
    assert payload["summaries_generated"] == 0
    assert payload["summaries_reused"] == 0


def test_ingest_ops_endpoints_are_available_in_development(client, monkeypatch) -> None:
    live_result = IngestOperationResult(
        mode="live",
        start_date=date(2024, 2, 15),
        end_date=date(2024, 2, 15),
        days_processed=1,
        entries_discovered=2,
        created_count=1,
        updated_count=0,
        skipped_count=1,
        accession_numbers=["0001234567-24-000001", "0001234567-24-000002"],
        recompute_result=SignalRecomputeResponse(
            generated=1,
            signal_ids=[42],
            alerts_created=0,
            summaries_generated=0,
            summaries_reused=0,
        ),
    )
    backfill_result = IngestOperationResult(
        mode="backfill",
        start_date=date(2024, 2, 12),
        end_date=date(2024, 2, 15),
        days_processed=4,
        entries_discovered=3,
        created_count=2,
        updated_count=0,
        skipped_count=0,
        failure_count=1,
        accession_numbers=["0001234567-24-000001", "0001234567-24-000004"],
        failures=[
            OperationFailure(
                target_date=date(2024, 2, 14),
                stage="daily_index",
                error="SEC temporarily unavailable",
            )
        ],
        recompute_result=SignalRecomputeResponse(
            generated=1,
            signal_ids=[42],
            alerts_created=0,
            summaries_generated=0,
            summaries_reused=0,
        ),
    )

    monkeypatch.setattr(OperationsService, "ingest_live", lambda self, **kwargs: live_result)
    monkeypatch.setattr(
        OperationsService,
        "ingest_backfill",
        lambda self, **kwargs: backfill_result,
    )

    live_response = client.post(
        "/ops/ingest/live",
        json={"target_date": "2024-02-15", "limit": 5, "recompute": True},
    )

    assert live_response.status_code == 200
    live_payload = live_response.json()
    assert live_payload["mode"] == "live"
    assert live_payload["created_count"] == 1
    assert live_payload["skipped_count"] == 1
    assert live_payload["recompute"]["signal_ids"] == [42]

    backfill_response = client.post(
        "/ops/ingest/backfill",
        json={"days": 4, "limit_per_day": 10, "recompute": True},
    )

    assert backfill_response.status_code == 200
    backfill_payload = backfill_response.json()
    assert backfill_payload["mode"] == "backfill"
    assert backfill_payload["failure_count"] == 1
    assert backfill_payload["failures"][0]["stage"] == "daily_index"


def test_ops_endpoints_require_token_outside_development(client, monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPS_API_TOKEN", "secret-token")
    get_settings.cache_clear()
    try:
        blocked = client.post("/ops/recompute-signals")
        assert blocked.status_code == 403

        allowed = client.post(
            "/ops/recompute-signals",
            headers={"X-Ops-Token": "secret-token"},
        )
        assert allowed.status_code == 200
    finally:
        get_settings.cache_clear()


def test_ops_endpoints_are_disabled_without_token_in_non_dev(client, monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("OPS_API_TOKEN", raising=False)
    get_settings.cache_clear()
    try:
        response = client.post("/ops/recompute-signals")
        assert response.status_code == 503
        assert response.json()["detail"] == "ops endpoints disabled"
    finally:
        get_settings.cache_clear()


def test_health_reports_running_scheduler_when_present(client) -> None:
    scheduler = PollScheduler(None, runner=lambda: None)
    client.app.state.poll_scheduler = scheduler
    try:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["scheduler_running"] is False
    finally:
        client.app.state.poll_scheduler = None