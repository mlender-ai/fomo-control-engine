from functools import lru_cache
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field("FOMO Control Engine", validation_alias=AliasChoices("FCE_APP_NAME", "APP_NAME"))
    env: str = Field("local", validation_alias=AliasChoices("FCE_ENV", "APP_ENV"))
    cors_origins: str = Field(
        "http://127.0.0.1:8876,http://localhost:8876",
        validation_alias=AliasChoices("FCE_CORS_ORIGINS", "CORS_ORIGINS"),
    )
    database_url: str = Field("sqlite:///./fomo_control_engine.db", validation_alias=AliasChoices("FCE_DATABASE_URL", "DATABASE_URL"))
    market_data_provider: str = Field("mock", validation_alias=AliasChoices("FCE_MARKET_DATA_PROVIDER", "MARKET_DATA_PROVIDER"))
    default_symbols: str = Field(
        "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT",
        validation_alias=AliasChoices("FCE_DEFAULT_SYMBOLS", "DEFAULT_SYMBOLS"),
    )
    bitget_base_url: str = Field("https://api.bitget.com", validation_alias=AliasChoices("FCE_BITGET_BASE_URL", "BITGET_BASE_URL"))
    bitget_product_type: str = Field("USDT-FUTURES", validation_alias=AliasChoices("FCE_BITGET_PRODUCT_TYPE", "BITGET_PRODUCT_TYPE"))
    bitget_margin_coin: str = Field("USDT", validation_alias=AliasChoices("FCE_BITGET_MARGIN_COIN", "BITGET_MARGIN_COIN"))
    bitget_locale: str = Field("en-US", validation_alias=AliasChoices("FCE_BITGET_LOCALE", "BITGET_LOCALE"))
    bitget_api_key: str = Field("", validation_alias=AliasChoices("FCE_BITGET_API_KEY", "BITGET_API_KEY"))
    bitget_api_secret: str = Field("", validation_alias=AliasChoices("FCE_BITGET_API_SECRET", "BITGET_API_SECRET"))
    bitget_api_passphrase: str = Field("", validation_alias=AliasChoices("FCE_BITGET_API_PASSPHRASE", "BITGET_API_PASSPHRASE"))
    run_live_bitget_tests: bool = Field(False, validation_alias=AliasChoices("FCE_RUN_LIVE_BITGET_TESTS", "RUN_LIVE_BITGET_TESTS"))
    openai_api_key: str = Field("", validation_alias=AliasChoices("FCE_OPENAI_API_KEY", "OPENAI_API_KEY"))
    insight_stale_after_minutes: int = Field(30, validation_alias=AliasChoices("FCE_INSIGHT_STALE_AFTER_MINUTES", "INSIGHT_STALE_AFTER_MINUTES"))
    insight_price_drift_stale_pct: float = Field(3.0, validation_alias=AliasChoices("FCE_INSIGHT_PRICE_DRIFT_STALE_PCT", "INSIGHT_PRICE_DRIFT_STALE_PCT"))
    live_position_sync_interval_seconds: int = Field(30, validation_alias=AliasChoices("FCE_LIVE_POSITION_SYNC_INTERVAL_SECONDS", "LIVE_POSITION_SYNC_INTERVAL_SECONDS"))
    insight_auto_refresh_enabled: bool = Field(False, validation_alias=AliasChoices("FCE_INSIGHT_AUTO_REFRESH_ENABLED", "INSIGHT_AUTO_REFRESH_ENABLED"))

    model_config = SettingsConfigDict(
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
