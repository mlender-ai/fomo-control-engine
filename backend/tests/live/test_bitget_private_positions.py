import os

import pytest

from app.exchange.bitget.client import BitgetClient
from app.exchange.bitget.provider import BitgetMarketDataProvider


pytestmark = pytest.mark.live


@pytest.mark.skipif(os.getenv("RUN_LIVE_BITGET_TESTS") != "true", reason="live Bitget tests disabled")
@pytest.mark.skipif(
    not (os.getenv("BITGET_API_KEY") and os.getenv("BITGET_API_SECRET") and os.getenv("BITGET_API_PASSPHRASE")),
    reason="Bitget private API credentials are missing",
)
def test_live_bitget_private_positions() -> None:
    provider = BitgetMarketDataProvider(
        BitgetClient(
            api_key=os.environ["BITGET_API_KEY"],
            api_secret=os.environ["BITGET_API_SECRET"],
            passphrase=os.environ["BITGET_API_PASSPHRASE"],
            timeout=15,
        )
    )

    positions = provider.get_positions()

    assert isinstance(positions, list)
