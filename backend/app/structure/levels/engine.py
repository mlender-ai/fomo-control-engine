from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean

from app.db.models import MarketCandle


FRACTAL_WINDOW = 2
MAX_LEVEL_CANDLES = 200


@dataclass(frozen=True)
class StructureLevel:
    price: float
    score: int
    touches: int
    last_touch_at: datetime
    kind: str
    sources: list[str]

    @property
    def strength(self) -> str:
        if self.score >= 75:
            return "strong"
        if self.score >= 55:
            return "medium"
        return "weak"

    @property
    def label(self) -> str:
        prefix = "지지" if self.kind == "support" else "저항"
        return f"{prefix} · 터치 {self.touches} · 점수 {self.score}"

    def model_dump(self) -> dict:
        return {
            "price": self.price,
            "score": self.score,
            "touches": self.touches,
            "last_touch_at": self.last_touch_at.isoformat(),
            "kind": self.kind,
            "sources": self.sources,
            "strength": self.strength,
            "label": self.label,
        }


@dataclass(frozen=True)
class SwingPoint:
    price: float
    kind: str
    volume: float
    timestamp: datetime
    candle_index: int
    relative_volume: float


def detect_structure_levels(candles: list[MarketCandle], mark_price: float | None = None, volume_profile: dict | None = None) -> dict[str, list[StructureLevel]]:
    ordered = sorted(candles, key=lambda candle: candle.timestamp)[-MAX_LEVEL_CANDLES:]
    if len(ordered) < FRACTAL_WINDOW * 2 + 1:
        return {"support": [], "resistance": []}
    atr = _atr(ordered)
    reference_price = mark_price or ordered[-1].close
    tolerance = max(atr * 0.5, reference_price * 0.004)
    hvn_prices = _hvn_prices(volume_profile)
    swings = _swing_points(ordered)
    support = _levels_for_kind(swings, ordered, tolerance, "support", hvn_prices)
    resistance = _levels_for_kind(swings, ordered, tolerance, "resistance", hvn_prices)
    if mark_price is not None:
        support = [level for level in support if level.price <= mark_price]
        resistance = [level for level in resistance if level.price >= mark_price]
    return {
        "support": sorted(support, key=lambda level: (-level.score, abs((mark_price or level.price) - level.price)))[:8],
        "resistance": sorted(resistance, key=lambda level: (-level.score, abs((mark_price or level.price) - level.price)))[:8],
    }


def _swing_points(candles: list[MarketCandle]) -> list[SwingPoint]:
    volumes = [candle.volume for candle in candles]
    baseline = mean(volumes) if volumes else 1.0
    swings: list[SwingPoint] = []
    for index in range(FRACTAL_WINDOW, len(candles) - FRACTAL_WINDOW):
        candle = candles[index]
        left = candles[index - FRACTAL_WINDOW : index]
        right = candles[index + 1 : index + 1 + FRACTAL_WINDOW]
        if all(candle.high > item.high for item in left + right):
            swings.append(
                SwingPoint(
                    price=candle.high,
                    kind="resistance",
                    volume=candle.volume,
                    timestamp=candle.timestamp,
                    candle_index=index,
                    relative_volume=candle.volume / baseline if baseline else 1.0,
                )
            )
        if all(candle.low < item.low for item in left + right):
            swings.append(
                SwingPoint(
                    price=candle.low,
                    kind="support",
                    volume=candle.volume,
                    timestamp=candle.timestamp,
                    candle_index=index,
                    relative_volume=candle.volume / baseline if baseline else 1.0,
                )
            )
    return swings


def _levels_for_kind(swings: list[SwingPoint], candles: list[MarketCandle], tolerance: float, kind: str, hvn_prices: list[float]) -> list[StructureLevel]:
    points = sorted([point for point in swings if point.kind == kind], key=lambda point: point.price)
    clusters: list[list[SwingPoint]] = []
    for point in points:
        matched = False
        for cluster in clusters:
            center = _weighted_price(cluster)
            if abs(point.price - center) <= tolerance:
                cluster.append(point)
                matched = True
                break
        if not matched:
            clusters.append([point])
    return [_level_from_cluster(cluster, candles, tolerance, kind, hvn_prices) for cluster in clusters if cluster]


def _level_from_cluster(cluster: list[SwingPoint], candles: list[MarketCandle], tolerance: float, kind: str, hvn_prices: list[float]) -> StructureLevel:
    price = round(_weighted_price(cluster), 8)
    touches = len(cluster)
    avg_relative_volume = mean([point.relative_volume for point in cluster])
    last_touch_index = max(point.candle_index for point in cluster)
    last_touch_at = max(point.timestamp for point in cluster)
    sources = ["swing"]
    score = _touch_score(touches)
    if avg_relative_volume > 1.3:
        score += 20
    if len(candles) - 1 - last_touch_index <= 30:
        score += 15
    if any(abs(price - hvn_price) <= tolerance for hvn_price in hvn_prices):
        score += 15
        sources.append("hvn")
    return StructureLevel(price=price, score=min(100, score), touches=touches, last_touch_at=last_touch_at, kind=kind, sources=sources)


def _weighted_price(cluster: list[SwingPoint]) -> float:
    total_volume = sum(max(point.volume, 0.0) for point in cluster)
    if total_volume <= 0:
        return mean([point.price for point in cluster])
    return sum(point.price * point.volume for point in cluster) / total_volume


def _touch_score(touches: int) -> int:
    if touches >= 5:
        return 50
    if touches >= 3:
        return 35
    if touches >= 2:
        return 20
    return 10


def _hvn_prices(volume_profile: dict | None) -> list[float]:
    if not isinstance(volume_profile, dict):
        return []
    bins = volume_profile.get("bins")
    if not isinstance(bins, list):
        return []
    valid_bins = [item for item in bins if isinstance(item, dict) and isinstance(item.get("volume"), (int, float))]
    if not valid_bins:
        return []
    top_bins = sorted(valid_bins, key=lambda item: item["volume"], reverse=True)[:3]
    prices = []
    for item in top_bins:
        low = item.get("price_low")
        high = item.get("price_high")
        if isinstance(low, (int, float)) and isinstance(high, (int, float)):
            prices.append((low + high) / 2)
    return prices


def _atr(candles: list[MarketCandle], period: int = 14) -> float:
    if len(candles) < 2:
        return candles[-1].close * 0.01 if candles else 1.0
    ranges = []
    previous_close = candles[0].close
    for candle in candles[1:]:
        ranges.append(max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close)))
        previous_close = candle.close
    window = ranges[-period:] if len(ranges) >= period else ranges
    return mean(window) if window else candles[-1].close * 0.01
