from datetime import timedelta
import math
import random

from app.db.models import DataQuality, MarketCandle, MarketSnapshot, utc_now
from app.exchange.base import MarketDataProvider


BASE_PRICES = {
    "BTCUSDT": 108420.0,
    "ETHUSDT": 3425.0,
    "SOLUSDT": 151.3,
    "XRPUSDT": 2.32,
    "DOGEUSDT": 0.172,
}


class MockMarketDataProvider(MarketDataProvider):
    def get_snapshot(self, symbol: str, timeframe: str = "4h") -> MarketSnapshot:
        normalized = symbol.upper().replace("/", "")
        base_price = BASE_PRICES.get(normalized, 100.0)
        seed = sum(ord(char) for char in normalized + timeframe)
        rng = random.Random(seed)
        now = utc_now()
        candles: list[MarketCandle] = []
        close = base_price * (0.94 + rng.random() * 0.08)

        profile = (seed % 5) - 2

        candle_count = 160
        for index in range(candle_count):
            wave = math.sin((index + seed % 13) / 7) * 0.006
            drift = profile * 0.0009
            mean_reversion = (base_price - close) / base_price * 0.0018
            shock = rng.uniform(-0.012, 0.012)
            open_price = close
            close = max(base_price * 0.4, open_price * (1 + wave + drift + mean_reversion + shock))
            high = max(open_price, close) * (1 + rng.uniform(0.002, 0.018))
            low = min(open_price, close) * (1 - rng.uniform(0.002, 0.018))
            volume = 1000 + abs(shock) * 90000 + index * 10 + rng.uniform(0, 1200)
            candles.append(
                MarketCandle(
                    timestamp=now - timedelta(hours=4 * (candle_count - 1 - index)),
                    open=round(open_price, 4),
                    high=round(high, 4),
                    low=round(low, 4),
                    close=round(close, 4),
                    volume=round(volume, 2),
                    quote_volume=round(volume * close, 2),
                )
            )

        first_day = candles[-7].close
        change_24h = ((candles[-1].close - first_day) / first_day) * 100

        return MarketSnapshot(
            symbol=normalized,
            timeframe=timeframe,
            price=round(candles[-1].close, 4),
            change_24h=round(change_24h, 2),
            funding_rate=round(rng.uniform(-0.018, 0.026), 4),
            open_interest_change=round(rng.uniform(-8.5, 14.5), 2),
            candles=candles,
            provider="mock",
            data_quality=DataQuality(
                ohlcv_ok=True,
                funding_ok=True,
                open_interest_ok=True,
                min_candles_met=True,
                fallback_used=False,
                candles=len(candles),
                last_candle_at=candles[-1].timestamp,
            ),
        )
