from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.db.models import Direction, MarketCandle, Position, PositionSnapshot
from app.positions.action_plan import build_action_plan
from app.positions.chart_analysis import build_chart_analysis
from app.structure.harmonic.engine import detect_harmonic_patterns


BASE_TIME = datetime(2026, 7, 4, tzinfo=timezone.utc)


def test_harmonic_engine_detects_textbook_bullish_gartley() -> None:
    candles = _swing_path([100.0, 120.0, 107.64, 114.0, 104.28])
    result = detect_harmonic_patterns(candles, levels=_support_levels(104.3), volume_profile=_volume_profile(104.25), atr_multiplier=3.0)

    pattern = next(item for item in result["patterns"] if item["name"] == "gartley" and item["status"] == "completed")

    assert pattern["direction"] == "bullish"
    assert pattern["confidence"] == sum(pattern["components"].values())
    assert pattern["components"]["ratio_fit"] >= 45
    assert pattern["components"]["confluence"] > 0
    assert pattern["prz"]["low"] < 104.28 < pattern["prz"]["high"]


def test_harmonic_engine_detects_textbook_bullish_bat() -> None:
    candles = _swing_path([100.0, 120.0, 110.0, 116.0, 102.28])
    result = detect_harmonic_patterns(candles, levels=_support_levels(102.3), volume_profile=_volume_profile(102.25), atr_multiplier=2.0)

    pattern = next(item for item in result["patterns"] if item["name"] == "bat" and item["status"] == "completed")

    assert pattern["direction"] == "bullish"
    assert 0.45 <= pattern["ratios"]["b_xa"] <= 0.55
    assert pattern["confidence"] >= 80
    assert pattern["confidence"] == sum(pattern["components"].values())


def test_harmonic_engine_rejects_ratio_miss_over_tolerance() -> None:
    candles = _swing_path([100.0, 120.0, 116.0, 118.0, 110.0])
    result = detect_harmonic_patterns(candles, levels=_support_levels(110.0), volume_profile=_volume_profile(110.0), atr_multiplier=3.0)

    names = {item["name"] for item in result["patterns"]}

    assert "gartley" not in names
    assert "bat" not in names


def test_harmonic_prz_flows_into_take_profit_action_plan() -> None:
    candles = _swing_path([120.0, 100.0, 112.36, 106.0, 115.72])
    position = Position(symbol="BTCUSDT", direction=Direction.long, entry_price=104.0, quantity=1.0, leverage=2, mark_price=108.0)
    snapshot = PositionSnapshot(
        position_id=uuid4(),
        symbol="BTCUSDT",
        as_of=BASE_TIME,
        mark_price=108.0,
        pnl_percent=7.69,
        health_score=72,
        status_label="관찰 필요",
        risk_score=34,
        score_json={},
        analysis_json={},
    )
    chart_analysis = _chart_analysis_payload(position, candles, levels=_resistance_levels(115.7), volume_profile=_volume_profile(115.75))

    plan = build_action_plan(position, snapshot, chart_analysis)

    harmonic_targets = [item for item in plan["take_profit"] if "PRZ" in item["basis"]]
    assert harmonic_targets
    assert harmonic_targets[0]["price"] > 108.0
    assert "Gartley PRZ" in harmonic_targets[0]["basis"]


def _chart_analysis_payload(position: Position, candles: list[MarketCandle], levels: dict, volume_profile: dict) -> dict:
    from app.db.models import MarketSnapshot

    snapshot = MarketSnapshot(symbol=position.symbol, timeframe="4h", price=position.mark_price or candles[-1].close, change_24h=0, funding_rate=0, open_interest_change=0, candles=_pad_candles(candles))
    analysis = build_chart_analysis(position, snapshot)
    harmonic = detect_harmonic_patterns(_pad_candles(candles), levels=levels, volume_profile=volume_profile, atr_multiplier=3.0)
    return {
        **analysis,
        "price_levels": {
            **analysis["price_levels"],
            "support": levels.get("support", []),
            "resistance": levels.get("resistance", []),
        },
        "volume_profile": volume_profile,
        "harmonic_patterns": harmonic["patterns"],
    }


def _swing_path(points: list[float]) -> list[MarketCandle]:
    candles: list[MarketCandle] = []
    index = 0
    previous = points[0]
    candles.append(_candle(index, previous, previous, previous + 0.05, previous - 0.05, 900.0))
    index += 1
    for target in points[1:]:
        steps = 5
        for step in range(steps):
            start = previous + (target - previous) * (step / steps)
            close = previous + (target - previous) * ((step + 1) / steps)
            high = max(start, close) + (0.05 if step < steps - 1 else 0.08)
            low = min(start, close) - (0.05 if step < steps - 1 else 0.08)
            candles.append(_candle(index, start, close, high, low, 900.0 + step * 20))
            index += 1
        previous = target
    return candles


def _pad_candles(candles: list[MarketCandle]) -> list[MarketCandle]:
    padded = []
    base_price = candles[0].open
    for index in range(120):
        price = base_price + ((index % 6) - 3) * 0.04
        padded.append(_candle(index, price, price, price + 0.08, price - 0.08, 700.0))
    offset = len(padded)
    for index, candle in enumerate(candles):
        padded.append(candle.model_copy(update={"timestamp": BASE_TIME + timedelta(hours=(offset + index) * 4)}))
    return padded


def _candle(index: int, open_price: float, close: float, high: float, low: float, volume: float) -> MarketCandle:
    return MarketCandle(
        timestamp=BASE_TIME + timedelta(hours=index * 4),
        open=open_price,
        high=max(high, open_price, close),
        low=min(low, open_price, close),
        close=close,
        volume=volume,
    )


def _support_levels(price: float) -> dict:
    return {"support": [{"price": price, "score": 86, "touches": 4, "kind": "support", "sources": ["swing"]}], "resistance": []}


def _resistance_levels(price: float) -> dict:
    return {"support": [], "resistance": [{"price": price, "score": 84, "touches": 4, "kind": "resistance", "sources": ["swing"]}]}


def _volume_profile(price: float) -> dict:
    return {
        "poc_price": price,
        "value_area_high": price * 1.01,
        "value_area_low": price * 0.99,
        "bins": [],
        "method": "ohlcv_estimated",
        "source_methods": ["ohlcv_estimated"],
    }
