def test_live_position_analysis_insight_memo_and_exit_flow(client) -> None:
    report_response = client.post("/api/reports", json={"symbol": "BTCUSDT", "timeframe": "4h"})
    report = report_response.json()

    position_response = client.post(
        "/api/positions",
        json={
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": report["price"],
            "quantity": 0.02,
            "leverage": 2,
            "entry_report_id": report["id"],
            "memo": "initial memo",
            "entry_memo": "상승 구조 지지 확인 후 진입",
            "planned_stop_price": report["price"] * 0.96,
            "planned_take_profit_price": report["price"] * 1.08,
            "thesis_text": "Higher low가 유지되어야 한다.",
        },
    )
    assert position_response.status_code == 200
    position = position_response.json()

    live_response = client.get("/api/live/positions")
    assert live_response.status_code == 200
    live_payload = live_response.json()
    assert live_payload["open_count"] == 1
    assert live_payload["positions"][0]["state"]["position"]["symbol"] == "BTCUSDT"

    analyze_response = client.post(f"/api/live/positions/{position['id']}/analyze")
    assert analyze_response.status_code == 200
    analyzed = analyze_response.json()
    assert analyzed["latest_snapshot"]["position_id"] == position["id"]
    assert 0 <= analyzed["state"]["health_score"] <= 100

    insight_response = client.post(f"/api/live/positions/{position['id']}/insight")
    assert insight_response.status_code == 200
    insight_payload = insight_response.json()
    assert insight_payload["latest_insight"]["position_id"] == position["id"]
    assert insight_payload["latest_insight"]["insight_source"] == "template"
    assert insight_payload["latest_insight"]["action_plan"]["invalidation"]["price"] == report["price"] * 0.96
    assert "단정하지 않습니다" not in insight_payload["latest_insight"]["insight_text"]

    memo_response = client.patch(
        f"/api/live/positions/{position['id']}/memo",
        json={
            "memo": "updated memo",
            "entry_memo": "업데이트된 진입 논리",
            "planned_stop_price": report["price"] * 0.95,
            "planned_take_profit_price": report["price"] * 1.1,
            "thesis_text": "지지 이탈 시 논리 약화",
        },
    )
    assert memo_response.status_code == 200
    assert memo_response.json()["memo"] == "updated memo"

    events_response = client.get(f"/api/live/positions/{position['id']}/events")
    assert events_response.status_code == 200
    event_types = {event["event_type"] for event in events_response.json()["events"]}
    assert {"ai_insight", "memo_updated"}.issubset(event_types)

    exit_response = client.post(
        f"/api/live/positions/{position['id']}/record-exit",
        json={
            "exit_price": report["price"] * 1.03,
            "exit_reason": "테스트 수동 이탈 기록",
            "memo": "거래소 주문이 아닌 내부 기록",
        },
    )
    assert exit_response.status_code == 200
    trade = exit_response.json()
    assert trade["memo"] == "거래소 주문이 아닌 내부 기록"
    assert trade["pnl_percent"] > 0

    trade_response = client.get(f"/api/trades/{trade['id']}")
    assert trade_response.status_code == 200
    assert trade_response.json()["id"] == trade["id"]

    timeline_response = client.get(f"/api/trades/{trade['id']}/timeline")
    assert timeline_response.status_code == 200
    timeline = timeline_response.json()
    assert timeline["trade"]["id"] == trade["id"]
    assert len(timeline["snapshots"]) >= 1
    assert any(event["event_type"] == "exit_recorded" for event in timeline["events"])


def test_live_position_chart_analysis_contract(client) -> None:
    report = client.post("/api/reports", json={"symbol": "BTCUSDT", "timeframe": "4h"}).json()
    position_response = client.post(
        "/api/positions",
        json={
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": report["price"],
            "quantity": 0.02,
            "leverage": 3,
            "planned_stop_price": report["price"] * 0.96,
        },
    )
    assert position_response.status_code == 200
    position = position_response.json()

    response = client.get(f"/api/live/positions/{position['id']}/chart-analysis")
    assert response.status_code == 200
    payload = response.json()
    assert payload["position_id"] == position["id"]
    assert payload["timeframe"] == "4h"
    assert len(payload["candles"]) >= 100
    assert payload["candles"] == sorted(payload["candles"], key=lambda candle: candle["time"])
    assert payload["price_levels"]["entry"] == position["entry_price"]
    assert payload["price_levels"]["mark"] > 0
    assert isinstance(payload["price_levels"]["support"], list)
    assert isinstance(payload["price_levels"]["resistance"], list)
    assert payload["volume_profile"]["method"] == "estimated_ohlcv_proxy"
    assert len(payload["volume_profile"]["bins"]) > 0
    assert payload["volume_profile"]["poc_price"] > 0
    assert payload["volume_xray"]["relative_volume"] > 0
    assert isinstance(payload["volume_xray"]["notes"], list)
    assert isinstance(payload["wyckoff_markers"], list)
