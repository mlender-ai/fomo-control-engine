from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Any, Literal

from app.db.models import MarketCandle
from app.structure.harmonic.zigzag import ZigZagPivot, extract_zigzag_pivots


PatternDirection = Literal["bullish", "bearish"]
PatternStatus = Literal["completed", "forming"]


@dataclass(frozen=True)
class RatioRule:
    name: str
    center: float | None = None
    low: float | None = None
    high: float | None = None
    tolerance: float = 0.03


@dataclass(frozen=True)
class HarmonicSpec:
    name: str
    label: str
    b: RatioRule
    c: RatioRule
    d_xa: RatioRule | None
    bc_projection: RatioRule | None
    abcd: RatioRule | None = None
    d_mode: Literal["retracement", "extension", "abcd"] = "retracement"


PATTERN_SPECS = [
    HarmonicSpec(
        name="gartley",
        label="Gartley",
        b=RatioRule("B/XA", center=0.618, tolerance=0.03),
        c=RatioRule("C/AB", low=0.382, high=0.886, tolerance=0.05),
        d_xa=RatioRule("D/XA", center=0.786, tolerance=0.05),
        bc_projection=RatioRule("CD/BC", low=1.272, high=1.618, tolerance=0.08),
        d_mode="retracement",
    ),
    HarmonicSpec(
        name="bat",
        label="Bat",
        b=RatioRule("B/XA", low=0.382, high=0.500, tolerance=0.04),
        c=RatioRule("C/AB", low=0.382, high=0.886, tolerance=0.05),
        d_xa=RatioRule("D/XA", center=0.886, tolerance=0.05),
        bc_projection=RatioRule("CD/BC", low=1.618, high=2.618, tolerance=0.12),
        d_mode="retracement",
    ),
    HarmonicSpec(
        name="butterfly",
        label="Butterfly",
        b=RatioRule("B/XA", center=0.786, tolerance=0.04),
        c=RatioRule("C/AB", low=0.382, high=0.886, tolerance=0.05),
        d_xa=RatioRule("D/XA", center=1.272, tolerance=0.08),
        bc_projection=RatioRule("CD/BC", low=1.618, high=2.240, tolerance=0.12),
        d_mode="extension",
    ),
    HarmonicSpec(
        name="crab",
        label="Crab",
        b=RatioRule("B/XA", low=0.382, high=0.618, tolerance=0.05),
        c=RatioRule("C/AB", low=0.382, high=0.886, tolerance=0.05),
        d_xa=RatioRule("D/XA", center=1.618, tolerance=0.08),
        bc_projection=RatioRule("CD/BC", low=2.240, high=3.618, tolerance=0.16),
        d_mode="extension",
    ),
    HarmonicSpec(
        name="abcd",
        label="AB=CD",
        b=RatioRule("B/AB", low=0.382, high=0.886, tolerance=0.05),
        c=RatioRule("CD/BC", low=1.130, high=2.618, tolerance=0.12),
        d_xa=None,
        bc_projection=None,
        abcd=RatioRule("CD/AB", center=1.000, tolerance=0.08),
        d_mode="abcd",
    ),
]


def detect_harmonic_patterns(
    candles: list[MarketCandle],
    *,
    levels: dict[str, list[Any]] | None = None,
    volume_profile: dict | None = None,
    atr_multiplier: float = 2.0,
    min_confidence: int = 55,
) -> dict[str, Any]:
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    pivots = extract_zigzag_pivots(ordered, atr_multiplier=atr_multiplier, max_pivots=24)
    recent = pivots[-12:]
    atr = _atr(ordered)
    patterns: list[dict[str, Any]] = []

    for start in range(max(0, len(recent) - 8), max(0, len(recent) - 4)):
        candidate = recent[start : start + 5]
        if len(candidate) == 5:
            patterns.extend(_match_completed(candidate, atr, levels, volume_profile))

    for start in range(max(0, len(recent) - 7), max(0, len(recent) - 3)):
        candidate = recent[start : start + 4]
        if len(candidate) == 4:
            patterns.extend(_match_forming(candidate, ordered[-1], atr, levels, volume_profile))

    filtered = [pattern for pattern in patterns if pattern["confidence"] >= min_confidence]
    filtered = _dedupe_patterns(filtered)
    return {
        "pivots": [pivot.model_dump() for pivot in recent],
        "patterns": sorted(filtered, key=lambda item: (-item["confidence"], item["status"], item["name"]))[:8],
        "min_confidence": min_confidence,
        "atr_multiplier": atr_multiplier,
    }


