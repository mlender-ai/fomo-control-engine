from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Any

from app.db.models import MarketCandle


@dataclass(frozen=True)
class SwingPoint:
    kind: str
    price: float
    timestamp: datetime
    index: int
    volume: float


def atr(candles: list[MarketCandle], period: int = 14) -> float:
    if len(candles) < 2:
        return candles[-1].close * 0.01 if candles else 1.0
    ranges = []
    previous_close = candles[0].close
    for candle in candles[1:]:
        ranges.append(max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close)))
        previous_close = candle.close
    window = ranges[-period:] if len(ranges) >= period else ranges
    return mean(window) if window else candles[-1].close * 0.01


def fractal_swings(candles: list[MarketCandle], window: int = 2) -> list[SwingPoint]:
    swings: list[SwingPoint] = []
    for index in range(window, len(candles) - window):
        candle = candles[index]
        neighbors = candles[index - window : index] + candles[index + 1 : index + 1 + window]
        if all(candle.high > item.high for item in neighbors):
            swings.append(SwingPoint("high", candle.high, candle.timestamp, index, candle.volume))
        if all(candle.low < item.low for item in neighbors):
            swings.append(SwingPoint("low", candle.low, candle.timestamp, index, candle.volume))
    return swings


def relative_volume(candles: list[MarketCandle], index: int, lookback: int = 20) -> float:
    start = max(0, index - lookback)
    baseline = [candle.volume for candle in candles[start:index]]
    if not baseline:
        return 1.0
    avg = mean(baseline)
    return candles[index].volume / avg if avg else 1.0


def clamp_int(value: float, low: int, high: int) -> int:
    return max(low, min(high, int(round(value))))


def optional_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def timestamp(value: datetime) -> str:
    return value.isoformat()
