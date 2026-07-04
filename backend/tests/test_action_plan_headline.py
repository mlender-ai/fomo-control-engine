from datetime import datetime, timezone
from uuid import uuid4

from app.db.models import Direction, Position, PositionSnapshot
from app.positions.action_plan import build_action_plan

BASE_TIME = datetime(2026, 7, 4, tzinfo=timezone.utc)


def _snapshot(position: Position, mark_price: float) -> PositionSnapshot:
    return PositionSnapshot(
        position_id=uuid4(),
        symbol=position.symbol,
        as_of=BASE_TIME,
        mark_price=mark_price,
        pnl_percent=0.0,
        health_score=60,
        status_label="관찰 필요",
        risk_score=40,
        score_json={},
        analysis_json={},
    )


def _chart_analysis(mark_price: float, support: list[dict] | None = None, resistance: list[dict] | None = None) -> dict:
    return {
        "mark_price": mark_price,
        "price_levels": {
            "support": support or [],
            "resistance": resistance or [],
            "invalidation": [],
        },
        "volume_profile": {},
        "volume_xray": {},
        "harmonic_patterns": [],
        "candles": [],
    }


def test_headline_picks_nearest_trigger_invalidation() -> None:
    position = Position(
        symbol="NBISUSDT",
        direction=Direction.long,
        entry_price=200.0,
        quantity=1.0,
        leverage=10,
        mark_price=225.0,
        planned_stop_price=206.63,
        planned_take_profit_price=290.67,
    )
    plan = build_action_plan(position, _snapshot(position, 225.0), _chart_analysis(225.0))

    # 무효화 -8.2% vs 익절 +29.2% → 무효화가 더 가깝다
    assert plan["headline_action"] == "지금 볼 것: 206.63 지지 유지 여부. 이탈 시 손절 검토."


def test_headline_picks_nearest_trigger_take_profit() -> None:
    position = Position(
        symbol="NBISUSDT",
        direction=Direction.long,
        entry_price=200.0,
        quantity=1.0,
        leverage=10,
        mark_price=225.0,
        planned_stop_price=150.0,
        planned_take_profit_price=241.37,
    )
    plan = build_action_plan(position, _snapshot(position, 225.0), _chart_analysis(225.0))

    # 익절 +7.3% vs 무효화 -33.3% → 익절이 더 가깝다
    assert plan["headline_action"] == "지금 볼 것: 241.37 저항 반응. 도달 시 부분 익절 검토."


def test_headline_short_direction_wording() -> None:
    position = Position(
        symbol="ETHUSDT",
        direction=Direction.short,
        entry_price=100.0,
        quantity=1.0,
        leverage=5,
        mark_price=95.0,
        planned_stop_price=98.0,
    )
    plan = build_action_plan(position, _snapshot(position, 95.0), _chart_analysis(95.0))

    assert plan["headline_action"] == "지금 볼 것: 98 저항 유지 여부. 돌파 시 손절 검토."


def test_headline_matches_action_plan_rows() -> None:
    """headline은 항상 액션 플랜에 실제 존재하는 트리거에서 파생된다."""
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100.0,
        quantity=1.0,
        leverage=3,
        mark_price=110.0,
        planned_stop_price=104.0,
        planned_take_profit_price=118.0,
    )
    plan = build_action_plan(position, _snapshot(position, 110.0), _chart_analysis(110.0))

    prices = [plan["invalidation"]["price"]] + [item["price"] for item in plan["take_profit"]]
    headline = plan["headline_action"]
    assert headline is not None
    assert any(f"{price:g}" in headline for price in prices)


def test_headline_falls_back_to_watch_trigger() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100.0,
        quantity=1.0,
        leverage=3,
        mark_price=110.0,
    )
    analysis = _chart_analysis(110.0)
    analysis["volume_profile"] = {"poc_price": 108.0}
    plan = build_action_plan(position, _snapshot(position, 110.0), analysis)

    headline = plan["headline_action"]
    assert headline is not None
    assert headline.startswith("지금 볼 것: POC 108")


def test_headline_none_when_no_triggers() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100.0,
        quantity=1.0,
        leverage=3,
        mark_price=110.0,
    )
    plan = build_action_plan(position, _snapshot(position, 110.0), _chart_analysis(110.0))

    assert plan["headline_action"] is None
