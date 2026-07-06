from __future__ import annotations

import time
from uuid import UUID

import pytest

from app.core.config import Settings
from app.notify.bot.callbacks import encode_callback, parse_callback
from app.notify.bot.bot import TelegramBotSupervisor
from app.notify.bot.formatters import (
    detail_keyboard,
    format_action_plan,
    format_flow,
    format_insight,
    format_position_verdict,
    format_positions_summary,
    format_scout,
    format_simulation,
    insight_keyboard,
    main_menu_keyboard,
    split_telegram_text,
)
from app.notify.bot.security import ChatGuard
from app.notify.state import NotificationState
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
    scout = format_scout(
        {
            "rows": [
                {
                    "symbol": "BASEDUSDT",
                    "setup_proximity_pct": 1.2,
                    "long_score": 72,
                    "short_score": 31,
                    "volume_state": "거래량 둔화",
                }
            ]
        }
    )

    assert "BTCUSDT" in verdict
    assert "PnL" in verdict
    assert "지금 볼 것" in verdict
    assert "플랜" in plan
    assert "무효화" in plan
    assert "라이브 포지션" in positions
    assert "스카우트" in scout
    assert "BASEDUSDT" in scout
    assert "매수하세요" not in verdict
    assert "매도하세요" not in verdict


def test_insight_formatter_uses_regenerate_button_without_pre() -> None:
    payload = {
        "position": {"symbol": "BASEDUSDT"},
        "latest_insight": {"insight_text": "기준 가격 0.0910에서 작성된 해설입니다."},
        "insight_status": {
            "has_insight": True,
            "is_stale": True,
            "message": "가격 차이가 큽니다.",
        },
    }
    text = format_insight(payload)
    keyboard = insight_keyboard("BASEDUSDT", regenerate=True)

    assert "과거 판단" in text
    assert "<pre>" not in text
    assert keyboard[0][0]["callback_data"] == "v1:regen_insight:BASEDUSDT"


def test_flow_formatter_and_callback_button() -> None:
    payload = {
        "symbol": "BASEDUSDT",
        "latest": {
            "symbol": "BASEDUSDT",
            "provider": "bitget",
            "source_status": "ok",
            "as_of": "2026-07-06T01:00:00+00:00",
            "funding_rate": 0.00082,
            "next_funding_time": "2026-07-06T08:00:00+00:00",
            "open_interest": 1234567,
            "open_interest_change_pct": 2.5,
            "long_account_ratio": 0.54,
            "short_account_ratio": 0.46,
            "long_short_ratio": 1.17,
            "notes": [],
        },
        "summary": {"headline": "펀딩 중립 · OI 증가 · 롱 비중 우세"},
        "coinglass": {
            "source_status": "locked",
            "notes": ["Coinglass V4 key is not configured."],
        },
    }

    text = format_flow(payload)
    keyboard = detail_keyboard("BASEDUSDT")

    assert "BASEDUSDT 수급" in text
    assert "펀딩" in text
    assert "OI" in text
    assert "롱/숏" in text
    assert any(button["callback_data"] == "v1:flow:BASEDUSDT" for row in keyboard for button in row)
    parsed = parse_callback("v1:flow:BASEDUSDT")
    assert parsed is not None
    assert parsed.action == "flow"


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
    assert {str(position.id) for position in ambiguous.candidates} >= {
        btc["id"],
        eth["id"],
    }


def test_callback_parser_and_chat_guard() -> None:
    encoded = encode_callback("detail", "basedusdt")
    regen = encode_callback("regen_insight", "basedusdt")
    parsed = parse_callback(encoded)
    regen_parsed = parse_callback(regen)

    assert parsed is not None
    assert parsed.action == "detail"
    assert parsed.symbol == "BASEDUSDT"
    assert regen_parsed is not None
    assert regen_parsed.action == "regen_insight"
    assert main_menu_keyboard()[0][0]["callback_data"] == "v1:list:"
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
    assert "sync_positions" in body["jobs"]
    assert "telegram_bot" in body["jobs"]
    assert "notifications" in body


@pytest.mark.asyncio
async def test_bot_command_timeout_returns_runtime_error() -> None:
    bot = TelegramBotSupervisor(
        Settings(database_url="memory://", telegram_command_timeout_seconds=0),
        NotificationState(),
    )

    with pytest.raises(RuntimeError, match="계산 중"):
        await bot._run(time.sleep, 0.05)
