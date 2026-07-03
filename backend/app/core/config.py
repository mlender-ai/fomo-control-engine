from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "FOMO Control Engine"
    env: str = "local"
    cors_origins: str = "http://127.0.0.1:8876,http://localhost:8876"
    database_url: str = "sqlite:///./fomo_control_engine.db"
    market_data_provider: str = "mock"
    default_symbols: str = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT"
    bitget_base_url: str = "https://api.bitget.com"
    bitget_product_type: str = "usdt-futures"
    bitget_api_key: str = ""
    bitget_api_secret: str = ""
    bitget_api_passphrase: str = ""

    model_config = SettingsConfigDict(
        env_prefix="FCE_",
        env_file=".env",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def symbol_list(self) -> list[str]:
        return [symbol.strip().upper() for symbol in self.default_symbols.split(",") if symbol.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
