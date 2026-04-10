from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("SEC_USER_AGENT", "SECTOR4/0.1 (test@example.com)")

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import entities  # noqa: F401
from app.schemas.signals import SignalRecomputeResponse
from app.services.signals import SignalService
from sector4_core.config import Settings
from sector4_core.observability import get_metrics_registry
from sector4_sec_ingestion.fixtures import load_fixture_manifest, load_proxy_fixture_manifest
from sector4_sec_ingestion.proxy_service import ProxyCompensationService
from sector4_sec_ingestion.service import IngestionService
from sector4_sec_ingestion.types import FilingMetadata

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "sec"


@pytest.fixture(autouse=True)
def reset_metrics() -> None:
    get_metrics_registry().reset()


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def metadata() -> FilingMetadata:
    return FilingMetadata(
        accession_number="0001234567-24-000001",
        form_type="4",
        filed_at=datetime(2024, 2, 15, 14, 30, tzinfo=UTC),
        source_url="https://www.sec.gov/Archives/edgar/data/1234567/000123456724000001.txt",
        xml_url="https://www.sec.gov/Archives/edgar/data/1234567/000123456724000001/ownership.xml",
    )


@pytest.fixture
def db_session(tmp_path: Path):
    database_path = tmp_path / "test.db"
    engine = create_engine(f"sqlite+pysqlite:///{database_path}", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def seed_sample_data(db_session, fixture_dir: Path, tmp_path: Path) -> SignalRecomputeResponse:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        raw_filings_dir=str(tmp_path / "raw_filings"),
        fixture_manifest_path=str(fixture_dir / "manifest.json"),
        proxy_fixture_manifest_path=str(fixture_dir / "proxy_manifest.json"),
    )
    ingestion_service = IngestionService(db_session, settings)
    for manifest_metadata, fixture_path in load_fixture_manifest(settings.fixture_manifest_path):
        ingestion_service.ingest_xml(
            manifest_metadata,
            fixture_path.read_text(encoding="utf-8"),
        )

    proxy_service = ProxyCompensationService(db_session, settings)
    try:
        for manifest_metadata, fixture_path in load_proxy_fixture_manifest(
            settings.proxy_fixture_manifest_path
        ):
            proxy_service.ingest_html(
                manifest_metadata,
                fixture_path.read_text(encoding="utf-8"),
            )
    finally:
        proxy_service.close()

    return SignalService(db_session, settings).recompute()
