from __future__ import annotations

from typing import Any

from app.db.models import MarketCandle
from app.structure.liquidity.common import atr, fractal_swings, optional_float

RANGE_LOOKBACK = 80


def detect_structure_shift(candles: list[MarketCandle]) -> dict[str, Any]:
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    swings = fractal_swings(ordered)
    highs = [point for point in swings if point.kind == "high"]
    lows = [point for point in swings if point.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return {"state": "insufficient_swings", "event": None, "trend_before": "unknown"}
    trend = _swing_trend(highs, lows)
    buffer = atr(ordered) * 0.08
    close = ordered[-1].close
    last_high = highs[-1]
    last_low = lows[-1]
    if close > last_high.price + buffer:
        event = "BOS" if trend == "bullish" else "CHoCH"
        return _break_payload(event, "up", close, last_high.price, trend)
    if close < last_low.price - buffer:
        event = "BOS" if trend == "bearish" else "CHoCH"
        return _break_payload(event, "down", close, last_low.price, trend)
    return {"state": "inside_structure", "event": None, "trend_before": trend}


def dealing_range(candles: list[MarketCandle], mark_price: float, wyckoff: dict[str, Any] | None = None) -> dict[str, Any]:
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    source = "recent_range"
    range_payload = wyckoff.get("range") if isinstance(wyckoff, dict) else None
    high = optional_float(range_payload.get("high")) if isinstance(range_payload, dict) else None
    low = optional_float(range_payload.get("low")) if isinstance(range_payload, dict) else None
    if high is None or low is None or high <= low:
        recent = ordered[-RANGE_LOOKBACK:]
        high = max(candle.high for candle in recent)
        low = min(candle.low for candle in recent)
    else:
        source = "wyckoff_range"
    midpoint = (high + low) / 2
    position_pct = (mark_price - low) / (high - low) if high > low else 0.5
    zone = "premium" if position_pct >= 0.55 else "discount" if position_pct <= 0.45 else "equilibrium"
    if position_pct >= 0.8:
        zone = "deep_premium"
    elif position_pct <= 0.2:
        zone = "deep_discount"
    return {
        "source": source,
        "high": round(high, 8),
        "low": round(low, 8),
        "midpoint": round(midpoint, 8),
        "position_pct": round(position_pct * 100, 2),
        "zone": zone,
        "label": _range_zone_label(zone),
    }


def _swing_trend(highs: list[Any], lows: list[Any]) -> str:
    high_up = highs[-1].price > highs[-2].price
    low_up = lows[-1].price > lows[-2].price
    high_down = highs[-1].price < highs[-2].price
    low_down = lows[-1].price < lows[-2].price
    if high_up and low_up:
        return "bullish"
    if high_down and low_down:
        return "bearish"
    return "range"


def _break_payload(event: str, direction: str, close: float, level: float, trend: str) -> dict[str, Any]:
    return {
        "state": "structure_break",
        "event": event,
        "direction": direction,
        "level": round(level, 8),
        "close": round(close, 8),
        "trend_before": trend,
        "label": "구조 지속 돌파" if event == "BOS" else "구조 전환 후보",
    }


def _range_zone_label(zone: str) -> str:
    labels = {
        "deep_premium": "고평가 상단 구간",
        "premium": "프리미엄 구간",
        "equilibrium": "균형 구간",
        "discount": "디스카운트 구간",
        "deep_discount": "저평가 하단 구간",
    }
    return labels.get(zone, zone)
