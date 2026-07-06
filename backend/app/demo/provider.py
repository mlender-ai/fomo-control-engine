from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import random

from app.db.models import DataQuality, MarketCandle, MarketSnapshot
from app.exchange.base import MarketDataProvider
from app.marketdata.assets import classify_asset_class
from app.marketdata.sessions import tag_candles_with_sessions


DEMO_SYMBOLS = {
    "BTCUSDT": {"base": 100_000.0, "scenario": "healthy_long"},
    "ETHUSDT": {"base": 3_400.0, "scenario": "critical_short"},
    "BASEDUSDT": {"base": 0.096, "scenario": "wyckoff_range"},
    "TSLAUSDT": {"base": 248.6, "scenario": "healthy_long"},
}


class FakeBitgetProvider(MarketDataProvider):
    """Fixed-seed market data provider used only with FCE_DEMO_MODE=true."""

    name = "demo"

    def list_contracts(self) -> list[dict]:
        return [
            {
                "symbol": symbol,
                "base_coin": symbol.removesuffix("USDT"),
                "quote_coin": "USDT",
                "status": "demo",
                "asset_class": classify_asset_class(symbol, symbol.removesuffix("USDT"), "USDT", {"isRwa": "YES"} if symbol == "TSLAUSDT" else {}),
                "source_category": "demo_rwa" if symbol == "TSLAUSDT" else "demo_contract",
                "funding_rate_interval_hours": 8,
                "raw_metadata": {"isRwa": "YES" if symbol == "TSLAUSDT" else "NO", "fundInterval": "8"},
                "maintenance_margin_rate": 0.005,
                "taker_fee_rate": 0.0006,
            }
            for symbol in DEMO_SYMBOLS
        ]

    def get_snapshot(self, symbol: str, timeframe: str = "4h") -> MarketSnapshot:
        normalized = symbol.upper().replace("/", "")
        spec = DEMO_SYMBOLS.get(normalized, {"base": 100.0, "scenario": "healthy_long"})
        candles = _scenario_candles(normalized, str(spec["scenario"]), float(spec["base"]), timeframe)
        candles = tag_candles_with_sessions(candles, classify_asset_class(normalized))
        previous = candles[-7].close if len(candles) >= 7 else candles[0].close
        change_24h = ((candles[-1].close - previous) / previous) * 100
        funding = 0.0008 if normalized == "BTCUSDT" else 0.012 if normalized == "ETHUSDT" else -0.0003
        oi_change = 9.8 if normalized == "ETHUSDT" else 4.2 if normalized == "BASEDUSDT" else 2.4
        return MarketSnapshot(
            symbol=normalized,
            timeframe=timeframe,
            price=round(candles[-1].close, _precision(normalized)),
            change_24h=round(change_24h, 2),
            funding_rate=funding,
            open_interest_change=oi_change,
            candles=candles,
            provider="demo",
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

    def get_trade_flow(self, symbol: str, timeframe: str, candles: list[MarketCandle]) -> dict:
        buckets = []
        fills = []
        cvd = []
        cumulative = 0.0
        for candle in candles[-96:]:
            direction = 1 if candle.close >= candle.open else -1
            delta = direction * candle.volume * 0.24
            buy = candle.volume * (0.5 + (0.12 if direction > 0 else -0.08))
            sell = max(0.0, candle.volume - buy)
            cumulative += delta
            buckets.append(
                {
                    "time": int(candle.timestamp.timestamp()),
                    "buy_volume": round(buy, 4),
                    "sell_volume": round(sell, 4),
                    "delta": round(delta, 4),
                    "method": "demo_fills",
                }
            )
            cvd.append({"time": int(candle.timestamp.timestamp()), "value": round(cumulative, 4)})
            fills.append(
                {
                    "trade_id": f"demo-{symbol}-{int(candle.timestamp.timestamp())}",
                    "symbol": symbol.upper(),
                    "timestamp": candle.timestamp.isoformat(),
                    "price": candle.close,
                    "size": round(candle.volume * 0.01, 4),
                    "side": "buy" if direction > 0 else "sell",
                }
            )
        return {
            "method": "trade_fills",
            "source": "demo_fills",
            "data_available": True,
            "coverage": {
                "from": candles[-96].timestamp.isoformat() if len(candles) >= 96 else candles[0].timestamp.isoformat(),
                "to": candles[-1].timestamp.isoformat(),
                "lookback_hours": 96 * 4,
                "fills": len(fills),
                "buckets": len(buckets),
            },
            "fills": fills,
            "buckets": buckets,
            "cvd": cvd,
            "notes": ["FCE_DEMO_MODE synthetic fills. Live exchange data is not used."],
        }


def _scenario_candles(symbol: str, scenario: str, base: float, timeframe: str) -> list[MarketCandle]:
    step_hours = {"15m": 0.25, "1h": 1, "4h": 4, "1d": 24}.get(timeframe, 4)
    now = datetime(2026, 7, 6, 0, 0, tzinfo=timezone.utc)
    rng = random.Random(f"fce-demo:{symbol}:{scenario}:{timeframe}")
    count = 220
    closes = _close_path(scenario, base, count)
    candles: list[MarketCandle] = []
    previous = closes[0]
    for index, close in enumerate(closes):
        open_price = previous
        noise = abs(close - open_price) / max(close, 1e-12)
        high = max(open_price, close) * (1 + 0.004 + rng.random() * 0.01 + noise * 0.3)
        low = min(open_price, close) * (1 - 0.004 - rng.random() * 0.01 - noise * 0.2)
        volume = _volume_for(scenario, index, count, base, rng)
        candles.append(
            MarketCandle(
                timestamp=now - timedelta(hours=step_hours * (count - 1 - index)),
                open=round(open_price, _precision(symbol)),
                high=round(high, _precision(symbol)),
                low=round(max(low, base * 0.2), _precision(symbol)),
                close=round(close, _precision(symbol)),
                volume=round(volume, 2),
                quote_volume=round(volume * close, 2),
            )
        )
        previous = close
    return candles


def _close_path(scenario: str, base: float, count: int) -> list[float]:
    path: list[float] = []
    for index in range(count):
        t = index / (count - 1)
        wave = math.sin(index / 6.0) * 0.006
        if scenario == "critical_short":
            drift = 0.02 + t * 0.09
            squeeze = 0.075 if index > count - 18 else 0.0
            value = base * (1 + drift + squeeze + wave)
        elif scenario == "wyckoff_range":
            if t < 0.28:
                value = base * (0.72 + t * 0.95 + wave)
            elif t < 0.76:
                value = base * (0.95 + math.sin(index / 8) * 0.075 + wave)
            elif t < 0.84:
                value = base * (0.86 - (t - 0.76) * 0.7 + wave)
            else:
                value = base * (0.86 + (t - 0.84) * 1.25 + wave)
        else:
            value = base * (0.94 + t * 0.16 + wave)
        path.append(max(value, base * 0.25))
    return path


def _volume_for(scenario: str, index: int, count: int, base: float, rng: random.Random) -> float:
    scale = 12_000 if base > 1000 else 5_000_000 if base < 1 else 80_000
    spike = 1.0
    if scenario == "critical_short" and index > count - 20:
        spike = 2.5
    if scenario == "wyckoff_range" and (int(count * 0.76) <= index <= int(count * 0.86)):
        spike = 3.0
    return scale * spike * (0.72 + rng.random() * 0.8)


def _precision(symbol: str) -> int:
    if symbol == "BASEDUSDT":
        return 5
    if symbol == "ETHUSDT":
        return 2
    return 2
