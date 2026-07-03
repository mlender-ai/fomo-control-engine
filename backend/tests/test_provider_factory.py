from app.core.config import Settings
from app.exchange.bitget.provider import BitgetMarketDataProvider
from app.exchange.factory import create_market_data_provider
from app.exchange.mock import MockMarketDataProvider


def test_provider_factory_defaults_to_mock() -> None:
    provider = create_market_data_provider(Settings())

    assert isinstance(provider, MockMarketDataProvider)


def test_provider_factory_builds_bitget_provider() -> None:
    provider = create_market_data_provider(
        Settings(
            MARKET_DATA_PROVIDER="bitget",
            BITGET_PRODUCT_TYPE="USDT-FUTURES",
            BITGET_MARGIN_COIN="USDT",
        )
    )

    assert isinstance(provider, BitgetMarketDataProvider)
    assert provider.product_type == "USDT-FUTURES"

