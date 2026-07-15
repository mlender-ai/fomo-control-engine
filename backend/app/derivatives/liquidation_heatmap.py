from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.models import LiquidationEvent, utc_now


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
