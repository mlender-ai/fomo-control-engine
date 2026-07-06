def test_health_check(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "fomo-control-engine"}


def test_system_status(client) -> None:
    response = client.get("/api/system/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "fomo-control-engine"
    assert payload["market_data_provider"] in {"mock", "bitget"}
    assert "BTCUSDT" in payload["default_symbols"]
