from decimal import Decimal

from sector4_sec_ingestion.parser import parse_ownership_xml
from sector4_sec_ingestion.types import FilingMetadata


def test_parse_open_market_purchase_fixture(fixture_dir, metadata: FilingMetadata) -> None:
    parsed = parse_ownership_xml(
        (fixture_dir / "form4_open_market_purchase.xml").read_text(encoding="utf-8"),
        metadata,
    )

    assert parsed.issuer.ticker == "ACME"
    assert len(parsed.insiders) == 1
    assert len(parsed.transactions) == 2

    purchase = next(txn for txn in parsed.transactions if not txn.is_derivative)
    assert purchase.transaction_code == "P"
    assert purchase.acquired_disposed == "A"
    assert purchase.is_candidate_buy is True
    assert purchase.is_likely_routine is False
    assert purchase.value_usd == Decimal("123400.00")
    assert "open-market purchase" in purchase.footnote_text.lower()


def test_parse_amendment_fixture_marks_document_as_amendment(fixture_dir) -> None:
    metadata = FilingMetadata(
        accession_number="0001234567-24-000002",
        form_type="4/A",
        filed_at=__import__("datetime").datetime(
            2024, 2, 20, 20, 15, tzinfo=__import__("datetime").timezone.utc
        ),
        source_url="https://www.sec.gov/Archives/edgar/data/1234567/000123456724000002.txt",
        xml_url="https://www.sec.gov/Archives/edgar/data/1234567/000123456724000002/ownership.xml",
    )
    parsed = parse_ownership_xml(
        (fixture_dir / "form4_amendment_purchase.xml").read_text(encoding="utf-8"),
        metadata,
    )

    assert parsed.document_type == "4/A"
    assert parsed.is_amendment is True
    assert parsed.transactions[0].shares == Decimal("12000")


def test_indirect_purchase_is_marked_routine(fixture_dir) -> None:
    metadata = FilingMetadata(
        accession_number="0007654321-24-000003",
        form_type="4",
        filed_at=__import__("datetime").datetime(
            2024, 3, 1, 16, 0, tzinfo=__import__("datetime").timezone.utc
        ),
        source_url="https://www.sec.gov/Archives/edgar/data/7654321/000765432124000003.txt",
        xml_url="https://www.sec.gov/Archives/edgar/data/7654321/000765432124000003/ownership.xml",
    )
    parsed = parse_ownership_xml(
        (fixture_dir / "form4_indirect_plan.xml").read_text(encoding="utf-8"),
        metadata,
    )

    transaction = parsed.transactions[0]
    assert transaction.transaction_code == "P"
    assert transaction.is_candidate_buy is False
    assert transaction.is_likely_routine is True
    assert transaction.routine_reason == "trading_plan_language"


def test_indirect_buy_counts_when_same_filing_has_direct_open_market_buy(fixture_dir) -> None:
    metadata = FilingMetadata(
        accession_number="0001234567-24-000010",
        form_type="4",
        filed_at=__import__("datetime").datetime(
            2024, 2, 19, 18, 45, tzinfo=__import__("datetime").timezone.utc
        ),
        source_url="https://www.sec.gov/Archives/edgar/data/1234567/000123456724000010.txt",
        xml_url="https://www.sec.gov/Archives/edgar/data/1234567/000123456724000010/ownership.xml",
    )
    parsed = parse_ownership_xml(
        (fixture_dir / "form4_mixed_direct_indirect_purchase.xml").read_text(encoding="utf-8"),
        metadata,
    )

    direct_transaction = next(txn for txn in parsed.transactions if txn.ownership_type == "D")
    indirect_transaction = next(txn for txn in parsed.transactions if txn.ownership_type == "I")

    assert direct_transaction.is_candidate_buy is True
    assert direct_transaction.is_likely_routine is False
    assert indirect_transaction.is_candidate_buy is True
    assert indirect_transaction.is_likely_routine is False
    assert indirect_transaction.routine_reason is None
