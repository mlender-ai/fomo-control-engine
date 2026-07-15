from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.db.models import LiquidationEvent, utc_now
from app.exchange.bitget.provider import BitgetMarketDataProvider


def collect_bitget_liquidations(
    provider: object,
    symbol: str,
    *,
    max_pages: int = 3,
) -> list[LiquidationEvent]:
    if not isinstance(provider, BitgetMarketDataProvider):
        return []
    rows = provider.get_liquidation_history(symbol, max_pages=max_pages)
    events = [event for row in rows if (event := parse_bitget_liquidation(row, symbol)) is not None]
    return sorted(events, key=lambda item: item.bucket_start, reverse=True)


def parse_bitget_liquidation(row: dict[str, Any], symbol: str) -> LiquidationEvent | None:
    normalized = str(row.get("symbol") or symbol).upper()
    side = str(row.get("side") or "").lower()
    price = _positive_float(row.get("price"))
    amount = _positive_float(row.get("amount"))
    timestamp = _timestamp_ms(row.get("ts"))
    if side not in {"buy", "sell"} or price is None or amount is None or timestamp is None:
        return None

    position_side = "long" if side == "buy" else "short"
    estimated_notional = price * amount
    stable_key = f"fce:liquidation:{normalized}:bitget:{int(timestamp.timestamp() * 1000)}:{side}:{price}:{amount}"
    return LiquidationEvent(
        id=uuid5(NAMESPACE_URL, stable_key),
        symbol=normalized,
        source="bitget",
        interval="event",
        bucket_start=timestamp,
        long_liquidation_usd=estimated_notional if position_side == "long" else 0.0,
        short_liquidation_usd=estimated_notional if position_side == "short" else 0.0,
        source_status="ok",
        data_quality={
            "source": "bitget_public_rest",
            "price_observed": True,
            "side_observed": True,
            "notional_estimated": True,
            "notional_method": "price_x_amount",
        },
        raw_json={
            "price": price,
            "amount": amount,
            "exchange_side": side,
            "position_side": position_side,
            "notional_usd_estimated": estimated_notional,
            "amount_unit": "not_specified_by_rest_documentation",
            "ts": int(timestamp.timestamp() * 1000),
        },
        created_at=utc_now(),
    )


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _timestamp_ms(value: Any) -> datetime | None:
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return None
    if milliseconds <= 0:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
