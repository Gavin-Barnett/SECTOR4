from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.models.entities import InsiderCompensation
from app.services.operations import OperationsService
from sector4_core.config import Settings
from sector4_sec_ingestion.fixtures import load_fixture_manifest
from sector4_sec_ingestion.types import FilingMetadata, SecIndexEntry


class FakeSecClient:
    def __init__(
        self,
        index_entries: dict[date, list[SecIndexEntry]],
        filings: dict[str, tuple[FilingMetadata, str]],
        submissions: dict[str, dict] | None = None,
        texts: dict[str, str] | None = None,
    ) -> None:
        self.index_entries = index_entries
        self.filings = filings
        self.submissions = submissions or {}
        self.texts = texts or {}
        self.closed = False
        self.settings = SimpleNamespace(sec_base_url="https://www.sec.gov")

    def fetch_daily_index(self, for_date: date) -> list[SecIndexEntry]:
        return list(self.index_entries.get(for_date, []))

    def fetch_filing_metadata(self, entry: SecIndexEntry) -> tuple[FilingMetadata, str]:
        return self.filings[entry.accession_number]

    def fetch_submissions(self, cik: str) -> dict:
        return self.submissions[cik]

    def fetch_text(self, url: str) -> str:
        return self.texts[url]

    def build_archive_document_url(
        self, cik: str, accession_number: str, primary_document: str
    ) -> str:
        return (
            "https://www.sec.gov/Archives/edgar/data/"
            f"{int(cik)}/{accession_number.replace('-', '')}/{primary_document}"
        )

    def close(self) -> None:
        self.closed = True


def test_operations_service_live_and_backfill_are_idempotent(
    db_session, fixture_dir, tmp_path
) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        raw_filings_dir=str(tmp_path / "ops_service_raw"),
        fixture_manifest_path=str(fixture_dir / "manifest.json"),
        ops_backfill_days=5,
        ops_live_ingest_limit=10,
    )
    fixtures = {
        metadata.accession_number: (metadata, fixture_path.read_text(encoding="utf-8-sig"))
        for metadata, fixture_path in load_fixture_manifest(settings.fixture_manifest_path)
    }
    first_entry = SecIndexEntry(
        form_type="4",
        company_name="Acme Robotics, Inc.",
        cik="1234567",
        filed_date=date(2024, 2, 15),
        filename="edgar/data/1234567/000123456724000001.txt",
    )
    second_entry = SecIndexEntry(
        form_type="4",
        company_name="Acme Robotics, Inc.",
        cik="1234567",
        filed_date=date(2024, 2, 19),
        filename="edgar/data/1234567/000123456724000004.txt",
    )
    fake_client = FakeSecClient(
        index_entries={
            date(2024, 2, 15): [first_entry],
            date(2024, 2, 19): [second_entry],
        },
        filings={
            "0001234567-24-000001": fixtures["0001234567-24-000001"],
            "0001234567-24-000004": fixtures["0001234567-24-000004"],
        },
    )
    service = OperationsService(
        db_session,
        settings,
        sec_client_factory=lambda: fake_client,
    )

    live_result = service.ingest_live(target_date=date(2024, 2, 15), recompute=False)

    assert live_result.mode == "live"
    assert live_result.days_processed == 1
    assert live_result.entries_discovered == 1
    assert live_result.created_count == 1
    assert live_result.updated_count == 0
    assert live_result.skipped_count == 0
    assert live_result.failure_count == 0
    assert live_result.accession_numbers == ["0001234567-24-000001"]

    backfill_result = service.ingest_backfill(
        start_date=date(2024, 2, 15),
        end_date=date(2024, 2, 19),
        recompute=False,
    )

    assert backfill_result.mode == "backfill"
    assert backfill_result.days_processed == 5
    assert backfill_result.entries_discovered == 2
    assert backfill_result.created_count == 1
    assert backfill_result.updated_count == 0
    assert backfill_result.skipped_count == 1
    assert backfill_result.failure_count == 0
    assert backfill_result.accession_numbers == [
        "0001234567-24-000001",
        "0001234567-24-000004",
    ]
    assert fake_client.closed is True


def test_operations_service_can_sync_proxy_compensation_when_enabled(
    db_session, fixture_dir, tmp_path
) -> None:
    settings = Settings(
        database_url="sqlite+pysqlite:///:memory:",
        raw_filings_dir=str(tmp_path / "ops_proxy_raw"),
        fixture_manifest_path=str(fixture_dir / "manifest.json"),
        sec_proxy_sync_enabled=True,
        ops_live_ingest_limit=10,
    )
    fixtures = {
        metadata.accession_number: (metadata, fixture_path.read_text(encoding="utf-8-sig"))
        for metadata, fixture_path in load_fixture_manifest(settings.fixture_manifest_path)
    }
    proxy_html = (fixture_dir / "def14a_acme_proxy_statement.html").read_text(encoding="utf-8")
    entry = SecIndexEntry(
        form_type="4",
        company_name="Acme Robotics, Inc.",
        cik="1234567",
        filed_date=date(2024, 2, 15),
        filename="edgar/data/1234567/000123456724000001.txt",
    )
    proxy_url = (
        "https://www.sec.gov/Archives/edgar/data/1234567/000123456724000050/proxy_statement.html"
    )
    fake_client = FakeSecClient(
        index_entries={date(2024, 2, 15): [entry]},
        filings={"0001234567-24-000001": fixtures["0001234567-24-000001"]},
        submissions={
            "1234567": {
                "name": "Acme Robotics, Inc.",
                "filings": {
                    "recent": {
                        "form": ["DEF 14A"],
                        "filingDate": ["2024-02-01"],
                        "accessionNumber": ["0001234567-24-000050"],
                        "primaryDocument": ["proxy_statement.html"],
                    }
                },
            }
        },
        texts={proxy_url: proxy_html},
    )
    service = OperationsService(
        db_session,
        settings,
        sec_client_factory=lambda: fake_client,
    )

    result = service.ingest_live(target_date=date(2024, 2, 15), recompute=False)

    assert result.created_count == 1
    records = db_session.query(InsiderCompensation).all()
    assert len(records) == 2
    assert records[0].filed_at.date() == date(2024, 2, 1)
    assert fake_client.closed is True
