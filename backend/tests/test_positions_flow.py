def test_position_monitor_exit_review_flow(client) -> None:
    report_response = client.post("/api/reports", json={"symbol": "BTCUSDT", "timeframe": "4h"})
    report = report_response.json()

    position_response = client.post(
        "/api/positions",
        json={
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": report["price"],
            "quantity": 0.01,
            "leverage": 1,
            "entry_report_id": report["id"],
            "memo": "test entry",
        },
    )
    assert position_response.status_code == 200
    position = position_response.json()
    assert position["status"] == "open"
    assert position["entry_score"] == report["entry_score"]

    monitor_response = client.post(f"/api/positions/{position['id']}/monitor")
    assert monitor_response.status_code == 200
    monitor = monitor_response.json()
    assert monitor["position_id"] == position["id"]
    assert "포지션 모니터링" in monitor["report_text"]

    exit_response = client.post(
        f"/api/positions/{position['id']}/exit",
        json={
            "exit_price": report["price"] * 1.02,
            "exit_reason": "test exit",
            "memo": "closed by test",
        },
    )
    assert exit_response.status_code == 200
    trade = exit_response.json()
    assert trade["symbol"] == "BTCUSDT"
    assert trade["pnl_percent"] > 0
    assert "Trade Review" in trade["review_text"]

    trades_response = client.get("/api/trades")
    assert trades_response.status_code == 200
    assert trades_response.json()[0]["id"] == trade["id"]

