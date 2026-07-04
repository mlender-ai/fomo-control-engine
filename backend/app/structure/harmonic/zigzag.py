from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from app.db.models import MarketCandle


@dataclass(frozen=True)
class ZigZagPivot:
    index: int
    time: int
    price: float
    kind: str

    def model_dump(self) -> dict:
        return {
            "index": self.index,
            "time": self.time,
            "price": round(self.price, 8),
            "kind": self.kind,
        }


def extract_zigzag_pivots(candles: list[MarketCandle], atr_multiplier: float = 2.0, max_pivots: int = 24) -> list[ZigZagPivot]:
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    if len(ordered) < 8:
        return []

    threshold = max(_atr(ordered) * atr_multiplier, ordered[-1].close * 0.002)
    candidate_high = _pivot(0, ordered[0].high, "high", ordered)
    candidate_low = _pivot(0, ordered[0].low, "low", ordered)
    direction = 0
    extreme: ZigZagPivot | None = None
    pivots: list[ZigZagPivot] = []

    for index, candle in enumerate(ordered[1:], start=1):
        high = _pivot(index, candle.high, "high", ordered)
        low = _pivot(index, candle.low, "low", ordered)

        if direction == 0:
            if high.price >= candidate_high.price:
                candidate_high = high
            if low.price <= candidate_low.price:
                candidate_low = low
            if candidate_high.price - candidate_low.price < threshold:
                continue
            if candidate_low.index <= candidate_high.index:
                pivots.append(candidate_low)
                direction = 1
                extreme = candidate_high
            else:
                pivots.append(candidate_high)
                direction = -1
                extreme = candidate_low
            continue

        if direction == 1:
            if extreme is None or high.price >= extreme.price:
                extreme = high
            if extreme is not None and extreme.price - low.price >= threshold:
                pivots.append(extreme)
                direction = -1
                extreme = low
        else:
            if extreme is None or low.price <= extreme.price:
                extreme = low
            if extreme is not None and high.price - extreme.price >= threshold:
                pivots.append(extreme)
                direction = 1
                extreme = high

    if extreme is not None:
        pivots.append(extreme)
    return _normalize_pivots(pivots)[-max_pivots:]


def _pivot(index: int, price: float, kind: str, candles: list[MarketCandle]) -> ZigZagPivot:
    return ZigZagPivot(index=index, time=int(candles[index].timestamp.timestamp()), price=price, kind=kind)


def _normalize_pivots(pivots: list[ZigZagPivot]) -> list[ZigZagPivot]:
    normalized: list[ZigZagPivot] = []
    for pivot in pivots:
        if normalized and pivot.index == normalized[-1].index and pivot.kind == normalized[-1].kind:
            normalized[-1] = pivot
            continue
        if normalized and pivot.kind == normalized[-1].kind:
            previous = normalized[-1]
            if pivot.kind == "high" and pivot.price > previous.price:
                normalized[-1] = pivot
            elif pivot.kind == "low" and pivot.price < previous.price:
                normalized[-1] = pivot
            continue
        normalized.append(pivot)
    return normalized


def _atr(candles: list[MarketCandle], period: int = 14) -> float:
    if len(candles) < 2:
        return candles[-1].close * 0.01 if candles else 1.0
    ranges: list[float] = []
    previous_close = candles[0].close
    for candle in candles[1:]:
        ranges.append(max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close)))
        previous_close = candle.close
    window = ranges[-period:] if len(ranges) >= period else ranges
    return max(mean(window), candles[-1].close * 0.0001) if window else candles[-1].close * 0.01
