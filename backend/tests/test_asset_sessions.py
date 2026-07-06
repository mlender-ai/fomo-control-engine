from datetime import datetime, timezone

from app.db.models import MarketCandle
from app.marketdata.assets import classify_asset_class
from app.marketdata.sessions import filter_analysis_candles, session_info_for_symbol


def test_asset_classifier_uses_bitget_rwa_metadata_for_stock_puffs() -> None:
    assert classify_asset_class("TSLAUSDT", "TSLA", "USDT", {"isRwa": "YES"}) == "stock"
    assert classify_asset_class("QQQUSDT", "QQQ", "USDT", {"isRwa": "YES"}) == "index"
    assert classify_asset_class("BTCUSDT", "BTC", "USDT", {"isRwa": "NO"}) == "crypto"


def test_us_stock_session_handles_regular_extended_and_holiday_closed() -> None:
    regular = session_info_for_symbol("TSLAUSDT", "stock", datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc))
    extended = session_info_for_symbol("TSLAUSDT", "stock", datetime(2026, 7, 6, 22, 0, tzinfo=timezone.utc))
    closed = session_info_for_symbol("TSLAUSDT", "stock", datetime(2026, 7, 3, 14, 0, tzinfo=timezone.utc))

    assert regular.state == "regular"
    assert extended.state == "extended"
    assert closed.state == "closed"
    assert closed.next_open_at is not None


def test_filter_analysis_candles_excludes_closed_stock_sessions() -> None:
    candles = [
        _candle(datetime(2026, 7, 3, 14, 0, tzinfo=timezone.utc)),
        _candle(datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)),
        _candle(datetime(2026, 7, 6, 22, 0, tzinfo=timezone.utc)),
    ]

    filtered, excluded = filter_analysis_candles(candles, "stock")

    assert excluded == 1
    assert [item.session for item in filtered] == ["regular", "extended"]


def _candle(timestamp: datetime) -> MarketCandle:
    return MarketCandle(timestamp=timestamp, open=100, high=101, low=99, close=100.5, volume=1000)
