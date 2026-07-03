from app.core.config import Settings
from app.exchange.base import MarketDataProvider
from app.exchange.bitget.client import BitgetReadOnlyClient
from app.exchange.mock import MockMarketDataProvider


def create_market_data_provider(settings: Settings) -> MarketDataProvider:
    provider = settings.market_data_provider.strip().lower()
    if provider == "mock":
        return MockMarketDataProvider()
    if provider == "bitget":
        return BitgetReadOnlyClient(
            base_url=settings.bitget_base_url,
            product_type=settings.bitget_product_type,
            api_key=settings.bitget_api_key,
            api_secret=settings.bitget_api_secret,
            passphrase=settings.bitget_api_passphrase,
        )
    raise ValueError(f"Unsupported market data provider: {settings.market_data_provider}")