def _match_completed(
    pivots: list[ZigZagPivot],
    atr: float,
    levels: dict[str, list[Any]] | None,
    volume_profile: dict | None,
) -> list[dict[str, Any]]:
    if not _alternating(pivots):
        return []
    x, a, b, c, d = pivots
    direction = _direction_from_d(d)
    if direction is None:
        return []
    ratios = _ratios(x, a, b, c, d)
    patterns: list[dict[str, Any]] = []
    for spec in PATTERN_SPECS:
        if not _basic_shape_valid(direction, x, a, b, c, d):
            continue
        if spec.name == "abcd":
            ratio_results = [_rule_result(spec.b, ratios["c_ab"]), _rule_result(spec.c, ratios["cd_bc"]), _rule_result(spec.abcd, ratios["cd_ab"])]
        else:
            ratio_results = [
                _rule_result(spec.b, ratios["b_xa"]),
                _rule_result(spec.c, ratios["c_ab"]),
                _rule_result(spec.d_xa, ratios["d_xa"]),
                _rule_result(spec.bc_projection, ratios["cd_bc"]),
            ]
        if any(result["miss"] > 1 for result in ratio_results):
            continue
        prz = _project_prz(spec, direction, x, a, b, c, atr)
        if not _price_near_prz(d.price, prz, atr):
            continue
        patterns.append(_pattern_payload(spec, direction, "completed", pivots, ratios, ratio_results, prz, atr, levels, volume_profile))
    return patterns


def _match_forming(
    pivots: list[ZigZagPivot],
    last_candle: MarketCandle,
    atr: float,
    levels: dict[str, list[Any]] | None,
    volume_profile: dict | None,
) -> list[dict[str, Any]]:
    if not _alternating(pivots):
        return []
    x, a, b, c = pivots
    expected_direction = "bullish" if c.kind == "high" else "bearish" if c.kind == "low" else None
    if expected_direction is None:
        return []
    ratios = _ratios_without_d(x, a, b, c)
    pseudo_d = ZigZagPivot(index=len(pivots), time=int(last_candle.timestamp.timestamp()), price=last_candle.close, kind="low" if expected_direction == "bullish" else "high")
    patterns: list[dict[str, Any]] = []
    for spec in PATTERN_SPECS:
        if spec.name == "abcd":
            ratio_results = [_rule_result(spec.b, ratios["c_ab"])]
        else:
            ratio_results = [_rule_result(spec.b, ratios["b_xa"]), _rule_result(spec.c, ratios["c_ab"])]
        if any(result["miss"] > 1 for result in ratio_results):
            continue
        prz = _project_prz(spec, expected_direction, x, a, b, c, atr)
        patterns.append(_pattern_payload(spec, expected_direction, "forming", [x, a, b, c, pseudo_d], ratios, ratio_results, prz, atr, levels, volume_profile))
    return patterns


def _pattern_payload(
    spec: HarmonicSpec,
    direction: PatternDirection,
    status: PatternStatus,
    pivots: list[ZigZagPivot],
    ratios: dict[str, float],
    ratio_results: list[dict[str, Any]],
    prz: dict[str, float],
    atr: float,
    levels: dict[str, list[Any]] | None,
    volume_profile: dict | None,
) -> dict[str, Any]:
    ratio_score = _ratio_score(ratio_results)
    confluence = _confluence_score(prz, direction, levels, volume_profile, atr)
    significance = _atr_significance_score(pivots, atr)
    confidence = min(100, ratio_score + confluence["score"] + significance)
    return {
        "id": f"{spec.name}-{direction}-{status}-{pivots[0].time}-{pivots[-1].time}",
        "name": spec.name,
        "label": spec.label,
        "direction": direction,
        "status": status,
        "confidence": confidence,
        "components": {
            "ratio_fit": ratio_score,
            "confluence": confluence["score"],
            "atr_significance": significance,
        },
        "points": _points_payload(pivots),
        "ratios": {key: round(value, 4) for key, value in ratios.items() if value is not None},
        "ratio_checks": ratio_results,
        "prz": prz,
        "confluence_sources": confluence["sources"],
        "basis": _basis(spec.label, prz, confluence["sources"]),
    }


