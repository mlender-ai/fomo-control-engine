from datetime import datetime, timezone
from typing import Any

import httpx

from app.db.models import MarketCandle, MarketSnapshot
from app.exchange.base import MarketDataProvider
from app.exchange.errors import MarketDataError


GRANULARITY_BY_TIMEFRAME = {
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


class BitgetReadOnlyClient(MarketDataProvider):
    """Read-only Bitget public market data provider."""

    def __init__(
        self,
        base_url: str = "https://api.bitget.com",
        product_type: str = "usdt-futures",
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.product_type = product_type
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.timeout = timeout

    def get_snapshot(self, symbol: str, timeframe: str = "4h") -> MarketSnapshot:
        normalized = symbol.upper().replace("/", "")
        granularity = GRANULARITY_BY_TIMEFRAME.get(timeframe.lower())
        if granularity is None:
            raise MarketDataError(f"Unsupported Bitget timeframe: {timeframe}")

        candles_payload = self._get(
            "/api/v2/mix/market/candles",
            {
                "symbol": normalized,
                "granularity": granularity,
                "limit": "100",
                "productType": self.product_type,
            },
        )
        ticker_payload = self._get(
            "/api/v2/mix/market/ticker",
            {
                "symbol": normalized,
                "productType": self.product_type,
            },
        )
        funding_payload = self._get(
            "/api/v2/mix/market/current-fund-rate",
            {
                "symbol": normalized,
                "productType": self.product_type,
            },
        )
        open_interest_payload = self._get(
            "/api/v2/mix/market/open-interest",
            {
                "symbol": normalized,
                "productType": self.product_type,
            },
        )

        candles = self._parse_candles(candles_payload)
        if len(candles) < 30:
            raise MarketDataError(f"Bitget returned too few candles for {normalized}: {len(candles)}")

        ticker = self._first_list_item(ticker_payload.get("data"), "ticker")
        funding = self._first_list_item(funding_payload.get("data"), "funding")
        open_interest_change = self._estimate_open_interest_change(open_interest_payload, ticker)
        price = _float(ticker.get("lastPr"), candles[-1].close)
        change_24h = _float(ticker.get("change24h"), 0.0) * 100

        return MarketSnapshot(
            symbol=normalized,
            timeframe=timeframe,
            price=round(price, 8),
            change_24h=round(change_24h, 2),
            funding_rate=_float(funding.get("fundingRate"), 0.0),
            open_interest_change=open_interest_change,
            candles=candles,
        )

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.get(path, params=params)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MarketDataError(f"Bitget request failed: {exc}") from exc

        payload = response.json()
        if payload.get("code") != "00000":
            message = payload.get("msg", "unknown Bitget error")
            raise MarketDataError(f"Bitget returned {payload.get('code')}: {message}")
        return payload

    def _parse_candles(self, payload: dict[str, Any]) -> list[MarketCandle]:
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise MarketDataError("Bitget candle response is missing data")

        candles = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 6:
                continue
            candles.append(
                MarketCandle(
                    timestamp=datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        return sorted(candles, key=lambda candle: candle.timestamp)

    def _first_list_item(self, value: Any, label: str) -> dict[str, Any]:
        if not isinstance(value, list) or not value or not isinstance(value[0], dict):
            raise MarketDataError(f"Bitget {label} response is missing data")
        return value[0]

    def _estimate_open_interest_change(self, payload: dict[str, Any], ticker: dict[str, Any]) -> float:
        current = None
        data = payload.get("data")
        if isinstance(data, dict):
            entries = data.get("openInterestList")
            if isinstance(entries, list) and entries and isinstance(entries[0], dict):
                current = _float(entries[0].get("size"), 0.0)

        holding_amount = _float(ticker.get("holdingAmount"), current or 0.0)
        if not current or current <= 0:
            return 0.0
        return round(((current - holding_amount) / current) * 100, 2)


def _float(value: Any, default: float) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
