from datetime import timedelta
from uuid import UUID

from app.services import http_handlers as routes


def test_position_insight_api_generates_structured_saved_insight(client) -> None:
    report = client.post("/api/reports", json={"symbol": "BTCUSDT", "timeframe": "4h"}).json()
    position_response = client.post(
        "/api/positions",
        json={
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": report["price"],
            "quantity": 0.02,
            "leverage": 3,
            "entry_report_id": report["id"],
            "entry_memo": "4H 지지선 반등과 거래량 증가 보고 진입",
            "planned_stop_price": report["price"] * 0.96,
        },
    )
    assert position_response.status_code == 200
    position = position_response.json()

    response = client.post(f"/api/live/positions/{position['id']}/insight")
    assert response.status_code == 200
    payload = response.json()
    insight = payload["latest_insight"]

    assert insight["position_id"] == position["id"]
    assert insight["as_of"] == insight["input_json"]["snapshot"]["as_of"]
    assert insight["age_minutes"] == 0
    assert insight["is_stale"] is False
    assert insight["price_drift_pct"] == 0
    assert payload["insight_status"]["has_insight"] is True
    assert payload["insight_status"]["is_stale"] is False
    assert insight["insight_type"] == "position_status"
    assert insight["input_json"]["position"]["symbol"] == "BTCUSDT"
    assert "chart" in insight["input_json"]
    assert "wyckoff" in insight["input_json"]
    assert "technical" in insight["input_json"]
    assert "volume_profile" in insight["input_json"]
    assert "action_plan" in insight["input_json"]
    assert insight["action_plan"]["as_of"] == insight["as_of"]
    assert insight["insight_source"] == "template"
    assert insight["fallback_reason"] == "openai_api_key_missing"
    assert "invalidation" in insight["action_plan"]
    assert "📍 BTCUSDT LONG 포지션 상태" in insight["insight_text"]
    for section in [
        "현재 상태:",
        "수익/리스크:",
        "차트 구조:",
        "와이코프/기술적 분석:",
        "진입 논리:",
        "주의할 가격:",
        "제 의견:",
    ]:
        assert section in insight["insight_text"]
    assert "매수하세요" not in insight["insight_text"]
    assert "매도하세요" not in insight["insight_text"]

    latest_response = client.get(f"/api/live/positions/{position['id']}")
    assert latest_response.status_code == 200
    latest_payload = latest_response.json()
    assert latest_payload["latest_insight"]["id"] == insight["id"]
    assert latest_payload["action_plan"]["as_of"]
    assert "as_of" in latest_payload["latest_snapshot"]


def test_position_insight_status_marks_drifted_insight_stale(client, monkeypatch) -> None:
    monkeypatch.setattr(routes.settings, "insight_auto_refresh_enabled", False)
    report = client.post("/api/reports", json={"symbol": "BTCUSDT", "timeframe": "4h"}).json()
    position = client.post(
        "/api/positions",
        json={
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": report["price"],
            "quantity": 0.02,
            "leverage": 5,
            "entry_report_id": report["id"],
        },
    ).json()
    insight_payload = client.post(f"/api/live/positions/{position['id']}/insight").json()
    assert insight_payload["insight_status"]["is_stale"] is False

    stored_position = routes.repository.get_position(UUID(position["id"]))
    assert stored_position is not None
    stored_position.mark_price = stored_position.entry_price * 0.96
    stored_position.current_price = stored_position.mark_price
    routes.repository.update_position(stored_position)

    latest_payload = client.get(f"/api/live/positions/{position['id']}").json()

    assert latest_payload["insight_status"]["is_stale"] is True
    assert "MARK_PRICE_CHANGED" in latest_payload["insight_status"]["reasons"]
    assert abs(latest_payload["latest_insight"]["price_drift_pct"]) >= 3
    assert latest_payload["latest_insight"]["id"] == insight_payload["latest_insight"]["id"]


def test_position_insight_auto_regenerates_old_insight_on_detail_view(client, monkeypatch) -> None:
    monkeypatch.setattr(routes.settings, "insight_auto_refresh_enabled", True)
    monkeypatch.setattr(routes.settings, "insight_min_regeneration_interval_minutes", 10)
    report = client.post("/api/reports", json={"symbol": "BTCUSDT", "timeframe": "4h"}).json()
    position = client.post(
        "/api/positions",
        json={
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": report["price"],
            "quantity": 0.02,
            "leverage": 5,
            "entry_report_id": report["id"],
        },
    ).json()
    first_payload = client.post(f"/api/live/positions/{position['id']}/insight").json()
    first_id = first_payload["latest_insight"]["id"]
    stored_insight = routes.repository.list_position_insights(UUID(position["id"]), limit=1)[0]
    stored_insight.created_at = stored_insight.created_at - timedelta(minutes=31)

    latest_payload = client.get(f"/api/live/positions/{position['id']}").json()

    assert latest_payload["latest_insight"]["id"] != first_id
    assert latest_payload["latest_insight"]["age_minutes"] == 0
    assert latest_payload["latest_insight"]["is_stale"] is False
    assert latest_payload["latest_insight"]["auto_generated"] is True
    assert latest_payload["latest_insight"]["insight_source"] == "template"
    assert len(routes.repository.list_position_insights(UUID(position["id"]), limit=10)) == 2