def _ratios(x: ZigZagPivot, a: ZigZagPivot, b: ZigZagPivot, c: ZigZagPivot, d: ZigZagPivot) -> dict[str, float]:
    values = _ratios_without_d(x, a, b, c)
    values.update(
        {
            "d_xa": _safe_ratio(abs(a.price - d.price), abs(a.price - x.price)),
            "cd_bc": _safe_ratio(abs(d.price - c.price), abs(c.price - b.price)),
            "cd_ab": _safe_ratio(abs(d.price - c.price), abs(a.price - b.price)),
        }
    )
    return values


def _ratios_without_d(x: ZigZagPivot, a: ZigZagPivot, b: ZigZagPivot, c: ZigZagPivot) -> dict[str, float]:
    return {
        "b_xa": _safe_ratio(abs(a.price - b.price), abs(a.price - x.price)),
        "c_ab": _safe_ratio(abs(c.price - b.price), abs(a.price - b.price)),
    }


def _project_prz(spec: HarmonicSpec, direction: PatternDirection, x: ZigZagPivot, a: ZigZagPivot, b: ZigZagPivot, c: ZigZagPivot, atr: float) -> dict[str, float]:
    projections: list[float] = []
    xa = abs(a.price - x.price)
    bc = abs(c.price - b.price)
    ab = abs(a.price - b.price)
    side = -1 if direction == "bullish" else 1

    if spec.d_xa is not None:
        ratios = _rule_projection_ratios(spec.d_xa)
        for ratio in ratios:
            projections.append(a.price + side * ratio * xa)
    if spec.bc_projection is not None:
        for ratio in _rule_projection_ratios(spec.bc_projection):
            projections.append(c.price + side * ratio * bc)
    if spec.abcd is not None:
        for ratio in _rule_projection_ratios(spec.abcd):
            projections.append(c.price + side * ratio * ab)

    if not projections:
        projections = [c.price + side * ab]
    low = min(projections)
    high = max(projections)
    padding = max(atr * 0.12, abs(high - low) * 0.12)
    return {"low": round(low - padding, 8), "high": round(high + padding, 8), "mid": round(mean(projections), 8)}


def _rule_projection_ratios(rule: RatioRule) -> list[float]:
    if rule.center is not None:
        return [rule.center]
    if rule.low is not None and rule.high is not None:
        return [rule.low, rule.high]
    return [1.0]


def _rule_result(rule: RatioRule | None, value: float | None) -> dict[str, Any]:
    if rule is None or value is None:
        return {"name": "n/a", "value": value, "target": None, "miss": 0.0}
    if rule.center is not None:
        miss = abs(value - rule.center) / rule.tolerance if rule.tolerance else 0.0
        target = f"{rule.center:.3f} ± {rule.tolerance:.3f}"
    else:
        low = rule.low if rule.low is not None else value
        high = rule.high if rule.high is not None else value
        if low <= value <= high:
            miss = 0.0
        else:
            miss = min(abs(value - low), abs(value - high)) / rule.tolerance if rule.tolerance else 0.0
        target = f"{low:.3f}-{high:.3f}"
    return {"name": rule.name, "value": round(value, 4), "target": target, "miss": round(min(2.0, miss), 4)}


def _ratio_score(results: list[dict[str, Any]]) -> int:
    if not results:
        return 0
    average_miss = mean([min(1.0, float(result.get("miss", 1.0))) for result in results])
    return _clamp(50 * (1 - average_miss))


