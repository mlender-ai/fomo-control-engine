import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db.models import Direction, MarketCandle, MarketSnapshot, Position
from app.positions.chart_analysis import PositionContext, build_chart_analysis

BASE_TIME = datetime(2026, 6, 1, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "chart_analysis_position_regression.json"


def _candles() -> list[MarketCandle]:
    candles = []
    for i in range(150):
        open_ = 100 + math.sin(i / 9.0) * 6 + i * 0.05
        close = 100 + math.sin((i + 1) / 9.0) * 6 + (i + 1) * 0.05
        candles.append(
            MarketCandle(
                timestamp=BASE_TIME + timedelta(hours=4 * i),
                open=open_,
                high=max(open_, close) + 1.2,
                low=min(open_, close) - 1.2,
                close=close,
                volume=1000 + (i % 7) * 140,
            )
        )
    return candles


def _snapshot() -> MarketSnapshot:
    candles = _candles()
    return MarketSnapshot(
        symbol="TESTUSDT",
        timeframe="4h",
        price=candles[-1].close,
        change_24h=1.5,
        funding_rate=0.0001,
        open_interest_change=0.3,
        candles=candles,
        provider="mock",
    )


def test_position_mode_output_matches_pre_refactor_fixture() -> None:
    """리팩토링 전(포지션 인자 시그니처) 출력과 바이트 동일해야 한다."""
    position = Position(
        symbol="TESTUSDT",
        direction=Direction.long,
        entry_price=104.0,
        quantity=1.0,
        leverage=5,
        mark_price=_snapshot().candles[-1].close,
        planned_stop_price=98.0,
        planned_take_profit_price=118.0,
    )
    payload = build_chart_analysis(_snapshot(), PositionContext.from_position(position), None)
    payload["position_id"] = "FIXED"

    expected = json.loads(FIXTURE.read_text())
    actual = json.loads(json.dumps(_without_phase_l_fields(payload), sort_keys=True, default=str))
    _assert_json_with_float_approx(actual, expected)

    assert payload["liquidity"]["method"] == "deterministic_ohlcv_liquidity_v2"
    assert "pools" in payload["liquidity"]


def test_scout_mode_omits_position_fields_and_adds_scenarios() -> None:
    payload = build_chart_analysis(_snapshot(), None, None)

    assert payload["position_id"] is None
    assert payload["direction"] is None
    assert payload["entry_price"] is None
    assert payload["liquidation_price"] is None
    assert payload["price_levels"]["entry"] is None
    assert payload["price_levels"]["liquidation"] is None
    assert payload["price_levels"]["invalidation"] == []

    scenarios = payload["scenarios"]
    assert set(scenarios.keys()) == {"long", "short"}
    for direction in ("long", "short"):
        scenario = scenarios[direction]
        assert "invalidation" in scenario
        assert isinstance(scenario["take_profit"], list)
        assert isinstance(scenario["watch_triggers"], list)
    long_invalidation = scenarios["long"]["invalidation"]
    short_invalidation = scenarios["short"]["invalidation"]
    if long_invalidation and short_invalidation:
        # 롱 무효화는 지지 아래, 숏 무효화는 저항 위 — 서로 다른 레벨이어야 한다
        assert long_invalidation["price"] != short_invalidation["price"]


def test_position_mode_has_no_scenarios_key() -> None:
    position = Position(
        symbol="TESTUSDT",
        direction=Direction.long,
        entry_price=104.0,
        quantity=1.0,
        leverage=5,
        mark_price=110.0,
    )
    payload = build_chart_analysis(_snapshot(), PositionContext.from_position(position), None)
    assert "scenarios" not in payload


def _without_phase_l_fields(payload: dict) -> dict:
    cleaned = json.loads(json.dumps(payload, sort_keys=True, default=str))
    cleaned.pop("asset_class", None)
    cleaned.pop("session", None)
    data_quality = cleaned.get("data_quality")
    if isinstance(data_quality, dict):
        data_quality.pop("analysis_candles", None)
        data_quality.pop("session_excluded_candles", None)
        data_quality.pop("unconfirmed_candles_excluded", None)
    for candle in cleaned.get("candles", []) if isinstance(cleaned.get("candles"), list) else []:
        if isinstance(candle, dict):
            candle.pop("session", None)
            candle.pop("is_regular_session", None)
    cleaned.pop("liquidity", None)
    # WO-43 추가 필드 (1줄 판정·혼합 신호 노트) — 리팩토링 전 픽스처엔 없는 의도된 추가.
    cleaned.pop("one_liners", None)
    wyckoff = cleaned.get("wyckoff")
    if isinstance(wyckoff, dict):
        wyckoff.pop("liquidity_crosscheck", None)
        wyckoff.pop("conflict_note", None)
        for event in wyckoff.get("events", []) if isinstance(wyckoff.get("events"), list) else []:
            if isinstance(event, dict):
                event.pop("liquidity_crosscheck", None)
                event.pop("display_label", None)
                event.pop("context_note", None)
                components = event.get("components")
                if isinstance(components, dict):
                    components.pop("liquidity_confirmation", None)
    for key in ("wyckoff_markers", "wyckoff_markers_low_confidence"):
        for event in cleaned.get(key, []) if isinstance(cleaned.get(key), list) else []:
            if isinstance(event, dict):
                event.pop("liquidity_crosscheck", None)
                event.pop("display_label", None)
                components = event.get("components")
                if isinstance(components, dict):
                    components.pop("liquidity_confirmation", None)
    phase = cleaned.get("wyckoff_phase")
    if isinstance(phase, dict):
        for event in phase.get("phase_evidence", []) if isinstance(phase.get("phase_evidence"), list) else []:
            if isinstance(event, dict):
                event.pop("liquidity_crosscheck", None)
                event.pop("display_label", None)
                components = event.get("components")
                if isinstance(components, dict):
                    components.pop("liquidity_confirmation", None)
    return cleaned


def _assert_json_with_float_approx(actual, expected) -> None:
    """Fixture values remain fixed; only floating point representation is tolerant."""
    if isinstance(expected, float):
        assert isinstance(actual, (int, float))
        assert actual == pytest.approx(expected, rel=1e-9)
        return
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        assert actual.keys() == expected.keys()
        for key, value in expected.items():
            _assert_json_with_float_approx(actual[key], value)
        return
    if isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(actual) == len(expected)
        for actual_item, expected_item in zip(actual, expected, strict=True):
            _assert_json_with_float_approx(actual_item, expected_item)
        return
    assert actual == expected
