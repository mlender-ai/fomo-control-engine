from app.core.config import Settings
from app.exchange.base import MarketDataProvider
from app.exchange.bitget.client import BitgetClient
from app.exchange.bitget.provider import BitgetMarketDataProvider
from app.exchange.mock import MockMarketDataProvider


def create_market_data_provider(settings: Settings) -> MarketDataProvider:
    provider = settings.market_data_provider.strip().lower()
    if provider == "mock":
        return MockMarketDataProvider()
    if provider == "bitget":
        return BitgetMarketDataProvider(
            client=BitgetClient(
                base_url=settings.bitget_base_url,
                api_key=settings.bitget_api_key,
                api_secret=settings.bitget_api_secret,
                passphrase=settings.bitget_api_passphrase,
                locale=settings.bitget_locale,
            ),
            product_type=settings.bitget_product_type,
            margin_coin=settings.bitget_margin_coin,
        )
    raise ValueError(f"Unsupported market data provider: {settings.market_data_provider}")
