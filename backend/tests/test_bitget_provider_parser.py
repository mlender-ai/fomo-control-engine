from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from app.exchange.bitget.client import BitgetClient
from app.exchange.bitget.provider import BitgetMarketDataProvider, _aggregate_daily_candles
from app.exchange.bitget.schemas import Candle


class FakeBitgetClient:
    async def public_get(self, path: str, params: dict):
        assert path == "/api/v2/mix/market/account-long-short"
        return {
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "longAccountRatio": "0.6006",
                    "shortAccountRatio": "0.3994",
                    "longShortAccountRatio": "1.5037",
                    "ts": "1783261500000",
                }
            ]
        }


class FakeContractsClient:
    async def public_get(self, path: str, params: dict):
        assert path == "/api/v2/mix/market/contracts"
        return {
            "data": [
                {
                    "symbol": "TSLAUSDT",
                    "baseCoin": "TSLA",
                    "quoteCoin": "USDT",
                    "symbolStatus": "normal",
                    "symbolType": "perpetual",
                    "isRwa": "YES",
                    "fundInterval": "8",
                    "takerFeeRate": "0.0006",
                }
            ]
        }


class FakeHistoryClient:
    def __init__(self, count: int = 7) -> None:
        base = datetime(2026, 7, 1, tzinfo=timezone.utc)
        self.rows = [
            [
                str(int((base + timedelta(hours=4 * index)).timestamp() * 1000)),
                str(100 + index),
                str(102 + index),
                str(99 + index),
                str(101 + index),
                "10",
                "1000",
            ]
            for index in range(count)
        ]

    async def public_get(self, path: str, params: dict):
        assert path == "/api/v2/mix/market/history-candles"
        rows = self.rows
        if params.get("endTime"):
            rows = [row for row in rows if int(row[0]) < int(params["endTime"])]
        return {"data": rows[-int(params["limit"]) :]}


def test_bitget_provider_filters_empty_positions() -> None:
    provider = BitgetMarketDataProvider(BitgetClient())
    parsed = provider._parse_position(
        {
            "marginCoin": "USDT",
            "symbol": "BTCUSDT",
            "holdSide": "long",
            "available": "0.01",
            "locked": "0",
            "total": "0.01",
            "leverage": "3",
            "openPriceAvg": "60000",
            "markPrice": "61000",
            "unrealizedPL": "10",
            "marginSize": "200",
            "liquidationPrice": "0",
            "marginMode": "crossed",
            "posMode": "hedge_mode",
            "marginRatio": "0.02",
            "breakEvenPrice": "60100",
            "cTime": "1766103799183",
            "uTime": "1767682800537",
        }
    )

    assert parsed.symbol == "BTCUSDT"
    assert parsed.hold_side == "long"
    assert parsed.total == 0.01
    assert parsed.margin_size == 200
    assert parsed.liquidation_price is None


@pytest.mark.asyncio
async def test_bitget_provider_parses_account_long_short_ratio() -> None:
    provider = BitgetMarketDataProvider(FakeBitgetClient())  # type: ignore[arg-type]

    ratio = await provider.get_long_short_ratio("BTCUSDT")

    assert ratio is not None
    assert ratio["long_account_ratio"] == 0.6006
    assert ratio["short_account_ratio"] == 0.3994
    assert ratio["long_short_ratio"] == 1.5037


@pytest.mark.asyncio
async def test_bitget_contracts_preserve_rwa_asset_class_and_funding_interval() -> None:
    provider = BitgetMarketDataProvider(FakeContractsClient())  # type: ignore[arg-type]

    contracts = await provider.get_contracts()

    assert contracts == [
        {
            "symbol": "TSLAUSDT",
            "base_coin": "TSLA",
            "quote_coin": "USDT",
            "status": "normal",
            "asset_class": "stock",
            "source_category": "bitget_rwa",
            "funding_rate_interval_hours": 8,
            "raw_metadata": {
                "symbolType": "perpetual",
                "isRwa": "YES",
                "fundInterval": "8",
            },
            "maintenance_margin_rate": None,
            "taker_fee_rate": 0.0006,
        }
    ]


@pytest.mark.asyncio
async def test_history_candles_page_dedupe_sort_and_drop_open_bar() -> None:
    provider = BitgetMarketDataProvider(cast(BitgetClient, FakeHistoryClient()))
    cutoff = datetime(2026, 7, 2, 2, tzinfo=timezone.utc)

    candles = await provider.get_history_ohlcv_async("BTCUSDT", "4h", 6, now=cutoff)

    assert [candle.close for candle in candles] == [101, 102, 103, 104, 105, 106]
    assert all(candle.timestamp < cutoff for candle in candles)
    assert candles[-1].timestamp + timedelta(hours=4) <= cutoff


@pytest.mark.asyncio
async def test_history_candles_pagination_keeps_every_boundary_candle() -> None:
    provider = BitgetMarketDataProvider(cast(BitgetClient, FakeHistoryClient(402)))
    cutoff = datetime(2026, 9, 6, tzinfo=timezone.utc)

    candles = await provider.get_history_ohlcv_async("BTCUSDT", "4h", 401, now=cutoff)

    assert len(candles) == 401
    assert all(right.timestamp - left.timestamp == timedelta(hours=4) for left, right in zip(candles, candles[1:]))


def test_daily_resample_keeps_only_complete_exchange_aligned_days() -> None:
    base = datetime(2026, 7, 1, 16, tzinfo=timezone.utc)
    four_hour = [
        Candle(
            timestamp=base + timedelta(hours=4 * index),
            open=100 + index,
            high=102 + index,
            low=99 + index,
            close=101 + index,
            volume=10 + index,
            quote_volume=1_000 + index,
        )
        for index in range(13)
    ]

    daily = _aggregate_daily_candles(four_hour, anchor_hour=16)

    assert len(daily) == 2
    assert daily[0].timestamp == base
    assert daily[0].open == 100
    assert daily[0].close == 106
    assert daily[0].high == 107
    assert daily[0].low == 99
    assert daily[1].timestamp == base + timedelta(days=1)
