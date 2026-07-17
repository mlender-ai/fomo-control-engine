import sqlite3
from datetime import datetime, timedelta, timezone

from app.exchange.bitget.trade_cache import BitgetTradeFillCache
from app.exchange.bitget.trades import BitgetTradeFill


BASE_TIME = datetime(2026, 7, 18, 0, 0, tzinfo=timezone.utc)


def test_cache_returns_latest_bounded_fills_in_chronological_order(tmp_path) -> None:
    cache = BitgetTradeFillCache(str(tmp_path / "trade-cache.db"))
    fills = [_fill(index) for index in range(5)]
    cache.store_fills("BTCUSDT", "4h", BASE_TIME, BASE_TIME + timedelta(hours=5), fills)

    result = cache.fresh_fills(
        "BTCUSDT",
        "4h",
        BASE_TIME,
        BASE_TIME + timedelta(hours=5),
        max_age_seconds=60,
        max_rows=3,
    )

    assert result is not None
    assert result.truncated is True
    assert [fill.trade_id for fill in result.fills] == ["2", "3", "4"]


def test_cache_payload_error_falls_back_without_raising(tmp_path) -> None:
    database_path = tmp_path / "trade-cache.db"
    cache = BitgetTradeFillCache(str(database_path))
    cache.store_fills(
        "BTCUSDT",
        "4h",
        BASE_TIME,
        BASE_TIME + timedelta(hours=1),
        [_fill(0)],
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE bitget_trade_fills SET payload = 'not-json'")

    assert (
        cache.fresh_fills(
            "BTCUSDT",
            "4h",
            BASE_TIME,
            BASE_TIME + timedelta(hours=1),
            max_age_seconds=60,
            max_rows=50_000,
        )
        is None
    )
    assert cache.stale_fills("BTCUSDT", BASE_TIME, BASE_TIME + timedelta(hours=1), 50_000).fills == []


def _fill(index: int) -> BitgetTradeFill:
    return BitgetTradeFill(
        trade_id=str(index),
        symbol="BTCUSDT",
        price=100 + index,
        size=1,
        side="buy" if index % 2 == 0 else "sell",
        timestamp=BASE_TIME + timedelta(hours=index),
    )
