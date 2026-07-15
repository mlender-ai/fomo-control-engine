from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any, Literal

from pydantic import BaseModel, Field

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


class BitgetAccountFill(BaseModel):
    trade_id: str
    order_id: str | None = None
    symbol: str
    price: float
    size: float
    quote_volume: float | None = None
    side: Literal["buy", "sell"]
    trade_side: str
    position_mode: str | None = None
    profit: float | None = None
    fee_usdt: float = 0.0
    fee_detail: list[dict[str, Any]] = Field(default_factory=list)
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


def parse_account_fill(row: dict[str, Any], margin_coin: str = "USDT") -> BitgetAccountFill:
    try:
        side = str(row.get("side", "")).strip().lower()
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        trade_id = str(row.get("tradeId") or "")
        if not trade_id:
            raise ValueError("tradeId is missing")
        fee_detail = [item for item in row.get("feeDetail", []) if isinstance(item, dict)]
        fee_usdt = sum(abs(float(item.get("totalFee") or 0.0)) for item in fee_detail if str(item.get("feeCoin") or "").upper() == margin_coin.upper())
        return BitgetAccountFill(
            trade_id=trade_id,
            order_id=str(row.get("orderId")) if row.get("orderId") is not None else None,
            symbol=str(row.get("symbol") or "").upper(),
            price=float(row["price"]),
            size=float(row.get("baseVolume") or row.get("size")),
            quote_volume=_optional_float(row.get("quoteVolume")),
            side=side,  # type: ignore[arg-type]
            trade_side=str(row.get("tradeSide") or "").strip().lower(),
            position_mode=str(row.get("posMode")) if row.get("posMode") is not None else None,
            profit=_optional_float(row.get("profit")),
            fee_usdt=round(fee_usdt, 12),
            fee_detail=fee_detail,
            timestamp=datetime.fromtimestamp(int(row["cTime"]) / 1000, tz=timezone.utc),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BitgetAPIError("invalid_account_fill", "Invalid Bitget account fill row.") from exc


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


def event_cvd_series_from_fills(fills: list[BitgetTradeFill], max_points: int = 24) -> list[dict]:
    """Return an event-time CVD series when coarse candles collapse fills into one bucket.

    The groups contain consecutive real fills and never affect candle-confirmed scoring.
    They exist only to make the observed trade flow inspectable in the UI.
    """
    ordered = sorted(fills, key=lambda item: (item.timestamp, item.trade_id))
    if not ordered or max_points <= 0:
        return []
    chunk_size = max(1, ceil(len(ordered) / max_points))
    cumulative = 0.0
    series: list[dict] = []
    for offset in range(0, len(ordered), chunk_size):
        chunk = ordered[offset : offset + chunk_size]
        delta = sum(fill.size if fill.side == "buy" else -fill.size for fill in chunk)
        cumulative += delta
        series.append(
            {
                "time": int(chunk[-1].timestamp.timestamp()),
                "value": round(cumulative, 8),
                "delta": round(delta, 8),
                "trades": len(chunk),
                "method": "event_time_fills",
            }
        )
    return series


def timeframe_seconds(timeframe: str) -> int:
    return TIMEFRAME_SECONDS.get(timeframe.lower(), 14400)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None and str(value) != "" else None
    except (TypeError, ValueError):
        return None
