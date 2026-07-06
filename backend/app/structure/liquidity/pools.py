from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Any

from app.db.models import MarketCandle
from app.structure.liquidity.common import SwingPoint, atr, clamp_int, fractal_swings

POOL_LOOKBACK = 100
MAX_POOLS_PER_TYPE = 6


@dataclass(frozen=True)
class LiquidityPool:
    id: str
    price: float
    kind: str
    touch_count: int
    first_seen: datetime
    last_touch_at: datetime
    swept: bool
    swept_at: datetime | None
    score: int
    side: str
    grade: str
    label: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "price": self.price,
            "kind": self.kind,
            "touch_count": self.touch_count,
            "touches": self.touch_count,
            "first_seen": self.first_seen.isoformat(),
            "last_touch_at": self.last_touch_at.isoformat(),
            "swept": self.swept,
            "swept_at": self.swept_at.isoformat() if self.swept_at else None,
            "score": self.score,
            "side": self.side,
            "grade": self.grade,
            "label": self.label,
        }


def detect_liquidity_pools(
    candles: list[MarketCandle],
    *,
    lookback: int = POOL_LOOKBACK,
    max_per_type: int = MAX_POOLS_PER_TYPE,
) -> list[LiquidityPool]:
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    recent = ordered[-lookback:]
    if len(recent) < 8:
        return []
    reference_price = recent[-1].close
    tolerance = max(atr(recent) * 0.1, reference_price * 0.0008)
    swings = fractal_swings(recent)
    pools: list[LiquidityPool] = []
    pools.extend(_equal_pools(recent, swings, "high", "eqh", tolerance))
    pools.extend(_equal_pools(recent, swings, "low", "eql", tolerance))
    pools.extend(_old_extreme_pools(recent, swings))
    capped: list[LiquidityPool] = []
    for kind in ("eqh", "eql", "old_high", "old_low"):
        items = [pool for pool in pools if pool.kind == kind]
        capped.extend(sorted(items, key=lambda pool: (pool.swept, -pool.touch_count, -pool.last_touch_at.timestamp()))[:max_per_type])
    return sorted(capped, key=lambda pool: (pool.side, -pool.score, -pool.last_touch_at.timestamp()))


def _equal_pools(
    candles: list[MarketCandle],
    swings: list[SwingPoint],
    swing_kind: str,
    pool_kind: str,
    tolerance: float,
) -> list[LiquidityPool]:
    points = sorted([point for point in swings if point.kind == swing_kind], key=lambda point: point.price)
    clusters: list[list[SwingPoint]] = []
    for point in points:
        matched = False
        for cluster in clusters:
            if abs(point.price - _weighted_price(cluster)) <= tolerance:
                cluster.append(point)
                matched = True
                break
        if not matched:
            clusters.append([point])
    return [_pool_from_points(candles, cluster, pool_kind) for cluster in clusters if len(cluster) >= 2]


def _old_extreme_pools(candles: list[MarketCandle], swings: list[SwingPoint]) -> list[LiquidityPool]:
    pools: list[LiquidityPool] = []
    for point in swings:
        if point.kind == "high" and not any(candle.high > point.price for candle in candles[point.index + 1 :]):
            pools.append(_pool_from_points(candles, [point], "old_high"))
        if point.kind == "low" and not any(candle.low < point.price for candle in candles[point.index + 1 :]):
            pools.append(_pool_from_points(candles, [point], "old_low"))
    return pools


def _pool_from_points(candles: list[MarketCandle], points: list[SwingPoint], kind: str) -> LiquidityPool:
    price = round(_weighted_price(points), 8)
    first_seen = min(point.timestamp for point in points)
    last_touch = max(point.timestamp for point in points)
    last_index = max(point.index for point in points)
    swept_at = _swept_at(candles, last_index, price, kind)
    touch_count = len(points)
    score = _pool_score(touch_count, last_index, len(candles), swept_at is not None)
    side = "buy_side" if kind in {"eqh", "old_high"} else "sell_side"
    return LiquidityPool(
        id=f"{kind}:{int(first_seen.timestamp())}:{price}",
        price=price,
        kind=kind,
        touch_count=touch_count,
        first_seen=first_seen,
        last_touch_at=last_touch,
        swept=swept_at is not None,
        swept_at=swept_at,
        score=score,
        side=side,
        grade=_grade(score),
        label=_pool_label(kind, touch_count, score),
    )


def _swept_at(candles: list[MarketCandle], last_index: int, price: float, kind: str) -> datetime | None:
    future = candles[last_index + 1 :]
    if kind in {"eqh", "old_high"}:
        match = next((candle for candle in future if candle.high > price), None)
    else:
        match = next((candle for candle in future if candle.low < price), None)
    return match.timestamp if match else None


def _pool_score(touch_count: int, last_index: int, candle_count: int, swept: bool) -> int:
    touch_score = min(55, 25 + max(0, touch_count - 1) * 15)
    recent_score = 25 if candle_count - 1 - last_index <= 30 else 12 if candle_count - 1 - last_index <= 60 else 5
    swept_penalty = -20 if swept else 0
    return clamp_int(touch_score + recent_score + swept_penalty, 0, 100)


def _weighted_price(points: list[SwingPoint]) -> float:
    total_volume = sum(max(point.volume, 0.0) for point in points)
    if total_volume <= 0:
        return mean([point.price for point in points])
    return sum(point.price * point.volume for point in points) / total_volume


def _pool_label(kind: str, touches: int, score: int) -> str:
    labels = {
        "eqh": "동일 고점 유동성",
        "eql": "동일 저점 유동성",
        "old_high": "전고점 유동성",
        "old_low": "전저점 유동성",
    }
    return f"{labels.get(kind, kind)} · 터치 {touches} · 점수 {score}"


def _grade(score: int) -> str:
    if score >= 75:
        return "Strong"
    if score >= 55:
        return "Mid"
    return "Weak"
