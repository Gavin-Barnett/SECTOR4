from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sector4_sec_ingestion.proxy_parser import parse_proxy_compensation_html
from sector4_sec_ingestion.types import ProxyFilingMetadata


def test_parse_proxy_compensation_html_extracts_summary_rows() -> None:
    html_text = Path("tests/fixtures/sec/def14a_acme_proxy_statement.html").read_text(
        encoding="utf-8"
    )
    metadata = ProxyFilingMetadata(
        accession_number="0001234567-24-000050",
        form_type="DEF 14A",
        filed_at=datetime(2024, 2, 1, tzinfo=UTC),
        source_url="https://www.sec.gov/Archives/edgar/data/1234567/000123456724000050.txt",
        document_url=(
            "https://www.sec.gov/Archives/edgar/data/1234567/"
            "000123456724000050/proxy_statement.html"
        ),
        issuer_cik="0001234567",
        issuer_name="Acme Robotics, Inc.",
        fiscal_year=2023,
    )

    parsed = parse_proxy_compensation_html(html_text, metadata)

    assert parsed.metadata.accession_number == metadata.accession_number
    assert len(parsed.records) == 2
    assert parsed.records[0].insider_name == "Jane Example"
    assert parsed.records[0].title == "Chief Executive Officer"
    assert parsed.records[0].fiscal_year == 2023
    assert parsed.records[0].salary_usd == Decimal("450000.00")
    assert parsed.records[0].total_compensation_usd == Decimal("1600000.00")
    assert parsed.records[1].insider_name == "Mark Example"
    assert parsed.records[1].title == "Chief Financial Officer"
    assert parsed.records[1].total_compensation_usd == Decimal("950000.00")
