from datetime import date, datetime
from decimal import Decimal

from sector4_core.config import Settings
from sector4_scoring import CandidateBuy, compute_signal_windows


def _candidate(
    *,
    transaction_id: int,
    filing_id: int,
    accession_number: str,
    filed_at: datetime,
    insider_id: int,
    insider_name: str,
    transaction_date: date,
    shares: str,
    price_per_share: str,
    value_usd: str,
    shares_after: str,
    insider_role: str = "Director",
    annual_compensation_usd: str | None = None,
    compensation_purchase_ratio: str | None = None,
    role_weight_multiplier: str = "1.10",
    is_amendment: bool = False,
) -> CandidateBuy:
    return CandidateBuy(
        transaction_id=transaction_id,
        filing_id=filing_id,
        accession_number=accession_number,
        source_url=f"source-{transaction_id}",
        xml_url=f"xml-{transaction_id}",
        filed_at=filed_at,
        is_amendment=is_amendment,
        issuer_id=1,
        issuer_cik="0001234567",
        issuer_name="Acme Robotics, Inc.",
        issuer_ticker="ACME",
        insider_id=insider_id,
        insider_name=insider_name,
        insider_role=insider_role,
        transaction_date=transaction_date,
        security_title="Common Stock",
        shares=Decimal(shares),
        price_per_share=Decimal(price_per_share),
        value_usd=Decimal(value_usd),
        shares_after=Decimal(shares_after),
        ownership_type="D",
        transaction_code="P",
        annual_compensation_usd=(
            Decimal(annual_compensation_usd) if annual_compensation_usd is not None else None
        ),
        compensation_purchase_ratio=(
            Decimal(compensation_purchase_ratio)
            if compensation_purchase_ratio is not None
            else None
        ),
        role_weight_multiplier=Decimal(role_weight_multiplier),
    )


def test_compute_signal_windows_is_reproducible_for_cluster() -> None:
    settings = Settings(default_cluster_window_days=30, default_min_unique_buyers=2)
    candidates = [
        _candidate(
            transaction_id=1,
            filing_id=11,
            accession_number="0001234567-24-000001",
            filed_at=datetime(2024, 2, 15, 14, 30),
            insider_id=101,
            insider_name="Jane Example",
            transaction_date=date(2024, 2, 14),
            shares="10000",
            price_per_share="12.34",
            value_usd="123400.00",
            shares_after="150000",
        ),
        _candidate(
            transaction_id=2,
            filing_id=12,
            accession_number="0001234567-24-000002",
            filed_at=datetime(2024, 2, 20, 20, 15),
            insider_id=101,
            insider_name="Jane Example",
            transaction_date=date(2024, 2, 14),
            shares="12000",
            price_per_share="11.90",
            value_usd="142800.00",
            shares_after="152000",
            is_amendment=True,
        ),
        _candidate(
            transaction_id=3,
            filing_id=13,
            accession_number="0001234567-24-000004",
            filed_at=datetime(2024, 2, 19, 18, 45),
            insider_id=202,
            insider_name="Mark Example",
            transaction_date=date(2024, 2, 18),
            shares="9000",
            price_per_share="13.00",
            value_usd="117000.00",
            shares_after="54000",
        ),
    ]

    signals = compute_signal_windows(candidates, settings)

    assert len(signals) == 1
    assert signals[0].unique_buyers == 2
    assert signals[0].total_purchase_usd == Decimal("259800.00")
    assert signals[0].average_purchase_usd == Decimal("129900.00")
    assert signals[0].signal_score == Decimal("79.38")
    assert signals[0].rationale_json["first_time_buyer_count"] == 2
    assert signals[0].rationale_json["market_cap_price_hint"] == "12.37"
    assert (
        signals[0].rationale_json["market_cap_price_hint_source"]
        == "weighted_average_cluster_purchase_price"
    )
    assert signals[0].rationale_json["routine_history_filter_applied"] is True


def test_compute_signal_windows_excludes_repeat_calendar_quarter_buyers() -> None:
    settings = Settings(default_cluster_window_days=30, default_min_unique_buyers=2)
    candidates = [
        _candidate(
            transaction_id=1,
            filing_id=11,
            accession_number="0001234567-23-000001",
            filed_at=datetime(2023, 2, 12, 14, 30),
            insider_id=101,
            insider_name="Jane Example",
            transaction_date=date(2023, 2, 10),
            shares="5000",
            price_per_share="10.00",
            value_usd="50000.00",
            shares_after="30000",
        ),
        _candidate(
            transaction_id=2,
            filing_id=12,
            accession_number="0001234567-24-000002",
            filed_at=datetime(2024, 2, 15, 20, 15),
            insider_id=101,
            insider_name="Jane Example",
            transaction_date=date(2024, 2, 14),
            shares="12000",
            price_per_share="11.90",
            value_usd="142800.00",
            shares_after="152000",
        ),
        _candidate(
            transaction_id=3,
            filing_id=13,
            accession_number="0001234567-24-000004",
            filed_at=datetime(2024, 2, 19, 18, 45),
            insider_id=202,
            insider_name="Mark Example",
            transaction_date=date(2024, 2, 18),
            shares="9000",
            price_per_share="13.00",
            value_usd="117000.00",
            shares_after="54000",
        ),
    ]

    signals = compute_signal_windows(candidates, settings)

    assert signals == []


def test_compute_signal_windows_rewards_compensation_commitment_and_executive_roles() -> None:
    settings = Settings(default_cluster_window_days=30, default_min_unique_buyers=2)
    candidates = [
        _candidate(
            transaction_id=1,
            filing_id=11,
            accession_number="0001234567-24-000010",
            filed_at=datetime(2024, 2, 15, 14, 30),
            insider_id=101,
            insider_name="Jane Example",
            transaction_date=date(2024, 2, 14),
            shares="10000",
            price_per_share="12.00",
            value_usd="120000.00",
            shares_after="20000",
            insider_role="Officer, Chief Executive Officer",
            annual_compensation_usd="1600000.00",
            compensation_purchase_ratio="0.0750",
            role_weight_multiplier="1.35",
        ),
        _candidate(
            transaction_id=2,
            filing_id=12,
            accession_number="0001234567-24-000011",
            filed_at=datetime(2024, 2, 18, 14, 30),
            insider_id=202,
            insider_name="Mark Example",
            transaction_date=date(2024, 2, 16),
            shares="9000",
            price_per_share="13.00",
            value_usd="117000.00",
            shares_after="45000",
            insider_role="Officer, Chief Financial Officer",
            annual_compensation_usd="950000.00",
            compensation_purchase_ratio="0.1232",
            role_weight_multiplier="1.30",
        ),
    ]

    signals = compute_signal_windows(candidates, settings)

    assert len(signals) == 1
    conviction = signals[0].rationale_json["component_breakdown"]["conviction"]["details"]
    assert conviction["compensation_coverage_count"] == 2
    assert conviction["compensation_purchase_ratio_points"] > 0
    assert conviction["executive_role_points"] > 0
    assert signals[0].rationale_json["executive_buyer_count"] == 2
    assert signals[0].rationale_json["compensation_coverage_count"] == 2
