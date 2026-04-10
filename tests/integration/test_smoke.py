from __future__ import annotations


def test_end_to_end_smoke_path(client, seed_sample_data) -> None:
    signals_response = client.get("/signals")
    assert signals_response.status_code == 200
    signals = signals_response.json()
    assert len(signals) == 1

    signal = signals[0]
    signal_detail_response = client.get(f"/signals/{signal['id']}")
    assert signal_detail_response.status_code == 200
    signal_detail = signal_detail_response.json()
    assert signal_detail["ticker"] == "ACME"
    assert len(signal_detail["qualifying_transactions"]) == 2

    first_transaction = signal_detail["qualifying_transactions"][0]
    filing_response = client.get(f"/filings/{first_transaction['accession_number']}")
    assert filing_response.status_code == 200
    filing = filing_response.json()
    assert filing["issuer"]["ticker"] == "ACME"

    issuer_response = client.get(f"/issuers/{signal['issuer_cik']}")
    assert issuer_response.status_code == 200
    issuer = issuer_response.json()
    assert issuer["latest_signal_id"] == signal["id"]

    issuer_transactions_response = client.get(
        f"/issuers/{signal['issuer_cik']}/transactions",
        params={"candidate_only": True},
    )
    assert issuer_transactions_response.status_code == 200
    issuer_transactions = issuer_transactions_response.json()
    assert len(issuer_transactions) >= len(signal_detail["qualifying_transactions"])
    issuer_accessions = {item["filing_accession_number"] for item in issuer_transactions}
    assert {item["accession_number"] for item in signal_detail["qualifying_transactions"]}.issubset(
        issuer_accessions
    )

    insider_response = client.get(f"/insiders/{first_transaction['insider_id']}")
    assert insider_response.status_code == 200
    insider = insider_response.json()
    assert insider["name"]
    assert insider["recent_transactions"]

    metrics_response = client.get("/metrics")
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()
    assert metrics["counters"]["signals.recompute_runs"] == 1
