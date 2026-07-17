from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.models import LiquidationEvent, MarketCandle, utc_now


RANGE_HOURS = {"12H": 12, "24H": 24, "3D": 72, "1W": 168, "1M": 24 * 30}
SIZE_FILTERS = {"all", "q2_plus", "q3_plus", "q4", "10x", "25x", "50x", "100x"}
LEVERAGE_MINIMUMS = {"10x": 10.0, "25x": 25.0, "50x": 50.0, "100x": 100.0}


def build_realized_liquidation_heatmap(
    events: list[LiquidationEvent],
    symbol: str,
    *,
    current_price: float | None = None,
    window_hours: int = 72,
    time_bins: int = 48,
    price_bins: int = 40,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized = symbol.upper()
    bounded_window = 24 if window_hours <= 24 else 72
    bounded_time_bins = max(12, min(time_bins, 96))
    bounded_price_bins = max(16, min(price_bins, 80))
    as_of = _as_utc(now or utc_now())
    window_start = as_of - timedelta(hours=bounded_window)
    observed = [
        item
        for event in events
        if event.symbol.upper() == normalized
        and event.source == "bitget"
        and window_start <= _as_utc(event.bucket_start) <= as_of
        and (item := _observed_event(event)) is not None
    ]
    observed.sort(key=lambda item: item["timestamp"])
    if not observed:
        return _empty_heatmap(normalized, current_price, bounded_window, window_start, as_of)

    prices = [item["price"] for item in observed]
    price_low, price_high = _price_range(prices)
    price_step = (price_high - price_low) / bounded_price_bins
    time_step_seconds = bounded_window * 3600 / bounded_time_bins
    cell_totals: dict[tuple[int, int], dict[str, float]] = defaultdict(lambda: {"long": 0.0, "short": 0.0, "amount": 0.0, "events": 0.0})
    zone_totals: dict[int, dict[str, float]] = defaultdict(lambda: {"long": 0.0, "short": 0.0, "amount": 0.0, "events": 0.0})
    for item in observed:
        elapsed = (_as_utc(item["timestamp"]) - window_start).total_seconds()
        time_index = min(bounded_time_bins - 1, max(0, int(elapsed / time_step_seconds)))
        price_index = min(bounded_price_bins - 1, max(0, int((item["price"] - price_low) / price_step)))
        side = item["position_side"]
        cell = cell_totals[(time_index, price_index)]
        zone = zone_totals[price_index]
        for target in (cell, zone):
            target[side] += item["notional_usd_estimated"]
            target["amount"] += item["amount"]
            target["events"] += 1

    max_total = max(values["long"] + values["short"] for values in cell_totals.values())
    cells = []
    for (time_index, price_index), values in sorted(cell_totals.items()):
        total = values["long"] + values["short"]
        cells.append(
            {
                "time": (window_start + timedelta(seconds=time_index * time_step_seconds)).isoformat(),
                "time_index": time_index,
                "price_index": price_index,
                "price_low": price_low + price_index * price_step,
                "price_high": price_low + (price_index + 1) * price_step,
                "long_usd_estimated": round(values["long"], 2),
                "short_usd_estimated": round(values["short"], 2),
                "total_usd_estimated": round(total, 2),
                "raw_amount": round(values["amount"], 8),
                "events": int(values["events"]),
                "intensity": round(math.log1p(total) / math.log1p(max_total), 4) if max_total > 0 else 0.0,
                "dominant_side": "long" if values["long"] >= values["short"] else "short",
            }
        )

    total_long = sum(item["notional_usd_estimated"] for item in observed if item["position_side"] == "long")
    total_short = sum(item["notional_usd_estimated"] for item in observed if item["position_side"] == "short")
    total_notional = total_long + total_short
    zones = []
    for price_index, values in zone_totals.items():
        total = values["long"] + values["short"]
        zones.append(
            {
                "price_low": price_low + price_index * price_step,
                "price_high": price_low + (price_index + 1) * price_step,
                "price_mid": price_low + (price_index + 0.5) * price_step,
                "long_usd_estimated": round(values["long"], 2),
                "short_usd_estimated": round(values["short"], 2),
                "total_usd_estimated": round(total, 2),
                "raw_amount": round(values["amount"], 8),
                "events": int(values["events"]),
                "share_pct": round(total / total_notional * 100, 2) if total_notional else 0.0,
                "dominant_side": "long" if values["long"] >= values["short"] else "short",
            }
        )
    zones.sort(key=lambda item: item["total_usd_estimated"], reverse=True)
    latest_event_at = _as_utc(observed[-1]["timestamp"])
    return {
        "symbol": normalized,
        "mode": "realized_liquidations",
        "source": "bitget_public_rest",
        "source_status": "ok",
        "as_of": as_of.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": as_of.isoformat(),
        "latest_event_at": latest_event_at.isoformat(),
        "window_hours": bounded_window,
        "sample_size": len(observed),
        "current_price": current_price,
        "price_low": price_low,
        "price_high": price_high,
        "price_bin_size": price_step,
        "price_bins": bounded_price_bins,
        "time_bins": bounded_time_bins,
        "cells": cells,
        "top_zones": zones[:5],
        "summary": {
            "long_usd_estimated": round(total_long, 2),
            "short_usd_estimated": round(total_short, 2),
            "total_usd_estimated": round(total_notional, 2),
            "dominant_side": "long" if total_long >= total_short else "short",
        },
        "coverage": {
            "history_limit": "Bitget public REST retains up to the latest 3 days.",
            "observed_fields": ["price", "side", "amount", "timestamp"],
            "notional_method": "price_x_amount",
            "notional_estimated": True,
        },
        "notes": [
            "실제로 발생한 Bitget 청산 주문의 가격·시각 분포입니다.",
            "Coinglass 예상 청산 레버리지 맵과 다르며 미래 청산 가격대를 예측하지 않습니다.",
            "강도는 price × amount 추정 명목액의 로그 스케일입니다. REST 문서는 amount 단위를 명시하지 않습니다.",
            "관측 전용이며 Entry Score·방향 판정·자동 진입에 사용하지 않습니다.",
        ],
    }


def build_unified_liquidation_heatmap(
    events: list[LiquidationEvent],
    candles: list[MarketCandle],
    symbol: str,
    *,
    timeframe_seconds: int,
    range_key: str = "3D",
    side: str = "all",
    size_filter: str = "all",
    min_size: float | None = None,
    mode: str = "persist",
    price_bins: int = 120,
    source: str = "realized",
    from_at: datetime | None = None,
    to_at: datetime | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the shared time×price raster used by the main chart.

    Values stay as observed/estimated notional totals. Persistence is a visual
    history transform only: an event is repeated to the right until a later,
    confirmed candle trades through its observed price.
    """
    normalized = symbol.upper()
    as_of = _as_utc(now or utc_now())
    bounded_step = max(60, int(timeframe_seconds))
    bounded_bins = max(32, min(int(price_bins), 240))
    normalized_range = range_key.upper() if range_key.upper() in RANGE_HOURS else "3D"
    window_end = min(_as_utc(to_at), as_of) if to_at else as_of
    window_start = _as_utc(from_at) if from_at else window_end - timedelta(hours=RANGE_HOURS[normalized_range])
    if window_start >= window_end:
        window_start = window_end - timedelta(hours=RANGE_HOURS[normalized_range])
    aligned_start = _floor_time(window_start, bounded_step)
    aligned_end = _floor_time(window_end, bounded_step) + timedelta(seconds=bounded_step)
    time_buckets = _time_buckets(aligned_start, aligned_end, bounded_step)

    if source == "coinglass_est":
        return _empty_unified_heatmap(
            normalized,
            time_buckets,
            bounded_bins,
            aligned_start,
            aligned_end,
            normalized_range,
            side,
            size_filter,
            mode,
            source="coinglass_est",
            status="locked",
            candles=candles,
            note="Coinglass 추정 청산 어댑터는 준비됐지만 수집 데이터는 아직 연결되지 않았습니다.",
        )

    observed_all = [
        item
        for event in events
        if event.symbol.upper() == normalized
        and event.source == "bitget"
        and aligned_start <= _as_utc(event.bucket_start) < aligned_end
        and (item := _observed_event(event)) is not None
    ]
    observed_all.sort(key=lambda item: item["timestamp"])
    leverages = [item["leverage"] for item in observed_all if item.get("leverage") is not None]
    leverage_available = bool(leverages) and len(leverages) == len(observed_all)
    quartiles = _quartile_thresholds([item["notional_usd_estimated"] for item in observed_all])
    selected = [item for item in observed_all if side == "all" or item["position_side"] == side]
    minimum = max(0.0, float(min_size or 0.0))
    normalized_size = size_filter if size_filter in SIZE_FILTERS else "all"
    if leverage_available and normalized_size in LEVERAGE_MINIMUMS:
        selected = [item for item in selected if (item.get("leverage") or 0.0) >= LEVERAGE_MINIMUMS[normalized_size]]
    elif normalized_size != "all" and normalized_size in quartiles:
        minimum = max(minimum, quartiles[normalized_size])
    selected = [item for item in selected if item["notional_usd_estimated"] >= minimum]

    visible_candles = [
        candle
        for candle in candles
        if aligned_start <= _as_utc(candle.timestamp) < aligned_end and _as_utc(candle.timestamp) + timedelta(seconds=bounded_step) <= as_of
    ]
    prices = [item["price"] for item in selected]
    candle_prices = [price for candle in visible_candles for price in (candle.low, candle.high)]
    if not prices and not candle_prices:
        return _empty_unified_heatmap(
            normalized,
            time_buckets,
            bounded_bins,
            aligned_start,
            aligned_end,
            normalized_range,
            side,
            normalized_size,
            mode,
            source="bitget_realized",
            status="empty",
            candles=candles,
            quartiles=quartiles,
            available_events=len(observed_all),
        )

    domain_prices = prices + candle_prices
    price_low, price_high = _price_range(domain_prices)
    price_step = (price_high - price_low) / bounded_bins
    grid = [[0.0 for _ in range(bounded_bins)] for _ in time_buckets]
    side_grid = {
        "long": [[0.0 for _ in range(bounded_bins)] for _ in time_buckets],
        "short": [[0.0 for _ in range(bounded_bins)] for _ in time_buckets],
    }
    candle_by_bucket = {_bucket_index(_as_utc(candle.timestamp), aligned_start, bounded_step): candle for candle in visible_candles}
    original_events: list[dict[str, Any]] = []
    zone_totals: dict[int, dict[str, float]] = defaultdict(lambda: {"long": 0.0, "short": 0.0, "events": 0.0})
    for item in selected:
        time_index = _bucket_index(_as_utc(item["timestamp"]), aligned_start, bounded_step)
        if time_index < 0 or time_index >= len(time_buckets):
            continue
        price_index = min(bounded_bins - 1, max(0, int((item["price"] - price_low) / price_step)))
        last_index = time_index
        ended_at: datetime | None = None
        if mode == "persist":
            for candidate_index in range(time_index + 1, len(time_buckets)):
                candle = candle_by_bucket.get(candidate_index)
                if candle is not None and candle.low <= item["price"] <= candle.high:
                    ended_at = aligned_start + timedelta(seconds=candidate_index * bounded_step)
                    break
                last_index = candidate_index
        amount = float(item["notional_usd_estimated"])
        for target_index in range(time_index, last_index + 1):
            grid[target_index][price_index] += amount
            side_grid[item["position_side"]][target_index][price_index] += amount
        zone = zone_totals[price_index]
        zone[item["position_side"]] += amount
        zone["events"] += 1
        original_events.append(
            {
                "timestamp": _as_utc(item["timestamp"]).isoformat(),
                "price": item["price"],
                "size_usd_estimated": round(amount, 2),
                "raw_amount": item["amount"],
                "side": item["position_side"],
                "leverage": item.get("leverage"),
                "persisted_until": ended_at.isoformat() if ended_at else None,
            }
        )

    rounded_grid = [[round(value, 2) for value in row] for row in grid]
    max_value = max((max(row) for row in rounded_grid), default=0.0)
    long_total = sum(item["notional_usd_estimated"] for item in selected if item["position_side"] == "long")
    short_total = sum(item["notional_usd_estimated"] for item in selected if item["position_side"] == "short")
    current_price = visible_candles[-1].close if visible_candles else None
    above_total = sum(item["notional_usd_estimated"] for item in selected if current_price is not None and item["price"] >= current_price)
    below_total = sum(item["notional_usd_estimated"] for item in selected if current_price is not None and item["price"] < current_price)
    zones = []
    for price_index, values in zone_totals.items():
        total = values["long"] + values["short"]
        zones.append(
            {
                "price_index": price_index,
                "price_low": round(price_low + price_index * price_step, 8),
                "price_high": round(price_low + (price_index + 1) * price_step, 8),
                "price_mid": round(price_low + (price_index + 0.5) * price_step, 8),
                "total_usd_estimated": round(total, 2),
                "long_usd_estimated": round(values["long"], 2),
                "short_usd_estimated": round(values["short"], 2),
                "events": int(values["events"]),
            }
        )
    zones.sort(key=lambda item: item["total_usd_estimated"], reverse=True)
    return {
        "symbol": normalized,
        "source": "bitget_realized",
        "source_status": "ok" if selected else "empty",
        "truth_label": "실제 청산 · 예상 아님",
        "timeframe_seconds": bounded_step,
        "range": normalized_range,
        "window_start": aligned_start.isoformat(),
        "window_end": aligned_end.isoformat(),
        "time_buckets": [bucket.isoformat() for bucket in time_buckets],
        "price_bins": {"count": bounded_bins, "min": price_low, "max": price_high, "step": price_step},
        "grid": rounded_grid,
        "side_grid": {key: [[round(value, 2) for value in row] for row in value] for key, value in side_grid.items()},
        "max_value_usd_estimated": round(max_value, 2),
        "n_events": len(selected),
        "available_events": len(observed_all),
        "sample_low": len(selected) < 30,
        "side_split": {"long_usd_estimated": round(long_total, 2), "short_usd_estimated": round(short_total, 2)},
        "position_split": {"above_usd_estimated": round(above_total, 2), "below_usd_estimated": round(below_total, 2)},
        "last_event_ts": original_events[-1]["timestamp"] if original_events else None,
        "top_zones": zones[:3],
        "events": original_events,
        "filters": {
            "side": side,
            "size": normalized_size,
            "min_size_usd": minimum,
            "mode": "persist" if mode == "persist" else "event",
            "filter_basis": "leverage" if leverage_available and normalized_size not in quartiles else "size_quartile",
            "leverage_available": leverage_available,
            "leverage_minimum": LEVERAGE_MINIMUMS.get(normalized_size),
            "available_thresholds": ["all", "10x", "25x", "50x", "100x"] if leverage_available else ["all", "q2_plus", "q3_plus", "q4"],
            "quartile_thresholds_usd": {key: round(value, 2) for key, value in quartiles.items()},
        },
        "rendering": {"normalization": "log1p", "default_opacity": 0.55, "raw_values_preserved": True},
        "coverage": {
            "history_limit": "Bitget public REST retains up to the latest 3 days.",
            "observed_fields": ["price", "side", "amount", "timestamp"],
            "notional_method": "price_x_amount",
            "notional_estimated": True,
        },
        "notes": [
            "실현 이벤트를 차트 캔들 경계와 같은 시간축에 집계했습니다.",
            "persist는 이벤트 이후 확정 캔들이 해당 가격을 재통과하기 전까지만 시각적으로 유지합니다.",
            "관측 전용이며 Entry Score·방향 판정·자동 진입에 사용하지 않습니다.",
        ],
    }


def _observed_event(event: LiquidationEvent) -> dict[str, Any] | None:
    raw = event.raw_json if isinstance(event.raw_json, dict) else {}
    try:
        price = float(raw.get("price"))
        amount = float(raw.get("amount"))
    except (TypeError, ValueError):
        return None
    side = str(raw.get("position_side") or "").lower()
    if price <= 0 or amount <= 0 or side not in {"long", "short"}:
        return None
    notional = event.long_liquidation_usd + event.short_liquidation_usd
    if notional <= 0:
        notional = price * amount
    return {
        "timestamp": event.bucket_start,
        "price": price,
        "amount": amount,
        "position_side": side,
        "notional_usd_estimated": notional,
        "leverage": _optional_positive_float(raw.get("leverage")),
    }


def _optional_positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _quartile_thresholds(values: list[float]) -> dict[str, float]:
    ordered = sorted(value for value in values if value > 0)
    if not ordered:
        return {"q2_plus": 0.0, "q3_plus": 0.0, "q4": 0.0}

    def percentile(ratio: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * ratio
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    return {
        "q2_plus": percentile(0.25),
        "q3_plus": percentile(0.5),
        "q4": percentile(0.75),
    }


def _floor_time(value: datetime, step_seconds: int) -> datetime:
    timestamp = int(_as_utc(value).timestamp())
    return datetime.fromtimestamp(timestamp - timestamp % step_seconds, tz=timezone.utc)


def _time_buckets(start: datetime, end: datetime, step_seconds: int) -> list[datetime]:
    count = max(1, min(744, math.ceil((end - start).total_seconds() / step_seconds)))
    return [start + timedelta(seconds=index * step_seconds) for index in range(count)]


def _bucket_index(value: datetime, start: datetime, step_seconds: int) -> int:
    return math.floor((_as_utc(value) - start).total_seconds() / step_seconds)


def _empty_unified_heatmap(
    symbol: str,
    time_buckets: list[datetime],
    price_bin_count: int,
    window_start: datetime,
    window_end: datetime,
    range_key: str,
    side: str,
    size_filter: str,
    mode: str,
    *,
    source: str,
    status: str,
    candles: list[MarketCandle],
    note: str = "선택 범위에 실현 청산 표본이 없습니다.",
    quartiles: dict[str, float] | None = None,
    available_events: int = 0,
) -> dict[str, Any]:
    candle_prices = [price for candle in candles for price in (candle.low, candle.high)]
    if candle_prices:
        price_low, price_high = _price_range(candle_prices)
    else:
        price_low, price_high = 0.0, 1.0
    price_step = (price_high - price_low) / price_bin_count
    empty_grid = [[0.0 for _ in range(price_bin_count)] for _ in time_buckets]
    thresholds = quartiles or {"q2_plus": 0.0, "q3_plus": 0.0, "q4": 0.0}
    return {
        "symbol": symbol,
        "source": source,
        "source_status": status,
        "truth_label": "추정 청산 · 실현과 분리" if source == "coinglass_est" else "실제 청산 · 예상 아님",
        "timeframe_seconds": int((time_buckets[1] - time_buckets[0]).total_seconds()) if len(time_buckets) > 1 else None,
        "range": range_key,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "time_buckets": [bucket.isoformat() for bucket in time_buckets],
        "price_bins": {"count": price_bin_count, "min": price_low, "max": price_high, "step": price_step},
        "grid": empty_grid,
        "side_grid": {"long": empty_grid, "short": empty_grid},
        "max_value_usd_estimated": 0.0,
        "n_events": 0,
        "available_events": available_events,
        "sample_low": True,
        "side_split": {"long_usd_estimated": 0.0, "short_usd_estimated": 0.0},
        "position_split": {"above_usd_estimated": 0.0, "below_usd_estimated": 0.0},
        "last_event_ts": None,
        "top_zones": [],
        "events": [],
        "filters": {
            "side": side,
            "size": size_filter,
            "min_size_usd": 0.0,
            "mode": "persist" if mode == "persist" else "event",
            "filter_basis": "size_quartile",
            "leverage_available": False,
            "leverage_minimum": None,
            "available_thresholds": ["all", "q2_plus", "q3_plus", "q4"],
            "quartile_thresholds_usd": {key: round(value, 2) for key, value in thresholds.items()},
        },
        "rendering": {"normalization": "log1p", "default_opacity": 0.55, "raw_values_preserved": True},
        "coverage": {
            "history_limit": "Bitget public REST retains up to the latest 3 days." if source != "coinglass_est" else "adapter_only",
            "notional_estimated": source != "coinglass_est",
        },
        "notes": [note, "관측 전용이며 Entry Score·방향 판정·자동 진입에 사용하지 않습니다."],
    }


def _price_range(prices: list[float]) -> tuple[float, float]:
    lowest = min(prices)
    highest = max(prices)
    spread = highest - lowest
    padding = max(spread * 0.04, max(abs(highest), 1.0) * 0.002)
    return lowest - padding, highest + padding


def _empty_heatmap(
    symbol: str,
    current_price: float | None,
    window_hours: int,
    window_start: datetime,
    as_of: datetime,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "mode": "realized_liquidations",
        "source": "bitget_public_rest",
        "source_status": "empty",
        "as_of": as_of.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": as_of.isoformat(),
        "latest_event_at": None,
        "window_hours": window_hours,
        "sample_size": 0,
        "current_price": current_price,
        "price_low": None,
        "price_high": None,
        "price_bin_size": None,
        "price_bins": 40,
        "time_bins": 48,
        "cells": [],
        "top_zones": [],
        "summary": {
            "long_usd_estimated": 0.0,
            "short_usd_estimated": 0.0,
            "total_usd_estimated": 0.0,
            "dominant_side": None,
        },
        "coverage": {
            "history_limit": "Bitget public REST retains up to the latest 3 days.",
            "observed_fields": ["price", "side", "amount", "timestamp"],
            "notional_method": "price_x_amount",
            "notional_estimated": True,
        },
        "notes": [
            "선택 범위에 실현 청산 표본이 없거나 아직 공개 이력을 수집하지 않았습니다.",
            "미래 예상 청산 가격대를 뜻하지 않습니다.",
        ],
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
