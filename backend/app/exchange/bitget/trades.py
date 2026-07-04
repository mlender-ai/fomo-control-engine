from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel

from app.db.models import MarketCandle
from app.exchange.bitget.errors import BitgetAPIError


TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}


class BitgetTradeFill(BaseModel):
    trade_id: str
    symbol: str
    price: float
    size: float
    side: Literal["buy", "sell"]
    timestamp: datetime


class TradeFlowBucket(BaseModel):
    time: int
    bucket_start: datetime
    bucket_end: datetime
    buy_volume: float
    sell_volume: float
    delta: float
    trades: int
    method: Literal["trade_fills"] = "trade_fills"


def parse_trade_fill(row: dict[str, Any], symbol: str) -> BitgetTradeFill:
    try:
        side = str(row.get("side", "")).strip().lower()
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        trade_id = str(row.get("tradeId") or row.get("id") or "")
        if not trade_id:
            raise ValueError("tradeId is missing")
        return BitgetTradeFill(
            trade_id=trade_id,
            symbol=str(row.get("symbol", symbol)).upper(),
            price=float(row["price"]),
            size=float(row["size"]),
            side=side,  # type: ignore[arg-type]
            timestamp=datetime.fromtimestamp(int(row["ts"]) / 1000, tz=timezone.utc),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BitgetAPIError("invalid_trade_fill", "Invalid Bitget trade fill row.") from exc


def aggregate_trade_buckets(fills: list[BitgetTradeFill], candles: list[MarketCandle], timeframe: str) -> list[TradeFlowBucket]:
    if not fills or not candles:
        return []
    ordered_candles = sorted(candles, key=lambda candle: candle.timestamp)
    starts = [candle.timestamp for candle in ordered_candles]
    seconds = timeframe_seconds(timeframe)
    raw: dict[datetime, dict[str, float | int | datetime]] = {}

    for fill in sorted(fills, key=lambda item: item.timestamp):
        index = bisect_right(starts, fill.timestamp) - 1
        if index < 0:
            continue
        bucket_start = starts[index]
        next_start = starts[index + 1] if index + 1 < len(starts) else bucket_start + timedelta(seconds=seconds)
        if fill.timestamp >= next_start:
            continue
        bucket = raw.setdefault(
            bucket_start,
            {
                "bucket_start": bucket_start,
                "bucket_end": next_start,
                "buy_volume": 0.0,
                "sell_volume": 0.0,
                "trades": 0,
            },
        )
        key = "buy_volume" if fill.side == "buy" else "sell_volume"
        bucket[key] = float(bucket[key]) + fill.size
        bucket["trades"] = int(bucket["trades"]) + 1

    buckets = []
    for item in raw.values():
        buy_volume = float(item["buy_volume"])
        sell_volume = float(item["sell_volume"])
        bucket_start = item["bucket_start"]
        bucket_end = item["bucket_end"]
        assert isinstance(bucket_start, datetime)
        assert isinstance(bucket_end, datetime)
        buckets.append(
            TradeFlowBucket(
                time=int(bucket_start.timestamp()),
                bucket_start=bucket_start,
                bucket_end=bucket_end,
                buy_volume=round(buy_volume, 8),
                sell_volume=round(sell_volume, 8),
                delta=round(buy_volume - sell_volume, 8),
                trades=int(item["trades"]),
            )
        )
    return sorted(buckets, key=lambda bucket: bucket.bucket_start)


def cvd_series_from_buckets(buckets: list[TradeFlowBucket]) -> list[dict]:
    cumulative = 0.0
    series = []
    for bucket in sorted(buckets, key=lambda item: item.bucket_start):
        cumulative += bucket.delta
        series.append(
            {
                "time": bucket.time,
                "value": round(cumulative, 8),
                "delta": bucket.delta,
                "method": bucket.method,
            }
        )
    return series


def timeframe_seconds(timeframe: str) -> int:
    return TIMEFRAME_SECONDS.get(timeframe.lower(), 14400)
