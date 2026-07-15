from __future__ import annotations

import time
from uuid import UUID

import pytest

from app.notify.bot import bot as bot_module
from app.core.config import Settings
from app.notify.bot.callbacks import encode_callback, parse_callback
from app.notify.bot.bot import TelegramBotSupervisor
from app.notify.bot.formatters import (
    detail_keyboard,
    format_action_plan,
    format_flow,
    format_help,
    format_engine_scoreboard,
    format_insight,
    lifecycle_alert_keyboard,
    format_one_liner_strip,
    format_position_verdict,
    format_positions_summary,
    format_paper_event,
    format_scout,
    format_scout_prompt,
    format_scout_quick_answer,
    format_scout_stopped,
    format_scout_tracking,
    format_simulation,
    insight_keyboard,
    main_menu_keyboard,
    positions_keyboard,
    scout_tracking_keyboard,
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
    assert positions_keyboard(summary)[-1][0]["callback_data"] == "v1:all_details:"


def test_position_verdict_explains_current_money_flow() -> None:
    payload = {
        "position": {"symbol": "ETHUSDT", "direction": "short", "leverage": 10},
        "state": {
            "status_label": "리스크 상승",
            "health_score": 25,
            "severity_rank": 4,
            "pnl_percent": -12.3,
            "pnl_source": "exchange",
            "as_of": "2026-07-15T00:00:00+00:00",
            "analysis": {
                "derivatives": {
                    "latest": {"open_interest_change_pct": 2.4},
                    "signals": {
                        "money_flow": {
                            "state": "futures_led",
                            "label": "선물 단독 견인 - 레버리지 상승 경계",
                            "available": True,
                            "provisional": False,
                            "source_label": "Bitget 단일 거래소 프록시",
                            "as_of": "2026-07-15T00:00:00+00:00",
                        }
                    },
                }
            },
        },
        "action_plan": {"headline_action": "1710 지지 반응을 확인합니다."},
        "insight_status": {"has_insight": False},
    }

    text = format_position_verdict(payload)

    assert "자금 흐름" in text
    assert "선물 단독 견인" in text
    assert "현물 CVD 유입은 확인되지 않았습니다" in text
    assert "방향 확정 신호는 아닙니다" in text


def test_one_liner_strip_formatter_is_shared_with_position_verdict() -> None:
    one_liners = {
        "lines": [
            {
                "module": "wyckoff",
                "module_label": "와이코프",
                "stance": "상방",
                "phrase": "매집 우세",
                "confidence_class": "강",
                "evidence_ref": "wyckoff.side=accumulation",
            },
            {
                "module": "liquidity",
                "module_label": "유동성",
                "stance": "상방",
                "phrase": "저점 청소 후 반등 구조",
                "confidence_class": "중",
                "evidence_ref": "liquidity.sweep:1",
            },
            {"module": "volume", "module_label": "볼륨", "stance": "횡보", "phrase": "균형", "confidence_class": "약", "evidence_ref": "volume"},
            {
                "module": "harmonic",
                "module_label": "하모닉",
                "stance": "하방",
                "phrase": "하방 반전 구간 접근",
                "confidence_class": "강",
                "evidence_ref": "harmonic.prz",
            },
            {"module": "levels", "module_label": "레벨", "stance": "판단불가", "phrase": "데이터 부족", "confidence_class": "약", "evidence_ref": "levels"},
            {"module": "derivatives", "module_label": "수급", "stance": "횡보", "phrase": "중립", "confidence_class": "약", "evidence_ref": "derivatives"},
            {"module": "indicators", "module_label": "지표", "stance": "상방", "phrase": "상승 우세", "confidence_class": "중", "evidence_ref": "indicators"},
        ],
        "counts": {"상방": 3, "하방": 1, "횡보": 2, "판단불가": 1},
        "overall_stance": "상방",
        "summary": "종합: 상방 3 · 하방 1 · 횡보 2 · 판단불가 1",
        "policy": "모듈 간 불일치는 그대로 노출합니다.",
    }
    payload = {
        "position": {"symbol": "BTCUSDT", "direction": "long", "leverage": 10, "status": "open", "liquidation_price": 100},
        "state": {"status_label": "관찰 필요", "health_score": 62, "pnl_percent": 2.5, "pnl_source": "exchange", "as_of": "2026-07-08T01:00:00+00:00"},
        "action_plan": {"headline_action": "지금 볼 것: 지지 유지 여부"},
        "chart_analysis": {"one_liners": one_liners},
        "insight_status": {"has_insight": False},
    }

    strip = format_one_liner_strip(one_liners)
    verdict = format_position_verdict(payload)

    assert "와이코프 ● 매집 우세" in strip
    assert "레벨 ○ 데이터 부족" in strip
    assert "종합: 상방 3 · 하방 1 · 중립 2 · 판단불가 1 · 충돌" in strip
    assert strip in verdict
    assert "신뢰도" not in strip
    assert "매수하세요" not in strip
    assert "매도하세요" not in strip


def test_scout_quick_answer_uses_one_liner_strip() -> None:
    one_liners = {
        "lines": [
            {"module": "wyckoff", "module_label": "와이코프", "stance": "하방", "phrase": "분산 우세", "confidence_class": "중", "evidence_ref": "wyckoff"},
            {
                "module": "liquidity",
                "module_label": "유동성",
                "stance": "하방",
                "phrase": "고점 청소 후 하락 경계",
                "confidence_class": "중",
                "evidence_ref": "liq",
            },
            {"module": "volume", "module_label": "볼륨", "stance": "횡보", "phrase": "균형", "confidence_class": "약", "evidence_ref": "vol"},
            {"module": "harmonic", "module_label": "하모닉", "stance": "판단불가", "phrase": "패턴 없음", "confidence_class": "약", "evidence_ref": "harm"},
        ],
        "counts": {"상방": 0, "하방": 2, "횡보": 1, "판단불가": 1},
        "overall_stance": "하방",
        "summary": "하방 2 · 중립 1 · 판단불가 1",
    }
    text = format_scout_quick_answer(
        {
            "symbol": "SOLUSDT",
            "timeframe": "4h",
            "as_of": "2026-07-08T01:00:00+00:00",
            "analysis": {"one_liners": one_liners},
            "summary": {"setup_proximity_pct": 1.2, "long_score": 30, "short_score": 70, "long_evidence_count": 1, "short_evidence_count": 3},
        }
    )

    assert "SOLUSDT" in text
    assert "현재 종합 판정" in text
    assert "하방 근거 우세" in text
    assert "와이코프 ● 분산 우세" in text
    assert "종합: 상방 0 · 하방 2 · 중립 1 · 판단불가 1" in text
    assert "셋업 트리거는 개별 조건 알림" in text
    assert "매수하세요" not in text
    assert "매도하세요" not in text


def test_scout_tracking_formatter_and_callbacks() -> None:
    payload = {
        "symbol": "SOLUSDT",
        "timeframe": "4h",
        "as_of": "2026-07-08T01:00:00+00:00",
        "tracking": {"active": True, "mode": "scout", "message": "스카우트 추적을 시작했습니다."},
        "analysis": {"one_liners": {"lines": [], "counts": {}}},
        "summary": {
            "setup_proximity_pct": 1.2,
            "long_score": 30,
            "short_score": 70,
            "long_evidence_count": 1,
            "short_evidence_count": 3,
        },
    }

    text = format_scout_tracking(payload)
    stopped = format_scout_stopped({"symbol": "SOLUSDT", "tracking": {"message": "스카우트 추적을 중지했습니다."}})
    keyboard = scout_tracking_keyboard("SOLUSDT")

    assert "스카우트 추적 시작" in text
    assert "/unscout SOL" in text
    assert "스카우트 추적을 중지했습니다." in stopped
    assert any(button["callback_data"] == "v1:unscout:SOLUSDT" for row in keyboard for button in row)
    assert any(button["text"] == "갱신" and button["callback_data"] == "v1:scout:SOLUSDT" for row in keyboard for button in row)
    prompt = format_scout_prompt({"items": [{"symbol": "SOLUSDT", "default_timeframe": "4h", "note": "telegram scout tracking"}]})
    assert "보고 싶은 티커를 보내세요" in prompt
    assert "SOLUSDT" in prompt
    assert "상위 5" not in prompt
    assert "/scout — 티커 입력 안내" in format_help()
    assert "/scout SOL" in format_help()
    assert "/unscout SOL" in format_help()
    assert "/engine — 엔진 페이퍼 대결 요약" in format_help()
    assert parse_callback("v1:unscout:SOLUSDT").action == "unscout"


def test_engine_scoreboard_and_paper_event_formatters() -> None:
    scoreboard = format_engine_scoreboard(
        {
            "scoreboard": {
                "engine": {"net_return_pct": 4.2, "win_rate_pct": 60, "profit_factor": 1.7, "mdd_pct": 3.1, "trade_count": 10, "scored_trade_count": 10},
                "user": {"net_return_pct": 2.1, "win_rate_pct": 50, "profit_factor": 1.2, "mdd_pct": 5.4, "trade_count": 8},
                "rolling_4w": {"engine_leading": True},
                "recent_28d": {"engine": {"net_return_pct": 2.8, "win_rate_pct": 55, "scored_trade_count": 9}},
                "fairness_note": "조건 상이 — 방향·타이밍 판단력의 비교",
            },
            "open_trades": [
                {
                    "symbol": "BTCUSDT",
                    "direction": "short",
                    "leverage": 3,
                    "entry_price": 63000,
                    "stop_price": 64500,
                    "take_profit_price": 61500,
                    "take_profit_2_price": 60000,
                    "holding_bars": 4,
                    "current_stance": {"stance": "short_leaning", "transitioning": False},
                    "entry_evidence": {"items": [{"claim": "UTAD 확인"}, {"claim": "고점 Strong 스윕"}]},
                    "exit_monitor": {
                        "mark_price": 62000,
                        "mark_net_pnl_usdt": 4.5,
                        "mark_net_return_pct": 4.5,
                        "invalidation_distance_pct": -4.03,
                        "take_profit_distance_pct": 0.81,
                        "take_profit_2_distance_pct": 3.23,
                    },
                }
            ],
        }
    )
    event = format_paper_event(
        {
            "kind": "opened",
            "trade": {
                "symbol": "SOXLUSDT",
                "direction": "long",
                "entry_price": 194.2,
                "entry_evidence": {"items": [{"claim": "저점 Strong 스윕"}, {"claim": "상방 전환 확정"}]},
            },
        }
    )
    partial = format_paper_event(
        {
            "kind": "partial",
            "reason": "take_profit_1",
            "trade": {
                "symbol": "ETHUSDT",
                "direction": "short",
                "partial_exit_price": 2900,
                "net_return_pct": 2.1,
            },
        }
    )

    assert "🤖 엔진 트레이딩 대결" in scoreboard
    assert "엔진 우세" in scoreboard
    assert "엔진 N=10 | 나 N=8" in scoreboard
    assert "최근 28일" in scoreboard
    assert "BTCUSDT · 숏 3.00x" in scoreboard
    assert "현재 62000.00" in scoreboard
    assert "UTAD 확인 + 고점 Strong 스윕" in scoreboard
    assert "TP2 60000.00" in scoreboard
    assert "현재 판정: 하방 우세 · 포지션 방향 유지" in scoreboard
    assert "🤖 엔진 진입 · SOXLUSDT 롱" in event
    assert "저점 Strong 스윕 + 상방 전환 확정" in event
    assert "실주문이 아닌 엔진 가상 거래" in event
    assert "🤖 엔진 부분 익절 · ETHUSDT 숏" in partial
    assert "사유 1차 익절" in partial


def test_engine_scoreboard_keeps_time_exit_samples_neutral() -> None:
    text = format_engine_scoreboard(
        {
            "scoreboard": {
                "engine": {
                    "net_return_pct": 0.9,
                    "win_rate_pct": None,
                    "profit_factor": None,
                    "mdd_pct": 0,
                    "trade_count": 2,
                    "scored_trade_count": 0,
                    "neutral_count": 2,
                },
                "user": {"trade_count": 0},
                "rolling_4w": {"engine_leading": False},
            },
            "open_trades": [],
        }
    )

    assert "표본 판정 N=0 / 전체 2 · 시간종료 중립 2" in text
    assert "승률 표본 부족" in text


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
    assert {item["symbol"] for item in service.list_open_position_refs()} == {"ETHUSDT"}


@pytest.mark.asyncio
async def test_all_position_details_sends_each_open_position_once(monkeypatch) -> None:
    bot = TelegramBotSupervisor(Settings(database_url="memory://"), NotificationState())
    refs = [{"id": "one", "symbol": "BTCUSDT"}, {"id": "two", "symbol": "ETHUSDT"}]

    def detail(symbol: str) -> dict:
        return {
            "position": {"symbol": symbol, "direction": "long", "leverage": 3},
            "state": {
                "status_label": "관찰 유지",
                "health_score": 70,
                "severity_rank": 1,
                "pnl_percent": 1.2,
                "pnl_source": "exchange",
                "as_of": "2026-07-15T00:00:00+00:00",
            },
            "action_plan": {"headline_action": "구조 유지 여부를 확인합니다."},
            "insight_status": {"has_insight": False},
        }

    details = {"one": detail("BTCUSDT"), "two": detail("ETHUSDT")}

    async def fake_run(func, *args):
        if func is service.list_open_position_refs:
            return refs
        return details[args[0]]

    sent: list[tuple[str, object | None]] = []

    async def fake_reply(_message, text, *, reply_markup=None):
        sent.append((text, reply_markup))

    monkeypatch.setattr(bot, "_run", fake_run)
    monkeypatch.setattr(bot, "_reply", fake_reply)
    monkeypatch.setattr(bot_module, "_markup", lambda rows, _context: rows)

    await bot._send_all_position_details(object(), object())

    assert len(sent) == 2
    assert "BTCUSDT" in sent[0][0]
    assert "ETHUSDT" in sent[1][0]
    assert sent[0][1][0][0]["callback_data"] == "v1:plan:BTCUSDT"


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
    assert parse_callback("v1:all_details:").action == "all_details"
    assert parsed.symbol == "BASEDUSDT"
    assert regen_parsed is not None
    assert regen_parsed.action == "regen_insight"
    assert main_menu_keyboard()[0][0]["callback_data"] == "v1:list:"
    assert parse_callback("v1:delete:BTCUSDT") is None
    assert parse_callback("bad") is None

    assert parse_callback("v1:one_liners:BTCUSDT").action == "one_liners"
    assert parse_callback("v1:chart:BTCUSDT").action == "chart"
    assert parse_callback("v1:review:").action == "review"
    assert parse_callback("v1:unscout:BTCUSDT").action == "unscout"

    guard = ChatGuard([123, 456])
    assert guard.is_allowed(123) is True
    assert guard.is_allowed(999) is False
    assert guard.is_allowed(None) is False


def test_lifecycle_keyboard_templates() -> None:
    opened = lifecycle_alert_keyboard("position_opened", "BTCUSDT")
    closed = lifecycle_alert_keyboard("position_closed", "BTCUSDT")
    verdict = lifecycle_alert_keyboard("verdict_changed", "BTCUSDT")
    pulse = lifecycle_alert_keyboard("periodic_pulse", "")

    assert [button["text"] for button in opened[0]] == ["플랜", "1줄 판정", "차트"]
    assert opened[0][1]["callback_data"] == "v1:one_liners:BTCUSDT"
    assert closed[0][0]["callback_data"] == "v1:review:"
    assert any(button["callback_data"] == "v1:refresh:BTCUSDT" for button in verdict[0])
    assert pulse[0][0]["callback_data"] == "v1:list:"


def test_alert_settings_preserve_lifecycle_rules_and_pulse_interval(client) -> None:
    response = client.patch(
        "/api/alerts/settings",
        json={
            "rules": {
                "position_opened": {"enabled": False},
                "periodic_pulse": {"enabled": True},
            },
            "pulse_interval_hours": 2.5,
        },
    )
    assert response.status_code == 200
    body = response.json()
    rules = {rule["id"]: rule for rule in body["rules"]}

    assert rules["position_opened"]["enabled"] is False
    assert rules["position_closed"]["enabled"] is True
    assert rules["verdict_changed"]["enabled"] is True
    assert rules["stance_flipped"]["enabled"] is True
    assert rules["evidence_insufficient"]["enabled"] is True
    assert rules["periodic_pulse"]["enabled"] is True
    assert body["telegram"]["pulse_interval_hours"] == 2.5

    # Keep the mutable test settings close to default for following tests.
    client.patch("/api/alerts/settings", json={"rules": {"position_opened": {"enabled": True}}, "pulse_interval_hours": 4.0})


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
