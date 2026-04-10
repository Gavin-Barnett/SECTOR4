import json

import httpx

from sector4_core.config import Settings
from sector4_sec_ingestion.client import SecClient, normalize_cik, parse_daily_index
from sector4_sec_ingestion.types import SecIndexEntry


def test_parse_daily_index_filters_for_form4_entries(fixture_dir) -> None:
    entries = parse_daily_index(
        (fixture_dir / "daily_index_sample.idx").read_text(encoding="utf-8-sig")
    )

    assert len(entries) == 2
    assert entries[0].form_type == "4"
    assert entries[0].accession_number == "0001234567-24-000001"
    assert entries[1].form_type == "4/A"


def test_parse_daily_index_supports_pipe_delimited_entries() -> None:
    entries = parse_daily_index(
        """
Description:           Daily Index of EDGAR Dissemination Feed
CIK|Company Name|Form Type|Date Filed|File Name
--------------------------------------------------------------------------------
1003078|MSC INDUSTRIAL DIRECT CO INC|4|20260409|edgar/data/1003078/0000950142-26-001093.txt
1008015|JACOBSON MITCHELL|4|20260409|edgar/data/1008015/0000950142-26-001093.txt
1000697|WATERS CORP /DE/|DEF 14A|20260409|edgar/data/1000697/0001193125-26-149657.txt
1012100|SEALED AIR CORP/DE|4/A|20260409|edgar/data/1012100/0001193125-26-149641.txt
        """.strip()
    )

    assert len(entries) == 3
    assert entries[0].form_type == "4"
    assert entries[0].cik == "1003078"
    assert entries[1].company_name == "JACOBSON MITCHELL"
    assert entries[2].form_type == "4/A"


def test_sec_index_entry_normalizes_directory_path() -> None:
    entry = SecIndexEntry(
        form_type="4",
        company_name="MSC INDUSTRIAL DIRECT CO INC",
        cik="1003078",
        filed_date=__import__("datetime").date(2026, 4, 9),
        filename="edgar/data/1003078/0000950142-26-001093.txt",
    )

    assert entry.accession_number == "0000950142-26-001093"
    assert entry.directory_path == "edgar/data/1003078/000095014226001093"


def test_normalize_cik_zero_pads_numeric_strings() -> None:
    assert normalize_cik("1234567") == "0001234567"
    assert normalize_cik("0001234567") == "0001234567"


def test_sec_client_fetches_companyfacts_and_submissions(fixture_dir) -> None:
    companyfacts = json.loads(
        (fixture_dir / "companyfacts_acme.json").read_text(encoding="utf-8-sig")
    )
    submissions = json.loads(
        (fixture_dir / "submissions_acme.json").read_text(encoding="utf-8-sig")
    )
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/submissions/CIK0001234567.json"):
            return httpx.Response(200, json=submissions)
        if request.url.path.endswith("/api/xbrl/companyfacts/CIK0001234567.json"):
            return httpx.Response(200, json=companyfacts)
        return httpx.Response(404)

    settings = Settings(
        sec_user_agent="SECTOR4/0.1 (test@example.com)",
        sec_data_base_url="https://data.sec.gov",
        sec_max_rps=1000,
    )
    client = SecClient(settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        submissions_payload = client.fetch_submissions("1234567")
        companyfacts_payload = client.fetch_companyfacts("1234567")
    finally:
        client.close()

    assert submissions_payload["sic"] == "3571"
    assert companyfacts_payload["cik"] == 1234567
    assert any(path.endswith("/submissions/CIK0001234567.json") for path in requested_paths)
    assert any(
        path.endswith("/api/xbrl/companyfacts/CIK0001234567.json") for path in requested_paths
    )
