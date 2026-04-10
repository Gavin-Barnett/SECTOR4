from decimal import Decimal

from app.models.entities import InsiderCompensation, Issuer
from sector4_core.config import Settings
from sector4_sec_ingestion.fixtures import load_fixture_manifest, load_proxy_fixture_manifest
from sector4_sec_ingestion.proxy_service import ProxyCompensationService
from sector4_sec_ingestion.service import IngestionService


def test_proxy_compensation_ingestion_persists_and_matches_existing_insiders(
    db_session, fixture_dir, tmp_path
) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        raw_filings_dir=str(tmp_path / "proxy_ingest_raw"),
        fixture_manifest_path=str(fixture_dir / "manifest.json"),
        proxy_fixture_manifest_path=str(fixture_dir / "proxy_manifest.json"),
    )
    ownership_service = IngestionService(db_session, settings)
    for metadata, fixture_path in load_fixture_manifest(settings.fixture_manifest_path):
        ownership_service.ingest_xml(metadata, fixture_path.read_text(encoding="utf-8-sig"))

    proxy_service = ProxyCompensationService(db_session, settings)
    try:
        for metadata, fixture_path in load_proxy_fixture_manifest(
            settings.proxy_fixture_manifest_path
        ):
            result = proxy_service.ingest_html(
                metadata,
                fixture_path.read_text(encoding="utf-8"),
            )
    finally:
        proxy_service.close()

    assert result.record_count == 2
    assert result.matched_insider_count == 2

    issuer = db_session.query(Issuer).filter(Issuer.cik == "0001234567").one()
    records = (
        db_session.query(InsiderCompensation)
        .filter(InsiderCompensation.issuer_id == issuer.id)
        .order_by(InsiderCompensation.insider_name.asc())
        .all()
    )

    assert len(records) == 2
    assert records[0].insider_name == "Jane Example"
    assert records[0].insider is not None
    assert records[0].insider.officer_title == "Chief Executive Officer"
    assert records[0].total_compensation_usd == Decimal("1600000.00")
    assert records[1].insider_name == "Mark Example"
    assert records[1].insider is not None
    assert records[1].insider.officer_title == "Chief Financial Officer"
    assert records[1].total_compensation_usd == Decimal("950000.00")
