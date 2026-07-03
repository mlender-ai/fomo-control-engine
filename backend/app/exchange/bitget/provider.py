from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.db.models import DataQuality, MarketCandle, MarketSnapshot
from app.exchange.base import MarketDataProvider
from app.exchange.bitget.client import BitgetClient
from app.exchange.bitget.errors import BitgetAPIError
from app.exchange.bitget.schemas import BitgetPosition, Candle, FundingRate, OpenInterest


TIMEFRAME_MAP = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1H",
    "2h": "2H",
    "4h": "4H",
    "6h": "6H",
    "12h": "12H",
    "1d": "1D",
    "1w": "1W",
}


class BitgetMarketDataProvider(MarketDataProvider):
    name = "bitget"

    def __init__(self, client: BitgetClient, product_type: str = "USDT-FUTURES", margin_coin: str = "USDT") -> None:
        self.client = client
        self.product_type = product_type.upper()
        self.margin_coin = margin_coin.upper()

    def get_snapshot(self, symbol: str, timeframe: str = "4h") -> MarketSnapshot:
        return _run(self.get_market_snapshot(symbol, timeframe))

    def get_positions(self) -> list[BitgetPosition]:
        return _run(self.get_account_positions())

    async def get_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> list[Candle]:
        granularity = TIMEFRAME_MAP.get(timeframe.lower())
        if granularity is None:
            raise BitgetAPIError("unsupported_timeframe", f"Unsupported Bitget timeframe: {timeframe}")

        payload = await self.client.public_get(
            "/api/v2/mix/market/candles",
            {
                "symbol": normalize_symbol(symbol),
                "productType": self.product_type,
                "granularity": granularity,
                "limit": str(limit),
            },
        )
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise BitgetAPIError("invalid_response", "Bitget candle response is missing data.")

        candles = []
        for index, row in enumerate(rows):
            try:
                if not isinstance(row, list) or len(row) < 6:
                    raise ValueError("row has insufficient fields")
                candles.append(
                    Candle(
                        timestamp=_timestamp_ms(row[0]),
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                        quote_volume=_optional_float(row[6]) if len(row) > 6 else None,
                    )
                )
            except (TypeError, ValueError) as exc:
                raise BitgetAPIError("invalid_candle", f"Invalid Bitget candle row at index {index}.") from exc
        return sorted(candles, key=lambda candle: candle.timestamp)

    async def get_funding_rate(self, symbol: str) -> FundingRate | None:
        payload = await self.client.public_get(
            "/api/v2/mix/market/current-fund-rate",
            {"symbol": normalize_symbol(symbol), "productType": self.product_type},
        )
        item = _first_dict(payload.get("data"))
        if item is None:
            return None
        return FundingRate(
            symbol=str(item.get("symbol", normalize_symbol(symbol))).upper(),
            funding_rate=_required_float(item.get("fundingRate"), "fundingRate"),
            funding_rate_interval_hours=_optional_int(item.get("fundingRateInterval")),
            next_update=_optional_timestamp_ms(item.get("nextUpdate")),
            min_funding_rate=_optional_float(item.get("minFundingRate")),
            max_funding_rate=_optional_float(item.get("maxFundingRate")),
        )

    async def get_open_interest(self, symbol: str) -> OpenInterest | None:
        payload = await self.client.public_get(
            "/api/v2/mix/market/open-interest",
            {"symbol": normalize_symbol(symbol), "productType": self.product_type},
        )
        data = payload.get("data")
        if not isinstance(data, dict):
            return None
        rows = data.get("openInterestList")
        item = _first_dict(rows)
        if item is None:
            return None
        return OpenInterest(
            symbol=str(item.get("symbol", normalize_symbol(symbol))).upper(),
            size=_required_float(item.get("size"), "size"),
            timestamp=_optional_timestamp_ms(data.get("ts")),
        )

    async def get_market_snapshot(self, symbol: str, timeframe: str) -> MarketSnapshot:
        normalized = normalize_symbol(symbol)
        candles = await self.get_ohlcv(normalized, timeframe, limit=200)
        funding = await self.get_funding_rate(normalized)
        open_interest = await self.get_open_interest(normalized)
        ticker = await self._get_ticker(normalized)
        if len(candles) < 30:
            raise BitgetAPIError("insufficient_candles", "Not enough candle data to calculate indicators.")

        market_candles = [
            MarketCandle(
                timestamp=candle.timestamp,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
                quote_volume=candle.quote_volume,
            )
            for candle in candles
        ]
        data_quality = DataQuality(
            ohlcv_ok=True,
            funding_ok=funding is not None,
            open_interest_ok=open_interest is not None,
            min_candles_met=len(candles) >= 30,
            fallback_used=False,
            candles=len(candles),
            last_candle_at=market_candles[-1].timestamp,
        )
        return MarketSnapshot(
            symbol=normalized,
            timeframe=timeframe,
            price=round(_required_float(ticker.get("lastPr"), "lastPr"), 8),
            change_24h=round(_required_float(ticker.get("change24h"), "change24h") * 100, 2),
            funding_rate=funding.funding_rate if funding else 0.0,
            open_interest_change=_estimate_open_interest_change(open_interest, ticker),
            candles=market_candles,
            provider=self.name,
            data_quality=data_quality,
        )

    async def get_account_positions(self) -> list[BitgetPosition]:
        payload = await self.client.private_get(
            "/api/v2/mix/position/all-position",
            {"productType": self.product_type, "marginCoin": self.margin_coin},
        )
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise BitgetAPIError("invalid_response", "Bitget positions response is missing data.")
        positions = [self._parse_position(row) for row in rows if isinstance(row, dict)]
        return [position for position in positions if position.total > 0]

    async def _get_ticker(self, symbol: str) -> dict[str, Any]:
        payload = await self.client.public_get(
            "/api/v2/mix/market/ticker",
            {"symbol": normalize_symbol(symbol), "productType": self.product_type},
        )
        item = _first_dict(payload.get("data"))
        if item is None:
            raise BitgetAPIError("invalid_response", "Bitget ticker response is missing data.")
        return item

    def _parse_position(self, row: dict[str, Any]) -> BitgetPosition:
        liquidation_price = _optional_float(row.get("liquidationPrice"))
        return BitgetPosition(
            symbol=str(row.get("symbol", "")).upper(),
            hold_side=str(row.get("holdSide", "long")).lower(),
            margin_coin=str(row.get("marginCoin", self.margin_coin)).upper(),
            total=_required_float(row.get("total"), "total"),
            available=_optional_float(row.get("available")),
            locked=_optional_float(row.get("locked")),
            leverage=_optional_float(row.get("leverage")),
            open_price_avg=_required_float(row.get("openPriceAvg"), "openPriceAvg"),
            mark_price=_optional_float(row.get("markPrice")),
            unrealized_pl=_optional_float(row.get("unrealizedPL")),
            liquidation_price=liquidation_price if liquidation_price and liquidation_price > 0 else None,
            margin_mode=_optional_string(row.get("marginMode")),
            position_mode=_optional_string(row.get("posMode")),
            margin_ratio=_optional_float(row.get("marginRatio")),
            break_even_price=_optional_float(row.get("breakEvenPrice")),
            created_at=_optional_timestamp_ms(row.get("cTime")),
            updated_at=_optional_timestamp_ms(row.get("uTime")),
        )


def normalize_symbol(symbol: str) -> str:
    return symbol.upper().replace("/", "").strip()


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("Bitget sync wrapper cannot be used inside an active event loop.")


def _first_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def _timestamp_ms(value: Any) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def _optional_timestamp_ms(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return _timestamp_ms(value)


def _required_float(value: Any, label: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise BitgetAPIError("invalid_number", f"Invalid Bitget numeric field: {label}.") from exc


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return _required_float(value, "optional")


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


def _optional_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _estimate_open_interest_change(open_interest: OpenInterest | None, ticker: dict[str, Any]) -> float:
    if open_interest is None or open_interest.size <= 0:
        return 0.0
    holding_amount = _optional_float(ticker.get("holdingAmount"))
    if holding_amount is None:
        return 0.0
    return round(((open_interest.size - holding_amount) / open_interest.size) * 100, 2)
