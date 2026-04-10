from app.services.browse import BrowseService


def test_browse_endpoints_expose_normalized_records(client, seed_sample_data) -> None:
    filing_response = client.get("/filings/0001234567-24-000001")
    assert filing_response.status_code == 200
    filing = filing_response.json()
    assert filing["issuer"]["ticker"] == "ACME"
    assert len(filing["transactions"]) == 2

    issuer_response = client.get("/issuers/ACME")
    assert issuer_response.status_code == 200
    issuer = issuer_response.json()
    assert issuer["filing_count"] == 3
    assert issuer["transaction_count"] == 4
    assert issuer["latest_signal_score"] == "79.38"
    assert issuer["latest_signal_health_status"] == "unknown"

    transactions_response = client.get(
        "/issuers/0001234567/transactions?candidate_only=true&include_derivative=false&include_routine=false"
    )
    assert transactions_response.status_code == 200
    transactions = transactions_response.json()
    assert len(transactions) == 3
    assert all(transaction["is_candidate_buy"] is True for transaction in transactions)

    insider_id = transactions[0]["insider"]["id"]
    insider_response = client.get(f"/insiders/{insider_id}")
    assert insider_response.status_code == 200
    insider = insider_response.json()
    assert insider["name"] in {"Jane Example", "Mark Example"}
    assert insider["transaction_count"] >= 1
    assert len(insider["recent_transactions"]) >= 1


def test_browse_service_returns_none_for_missing_issuer(db_session) -> None:
    assert BrowseService(db_session).get_issuer("MISSING") is None
