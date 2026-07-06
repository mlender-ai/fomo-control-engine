from datetime import datetime, timedelta, timezone

from app.db.models import (
    Direction,
    MarketCandle,
    MarketSnapshot,
    Position,
    Report,
    ScoreBreakdown,
)
from app.positions.chart_analysis import PositionContext, build_chart_analysis
from app.positions.engine import build_position_state
from app.structure.levels.engine import detect_structure_levels


BASE_TIME = datetime(2026, 7, 4, tzinfo=timezone.utc)


def test_structure_levels_rank_repeated_touch_over_nearby_noise() -> None:
    candles = [_flat_candle(index, close=108.0, low=105.8, high=110.0, volume=520.0) for index in range(80)]
    for index, low in [(12, 100.0), (31, 100.2), (56, 99.9)]:
        candles[index] = _flat_candle(index, close=106.4, low=low, high=109.0, volume=2400.0)
    candles[75] = _flat_candle(75, close=107.2, low=104.7, high=109.0, volume=500.0)

    levels = detect_structure_levels(candles, mark_price=108.0)
    support = levels["support"]

    assert support
    assert 99.5 <= support[0].price <= 100.5
    assert support[0].touches >= 3
    assert support[0].score >= 55
    nearby_noise = [level for level in support if 104.0 <= level.price <= 105.5]
    assert not nearby_noise or support[0].score > nearby_noise[0].score


def test_short_position_invalidation_uses_structural_resistance() -> None:
    candles = [_flat_candle(index, close=100.0, low=96.0, high=104.0, volume=650.0) for index in range(120)]
    for index, high in [(45, 110.0), (72, 110.2), (95, 109.9)]:
        candles[index] = _flat_candle(index, close=101.0, low=97.8, high=high, volume=2600.0)
    snapshot = MarketSnapshot(
        symbol="BTCUSDT",
        timeframe="4h",
        price=100.0,
        change_24h=0.0,
        funding_rate=0.0,
        open_interest_change=0.0,
        candles=candles,
    )
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.short,
        entry_price=103.0,
        quantity=1.0,
        leverage=5,
        mark_price=100.0,
    )

    analysis = build_chart_analysis(snapshot, PositionContext.from_position(position))
    invalidation = analysis["price_levels"]["invalidation"][0]

    assert invalidation["source"] == "structure_level"
    assert invalidation["kind"] == "resistance"
    assert 109.0 <= invalidation["price"] <= 111.0
    assert invalidation["score"] >= 40


def test_critical_levels_do_not_relabel_bollinger_bands_as_support_resistance() -> None:
    report = _report()
    report.raw_json["structure_levels"] = {
        "support": [_level_payload(price=95.0, kind="support")],
        "resistance": [_level_payload(price=123.0, kind="resistance")],
    }
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100.0,
        quantity=0.5,
        leverage=2,
        current_price=110.0,
        liquidation_price=80.0,
    )

    state = build_position_state(position, report, [])
    prices = {level["price"] for level in state["analysis"]["risk"]["critical_levels"]}
    kinds = {level.get("kind") for level in state["analysis"]["risk"]["critical_levels"]}

    assert 101.0 not in prices
    assert 116.0 not in prices
    assert {95.0, 123.0}.issubset(prices)
    assert {"support", "resistance"}.issubset(kinds)


def _flat_candle(index: int, close: float, low: float, high: float, volume: float) -> MarketCandle:
    open_price = close * 0.998
    return MarketCandle(
        timestamp=BASE_TIME + timedelta(hours=index * 4),
        open=open_price,
        high=max(high, open_price, close),
        low=min(low, open_price, close),
        close=close,
        volume=volume,
    )


def _report() -> Report:
    return Report(
        symbol="BTCUSDT",
        timeframe="4h",
        price=110.0,
        change_24h=2.4,
        entry_score=82,
        scores=ScoreBreakdown(structure=80, volume=76, liquidity=70, momentum=72, risk=24, fomo=30),
        state_label="관찰 후보",
        raw_json={
            "indicators": {
                "rsi": 58,
                "macd_histogram": 0.2,
                "last_close": 110.0,
                "previous_close": 108.0,
                "bollinger_upper": 116.0,
                "bollinger_lower": 101.0,
                "relative_volume": 1.1,
                "atr": 2.8,
            },
            "structure": {
                "trend": {
                    "direction": "neutral_to_bullish",
                    "higher_low": True,
                    "break_of_structure": False,
                },
                "wyckoff": {
                    "accumulation_score": 54,
                    "distribution_score": 24,
                    "phase_hint": "neutral_range",
                    "spring_candidate": False,
                    "sos_confirmed": False,
                },
            },
            "liquidity": {
                "open_interest_change": "stable",
                "funding_rate_state": "neutral",
            },
        },
        report="deterministic report",
        provider="mock",
    )


def _level_payload(price: float, kind: str) -> dict:
    return {
        "price": price,
        "score": 72,
        "touches": 4,
        "last_touch_at": BASE_TIME.isoformat(),
        "kind": kind,
        "sources": ["swing"],
        "strength": "medium",
        "label": "구조 레벨",
    }
