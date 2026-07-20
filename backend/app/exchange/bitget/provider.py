from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db.models import DataQuality, MarketCandle, MarketSnapshot
from app.exchange.base import MarketDataProvider
from app.exchange.bitget.client import BitgetClient
from app.exchange.bitget.errors import BitgetAPIError
from app.exchange.bitget.schemas import (
    BitgetPosition,
    Candle,
    FundingRate,
    OpenInterest,
)
from app.exchange.bitget.trade_cache import BitgetTradeFillCache
from app.exchange.bitget.trades import (
    BitgetAccountFill,
    BitgetTradeFill,
    aggregate_trade_buckets,
    cvd_series_from_buckets,
    event_cvd_series_from_fills,
    parse_account_fill,
    parse_trade_fill,
    timeframe_seconds,
)
from app.marketdata.assets import classify_asset_class, funding_interval_from_contract, source_category_from_contract


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

    def __init__(
        self,
        client: BitgetClient,
        product_type: str = "USDT-FUTURES",
        margin_coin: str = "USDT",
        trade_cache: BitgetTradeFillCache | None = None,
        trade_fill_lookback_hours: int = 96,
        trade_fill_cache_ttl_seconds: int = 60,
        trade_fill_max_rows: int = 50_000,
        snapshot_cache_ttl_seconds: int = 20,
    ) -> None:
        self.client = client
        self.product_type = product_type.upper()
        self.margin_coin = margin_coin.upper()
        self.trade_cache = trade_cache
        self.trade_fill_lookback_hours = trade_fill_lookback_hours
        self.trade_fill_cache_ttl_seconds = trade_fill_cache_ttl_seconds
        self.trade_fill_max_rows = max(1, trade_fill_max_rows)
        self.snapshot_cache_ttl_seconds = max(0, snapshot_cache_ttl_seconds)
        self._cache_guard = threading.Lock()
        self._snapshot_locks: dict[tuple[str, str], threading.Lock] = {}
        self._trade_flow_locks: dict[tuple[str, str], threading.Lock] = {}
        self._snapshot_cache: dict[tuple[str, str], tuple[float, MarketSnapshot]] = {}

    def get_snapshot(self, symbol: str, timeframe: str = "4h") -> MarketSnapshot:
        key = (normalize_symbol(symbol), timeframe.lower())
        cached = self._fresh_snapshot(key)
        if cached is not None:
            return cached
        lock = self._key_lock(self._snapshot_locks, key)
        with lock:
            cached = self._fresh_snapshot(key)
            if cached is not None:
                return cached
            snapshot = _run(self.get_market_snapshot(key[0], timeframe))
            self._snapshot_cache[key] = (time.monotonic(), snapshot)
            return snapshot

    def get_positions(self) -> list[BitgetPosition]:
        return _run(self.get_account_positions())

    def get_account_fills(self, start_time: datetime, end_time: datetime) -> list[BitgetAccountFill]:
        return _run(self.get_account_trade_fills(start_time, end_time))

    def get_trade_flow(self, symbol: str, timeframe: str, candles: list[MarketCandle]) -> dict:
        return _run(self.get_trade_flow_async(symbol, timeframe, candles))

    def get_spot_trade_flow(self, symbol: str, timeframe: str, candles: list[MarketCandle]) -> dict:
        return _run(self.get_spot_trade_flow_async(symbol, timeframe, candles))

    def get_derivative_snapshot(self, symbol: str, ratio_period: str = "5m") -> dict:
        return _run(self.get_derivative_snapshot_async(symbol, ratio_period))

    def get_liquidation_history(self, symbol: str, max_pages: int = 3) -> list[dict[str, Any]]:
        return _run(self.get_liquidation_history_async(symbol, max_pages=max_pages))

    def list_contracts(self) -> list[dict]:
        return _run(self.get_contracts())

    def list_tickers(self) -> list[dict]:
        return _run(self.get_tickers())

    def get_history_ohlcv(
        self,
        symbol: str,
        timeframe: str = "4h",
        limit: int = 2_196,
        *,
        now: datetime | None = None,
    ) -> list[Candle]:
        """Return paginated, de-duplicated, confirmed public candles.

        Bitget's regular candles endpoint is capped to the most recent page.
        The validation harness needs a longer immutable prefix, so this path
        deliberately uses the public history endpoint and never private API
        credentials.
        """

        return _run(self.get_history_ohlcv_async(symbol, timeframe, limit, now=now))

    async def get_contracts(self) -> list[dict]:
        payload = await self.client.public_get(
            "/api/v2/mix/market/contracts",
            {"productType": self.product_type},
        )
        rows = payload.get("data") or []
        contracts: list[dict] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("symbol"):
                continue
            contracts.append(
                {
                    "symbol": str(row["symbol"]).upper(),
                    "base_coin": str(row.get("baseCoin", "")),
                    "quote_coin": str(row.get("quoteCoin", "")),
                    "status": str(row.get("symbolStatus", "")),
                    "asset_class": classify_asset_class(
                        str(row["symbol"]).upper(),
                        str(row.get("baseCoin", "")),
                        str(row.get("quoteCoin", "")),
                        row,
                    ),
                    "source_category": source_category_from_contract(row),
                    "funding_rate_interval_hours": funding_interval_from_contract(row),
                    "raw_metadata": {
                        key: row.get(key)
                        for key in (
                            "symbolType",
                            "isRwa",
                            "fundInterval",
                            "maxLever",
                            "openTime",
                            "pricePlace",
                            "volumePlace",
                        )
                        if key in row
                    },
                    "maintenance_margin_rate": _first_float(
                        row,
                        (
                            "maintainMarginRate",
                            "maintenanceMarginRate",
                            "keepMarginRate",
                        ),
                    ),
                    "taker_fee_rate": _first_float(row, ("takerFeeRate", "takerFee")),
                }
            )
        return contracts

    async def get_tickers(self) -> list[dict]:
        payload = await self.client.public_get(
            "/api/v2/mix/market/tickers",
            {"productType": self.product_type},
        )
        rows = payload.get("data") or []
        tickers: list[dict] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("symbol"):
                continue
            quote_volume = _first_float(
                row,
                (
                    "usdtVolume",
                    "quoteVolume",
                    "quoteVol",
                    "turnover24h",
                    "turnover",
                ),
            )
            if quote_volume is None:
                base_volume = _first_float(row, ("baseVolume", "baseVol", "volume24h", "volume"))
                last_price = _first_float(row, ("lastPr", "last", "close"))
                if base_volume is not None and last_price is not None:
                    quote_volume = base_volume * last_price
            tickers.append(
                {
                    "symbol": str(row["symbol"]).upper(),
                    "quote_volume_24h": quote_volume,
                    "last_price": _first_float(row, ("lastPr", "last", "close")),
                    "raw": row,
                }
            )
        return tickers

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
        return _parse_candle_rows(payload.get("data"))

    async def get_history_ohlcv_async(
        self,
        symbol: str,
        timeframe: str = "4h",
        limit: int = 2_196,
        *,
        now: datetime | None = None,
    ) -> list[Candle]:
        granularity = TIMEFRAME_MAP.get(timeframe.lower())
        if granularity is None:
            raise BitgetAPIError("unsupported_timeframe", f"Unsupported Bitget timeframe: {timeframe}")
        target = max(1, min(int(limit), 5_000))
        # The public history page may include the currently open candle. Fetch
        # one extra row so dropping it does not silently shrink the requested
        # confirmed history window.
        fetch_target = min(5_000, target + 1)
        by_timestamp: dict[datetime, Candle] = {}
        end_time_ms: int | None = None
        while len(by_timestamp) < fetch_target:
            page_limit = min(200, fetch_target - len(by_timestamp))
            payload = await self.client.public_get(
                "/api/v2/mix/market/history-candles",
                {
                    "symbol": normalize_symbol(symbol),
                    "productType": self.product_type,
                    "granularity": granularity,
                    "limit": str(page_limit),
                    "endTime": str(end_time_ms) if end_time_ms is not None else None,
                },
            )
            page = _parse_candle_rows(payload.get("data"))
            if not page:
                break
            for candle in page:
                by_timestamp[candle.timestamp] = candle
            oldest_ms = int(page[0].timestamp.timestamp() * 1000)
            if end_time_ms is not None and oldest_ms >= end_time_ms:
                break
            # Bitget aligns endTime to the candle bucket. Subtracting 1 ms can
            # be rounded down to the preceding bucket and silently skip one
            # confirmed candle at every page boundary. Reuse the oldest bucket
            # timestamp; the next response walks earlier and dedupe handles an
            # inclusive boundary if the exchange returns it.
            end_time_ms = oldest_ms
            if len(page) < page_limit:
                break

        cutoff = now or datetime.now(timezone.utc)
        seconds = timeframe_seconds(timeframe)
        confirmed = [
            candle for candle in sorted(by_timestamp.values(), key=lambda item: item.timestamp) if candle.timestamp + timedelta(seconds=seconds) <= cutoff
        ]
        return confirmed[-target:]

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

    async def get_long_short_ratio(self, symbol: str, period: str = "5m") -> dict[str, Any] | None:
        payload = await self.client.public_get(
            "/api/v2/mix/market/account-long-short",
            {
                "symbol": normalize_symbol(symbol),
                "productType": self.product_type,
                "period": period,
            },
        )
        item = _first_dict(payload.get("data"))
        if item is None and isinstance(payload.get("data"), dict):
            item = payload["data"]
        if item is None:
            return None
        return {
            "symbol": str(item.get("symbol", normalize_symbol(symbol))).upper(),
            "period": period,
            "long_short_ratio": _optional_float(item.get("longShortRatio") or item.get("longShortAccountRatio")),
            "long_account_ratio": _optional_float(item.get("longAccountRatio")),
            "short_account_ratio": _optional_float(item.get("shortAccountRatio")),
            "timestamp": _optional_timestamp_ms(item.get("ts") or item.get("timestamp")),
            "raw": item,
        }

    async def get_derivative_snapshot_async(self, symbol: str, ratio_period: str = "5m") -> dict[str, Any]:
        normalized = normalize_symbol(symbol)
        notes: list[str] = []
        raw: dict[str, Any] = {}
        funding = None
        open_interest = None
        ratio = None
        for label, getter in (
            ("funding", lambda: self.get_funding_rate(normalized)),
            ("open_interest", lambda: self.get_open_interest(normalized)),
            (
                "long_short_ratio",
                lambda: self.get_long_short_ratio(normalized, ratio_period),
            ),
            ("ticker", lambda: self._get_ticker(normalized)),
        ):
            try:
                value = await getter()
            except BitgetAPIError as exc:
                notes.append(f"Bitget {label} unavailable: {exc.message}")
                continue
            raw[label] = _model_or_value(value)
            if label == "funding":
                funding = value
            elif label == "open_interest":
                open_interest = value
            elif label == "long_short_ratio":
                ratio = value

        oi_size = open_interest.size if open_interest else None
        ok_count = sum(value is not None for value in (funding, open_interest, ratio))
        return {
            "symbol": normalized,
            "provider": "bitget",
            "tier": "bitget_public",
            "as_of": datetime.now(timezone.utc),
            "open_interest": oi_size,
            "open_interest_value": None,
            "open_interest_change_pct": None,
            "funding_rate": funding.funding_rate if funding else None,
            "funding_rate_interval_hours": funding.funding_rate_interval_hours if funding else None,
            "next_funding_time": funding.next_update if funding else None,
            "long_short_ratio": ratio.get("long_short_ratio") if ratio else None,
            "long_account_ratio": ratio.get("long_account_ratio") if ratio else None,
            "short_account_ratio": ratio.get("short_account_ratio") if ratio else None,
            "data_quality": {
                "funding_ok": funding is not None,
                "open_interest_ok": open_interest is not None,
                "long_short_ratio_ok": ratio is not None,
                "source": "bitget_public",
            },
            "source_status": "ok" if ok_count == 3 else "partial" if ok_count else "error",
            "notes": notes,
            "raw_json": raw,
        }

    async def get_liquidation_history_async(self, symbol: str, max_pages: int = 3) -> list[dict[str, Any]]:
        normalized = normalize_symbol(symbol)
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        cursor: str | None = None
        for page in range(max(1, min(max_pages, 10))):
            payload = await self.client.public_get(
                "/api/v3/market/liquidations",
                {
                    "category": self.product_type,
                    "symbol": normalized,
                    "limit": "100",
                    "cursor": cursor,
                },
            )
            data = payload.get("data")
            page_rows = data.get("list") if isinstance(data, dict) else None
            if not isinstance(page_rows, list):
                raise BitgetAPIError("invalid_response", "Bitget liquidation response is missing data.list.")
            added = 0
            for row in page_rows:
                if not isinstance(row, dict):
                    continue
                fingerprint = tuple(str(row.get(key, "")) for key in ("ts", "side", "price", "amount"))
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                rows.append(row)
                added += 1
            next_cursor = str(data.get("cursor") or "") if isinstance(data, dict) else ""
            if len(page_rows) < 100 or not next_cursor or next_cursor == cursor or added == 0:
                break
            cursor = next_cursor
            if page < max_pages - 1:
                await asyncio.sleep(0.21)
        return rows

    async def get_trade_fills(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1000,
        max_pages: int = 8,
    ) -> list[BitgetTradeFill]:
        normalized = normalize_symbol(symbol)
        fills: list[BitgetTradeFill] = []
        seen: set[str] = set()
        id_less_than: str | None = None
        page_limit = min(max(limit, 1), 1000)
        for page in range(max_pages):
            params = {
                "symbol": normalized,
                "productType": self.product_type,
                "startTime": str(int(start_time.timestamp() * 1000)),
                "endTime": str(int(end_time.timestamp() * 1000)),
                "limit": str(page_limit),
                "idLessThan": id_less_than,
            }
            payload = await self.client.public_get("/api/v2/mix/market/fills-history", params)
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise BitgetAPIError("invalid_response", "Bitget trade fills response is missing data.")
            page_fills = [parse_trade_fill(row, normalized) for row in rows if isinstance(row, dict)]
            for fill in page_fills:
                if fill.trade_id not in seen:
                    seen.add(fill.trade_id)
                    fills.append(fill)
            if len(rows) < page_limit or not page_fills:
                break
            oldest = min(page_fills, key=lambda item: item.timestamp)
            if oldest.timestamp <= start_time:
                break
            id_less_than = oldest.trade_id
            if page < max_pages - 1:
                await asyncio.sleep(0.25)
        return sorted(fills, key=lambda fill: fill.timestamp)

    async def get_spot_trade_fills(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1000,
        max_pages: int = 8,
    ) -> list[BitgetTradeFill]:
        normalized = normalize_symbol(symbol)
        fills: list[BitgetTradeFill] = []
        seen: set[str] = set()
        id_less_than: str | None = None
        page_limit = min(max(limit, 1), 1000)
        for page in range(max_pages):
            payload = await self.client.public_get(
                "/api/v2/spot/market/fills-history",
                {
                    "symbol": normalized,
                    "startTime": str(int(start_time.timestamp() * 1000)),
                    "endTime": str(int(end_time.timestamp() * 1000)),
                    "limit": str(page_limit),
                    "idLessThan": id_less_than,
                },
            )
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise BitgetAPIError("spot_mapping_unavailable", f"Bitget spot market is unavailable for {normalized}.")
            page_fills = [parse_trade_fill(row, normalized) for row in rows if isinstance(row, dict)]
            for fill in page_fills:
                if fill.trade_id not in seen:
                    seen.add(fill.trade_id)
                    fills.append(fill)
            if len(rows) < page_limit or not page_fills:
                break
            oldest = min(page_fills, key=lambda item: item.timestamp)
            if oldest.timestamp <= start_time:
                break
            id_less_than = oldest.trade_id
            if page < max_pages - 1:
                await asyncio.sleep(0.25)
        return sorted(fills, key=lambda fill: fill.timestamp)

    async def get_spot_trade_flow_async(self, symbol: str, timeframe: str, candles: list[MarketCandle]) -> dict:
        return await self._market_trade_flow_async(symbol, timeframe, candles, market="spot")

    async def get_trade_flow_async(self, symbol: str, timeframe: str, candles: list[MarketCandle]) -> dict:
        return await self._market_trade_flow_async(symbol, timeframe, candles, market="futures")

    async def _market_trade_flow_async(self, symbol: str, timeframe: str, candles: list[MarketCandle], *, market: str) -> dict:
        if not candles:
            return _empty_trade_flow("no_candles", "캔들 데이터가 없어 실체결 집계를 만들 수 없습니다.")
        normalized = normalize_symbol(symbol)
        cache_symbol = normalized if market == "futures" else f"SPOT:{normalized}"
        ordered = sorted(candles, key=lambda candle: candle.timestamp)
        now = datetime.now(timezone.utc)
        candle_end = ordered[-1].timestamp + timedelta(seconds=timeframe_seconds(timeframe))
        end_time = min(now, candle_end)
        start_time = max(
            ordered[0].timestamp,
            end_time - timedelta(hours=self.trade_fill_lookback_hours),
        )
        cached_slice = (
            self.trade_cache.fresh_fills(
                cache_symbol,
                timeframe,
                start_time,
                end_time,
                self.trade_fill_cache_ttl_seconds,
                self.trade_fill_max_rows,
            )
            if self.trade_cache
            else None
        )
        fills = cached_slice.fills if cached_slice is not None else None
        truncated = cached_slice.truncated if cached_slice is not None else False
        source = "bitget_fills_history_cache" if cached_slice is not None else "bitget_fills_history"
        error_note = None
        if fills is None:
            key_lock = self._key_lock(self._trade_flow_locks, (cache_symbol, timeframe.lower()))
            await asyncio.to_thread(key_lock.acquire)
            try:
                # Another request may have populated the cache while this request
                # waited. Recheck before calling the paginated fills endpoint.
                cached_slice = (
                    self.trade_cache.fresh_fills(
                        cache_symbol,
                        timeframe,
                        start_time,
                        end_time,
                        self.trade_fill_cache_ttl_seconds,
                        self.trade_fill_max_rows,
                    )
                    if self.trade_cache
                    else None
                )
                fills = cached_slice.fills if cached_slice is not None else None
                truncated = cached_slice.truncated if cached_slice is not None else False
                if cached_slice is not None:
                    source = "bitget_fills_history_cache"
                else:
                    try:
                        fills = (
                            await self.get_spot_trade_fills(normalized, start_time, end_time)
                            if market == "spot"
                            else await self.get_trade_fills(normalized, start_time, end_time)
                        )
                        if self.trade_cache is not None:
                            self.trade_cache.store_fills(cache_symbol, timeframe, start_time, end_time, fills)
                    except BitgetAPIError as exc:
                        stale_slice = (
                            self.trade_cache.stale_fills(
                                cache_symbol,
                                start_time,
                                end_time,
                                self.trade_fill_max_rows,
                            )
                            if self.trade_cache
                            else None
                        )
                        if stale_slice is None or not stale_slice.fills:
                            empty = _empty_trade_flow(
                                "fills_unavailable",
                                "Bitget 현물 마켓 매핑 또는 체결 데이터를 확인할 수 없습니다."
                                if market == "spot"
                                else "Bitget 실체결 데이터를 가져오지 못해 체결 델타 판정을 보류합니다.",
                            )
                            empty.update(
                                {
                                    "source": "bitget_spot" if market == "spot" else "bitget_futures",
                                    "status": "mapping_unavailable" if market == "spot" else "unavailable",
                                }
                            )
                            return empty
                        fills = stale_slice.fills
                        truncated = stale_slice.truncated
                        source = "bitget_fills_history_stale_cache"
                        error_note = str(exc)
            finally:
                key_lock.release()

        buckets = aggregate_trade_buckets(fills, ordered, timeframe)
        event_cvd = event_cvd_series_from_fills(fills)
        notes = []
        if not fills:
            notes.append("조회 범위 내 Bitget 실체결 데이터가 없습니다.")
        if error_note:
            notes.append("Bitget 실체결 API 오류로 캐시된 체결 데이터를 사용했습니다.")
        if truncated:
            notes.append(f"고빈도 체결은 응답 지연을 막기 위해 가장 최근 {self.trade_fill_max_rows:,}건만 관측했습니다.")
        coverage_from = fills[0].timestamp.isoformat() if fills else None
        coverage_to = fills[-1].timestamp.isoformat() if fills else None
        return {
            "method": "trade_fills" if fills else "data_unavailable",
            "source": "bitget_spot" if market == "spot" else "bitget_futures",
            "cache_source": source,
            "status": "ok",
            "data_available": bool(fills),
            "coverage": {
                "from": coverage_from,
                "to": coverage_to,
                "requested_from": start_time.isoformat(),
                "requested_to": end_time.isoformat(),
                "lookback_hours": self.trade_fill_lookback_hours,
                "fills": len(fills),
                "max_rows": self.trade_fill_max_rows,
                "truncated": truncated,
                "buckets": len(buckets),
                "cvd_points": len(event_cvd),
                "cvd_method": "event_time_fills" if event_cvd else None,
            },
            "fills": [fill.model_dump() for fill in fills],
            "buckets": [bucket.model_dump(mode="json") for bucket in buckets],
            "cvd": cvd_series_from_buckets(buckets),
            "event_cvd": event_cvd,
            "notes": notes,
        }

    async def get_market_snapshot(self, symbol: str, timeframe: str) -> MarketSnapshot:
        normalized = normalize_symbol(symbol)
        candles, funding, open_interest, ticker = await asyncio.gather(
            self.get_ohlcv(normalized, timeframe, limit=200),
            self.get_funding_rate(normalized),
            self.get_open_interest(normalized),
            self._get_ticker(normalized),
        )
        if len(candles) < 30:
            raise BitgetAPIError(
                "insufficient_candles",
                "Not enough candle data to calculate indicators.",
            )

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

    def _fresh_snapshot(self, key: tuple[str, str]) -> MarketSnapshot | None:
        cached = self._snapshot_cache.get(key)
        if cached is None:
            return None
        cached_at, snapshot = cached
        if time.monotonic() - cached_at > self.snapshot_cache_ttl_seconds:
            return None
        return snapshot

    def _key_lock(
        self,
        locks: dict[tuple[str, str], threading.Lock],
        key: tuple[str, str],
    ) -> threading.Lock:
        with self._cache_guard:
            return locks.setdefault(key, threading.Lock())

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

    async def get_account_trade_fills(
        self,
        start_time: datetime,
        end_time: datetime,
        *,
        limit: int = 100,
        max_pages: int = 50,
    ) -> list[BitgetAccountFill]:
        fills: list[BitgetAccountFill] = []
        seen: set[str] = set()
        id_less_than: str | None = None
        page_limit = min(max(1, limit), 100)
        for page in range(max_pages):
            payload = await self.client.private_get(
                "/api/v2/mix/order/fills",
                {
                    "productType": self.product_type,
                    "startTime": str(int(start_time.timestamp() * 1000)),
                    "endTime": str(int(end_time.timestamp() * 1000)),
                    "limit": str(page_limit),
                    "idLessThan": id_less_than,
                },
            )
            data = payload.get("data")
            rows = data.get("fillList") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise BitgetAPIError("invalid_response", "Bitget account fills response is missing fillList.")
            page_fills = [parse_account_fill(row, self.margin_coin) for row in rows if isinstance(row, dict)]
            for fill in page_fills:
                if fill.trade_id not in seen:
                    seen.add(fill.trade_id)
                    fills.append(fill)
            if len(rows) < page_limit or not page_fills:
                break
            oldest = min(page_fills, key=lambda item: item.timestamp)
            if oldest.timestamp <= start_time:
                break
            id_less_than = str(data.get("endId") or oldest.trade_id)
            if page < max_pages - 1:
                await asyncio.sleep(0.1)
        return sorted(fills, key=lambda fill: (fill.timestamp, fill.trade_id))

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
            margin_size=_optional_float(row.get("marginSize")),
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


