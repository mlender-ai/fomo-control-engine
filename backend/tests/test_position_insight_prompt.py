import json

import pytest

from app.db.models import Direction, Position
from app.positions.engine import build_position_state, make_snapshot
from app.positions.insight import (
    PositionInsightConfigError,
    build_position_insight_input,
    generate_position_insight_text,
    render_ai_position_insight,
    require_openai_api_key,
    validate_llm_numbers,
)
from app.report.prompts.position_insight_prompt import build_position_insight_prompt
from tests.test_position_state_engine import _report


def test_position_insight_input_json_includes_chart_and_volume_context() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100.0,
        quantity=0.5,
        leverage=10,
        current_price=108.0,
        liquidation_price=80.0,
        entry_score=82,
        entry_memo="4H 지지선 반등과 거래량 증가 보고 진입",
    )
    report = _report()
    report.price = 108.0
    state = build_position_state(position, report, [])
    snapshot = make_snapshot(position, state)
    chart_analysis = {
        "price_levels": {
            "support": [{"price": 104.0, "strength": "medium", "label": "주요 지지"}],
            "resistance": [{"price": 112.0, "strength": "strong", "label": "주요 저항"}],
            "invalidation": [{"price": 103.0, "label": "이탈 시 진입 논리 약화"}],
        },
        "volume_profile": {
            "poc_price": 106.0,
            "value_area_high": 110.0,
            "value_area_low": 102.0,
            "method": "ohlcv_estimated",
        },
        "volume_xray": {
            "relative_volume": 1.8,
            "volume_state": "declining_after_push",
        },
    }

    payload = build_position_insight_input(position, snapshot, chart_analysis, [snapshot])

    assert payload["position"]["symbol"] == "BTCUSDT"
    assert payload["chart"]["critical_support"] == 104.0
    assert payload["chart"]["critical_resistance"] == 112.0
    assert payload["chart"]["invalidation_price"] == 103.0
    assert payload["technical"]["relative_volume"] == 1.8
    assert payload["volume_profile"]["current_position_vs_poc"] == "above"
    assert payload["entry_context"]["entry_memo"] == "4H 지지선 반등과 거래량 증가 보고 진입"


def test_position_insight_prompt_and_renderer_follow_required_sections() -> None:
    position = Position(symbol="ETHUSDT", direction=Direction.short, entry_price=100.0, quantity=1, leverage=5, current_price=96.0)
    report = _report()
    report.price = 96.0
    state = build_position_state(position, report, [])
    snapshot = make_snapshot(position, state)
    payload = build_position_insight_input(position, snapshot, {"price_levels": {}, "volume_profile": {}, "volume_xray": {}}, [snapshot])
    prompt = build_position_insight_prompt(json.dumps(payload, ensure_ascii=False))
    text = render_ai_position_insight(payload)

    assert "{{POSITION_STATE_JSON}}" not in prompt
    for section in ["현재 상태:", "수익/리스크:", "차트 구조:", "와이코프/기술적 분석:", "진입 논리:", "주의할 가격:", "제 의견:"]:
        assert section in text
    assert "매수하세요" not in text
    assert "매도하세요" not in text
    assert "단정하지 않습니다" not in text
    assert "투자 조언이 아닙니다" not in text


def test_openai_key_missing_error_is_explicit() -> None:
    with pytest.raises(PositionInsightConfigError, match="OpenAI API key is not configured"):
        require_openai_api_key("")


def test_llm_insight_source_is_recorded_when_output_numbers_are_valid() -> None:
    calls = []
    input_json = {
        "position": {"symbol": "BTCUSDT", "direction": "long", "entry_price": 100.0, "mark_price": 105.0},
        "health": {"health_score": 72, "risk_score": 35, "score_scale": 100, "status_label": "관찰 필요"},
        "chart": {},
        "wyckoff": {},
        "technical": {},
        "volume_profile": {},
        "entry_context": {},
    }
    action_plan = {
        "invalidation": {"price": 99.0, "basis": "사용자 기록", "distance_pct": -5.71, "action": "이탈 시 손절 검토"},
        "take_profit": [{"price": 110.0, "basis": "주요 저항", "distance_pct": 4.76, "action": "부분 익절 검토"}],
        "watch_triggers": [],
    }

    def fake_client(prompt: str, model: str) -> str:
        calls.append((prompt, model))
        return "현재 상태:\nBTCUSDT는 105.0 기준 관찰 필요입니다. 99.0 이탈이면 손절 검토, 110.0 도달이면 부분 익절 검토입니다."

    text, source, reason = generate_position_insight_text(
        input_json=input_json,
        action_plan=action_plan,
        api_key="test-key",
        model="test-model",
        llm_client=fake_client,
    )

    assert calls
    assert source == "llm"
    assert reason is None
    assert "110.0" in text


def test_llm_output_with_unknown_number_falls_back_to_template() -> None:
    input_json = {
        "position": {"symbol": "BTCUSDT", "direction": "long", "entry_price": 100.0, "mark_price": 105.0},
        "health": {"health_score": 72, "risk_score": 35, "score_scale": 100, "status_label": "관찰 필요"},
        "chart": {},
        "wyckoff": {},
        "technical": {},
        "volume_profile": {},
        "entry_context": {},
    }
    action_plan = {"invalidation": {"price": 99.0, "basis": "사용자 기록", "distance_pct": -5.71, "action": "이탈 시 손절 검토"}}

    assert validate_llm_numbers("허위 목표가 999.0", {"position_state": input_json, "action_plan": action_plan}) is False

    _text, source, reason = generate_position_insight_text(
        input_json=input_json,
        action_plan=action_plan,
        api_key="test-key",
        model="test-model",
        llm_client=lambda _prompt, _model: "허위 목표가 999.0",
    )

    assert source == "fallback_template"
    assert reason == "llm_number_validation_failed"
