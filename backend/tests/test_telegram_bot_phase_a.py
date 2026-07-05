from __future__ import annotations

from uuid import UUID

from app.notify.bot.callbacks import encode_callback, parse_callback
from app.notify.bot.formatters import (
    format_action_plan,
    format_position_verdict,
    format_positions_summary,
    format_simulation,
    split_telegram_text,
)
from app.notify.bot.security import ChatGuard
from app.services import runtime as service


def _create_live_position(client, symbol: str = "BTCUSDT") -> dict:
    report = client.post("/api/reports", json={"symbol": symbol, "timeframe": "4h"}).json()
    response = client.post(
        "/api/positions",
        json={
            "symbol": symbol,
            "direction": "long",
            "entry_price": report["price"],
            "quantity": 0.02,
            "leverage": 10,
            "entry_report_id": report["id"],
            "planned_stop_price": report["price"] * 0.96,
            "planned_take_profit_price": report["price"] * 1.08,
        },
    )
    assert response.status_code == 200
    return response.json()


def test_bot_formatters_render_verdict_plan_and_positions(client) -> None:
    position = _create_live_position(client, "BTCUSDT")
    detail = service.live_position_detail(UUID(position["id"]))
    summary = service.list_live_positions()

    verdict = format_position_verdict(detail)
    plan = format_action_plan(detail)
    positions = format_positions_summary(summary)

    assert "BTCUSDT" in verdict
    assert "PnL" in verdict
    assert "지금 볼 것" in verdict
    assert "플랜" in plan
    assert "무효화" in plan
    assert "라이브 포지션" in positions
    assert "매수하세요" not in verdict
    assert "매도하세요" not in verdict


def test_bot_service_matches_live_position_api(client) -> None:
    position = _create_live_position(client, "ETHUSDT")
    api_payload = client.get(f"/api/live/positions/{position['id']}").json()
    service_payload = service.live_position_detail(UUID(position["id"]))

    assert service_payload["state"]["pnl_percent"] == api_payload["state"]["pnl_percent"]
    assert service_payload["state"]["health_score"] == api_payload["state"]["health_score"]
    assert service_payload["action_plan"]["invalidation"]["price"] == api_payload["action_plan"]["invalidation"]["price"]


def test_symbol_partial_matching_and_ambiguity(client) -> None:
    btc = _create_live_position(client, "BTCUSDT")
    eth = _create_live_position(client, "ETHUSDT")

    match = service.match_position_symbol("BTC")
    assert match.position is not None
    assert str(match.position.id) == btc["id"]

    ambiguous = service.match_position_symbol("USDT")
    assert ambiguous.position is None
    assert {str(position.id) for position in ambiguous.candidates} >= {btc["id"], eth["id"]}


def test_callback_parser_and_chat_guard() -> None:
    encoded = encode_callback("detail", "basedusdt")
    parsed = parse_callback(encoded)

    assert parsed is not None
    assert parsed.action == "detail"
    assert parsed.symbol == "BASEDUSDT"
    assert parse_callback("v1:delete:BTCUSDT") is None
    assert parse_callback("bad") is None

    guard = ChatGuard([123, 456])
    assert guard.is_allowed(123) is True
    assert guard.is_allowed(999) is False
    assert guard.is_allowed(None) is False


def test_telegram_text_split_respects_limit() -> None:
    chunks = split_telegram_text("a\n" * 5000, limit=4096)
    assert len(chunks) > 1
    assert all(len(chunk) <= 4096 for chunk in chunks)


def test_simulation_formatter(client) -> None:
    result = service.simulate_entry("BTCUSDT", "long", 10)
    text = format_simulation(result)

    assert "BTCUSDT" in text
    assert "R:R" in text
    assert "체크리스트" in text


def test_worker_status_endpoint_exposes_heartbeat(client) -> None:
    response = client.get("/api/system/worker")
    assert response.status_code == 200
    body = response.json()
    assert "jobs" in body
    assert "position_sync" in body["jobs"]
    assert "telegram_bot" in body["jobs"]
    assert "notifications" in body