def _confluence_score(prz: dict[str, float], direction: PatternDirection, levels: dict[str, list[Any]] | None, volume_profile: dict | None, atr: float) -> dict[str, Any]:
    sources: list[str] = []
    score = 0
    zone_low = prz["low"] - atr * 0.35
    zone_high = prz["high"] + atr * 0.35
    level_side = "support" if direction == "bullish" else "resistance"
    for level in _levels(levels, level_side):
        price = _optional_float(level.get("price"))
        if price is None or not zone_low <= price <= zone_high:
            continue
        level_score = int(level.get("score", 45))
        score = max(score, min(20, round(level_score * 0.25)))
        sources.append(_level_name(level, "지지" if level_side == "support" else "저항"))
        break
    for key, label in [("poc_price", "POC"), ("value_area_high", "VAH"), ("value_area_low", "VAL")]:
        price = _optional_float(volume_profile.get(key) if isinstance(volume_profile, dict) else None)
        if price is not None and zone_low <= price <= zone_high:
            score += 8
            sources.append(label)
            break
    return {"score": min(30, score), "sources": sources}


def _atr_significance_score(pivots: list[ZigZagPivot], atr: float) -> int:
    if atr <= 0:
        return 0
    height = max(pivot.price for pivot in pivots) - min(pivot.price for pivot in pivots)
    ratio = height / atr
    if ratio >= 8:
        return 20
    if ratio >= 5:
        return 16
    if ratio >= 3:
        return 12
    if ratio >= 2:
        return 8
    return 4


def _price_near_prz(price: float, prz: dict[str, float], atr: float) -> bool:
    return prz["low"] - atr * 0.45 <= price <= prz["high"] + atr * 0.45


def _basic_shape_valid(direction: PatternDirection, x: ZigZagPivot, a: ZigZagPivot, b: ZigZagPivot, c: ZigZagPivot, d: ZigZagPivot) -> bool:
    if direction == "bullish":
        return x.kind == "low" and a.kind == "high" and b.kind == "low" and c.kind == "high" and d.kind == "low"
    return x.kind == "high" and a.kind == "low" and b.kind == "high" and c.kind == "low" and d.kind == "high"


def _direction_from_d(pivot: ZigZagPivot) -> PatternDirection | None:
    if pivot.kind == "low":
        return "bullish"
    if pivot.kind == "high":
        return "bearish"
    return None


def _alternating(pivots: list[ZigZagPivot]) -> bool:
    return all(left.kind != right.kind for left, right in zip(pivots, pivots[1:]))


def _points_payload(pivots: list[ZigZagPivot]) -> list[dict[str, Any]]:
    labels = ["X", "A", "B", "C", "D"]
    return [{**pivot.model_dump(), "label": labels[index]} for index, pivot in enumerate(pivots[:5])]


def _dedupe_patterns(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, str, int], dict[str, Any]] = {}
    for pattern in patterns:
        key = (pattern["name"], pattern["direction"], int(pattern["points"][-1]["time"]))
        previous = best.get(key)
        if previous is None:
            best[key] = pattern
        elif previous["status"] == "forming" and pattern["status"] == "completed":
            best[key] = pattern
        elif previous["status"] == pattern["status"] and pattern["confidence"] > previous["confidence"]:
            best[key] = pattern
    return list(best.values())


def _levels(levels: dict[str, list[Any]] | None, side: str) -> list[dict[str, Any]]:
    if not isinstance(levels, dict):
        return []
    items = levels.get(side, [])
    return [item.model_dump() if hasattr(item, "model_dump") else item for item in items if isinstance(item, dict) or hasattr(item, "model_dump")]


def _level_name(level: dict[str, Any], fallback: str) -> str:
    label = level.get("label")
    if label:
        return str(label)
    touches = level.get("touches")
    score = level.get("score")
    if touches is not None and score is not None:
        return f"{fallback} · 터치 {touches} · 점수 {score}"
    return fallback


def _basis(label: str, prz: dict[str, float], sources: list[str]) -> str:
    if sources:
        return f"{label} PRZ + {'/'.join(sources)} 합류"
    return f"{label} PRZ"


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


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


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float) -> int:
    return max(0, min(100, int(round(value))))
