from app.exchange.bitget.client import BitgetClient
from app.exchange.bitget.provider import BitgetMarketDataProvider


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
