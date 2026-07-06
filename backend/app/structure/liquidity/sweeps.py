from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.db.models import MarketCandle
from app.structure.liquidity.common import atr, clamp_int, relative_volume
from app.structure.liquidity.pools import LiquidityPool

MAX_RETURN_CANDLES = 3
VOLUME_CONFIRMATION_RATIO = 1.5
RECENT_SWEEP_CANDLES = 100


def detect_liquidity_sweeps(
    candles: list[MarketCandle],
    pools: list[LiquidityPool],
    *,
    trade_flow: dict[str, Any] | None = None,
    max_return_candles: int = MAX_RETURN_CANDLES,
    volume_threshold: float = VOLUME_CONFIRMATION_RATIO,
) -> dict[str, list[dict[str, Any]]]:
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    candle_atr = atr(ordered)
    start_index = max(0, len(ordered) - RECENT_SWEEP_CANDLES)
    confirmed: list[dict[str, Any]] = []
    unconfirmed: list[dict[str, Any]] = []
    for pool in pools:
        for index in range(start_index, len(ordered)):
            event = _pool_sweep_event(
                ordered,
                index,
                pool,
                candle_atr,
                trade_flow,
                max_return_candles=max_return_candles,
                volume_threshold=volume_threshold,
            )
            if event is None:
                continue
            if event["confirmed"]:
                confirmed.append(event)
            else:
                unconfirmed.append(event)
    return {
        "sweeps": _cap_sweeps(confirmed),
        "rejected_sweeps": sorted(unconfirmed, key=lambda item: item["timestamp"], reverse=True)[:8],
    }


def detect_htf_range_sweeps(
    candles: list[MarketCandle],
    *,
    trade_flow: dict[str, Any] | None = None,
    max_return_candles: int = MAX_RETURN_CANDLES,
    volume_threshold: float = VOLUME_CONFIRMATION_RATIO,
) -> list[dict[str, Any]]:
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    if len(ordered) < 12:
        return []
    candle_atr = atr(ordered)
    daily = _daily_ranges(ordered)
    events = []
    for index, candle in enumerate(ordered):
        previous_day = _previous_daily_range(daily, candle)
        if previous_day is None:
            continue
        for side, level in (("buy_side", previous_day["high"]), ("sell_side", previous_day["low"])):
            pool = LiquidityPool(
                id=f"htf:{side}:{previous_day['date']}:{round(level, 8)}",
                price=round(level, 8),
                kind="old_high" if side == "buy_side" else "old_low",
                touch_count=1,
                first_seen=previous_day["start"],
                last_touch_at=previous_day["end"],
                swept=False,
                swept_at=None,
                score=55,
                side=side,
                grade="Mid",
                label="직전 1D 고가" if side == "buy_side" else "직전 1D 저가",
            )
            event = _pool_sweep_event(
                ordered,
                index,
                pool,
                candle_atr,
                trade_flow,
                max_return_candles=max_return_candles,
                volume_threshold=volume_threshold,
                event_type="htf_range_sweep",
            )
            if event and event["confirmed"]:
                events.append(event)
    return sorted(events, key=lambda item: item["timestamp"], reverse=True)[:4]


def _pool_sweep_event(
    candles: list[MarketCandle],
    index: int,
    pool: LiquidityPool,
    candle_atr: float,
    trade_flow: dict[str, Any] | None,
    *,
    max_return_candles: int,
    volume_threshold: float,
    event_type: str = "liquidity_sweep",
) -> dict[str, Any] | None:
    candle = candles[index]
    if pool.side == "buy_side":
        depth = candle.high - pool.price
        penetrated = depth > max(pool.price * 0.0005, candle_atr * 0.05)
        expected_move = "down"
        wick_extreme = candle.high
        wyckoff_equivalent = "utad_candidate"
        label = "상단 유동성 스윕"
    else:
        depth = pool.price - candle.low
        penetrated = depth > max(pool.price * 0.0005, candle_atr * 0.05)
        expected_move = "up"
        wick_extreme = candle.low
        wyckoff_equivalent = "spring_candidate"
        label = "하단 유동성 스윕"
    if not penetrated:
        return None
    return_offset = _return_offset(candles, index, pool, max_return_candles)
    if return_offset is None:
        return None
    volume_ratio = relative_volume(candles, index)
    delta_aligned = _delta_aligned(trade_flow, candle, expected_move)
    components = _confidence_components(depth, candle_atr, volume_ratio, return_offset, pool.score, delta_aligned)
    confidence = clamp_int(sum(components.values()), 0, 100)
    volume_confirmed = volume_ratio >= volume_threshold
    return_candle = candles[index + return_offset]
    depth_atr = depth / candle_atr if candle_atr else 0.0
    return {
        "id": f"{event_type}:{pool.id}:{int(candle.timestamp.timestamp())}",
        "type": event_type,
        "side": pool.side,
        "pool_id": pool.id,
        "pool_kind": pool.kind,
        "pool_price": pool.price,
        "price": round(pool.price, 8),
        "wick_extreme": round(wick_extreme, 8),
        "time": candle.timestamp.isoformat(),
        "timestamp": int(candle.timestamp.timestamp()),
        "return_at": return_candle.timestamp.isoformat(),
        "return_candles": return_offset,
        "depth_pct": round(depth / pool.price * 100, 4),
        "depth_atr": round(depth_atr, 3),
        "volume_ratio": round(volume_ratio, 3),
        "volume_confirmed": volume_confirmed,
        "confirmed": volume_confirmed,
        "status": "confirmed" if volume_confirmed else "unconfirmed",
        "confidence": confidence,
        "grade": _depth_grade(depth_atr),
        "expected_move": expected_move,
        "wyckoff_equivalent": wyckoff_equivalent,
        "delta_aligned": delta_aligned,
        "label": label,
        "basis": f"{pool.label} 관통 후 {return_offset + 1}캔들 내 몸통이 경계 안쪽으로 복귀",
        "components": components,
    }


