from datetime import datetime, timedelta, timezone

from app.db.models import Direction, MarketCandle, MarketSnapshot, Position
from app.exchange.bitget.trades import aggregate_trade_buckets, cvd_series_from_buckets, parse_trade_fill
from app.positions.chart_analysis import build_chart_analysis


BASE_TIME = datetime(2026, 7, 4, tzinfo=timezone.utc)


def test_bitget_trade_fill_parser_normalizes_side_and_timestamp() -> None:
    fill = parse_trade_fill(
        {
            "tradeId": "123",
            "price": "26372.5",
            "size": "9.25",
            "side": "Sell",
            "ts": "1695865151000",
            "symbol": "BTCUSDT",
        },
        "BTCUSDT",
    )

    assert fill.trade_id == "123"
    assert fill.side == "sell"
    assert fill.price == 26372.5
    assert fill.size == 9.25
    assert fill.timestamp.tzinfo is not None


def test_trade_buckets_and_cvd_are_aggregated_by_candle() -> None:
    candles = [_candle(index) for index in range(4)]
    fills = [
        parse_trade_fill({"tradeId": "1", "price": "101", "size": "3", "side": "Buy", "ts": _ms(candles[1].timestamp + timedelta(minutes=5)), "symbol": "BTCUSDT"}, "BTCUSDT"),
        parse_trade_fill({"tradeId": "2", "price": "102", "size": "1", "side": "Sell", "ts": _ms(candles[1].timestamp + timedelta(minutes=10)), "symbol": "BTCUSDT"}, "BTCUSDT"),
        parse_trade_fill({"tradeId": "3", "price": "103", "size": "2", "side": "Sell", "ts": _ms(candles[2].timestamp + timedelta(minutes=1)), "symbol": "BTCUSDT"}, "BTCUSDT"),
    ]

    buckets = aggregate_trade_buckets(fills, candles, "4h")
    cvd = cvd_series_from_buckets(buckets)

    assert len(buckets) == 2
    assert buckets[0].buy_volume == 3
    assert buckets[0].sell_volume == 1
    assert buckets[0].delta == 2
    assert buckets[1].delta == -2
    assert cvd == [
        {"time": buckets[0].time, "value": 2.0, "delta": 2.0, "method": "trade_fills"},
        {"time": buckets[1].time, "value": 0.0, "delta": -2.0, "method": "trade_fills"},
    ]


def test_uncovered_volume_profile_bins_do_not_expose_fake_buy_sell() -> None:
    candles = [_candle(index) for index in range(120)]
    snapshot = MarketSnapshot(symbol="BTCUSDT", timeframe="4h", price=110.0, change_24h=0.0, funding_rate=0.0, open_interest_change=0.0, candles=candles)
    position = Position(symbol="BTCUSDT", direction=Direction.long, entry_price=100.0, quantity=1.0, mark_price=110.0)

    analysis = build_chart_analysis(position, snapshot)

    assert analysis["volume_profile"]["method"] == "ohlcv_estimated"
    assert analysis["volume_profile"]["has_trade_fills"] is False
    assert all("buy_volume" not in item for item in analysis["volume_profile"]["bins"])
    assert all("sell_volume" not in item for item in analysis["volume_profile"]["bins"])
    assert analysis["volume_xray"]["method"] == "data_unavailable"


def test_trade_fill_volume_profile_exposes_real_delta_only_for_covered_bins() -> None:
    candles = [_candle(index) for index in range(120)]
    fills = [
        {"trade_id": "1", "symbol": "BTCUSDT", "price": 108.0, "size": 4.0, "side": "buy", "timestamp": candles[-2].timestamp},
        {"trade_id": "2", "symbol": "BTCUSDT", "price": 108.2, "size": 1.5, "side": "sell", "timestamp": candles[-2].timestamp + timedelta(minutes=1)},
    ]
    trade_flow = {
        "method": "trade_fills",
        "source": "unit_test",
        "data_available": True,
        "coverage": {"from": candles[-3].timestamp.isoformat(), "to": (candles[-1].timestamp + timedelta(hours=4)).isoformat(), "fills": 2, "buckets": 1},
        "fills": fills,
        "buckets": [{"time": int(candles[-2].timestamp.timestamp()), "buy_volume": 4.0, "sell_volume": 1.5, "delta": 2.5, "trades": 2, "method": "trade_fills"}],
        "cvd": [{"time": int(candles[-2].timestamp.timestamp()), "value": 2.5, "delta": 2.5, "method": "trade_fills"}],
        "notes": [],
    }
    snapshot = MarketSnapshot(symbol="BTCUSDT", timeframe="4h", price=110.0, change_24h=0.0, funding_rate=0.0, open_interest_change=0.0, candles=candles)
    position = Position(symbol="BTCUSDT", direction=Direction.long, entry_price=100.0, quantity=1.0, mark_price=110.0)

    analysis = build_chart_analysis(position, snapshot, trade_flow)
    trade_bins = [item for item in analysis["volume_profile"]["bins"] if item["method"] in {"trade_fills", "mixed"}]

    assert analysis["volume_profile"]["has_trade_fills"] is True
    assert trade_bins
    assert any(item.get("buy_volume", 0) > item.get("sell_volume", 0) for item in trade_bins)
    assert analysis["trade_flow"]["cvd"] == trade_flow["cvd"]
    assert analysis["volume_xray"]["method"] == "trade_fills"


def _candle(index: int) -> MarketCandle:
    close = 100 + index * 0.1
    return MarketCandle(
        timestamp=BASE_TIME + timedelta(hours=index * 4),
        open=close - 0.2,
        high=close + 1.2,
        low=close - 1.1,
        close=close,
        volume=1000 + index,
    )


def _ms(value: datetime) -> str:
    return str(int(value.timestamp() * 1000))
