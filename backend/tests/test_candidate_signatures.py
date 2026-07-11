from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import MarketCandle
from app.structure.candidates import engine
from app.structure.liquidity.common import SwingPoint


def candle(index: int, close: float, *, open_: float | None = None, high: float | None = None, low: float | None = None, volume: float = 100.0) -> MarketCandle:
    return MarketCandle(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=4 * index),
        open=close if open_ is None else open_,
        high=close + 1 if high is None else high,
        low=close - 1 if low is None else low,
        close=close,
        volume=volume,
    )


def test_fvg_is_confirmed_on_third_candle_without_future_data() -> None:
    candles = [candle(0, 100, high=101), candle(1, 102), candle(2, 105, low=103)]
    events = engine.detect_candidate_signatures(candles)["events"]
    fvg = next(item for item in events if item["engine"] == "fvg")
    assert fvg["direction"] == "long"
    assert fvg["components"]["zone_low"] == 101
    assert fvg["components"]["zone_high"] == 103
    frozen = fvg.copy()
    assert frozen == next(item for item in engine.detect_candidate_signatures(candles)["events"] if item["engine"] == "fvg")
    filled = [*candles, candle(3, 101.5, high=102.5, low=101.0)]
    assert engine.detect_candidate_signatures(filled)["active_fvgs"] == []


def test_order_block_uses_first_retest_after_structure_break(monkeypatch) -> None:
    candles = [candle(index, 100 + index) for index in range(12)]
    candles[8] = candle(8, 99, open_=102, high=103, low=98)
    candles[-1] = candle(11, 101, open_=101, high=102, low=100)
    monkeypatch.setattr(
        engine,
        "detect_structure_shift",
        lambda prefix: {"state": "structure_break", "direction": "up", "event": "BOS"} if len(prefix) == 10 else {"state": "inside_structure"},
    )
    events = engine.detect_candidate_signatures(candles)["events"]
    order_block = next(item for item in events if item["engine"] == "order_block")
    assert order_block["event_type"] == "retest"
    assert order_block["direction"] == "long"


def test_vcp_requires_three_contracting_swings_and_low_relative_volume(monkeypatch) -> None:
    candles = [candle(index, 100, volume=100) for index in range(30)]
    swings = [
        SwingPoint("high", 110, candles[20].timestamp, 20, 100),
        SwingPoint("low", 100, candles[22].timestamp, 22, 100),
        SwingPoint("high", 107, candles[24].timestamp, 24, 100),
        SwingPoint("low", 102, candles[26].timestamp, 26, 100),
    ]
    monkeypatch.setattr(engine, "fractal_swings", lambda items: swings)
    monkeypatch.setattr(engine, "relative_volume", lambda items, index: 0.5)
    events = engine.detect_candidate_signatures(candles)["events"]
    assert any(item["engine"] == "vcp" for item in events)


def test_stage2_template_uses_ma_order_slope_and_high_distance() -> None:
    candles = [candle(index, 100 + index * 0.5) for index in range(240)]
    stage = engine.detect_stage2_template(candles)
    assert stage["active"] is True
    assert stage["ma150"] > stage["ma200"]
