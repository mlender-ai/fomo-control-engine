from app.exchange.mock import MockMarketDataProvider


def test_mock_provider_returns_reproducible_shape() -> None:
    provider = MockMarketDataProvider()

    snapshot = provider.get_snapshot("BTCUSDT", "4h")

    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.timeframe == "4h"
    assert snapshot.price > 0
    assert len(snapshot.candles) >= 100
    assert snapshot.candles[-1].timestamp > snapshot.candles[0].timestamp
