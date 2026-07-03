def test_report_creation_persists_and_latest_report_is_returned(client) -> None:
    create_response = client.post("/api/reports", json={"symbol": "BTCUSDT", "timeframe": "4h"})

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["symbol"] == "BTCUSDT"
    assert 0 <= created["entry_score"] <= 100
    assert "scores" in created["raw_json"]
    assert "현재 시장은" in created["report"]

    latest_response = client.get("/api/reports/BTCUSDT")
    assert latest_response.status_code == 200
    assert latest_response.json()["id"] == created["id"]


def test_market_summary_uses_configured_watchlist(client) -> None:
    response = client.get("/api/market/summary")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["reports"]) >= 5
    assert payload["market_data_provider"] in {"mock", "bitget"}

