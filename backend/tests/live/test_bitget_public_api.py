import os

import pytest

from app.exchange.bitget.client import BitgetClient
from app.exchange.bitget.provider import BitgetMarketDataProvider


pytestmark = pytest.mark.live


@pytest.mark.skipif(os.getenv("RUN_LIVE_BITGET_TESTS") != "true", reason="live Bitget tests disabled")
def test_live_bitget_public_snapshot() -> None:
    provider = BitgetMarketDataProvider(BitgetClient(timeout=15))

    snapshot = provider.get_snapshot("BTCUSDT", "4h")

    assert snapshot.symbol == "BTCUSDT"
    assert snapshot.provider == "bitget"
    assert snapshot.data_quality.candles >= 30

