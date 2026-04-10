from decimal import Decimal

from sqlalchemy import func, select

from app.models.entities import Filing, Transaction
from sector4_core.config import Settings
from sector4_sec_ingestion.service import IngestionService


def test_ingestion_is_idempotent(db_session, fixture_dir, metadata, tmp_path) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        raw_filings_dir=str(tmp_path / "raw"),
    )
    service = IngestionService(db_session, settings)
    xml = (fixture_dir / "form4_open_market_purchase.xml").read_text(encoding="utf-8")

    first = service.ingest_xml(metadata, xml)
    second = service.ingest_xml(metadata, xml)

    assert first.status == "created"
    assert second.status == "skipped"
    assert db_session.scalar(select(func.count()).select_from(Filing)) == 1
    assert db_session.scalar(select(func.count()).select_from(Transaction)) == 2
    assert (tmp_path / "raw" / f"{metadata.accession_number}.xml").exists()


def test_ingestion_updates_when_fingerprint_changes(
    db_session, fixture_dir, metadata, tmp_path
) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        raw_filings_dir=str(tmp_path / "raw_update"),
    )
    service = IngestionService(db_session, settings)
    original_xml = (fixture_dir / "form4_open_market_purchase.xml").read_text(encoding="utf-8")
    updated_xml = original_xml.replace("<value>12.34</value>", "<value>13.00</value>", 1)

    service.ingest_xml(metadata, original_xml)
    updated = service.ingest_xml(metadata, updated_xml)

    assert updated.status == "updated"
    purchase = db_session.scalars(
        select(Transaction).where(Transaction.transaction_code == "P")
    ).one()
    assert purchase.price_per_share == Decimal("13.0000")