def _parse_candle_rows(value: Any) -> list[Candle]:
    if not isinstance(value, list):
        raise BitgetAPIError("invalid_response", "Bitget candle response is missing data.")
    candles: list[Candle] = []
    for index, row in enumerate(value):
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


def _first_float(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _optional_float(row.get(key))
        if value is not None:
            return value
    return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(float(value))


def _optional_string(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _model_or_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def utc_iso_from_values(*values: datetime | None) -> datetime:
    timestamps = [value for value in values if value is not None]
    return max(timestamps) if timestamps else datetime.now(timezone.utc)


def _empty_trade_flow(reason: str, note: str) -> dict:
    return {
        "method": "data_unavailable",
        "source": "bitget_fills_history",
        "data_available": False,
        "coverage": {
            "from": None,
            "to": None,
            "lookback_hours": None,
            "fills": 0,
            "buckets": 0,
        },
        "fills": [],
        "buckets": [],
        "cvd": [],
        "notes": [note],
        "reason": reason,
    }


def _estimate_open_interest_change(open_interest: OpenInterest | None, ticker: dict[str, Any]) -> float:
    if open_interest is None or open_interest.size <= 0:
        return 0.0
    holding_amount = _optional_float(ticker.get("holdingAmount"))
    if holding_amount is None:
        return 0.0
    return round(((open_interest.size - holding_amount) / open_interest.size) * 100, 2)