def _return_offset(candles: list[MarketCandle], index: int, pool: LiquidityPool, max_return_candles: int) -> int | None:
    for offset in range(0, max_return_candles + 1):
        if index + offset >= len(candles):
            break
        close = candles[index + offset].close
        if pool.side == "buy_side" and close < pool.price:
            return offset
        if pool.side == "sell_side" and close > pool.price:
            return offset
    return None


def _confidence_components(depth: float, candle_atr: float, volume_ratio: float, return_offset: int, pool_score: int, delta_aligned: bool | None) -> dict[str, int]:
    volume_score = _volume_score(volume_ratio)
    if delta_aligned is True:
        volume_score = min(35, volume_score + 5)
    return {
        "depth_significance": _depth_score(depth, candle_atr),
        "volume_confirmation": volume_score,
        "return_speed": max(0, 20 - return_offset * 6),
        "pool_quality": clamp_int(pool_score / 10, 0, 10),
    }


def _depth_score(depth: float, candle_atr: float) -> int:
    if candle_atr <= 0:
        return 0
    ratio = depth / candle_atr
    if ratio > 0.8:
        return 35
    if ratio >= 0.3:
        return clamp_int(16 + (ratio - 0.3) / 0.5 * 14, 16, 30)
    return clamp_int(ratio / 0.3 * 15, 1, 15)


def _volume_score(volume_ratio: float) -> int:
    if volume_ratio < VOLUME_CONFIRMATION_RATIO:
        return 0
    return clamp_int(20 + min(1.5, volume_ratio - VOLUME_CONFIRMATION_RATIO) / 1.5 * 15, 20, 35)


def _depth_grade(depth_atr: float) -> str:
    if depth_atr > 0.8:
        return "Strong"
    if depth_atr >= 0.3:
        return "Mid"
    return "Weak"


def _cap_sweeps(events: Iterable[dict[str, Any]], max_per_side: int = 3) -> list[dict[str, Any]]:
    capped = []
    for side in ("buy_side", "sell_side"):
        side_events = [event for event in events if event.get("side") == side]
        capped.extend(sorted(side_events, key=lambda item: (item["timestamp"], item["confidence"]), reverse=True)[:max_per_side])
    return sorted(capped, key=lambda item: (item["timestamp"], item["confidence"]), reverse=True)


def _delta_aligned(trade_flow: dict[str, Any] | None, candle: MarketCandle, expected_move: str) -> bool | None:
    if not isinstance(trade_flow, dict) or not trade_flow.get("data_available"):
        return None
    buckets = trade_flow.get("buckets")
    if not isinstance(buckets, list):
        return None
    candle_ts = int(candle.timestamp.timestamp())
    bucket = next((item for item in buckets if isinstance(item, dict) and int(item.get("time", -1)) == candle_ts), None)
    if bucket is None or not isinstance(bucket.get("delta"), (int, float)):
        return None
    delta = float(bucket["delta"])
    return delta > 0 if expected_move == "up" else delta < 0


def _daily_ranges(candles: list[MarketCandle]) -> list[dict[str, Any]]:
    by_day: dict[str, list[MarketCandle]] = {}
    for candle in candles:
        by_day.setdefault(candle.timestamp.date().isoformat(), []).append(candle)
    ranges = []
    for day, items in sorted(by_day.items()):
        ranges.append(
            {
                "date": day,
                "start": items[0].timestamp,
                "end": items[-1].timestamp,
                "high": max(candle.high for candle in items),
                "low": min(candle.low for candle in items),
            }
        )
    return ranges


def _previous_daily_range(daily: list[dict[str, Any]], candle: MarketCandle) -> dict[str, Any] | None:
    day = candle.timestamp.date().isoformat()
    previous = None
    for item in daily:
        if item["date"] >= day:
            return previous
        previous = item
    return previous
