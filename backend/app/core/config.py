from functools import lru_cache
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field("FOMO Control Engine", validation_alias=AliasChoices("FCE_APP_NAME", "APP_NAME"))
    env: str = Field("local", validation_alias=AliasChoices("FCE_ENV", "APP_ENV"))
    demo_mode: bool = Field(False, validation_alias=AliasChoices("FCE_DEMO_MODE", "DEMO_MODE"))
    cors_origins: str = Field(
        "http://127.0.0.1:8876,http://localhost:8876",
        validation_alias=AliasChoices("FCE_CORS_ORIGINS", "CORS_ORIGINS"),
    )
    database_url: str = Field(
        "sqlite:///./fomo_control_engine.db",
        validation_alias=AliasChoices("FCE_DATABASE_URL", "DATABASE_URL"),
    )
    market_data_provider: str = Field(
        "mock",
        validation_alias=AliasChoices("FCE_MARKET_DATA_PROVIDER", "MARKET_DATA_PROVIDER"),
    )
    default_symbols: str = Field(
        "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT",
        validation_alias=AliasChoices("FCE_DEFAULT_SYMBOLS", "DEFAULT_SYMBOLS"),
    )
    bitget_base_url: str = Field(
        "https://api.bitget.com",
        validation_alias=AliasChoices("FCE_BITGET_BASE_URL", "BITGET_BASE_URL"),
    )
    bitget_product_type: str = Field(
        "USDT-FUTURES",
        validation_alias=AliasChoices("FCE_BITGET_PRODUCT_TYPE", "BITGET_PRODUCT_TYPE"),
    )
    bitget_margin_coin: str = Field(
        "USDT",
        validation_alias=AliasChoices("FCE_BITGET_MARGIN_COIN", "BITGET_MARGIN_COIN"),
    )
    bitget_locale: str = Field("en-US", validation_alias=AliasChoices("FCE_BITGET_LOCALE", "BITGET_LOCALE"))
    bitget_api_key: str = Field("", validation_alias=AliasChoices("FCE_BITGET_API_KEY", "BITGET_API_KEY"))
    bitget_api_secret: str = Field("", validation_alias=AliasChoices("FCE_BITGET_API_SECRET", "BITGET_API_SECRET"))
    bitget_api_passphrase: str = Field(
        "",
        validation_alias=AliasChoices("FCE_BITGET_API_PASSPHRASE", "BITGET_API_PASSPHRASE"),
    )
    bitget_trade_fill_lookback_hours: int = Field(
        96,
        validation_alias=AliasChoices("FCE_BITGET_TRADE_FILL_LOOKBACK_HOURS", "BITGET_TRADE_FILL_LOOKBACK_HOURS"),
    )
    bitget_trade_fill_cache_ttl_seconds: int = Field(
        60,
        validation_alias=AliasChoices(
            "FCE_BITGET_TRADE_FILL_CACHE_TTL_SECONDS",
            "BITGET_TRADE_FILL_CACHE_TTL_SECONDS",
        ),
    )
    bitget_trade_fill_max_rows: int = Field(
        50_000,
        ge=1_000,
        le=500_000,
        validation_alias=AliasChoices("FCE_BITGET_TRADE_FILL_MAX_ROWS", "BITGET_TRADE_FILL_MAX_ROWS"),
    )
    bitget_liquidation_history_enabled: bool = Field(
        True,
        validation_alias=AliasChoices(
            "FCE_BITGET_LIQUIDATION_HISTORY_ENABLED",
            "BITGET_LIQUIDATION_HISTORY_ENABLED",
        ),
    )
    bitget_liquidation_history_pages: int = Field(
        3,
        ge=1,
        le=10,
        validation_alias=AliasChoices(
            "FCE_BITGET_LIQUIDATION_HISTORY_PAGES",
            "BITGET_LIQUIDATION_HISTORY_PAGES",
        ),
    )
    derivative_tracking_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_DERIVATIVE_TRACKING_ENABLED", "DERIVATIVE_TRACKING_ENABLED"),
    )
    derivative_tracking_interval_seconds: int = Field(
        300,
        validation_alias=AliasChoices(
            "FCE_DERIVATIVE_TRACKING_INTERVAL_SECONDS",
            "DERIVATIVE_TRACKING_INTERVAL_SECONDS",
        ),
    )
    hyperliquid_whale_tracking_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_HYPERLIQUID_WHALE_TRACKING_ENABLED", "HYPERLIQUID_WHALE_TRACKING_ENABLED"),
    )
    hyperliquid_info_url: str = Field(
        "https://api.hyperliquid.xyz/info",
        validation_alias=AliasChoices("FCE_HYPERLIQUID_INFO_URL", "HYPERLIQUID_INFO_URL"),
    )
    hyperliquid_leaderboard_url: str = Field(
        "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard",
        validation_alias=AliasChoices("FCE_HYPERLIQUID_LEADERBOARD_URL", "HYPERLIQUID_LEADERBOARD_URL"),
    )
    hyperliquid_whale_discovery_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_HYPERLIQUID_WHALE_DISCOVERY_ENABLED", "HYPERLIQUID_WHALE_DISCOVERY_ENABLED"),
    )
    hyperliquid_whale_discovery_interval_seconds: int = Field(
        3600,
        validation_alias=AliasChoices(
            "FCE_HYPERLIQUID_WHALE_DISCOVERY_INTERVAL_SECONDS",
            "HYPERLIQUID_WHALE_DISCOVERY_INTERVAL_SECONDS",
        ),
    )
    hyperliquid_whale_discovery_min_account_usd: float = Field(
        1_000_000.0,
        validation_alias=AliasChoices("FCE_HYPERLIQUID_WHALE_DISCOVERY_MIN_ACCOUNT_USD", "HYPERLIQUID_WHALE_DISCOVERY_MIN_ACCOUNT_USD"),
    )
    hyperliquid_whale_discovery_min_month_pnl_usd: float = Field(
        100_000.0,
        validation_alias=AliasChoices("FCE_HYPERLIQUID_WHALE_DISCOVERY_MIN_MONTH_PNL_USD", "HYPERLIQUID_WHALE_DISCOVERY_MIN_MONTH_PNL_USD"),
    )
    hyperliquid_whale_discovery_min_month_roi: float = Field(
        0.02,
        validation_alias=AliasChoices("FCE_HYPERLIQUID_WHALE_DISCOVERY_MIN_MONTH_ROI", "HYPERLIQUID_WHALE_DISCOVERY_MIN_MONTH_ROI"),
    )
    hyperliquid_whale_discovery_min_month_volume_usd: float = Field(
        10_000_000.0,
        validation_alias=AliasChoices("FCE_HYPERLIQUID_WHALE_DISCOVERY_MIN_MONTH_VOLUME_USD", "HYPERLIQUID_WHALE_DISCOVERY_MIN_MONTH_VOLUME_USD"),
    )
    hyperliquid_whale_discovery_max_turnover: float = Field(
        250.0,
        validation_alias=AliasChoices("FCE_HYPERLIQUID_WHALE_DISCOVERY_MAX_TURNOVER", "HYPERLIQUID_WHALE_DISCOVERY_MAX_TURNOVER"),
    )
    hyperliquid_whale_discovery_scan_limit: int = Field(
        120,
        validation_alias=AliasChoices("FCE_HYPERLIQUID_WHALE_DISCOVERY_SCAN_LIMIT", "HYPERLIQUID_WHALE_DISCOVERY_SCAN_LIMIT"),
    )
    hyperliquid_whale_directional_slots: int = Field(
        8,
        validation_alias=AliasChoices("FCE_HYPERLIQUID_WHALE_DIRECTIONAL_SLOTS", "HYPERLIQUID_WHALE_DIRECTIONAL_SLOTS"),
    )
    hyperliquid_whale_focus_symbols: str = Field(
        "BTC,ETH",
        validation_alias=AliasChoices("FCE_HYPERLIQUID_WHALE_FOCUS_SYMBOLS", "HYPERLIQUID_WHALE_FOCUS_SYMBOLS"),
    )
    hyperliquid_whale_poll_interval_seconds: int = Field(
        30,
        ge=30,
        validation_alias=AliasChoices("FCE_HYPERLIQUID_WHALE_POLL_INTERVAL_SECONDS", "HYPERLIQUID_WHALE_POLL_INTERVAL_SECONDS"),
    )
    hyperliquid_whale_alert_batch_window_seconds: int = Field(
        180,
        ge=30,
        le=900,
        validation_alias=AliasChoices(
            "FCE_HYPERLIQUID_WHALE_ALERT_BATCH_WINDOW_SECONDS",
            "HYPERLIQUID_WHALE_ALERT_BATCH_WINDOW_SECONDS",
        ),
    )
    hyperliquid_whale_min_size_usd: float = Field(
        100000.0,
        validation_alias=AliasChoices("FCE_HYPERLIQUID_WHALE_MIN_SIZE_USD", "HYPERLIQUID_WHALE_MIN_SIZE_USD"),
    )
    hyperliquid_whale_initial_lookback_hours: int = Field(
        168,
        validation_alias=AliasChoices("FCE_HYPERLIQUID_WHALE_INITIAL_LOOKBACK_HOURS", "HYPERLIQUID_WHALE_INITIAL_LOOKBACK_HOURS"),
    )
    hyperliquid_whale_max_wallets: int = Field(
        20,
        validation_alias=AliasChoices("FCE_HYPERLIQUID_WHALE_MAX_WALLETS", "HYPERLIQUID_WHALE_MAX_WALLETS"),
    )
    hyperliquid_request_timeout_seconds: float = Field(
        10.0,
        validation_alias=AliasChoices("FCE_HYPERLIQUID_REQUEST_TIMEOUT_SECONDS", "HYPERLIQUID_REQUEST_TIMEOUT_SECONDS"),
    )
    derivative_ratio_period: str = Field(
        "5m",
        validation_alias=AliasChoices("FCE_DERIVATIVE_RATIO_PERIOD", "DERIVATIVE_RATIO_PERIOD"),
    )
    coinglass_api_key: str = Field("", validation_alias=AliasChoices("FCE_COINGLASS_API_KEY", "COINGLASS_API_KEY"))
    coinglass_base_url: str = Field(
        "https://open-api-v4.coinglass.com",
        validation_alias=AliasChoices("FCE_COINGLASS_BASE_URL", "COINGLASS_BASE_URL"),
    )
    coinglass_exchange_list: str = Field(
        "Binance,OKX,Bybit",
        validation_alias=AliasChoices("FCE_COINGLASS_EXCHANGE_LIST", "COINGLASS_EXCHANGE_LIST"),
    )
    coinglass_top_ratio_exchange: str = Field(
        "Binance",
        validation_alias=AliasChoices("FCE_COINGLASS_TOP_RATIO_EXCHANGE", "COINGLASS_TOP_RATIO_EXCHANGE"),
    )
    coinglass_interval: str = Field(
        "4h",
        validation_alias=AliasChoices("FCE_COINGLASS_INTERVAL", "COINGLASS_INTERVAL"),
    )
    coinglass_liquidation_interval: str = Field(
        "1h",
        validation_alias=AliasChoices("FCE_COINGLASS_LIQUIDATION_INTERVAL", "COINGLASS_LIQUIDATION_INTERVAL"),
    )
    coinglass_heatmap_range: str = Field(
        "3d",
        validation_alias=AliasChoices("FCE_COINGLASS_HEATMAP_RANGE", "COINGLASS_HEATMAP_RANGE"),
    )
    coinglass_rate_limit_per_minute: int = Field(
        30,
        validation_alias=AliasChoices("FCE_COINGLASS_RATE_LIMIT_PER_MINUTE", "COINGLASS_RATE_LIMIT_PER_MINUTE"),
    )
    coinglass_requests_per_symbol: int = Field(
        11,
        validation_alias=AliasChoices("FCE_COINGLASS_REQUESTS_PER_SYMBOL", "COINGLASS_REQUESTS_PER_SYMBOL"),
    )
    occ_options_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_OCC_OPTIONS_ENABLED", "OCC_OPTIONS_ENABLED"),
    )
    occ_options_cache_ttl_seconds: int = Field(
        1800,
        ge=60,
        validation_alias=AliasChoices("FCE_OCC_OPTIONS_CACHE_TTL_SECONDS", "OCC_OPTIONS_CACHE_TTL_SECONDS"),
    )
    occ_options_timeout_seconds: float = Field(
        10.0,
        gt=0,
        validation_alias=AliasChoices("FCE_OCC_OPTIONS_TIMEOUT_SECONDS", "OCC_OPTIONS_TIMEOUT_SECONDS"),
    )
    db_backup_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_DB_BACKUP_ENABLED", "DB_BACKUP_ENABLED"),
    )
    db_backup_dir: str = Field("./backups", validation_alias=AliasChoices("FCE_DB_BACKUP_DIR", "DB_BACKUP_DIR"))
    db_backup_interval_seconds: int = Field(
        86400,
        validation_alias=AliasChoices("FCE_DB_BACKUP_INTERVAL_SECONDS", "DB_BACKUP_INTERVAL_SECONDS"),
    )
    db_retention_days: int = Field(30, validation_alias=AliasChoices("FCE_DB_RETENTION_DAYS", "DB_RETENTION_DAYS"))
    db_backup_keep_days: int = Field(
        14,
        validation_alias=AliasChoices("FCE_DB_BACKUP_KEEP_DAYS", "DB_BACKUP_KEEP_DAYS"),
    )
    db_trade_fill_retention_days: int = Field(
        # 2일: 머니플로우/CVD는 최근 짧은 창 + 행수 상한으로만 읽는다(trade_cache._latest_rows).
        # 하루 ~2M행 폭증(2026-07-23 12.8GB 사건) — 7일이면 상시 ~4-5GB. 2일이면 ~1GB.
        2,
        validation_alias=AliasChoices("FCE_DB_TRADE_FILL_RETENTION_DAYS", "DB_TRADE_FILL_RETENTION_DAYS"),
    )
    db_alert_retention_days: int = Field(
        90,
        validation_alias=AliasChoices("FCE_DB_ALERT_RETENTION_DAYS", "DB_ALERT_RETENTION_DAYS"),
    )
    db_worker_heartbeat_retention_days: int = Field(
        14,
        validation_alias=AliasChoices(
            "FCE_DB_WORKER_HEARTBEAT_RETENTION_DAYS",
            "DB_WORKER_HEARTBEAT_RETENTION_DAYS",
        ),
    )
    db_closed_snapshot_retention_days: int = Field(
        30,
        validation_alias=AliasChoices("FCE_DB_CLOSED_SNAPSHOT_RETENTION_DAYS", "DB_CLOSED_SNAPSHOT_RETENTION_DAYS"),
    )
    db_snapshot_downsample_minutes: int = Field(
        60,
        validation_alias=AliasChoices("FCE_DB_SNAPSHOT_DOWNSAMPLE_MINUTES", "DB_SNAPSHOT_DOWNSAMPLE_MINUTES"),
    )
    db_deriv_metrics_raw_days: int = Field(
        90,
        validation_alias=AliasChoices("FCE_DB_DERIV_METRICS_RAW_DAYS", "DB_DERIV_METRICS_RAW_DAYS"),
    )
    db_deriv_metrics_downsample_minutes: int = Field(
        1440,
        validation_alias=AliasChoices(
            "FCE_DB_DERIV_METRICS_DOWNSAMPLE_MINUTES",
            "DB_DERIV_METRICS_DOWNSAMPLE_MINUTES",
        ),
    )
    db_liquidation_event_retention_days: int = Field(
        30,
        validation_alias=AliasChoices(
            "FCE_DB_LIQUIDATION_EVENT_RETENTION_DAYS",
            "DB_LIQUIDATION_EVENT_RETENTION_DAYS",
        ),
    )
    db_maintenance_timezone: str = Field(
        "Asia/Seoul",
        validation_alias=AliasChoices("FCE_DB_MAINTENANCE_TIMEZONE", "DB_MAINTENANCE_TIMEZONE"),
    )
    log_level: str = Field("INFO", validation_alias=AliasChoices("FCE_LOG_LEVEL", "LOG_LEVEL"))
    log_dir: str = Field("./logs", validation_alias=AliasChoices("FCE_LOG_DIR", "LOG_DIR"))
    harmonic_zigzag_atr_multiplier: float = Field(
        2.0,
        validation_alias=AliasChoices("FCE_HARMONIC_ZIGZAG_ATR_MULTIPLIER", "HARMONIC_ZIGZAG_ATR_MULTIPLIER"),
    )
    harmonic_min_confidence: int = Field(
        55,
        validation_alias=AliasChoices("FCE_HARMONIC_MIN_CONFIDENCE", "HARMONIC_MIN_CONFIDENCE"),
    )
    harmonic_ratio_tolerance_multiplier: float = Field(
        1.0,
        validation_alias=AliasChoices(
            "FCE_HARMONIC_RATIO_TOLERANCE_MULTIPLIER",
            "HARMONIC_RATIO_TOLERANCE_MULTIPLIER",
        ),
    )
    wyckoff_event_min_confidence: int = Field(
        55,
        validation_alias=AliasChoices("FCE_WYCKOFF_EVENT_MIN_CONFIDENCE", "WYCKOFF_EVENT_MIN_CONFIDENCE"),
    )
    min_invalidation_level_score: int = Field(
        40,
        validation_alias=AliasChoices("FCE_MIN_INVALIDATION_LEVEL_SCORE", "MIN_INVALIDATION_LEVEL_SCORE"),
    )
    # WO-53: 방향 히스테리시스 튜너블 (hard bound는 param_registry.py). 전환 관성 파라미터 —
    # 진동 흡수용이며, build_confluence가 bound로 클램프한다.
    directional_ema_span: float = Field(
        2.0,
        validation_alias=AliasChoices("FCE_DIRECTIONAL_EMA_SPAN", "DIRECTIONAL_EMA_SPAN"),
    )
    directional_flip_margin: float = Field(
        0.30,
        validation_alias=AliasChoices("FCE_DIRECTIONAL_FLIP_MARGIN", "DIRECTIONAL_FLIP_MARGIN"),
    )
    directional_flip_persist: int = Field(
        2,
        validation_alias=AliasChoices("FCE_DIRECTIONAL_FLIP_PERSIST", "DIRECTIONAL_FLIP_PERSIST"),
    )
    run_live_bitget_tests: bool = Field(
        False,
        validation_alias=AliasChoices("FCE_RUN_LIVE_BITGET_TESTS", "RUN_LIVE_BITGET_TESTS"),
    )
    openai_api_key: str = Field("", validation_alias=AliasChoices("FCE_OPENAI_API_KEY", "OPENAI_API_KEY"))
    insight_model: str = Field(
        "gpt-4.1-mini",
        validation_alias=AliasChoices("FCE_INSIGHT_MODEL", "INSIGHT_MODEL"),
    )
    insight_stale_after_minutes: int = Field(
        30,
        validation_alias=AliasChoices("FCE_INSIGHT_STALE_AFTER_MINUTES", "INSIGHT_STALE_AFTER_MINUTES"),
    )
    insight_price_drift_stale_pct: float = Field(
        3.0,
        validation_alias=AliasChoices("FCE_INSIGHT_PRICE_DRIFT_STALE_PCT", "INSIGHT_PRICE_DRIFT_STALE_PCT"),
    )
    live_position_sync_interval_seconds: int = Field(
        30,
        validation_alias=AliasChoices(
            "FCE_LIVE_POSITION_SYNC_INTERVAL_SECONDS",
            "LIVE_POSITION_SYNC_INTERVAL_SECONDS",
        ),
    )
    background_worker_enabled: bool = Field(
        True,
        validation_alias=AliasChoices(
            "FCE_WORKER_ENABLED",
            "FCE_BACKGROUND_WORKER_ENABLED",
            "BACKGROUND_WORKER_ENABLED",
        ),
    )
    worker_startup_delay_seconds: int = Field(
        3,
        validation_alias=AliasChoices("FCE_WORKER_STARTUP_DELAY_SECONDS", "WORKER_STARTUP_DELAY_SECONDS"),
    )
    worker_sync_positions_interval_seconds: int = Field(
        90,
        validation_alias=AliasChoices(
            "FCE_WORKER_SYNC_POSITIONS_INTERVAL_SECONDS",
            "WORKER_SYNC_POSITIONS_INTERVAL_SECONDS",
        ),
    )
    worker_refresh_market_data_interval_seconds: int = Field(
        300,
        validation_alias=AliasChoices(
            "FCE_WORKER_REFRESH_MARKET_DATA_INTERVAL_SECONDS",
            "WORKER_REFRESH_MARKET_DATA_INTERVAL_SECONDS",
        ),
    )
    worker_score_candidates_interval_seconds: int = Field(
        86400,
        validation_alias=AliasChoices(
            "FCE_WORKER_SCORE_CANDIDATES_INTERVAL_SECONDS",
            "WORKER_SCORE_CANDIDATES_INTERVAL_SECONDS",
        ),
    )
    worker_regen_stale_insights_interval_seconds: int = Field(
        120,
        validation_alias=AliasChoices(
            "FCE_WORKER_REGEN_STALE_INSIGHTS_INTERVAL_SECONDS",
            "WORKER_REGEN_STALE_INSIGHTS_INTERVAL_SECONDS",
        ),
    )
    worker_detect_closures_interval_seconds: int = Field(
        90,
        validation_alias=AliasChoices(
            "FCE_WORKER_DETECT_CLOSURES_INTERVAL_SECONDS",
            "WORKER_DETECT_CLOSURES_INTERVAL_SECONDS",
        ),
    )
    worker_interim_scoring_interval_seconds: int = Field(
        86400,
        validation_alias=AliasChoices(
            "FCE_WORKER_INTERIM_SCORING_INTERVAL_SECONDS",
            "WORKER_INTERIM_SCORING_INTERVAL_SECONDS",
        ),
    )
    worker_alert_response_interval_seconds: int = Field(
        600,
        validation_alias=AliasChoices(
            "FCE_WORKER_ALERT_RESPONSE_INTERVAL_SECONDS",
            "WORKER_ALERT_RESPONSE_INTERVAL_SECONDS",
        ),
    )
    worker_scout_scan_interval_seconds: int = Field(
        900,
        validation_alias=AliasChoices(
            "FCE_WORKER_SCOUT_SCAN_INTERVAL_SECONDS",
            "WORKER_SCOUT_SCAN_INTERVAL_SECONDS",
        ),
    )
    worker_scout_scan_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_WORKER_SCOUT_SCAN_ENABLED", "WORKER_SCOUT_SCAN_ENABLED"),
    )
    worker_calibration_cache_interval_seconds: int = Field(
        1800,
        validation_alias=AliasChoices(
            "FCE_WORKER_CALIBRATION_CACHE_INTERVAL_SECONDS",
            "WORKER_CALIBRATION_CACHE_INTERVAL_SECONDS",
        ),
    )
    worker_candidate_scoring_enabled: bool = Field(
        True,
        validation_alias=AliasChoices(
            "FCE_WORKER_CANDIDATE_SCORING_ENABLED",
            "WORKER_CANDIDATE_SCORING_ENABLED",
        ),
    )
    worker_candidate_scoring_interval_seconds: int = Field(
        86400,
        validation_alias=AliasChoices(
            "FCE_WORKER_CANDIDATE_SCORING_INTERVAL_SECONDS",
            "WORKER_CANDIDATE_SCORING_INTERVAL_SECONDS",
        ),
    )
    worker_stance_backtest_enabled: bool = Field(
        True,
        validation_alias=AliasChoices(
            "FCE_WORKER_STANCE_BACKTEST_ENABLED",
            "WORKER_STANCE_BACKTEST_ENABLED",
        ),
    )
    worker_stance_backtest_interval_seconds: int = Field(
        86400,
        ge=3600,
        validation_alias=AliasChoices(
            "FCE_WORKER_STANCE_BACKTEST_INTERVAL_SECONDS",
            "WORKER_STANCE_BACKTEST_INTERVAL_SECONDS",
        ),
    )
    worker_user_fill_sync_enabled: bool = Field(
        True,
        validation_alias=AliasChoices(
            "FCE_WORKER_USER_FILL_SYNC_ENABLED",
            "WORKER_USER_FILL_SYNC_ENABLED",
        ),
    )
    worker_user_fill_sync_interval_seconds: int = Field(
        120,
        validation_alias=AliasChoices(
            "FCE_WORKER_USER_FILL_SYNC_INTERVAL_SECONDS",
            "WORKER_USER_FILL_SYNC_INTERVAL_SECONDS",
        ),
    )
    worker_symbol_catalog_interval_seconds: int = Field(
        86400,
        validation_alias=AliasChoices(
            "FCE_WORKER_SYMBOL_CATALOG_INTERVAL_SECONDS",
            "WORKER_SYMBOL_CATALOG_INTERVAL_SECONDS",
        ),
    )
    scout_watchlist_symbol_limit: int = Field(
        30,
        validation_alias=AliasChoices("FCE_SCOUT_WATCHLIST_SYMBOL_LIMIT", "SCOUT_WATCHLIST_SYMBOL_LIMIT"),
    )
    scout_tracking_symbol_limit: int = Field(
        10,
        validation_alias=AliasChoices("FCE_SCOUT_TRACKING_SYMBOL_LIMIT", "SCOUT_TRACKING_SYMBOL_LIMIT"),
    )
    scout_auto_arm_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_SCOUT_AUTO_ARM_ENABLED", "SCOUT_AUTO_ARM_ENABLED"),
    )
    scout_auto_arm_symbol_limit: int = Field(
        5,
        validation_alias=AliasChoices("FCE_SCOUT_AUTO_ARM_SYMBOL_LIMIT", "SCOUT_AUTO_ARM_SYMBOL_LIMIT"),
    )
    scout_max_armed_setups_per_symbol: int = Field(
        3,
        validation_alias=AliasChoices("FCE_SCOUT_MAX_ARMED_SETUPS_PER_SYMBOL", "SCOUT_MAX_ARMED_SETUPS_PER_SYMBOL"),
    )
    scout_harmonic_auto_arm_confidence: int = Field(
        70,
        validation_alias=AliasChoices(
            "FCE_SCOUT_HARMONIC_AUTO_ARM_CONFIDENCE",
            "SCOUT_HARMONIC_AUTO_ARM_CONFIDENCE",
        ),
    )
    scout_harmonic_auto_arm_distance_pct: float = Field(
        3.0,
        validation_alias=AliasChoices(
            "FCE_SCOUT_HARMONIC_AUTO_ARM_DISTANCE_PCT",
            "SCOUT_HARMONIC_AUTO_ARM_DISTANCE_PCT",
        ),
    )
    scout_level_auto_arm_score: int = Field(
        70,
        validation_alias=AliasChoices("FCE_SCOUT_LEVEL_AUTO_ARM_SCORE", "SCOUT_LEVEL_AUTO_ARM_SCORE"),
    )
    scout_level_auto_arm_distance_pct: float = Field(
        2.0,
        validation_alias=AliasChoices("FCE_SCOUT_LEVEL_AUTO_ARM_DISTANCE_PCT", "SCOUT_LEVEL_AUTO_ARM_DISTANCE_PCT"),
    )
    scout_wyckoff_auto_arm_confidence: int = Field(
        70,
        validation_alias=AliasChoices("FCE_SCOUT_WYCKOFF_AUTO_ARM_CONFIDENCE", "SCOUT_WYCKOFF_AUTO_ARM_CONFIDENCE"),
    )
    scout_setup_near_pct: float = Field(
        1.5,
        validation_alias=AliasChoices("FCE_SCOUT_SETUP_NEAR_PCT", "SCOUT_SETUP_NEAR_PCT"),
    )
    scout_setup_rearm_pct: float = Field(
        3.0,
        validation_alias=AliasChoices("FCE_SCOUT_SETUP_REARM_PCT", "SCOUT_SETUP_REARM_PCT"),
    )
    scout_setup_score_after_hours: float = Field(
        24.0,
        validation_alias=AliasChoices("FCE_SCOUT_SETUP_SCORE_AFTER_HOURS", "SCOUT_SETUP_SCORE_AFTER_HOURS"),
    )
    scout_conflict_setup_alerts_enabled: bool = Field(
        True,
        validation_alias=AliasChoices(
            "FCE_SCOUT_CONFLICT_SETUP_ALERTS_ENABLED",
            "SCOUT_CONFLICT_SETUP_ALERTS_ENABLED",
        ),
    )
    setup_min_invalidation_distance_pct: float = Field(
        0.8,
        validation_alias=AliasChoices(
            "FCE_SETUP_MIN_INVALIDATION_DISTANCE_PCT",
            "SETUP_MIN_INVALIDATION_DISTANCE_PCT",
        ),
    )
    setup_rr_display_cap: float = Field(
        10.0,
        validation_alias=AliasChoices("FCE_SETUP_RR_DISPLAY_CAP", "SETUP_RR_DISPLAY_CAP"),
    )
    entry_intent_max_per_symbol: int = Field(
        3,
        validation_alias=AliasChoices("FCE_ENTRY_INTENT_MAX_PER_SYMBOL", "ENTRY_INTENT_MAX_PER_SYMBOL"),
    )
    entry_intent_max_active: int = Field(
        20,
        validation_alias=AliasChoices("FCE_ENTRY_INTENT_MAX_ACTIVE", "ENTRY_INTENT_MAX_ACTIVE"),
    )
    entry_intent_default_expiry_days: int = Field(
        14,
        validation_alias=AliasChoices("FCE_ENTRY_INTENT_DEFAULT_EXPIRY_DAYS", "ENTRY_INTENT_DEFAULT_EXPIRY_DAYS"),
    )
    entry_intent_tight_tolerance_pct: float = Field(
        0.5,
        validation_alias=AliasChoices("FCE_ENTRY_INTENT_TIGHT_TOLERANCE_PCT", "ENTRY_INTENT_TIGHT_TOLERANCE_PCT"),
    )
    entry_intent_normal_tolerance_pct: float = Field(
        1.5,
        validation_alias=AliasChoices("FCE_ENTRY_INTENT_NORMAL_TOLERANCE_PCT", "ENTRY_INTENT_NORMAL_TOLERANCE_PCT"),
    )
    entry_intent_loose_tolerance_pct: float = Field(
        3.0,
        validation_alias=AliasChoices("FCE_ENTRY_INTENT_LOOSE_TOLERANCE_PCT", "ENTRY_INTENT_LOOSE_TOLERANCE_PCT"),
    )
    entry_intent_rearm_pct: float = Field(
        3.0,
        validation_alias=AliasChoices("FCE_ENTRY_INTENT_REARM_PCT", "ENTRY_INTENT_REARM_PCT"),
    )
    entry_intent_score_after_hours: float = Field(
        24.0,
        validation_alias=AliasChoices("FCE_ENTRY_INTENT_SCORE_AFTER_HOURS", "ENTRY_INTENT_SCORE_AFTER_HOURS"),
    )
    backtest_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_BACKTEST_ENABLED", "BACKTEST_ENABLED"),
    )
    backtest_min_window_candles: int = Field(
        60,
        validation_alias=AliasChoices("FCE_BACKTEST_MIN_WINDOW_CANDLES", "BACKTEST_MIN_WINDOW_CANDLES"),
    )
    backtest_lookahead_bars: int = Field(
        48,
        validation_alias=AliasChoices("FCE_BACKTEST_LOOKAHEAD_BARS", "BACKTEST_LOOKAHEAD_BARS"),
    )
    backtest_sample_floor: int = Field(
        10,
        validation_alias=AliasChoices("FCE_BACKTEST_SAMPLE_FLOOR", "BACKTEST_SAMPLE_FLOOR"),
    )
    backtest_taker_fee_pct: float = Field(
        0.06,
        validation_alias=AliasChoices("FCE_BACKTEST_TAKER_FEE_PCT", "BACKTEST_TAKER_FEE_PCT"),
    )
    backtest_slippage_crypto_pct: float = Field(
        0.03,
        validation_alias=AliasChoices("FCE_BACKTEST_SLIPPAGE_CRYPTO_PCT", "BACKTEST_SLIPPAGE_CRYPTO_PCT"),
    )
    backtest_slippage_stock_pct: float = Field(
        0.08,
        validation_alias=AliasChoices("FCE_BACKTEST_SLIPPAGE_STOCK_PCT", "BACKTEST_SLIPPAGE_STOCK_PCT"),
    )
    backtest_slippage_index_pct: float = Field(
        0.05,
        validation_alias=AliasChoices("FCE_BACKTEST_SLIPPAGE_INDEX_PCT", "BACKTEST_SLIPPAGE_INDEX_PCT"),
    )
    backtest_slippage_shallow_extra_pct: float = Field(
        0.05,
        validation_alias=AliasChoices("FCE_BACKTEST_SLIPPAGE_SHALLOW_EXTRA_PCT", "BACKTEST_SLIPPAGE_SHALLOW_EXTRA_PCT"),
    )
    backtest_shallow_quote_volume_24h: float = Field(
        3_000_000.0,
        validation_alias=AliasChoices("FCE_BACKTEST_SHALLOW_QUOTE_VOLUME_24H", "BACKTEST_SHALLOW_QUOTE_VOLUME_24H"),
    )
    backtest_data_quality_floor: int = Field(
        70,
        validation_alias=AliasChoices("FCE_BACKTEST_DATA_QUALITY_FLOOR", "BACKTEST_DATA_QUALITY_FLOOR"),
    )
    backtest_cache_ttl_hours: int = Field(
        24,
        validation_alias=AliasChoices("FCE_BACKTEST_CACHE_TTL_HOURS", "BACKTEST_CACHE_TTL_HOURS"),
    )
    backtest_bootstrap_iterations: int = Field(
        1000,
        validation_alias=AliasChoices("FCE_BACKTEST_BOOTSTRAP_ITERATIONS", "BACKTEST_BOOTSTRAP_ITERATIONS"),
    )
    backtest_ci_confidence: float = Field(
        0.95,
        validation_alias=AliasChoices("FCE_BACKTEST_CI_CONFIDENCE", "BACKTEST_CI_CONFIDENCE"),
    )
    backtest_oos_validation_ratio: float = Field(
        0.30,
        validation_alias=AliasChoices("FCE_BACKTEST_OOS_VALIDATION_RATIO", "BACKTEST_OOS_VALIDATION_RATIO"),
    )
    backtest_oos_unstable_gap_pct: float = Field(
        15.0,
        validation_alias=AliasChoices("FCE_BACKTEST_OOS_UNSTABLE_GAP_PCT", "BACKTEST_OOS_UNSTABLE_GAP_PCT"),
    )
    backtest_walk_forward_window_days: int = Field(
        180,
        validation_alias=AliasChoices("FCE_BACKTEST_WALK_FORWARD_WINDOW_DAYS", "BACKTEST_WALK_FORWARD_WINDOW_DAYS"),
    )
    backtest_walk_forward_step_days: int = Field(
        60,
        validation_alias=AliasChoices("FCE_BACKTEST_WALK_FORWARD_STEP_DAYS", "BACKTEST_WALK_FORWARD_STEP_DAYS"),
    )
    backtest_overlap_threshold: float = Field(
        0.7,
        validation_alias=AliasChoices("FCE_BACKTEST_OVERLAP_THRESHOLD", "BACKTEST_OVERLAP_THRESHOLD"),
    )
    regime_ma_period: int = Field(
        200,
        validation_alias=AliasChoices("FCE_REGIME_MA_PERIOD", "REGIME_MA_PERIOD"),
    )
    regime_ma_slope_window: int = Field(
        20,
        validation_alias=AliasChoices("FCE_REGIME_MA_SLOPE_WINDOW", "REGIME_MA_SLOPE_WINDOW"),
    )
    regime_ma_slope_threshold_pct: float = Field(
        1.0,
        validation_alias=AliasChoices("FCE_REGIME_MA_SLOPE_THRESHOLD_PCT", "REGIME_MA_SLOPE_THRESHOLD_PCT"),
    )
    regime_atr_lookback: int = Field(
        120,
        validation_alias=AliasChoices("FCE_REGIME_ATR_LOOKBACK", "REGIME_ATR_LOOKBACK"),
    )
    regime_atr_high_percentile: float = Field(
        70.0,
        validation_alias=AliasChoices("FCE_REGIME_ATR_HIGH_PERCENTILE", "REGIME_ATR_HIGH_PERCENTILE"),
    )
    universe_scanner_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_UNIVERSE_SCANNER_ENABLED", "UNIVERSE_SCANNER_ENABLED"),
    )
    universe_stage2_gate_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_UNIVERSE_STAGE2_GATE_ENABLED", "UNIVERSE_STAGE2_GATE_ENABLED"),
    )
    worker_universe_scan_interval_seconds: int = Field(
        1800,
        validation_alias=AliasChoices(
            "FCE_WORKER_UNIVERSE_SCAN_INTERVAL_SECONDS",
            "WORKER_UNIVERSE_SCAN_INTERVAL_SECONDS",
        ),
    )
    stance_backtest_symbols: str = Field(
        "BTCUSDT,ETHUSDT,SOXLUSDT",
        validation_alias=AliasChoices("FCE_STANCE_BACKTEST_SYMBOLS", "STANCE_BACKTEST_SYMBOLS"),
    )
    stance_backtest_history_bars: int = Field(
        2196,
        ge=120,
        le=5000,
        validation_alias=AliasChoices("FCE_STANCE_BACKTEST_HISTORY_BARS", "STANCE_BACKTEST_HISTORY_BARS"),
    )
    stance_backtest_horizon_bars: int = Field(
        6,
        ge=1,
        le=48,
        validation_alias=AliasChoices("FCE_STANCE_BACKTEST_HORIZON_BARS", "STANCE_BACKTEST_HORIZON_BARS"),
    )
    stance_backtest_sample_floor: int = Field(
        30,
        ge=10,
        validation_alias=AliasChoices("FCE_STANCE_BACKTEST_SAMPLE_FLOOR", "STANCE_BACKTEST_SAMPLE_FLOOR"),
    )
    universe_crypto_symbol_limit: int = Field(
        40,
        validation_alias=AliasChoices("FCE_UNIVERSE_CRYPTO_SYMBOL_LIMIT", "UNIVERSE_CRYPTO_SYMBOL_LIMIT"),
    )
    universe_stock_symbol_limit: int = Field(
        40,
        validation_alias=AliasChoices("FCE_UNIVERSE_STOCK_SYMBOL_LIMIT", "UNIVERSE_STOCK_SYMBOL_LIMIT"),
    )
    # 유니버스 큐레이션(2026-07-10 사용자 지시): 거래량 순위만으로는 마이크로캡 잡주가 올라온다.
    # base ticker CSV. 빈 문자열이면 허용 리스트 비활성(전체 카탈로그). 지수(index)는 6종 전부 메이저라 제외.
    # 코인: 시총 10위권(스테이블 제외) — 정적 스냅샷이므로 순위 변동 시 env로 갱신.
    universe_crypto_allowlist: str = Field(
        "BTC,ETH,XRP,BNB,SOL,DOGE,ADA,TRX,LINK,HYPE",
        validation_alias=AliasChoices("FCE_UNIVERSE_CRYPTO_ALLOWLIST", "UNIVERSE_CRYPTO_ALLOWLIST"),
    )
    # 주식: 미국 상장 시총 상위(메가캡) + 최근 핫한 기업(AI 인프라·퀀텀·우주·원전·크립토 프록시).
    universe_stock_allowlist: str = Field(
        "AAPL,MSFT,NVDA,GOOGL,AMZN,META,AVGO,TSLA,BRKB,LLY,JPM,WMT,V,UNH,XOM,ORCL,COST,NFLX,CRM,AMD,"
        "ADBE,CSCO,MCD,IBM,QCOM,TXN,GE,ISRG,GS,INTC,MU,LRCX,ADI,KLAC,AMAT,PANW,CRWD,ETN,BA,KO,CAT,"
        "LMT,NOW,TSM,ASML,BABA,MRVL,DELL,SNOW,ARM,NKE,"
        "MSTR,COIN,HOOD,PLTR,SMCI,IONQ,RKLB,OKLO,SMR,CRWV,NBIS,IREN,RDDT,ASTS,JOBY",
        validation_alias=AliasChoices("FCE_UNIVERSE_STOCK_ALLOWLIST", "UNIVERSE_STOCK_ALLOWLIST"),
    )
    universe_round_robin_batch_size: int = Field(
        12,
        validation_alias=AliasChoices("FCE_UNIVERSE_ROUND_ROBIN_BATCH_SIZE", "UNIVERSE_ROUND_ROBIN_BATCH_SIZE"),
    )
    universe_min_quote_volume_24h: float = Field(
        1_000_000.0,
        validation_alias=AliasChoices("FCE_UNIVERSE_MIN_QUOTE_VOLUME_24H", "UNIVERSE_MIN_QUOTE_VOLUME_24H"),
    )
    universe_min_confidence: int = Field(
        70,
        validation_alias=AliasChoices("FCE_UNIVERSE_MIN_CONFIDENCE", "UNIVERSE_MIN_CONFIDENCE"),
    )
    universe_backtest_min_sample: int = Field(
        30,
        validation_alias=AliasChoices("FCE_UNIVERSE_BACKTEST_MIN_SAMPLE", "UNIVERSE_BACKTEST_MIN_SAMPLE"),
    )
    universe_backtest_min_win_1r_pct: float = Field(
        55.0,
        validation_alias=AliasChoices("FCE_UNIVERSE_BACKTEST_MIN_WIN_1R_PCT", "UNIVERSE_BACKTEST_MIN_WIN_1R_PCT"),
    )
    universe_backtest_min_ci_low_pct: float = Field(
        50.0,
        validation_alias=AliasChoices("FCE_UNIVERSE_BACKTEST_MIN_CI_LOW_PCT", "UNIVERSE_BACKTEST_MIN_CI_LOW_PCT"),
    )
    # WO-37 자율 검증 루프 — 비대칭 자율 (강등/격리 자율, 승격/복귀 제안-승인).
    autonomy_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_AUTONOMY_ENABLED", "AUTONOMY_ENABLED"),
    )
    autonomy_weekly_transition_cap: int = Field(
        5,
        validation_alias=AliasChoices("FCE_AUTONOMY_WEEKLY_TRANSITION_CAP", "AUTONOMY_WEEKLY_TRANSITION_CAP"),
    )
    decay_live_divergence_pct: float = Field(
        20.0,
        validation_alias=AliasChoices("FCE_DECAY_LIVE_DIVERGENCE_PCT", "DECAY_LIVE_DIVERGENCE_PCT"),
    )
    decay_live_min_sample: int = Field(
        15,
        validation_alias=AliasChoices("FCE_DECAY_LIVE_MIN_SAMPLE", "DECAY_LIVE_MIN_SAMPLE"),
    )
    signature_validated_min_sample: int = Field(
        30,
        validation_alias=AliasChoices("FCE_SIGNATURE_VALIDATED_MIN_SAMPLE", "SIGNATURE_VALIDATED_MIN_SAMPLE"),
    )
    universe_daily_alert_limit: int = Field(
        3,
        validation_alias=AliasChoices("FCE_UNIVERSE_DAILY_ALERT_LIMIT", "UNIVERSE_DAILY_ALERT_LIMIT"),
    )
    universe_symbol_cooldown_hours: int = Field(
        48,
        validation_alias=AliasChoices("FCE_UNIVERSE_SYMBOL_COOLDOWN_HOURS", "UNIVERSE_SYMBOL_COOLDOWN_HOURS"),
    )
    universe_blacklist: str = Field(
        "",
        validation_alias=AliasChoices("FCE_UNIVERSE_BLACKLIST", "UNIVERSE_BLACKLIST"),
    )
    universe_classes_enabled: str = Field(
        "crypto,stock,index",
        validation_alias=AliasChoices("FCE_UNIVERSE_CLASSES_ENABLED", "UNIVERSE_CLASSES_ENABLED"),
    )
    worker_backoff_failure_threshold: int = Field(
        3,
        validation_alias=AliasChoices("FCE_WORKER_BACKOFF_FAILURE_THRESHOLD", "WORKER_BACKOFF_FAILURE_THRESHOLD"),
    )
    worker_backoff_max_multiplier: int = Field(
        8,
        validation_alias=AliasChoices("FCE_WORKER_BACKOFF_MAX_MULTIPLIER", "WORKER_BACKOFF_MAX_MULTIPLIER"),
    )
    telegram_bot_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_TELEGRAM_BOT_ENABLED", "TELEGRAM_BOT_ENABLED"),
    )
    telegram_bot_token: str = Field(
        "",
        validation_alias=AliasChoices("FCE_TELEGRAM_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
    )
    telegram_chat_id: str = Field("", validation_alias=AliasChoices("FCE_TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID"))
    telegram_allowed_chat_ids: str = Field(
        "",
        validation_alias=AliasChoices("FCE_TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_ALLOWED_CHAT_IDS"),
    )
    telegram_alerts_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_TELEGRAM_ALERTS_ENABLED", "TELEGRAM_ALERTS_ENABLED"),
    )
    telegram_alert_min_interval_seconds: int = Field(
        600,
        validation_alias=AliasChoices(
            "FCE_TELEGRAM_ALERT_MIN_INTERVAL_SECONDS",
            "TELEGRAM_ALERT_MIN_INTERVAL_SECONDS",
        ),
    )
    telegram_command_timeout_seconds: int = Field(
        10,
        validation_alias=AliasChoices("FCE_TELEGRAM_COMMAND_TIMEOUT_SECONDS", "TELEGRAM_COMMAND_TIMEOUT_SECONDS"),
    )
    telegram_daily_summary_time: str = Field(
        "08:30",
        validation_alias=AliasChoices("FCE_TELEGRAM_DAILY_SUMMARY_TIME", "TELEGRAM_DAILY_SUMMARY_TIME"),
    )
    telegram_weekly_calibration_enabled: bool = Field(
        True,
        validation_alias=AliasChoices(
            "FCE_TELEGRAM_WEEKLY_CALIBRATION_ENABLED",
            "TELEGRAM_WEEKLY_CALIBRATION_ENABLED",
        ),
    )
    telegram_weekly_calibration_day: int = Field(
        6,
        validation_alias=AliasChoices("FCE_TELEGRAM_WEEKLY_CALIBRATION_DAY", "TELEGRAM_WEEKLY_CALIBRATION_DAY"),
    )
    telegram_weekly_calibration_time: str = Field(
        "20:00",
        validation_alias=AliasChoices("FCE_TELEGRAM_WEEKLY_CALIBRATION_TIME", "TELEGRAM_WEEKLY_CALIBRATION_TIME"),
    )
    telegram_quiet_hours_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_TELEGRAM_QUIET_HOURS_ENABLED", "TELEGRAM_QUIET_HOURS_ENABLED"),
    )
    telegram_quiet_hours_start: str = Field(
        "01:00",
        validation_alias=AliasChoices("FCE_TELEGRAM_QUIET_HOURS_START", "TELEGRAM_QUIET_HOURS_START"),
    )
    telegram_quiet_hours_end: str = Field(
        "08:00",
        validation_alias=AliasChoices("FCE_TELEGRAM_QUIET_HOURS_END", "TELEGRAM_QUIET_HOURS_END"),
    )
    telegram_quiet_hours_timezone: str = Field(
        "Asia/Seoul",
        validation_alias=AliasChoices("FCE_TELEGRAM_QUIET_HOURS_TIMEZONE", "TELEGRAM_QUIET_HOURS_TIMEZONE"),
    )
    alert_rules_enabled: str = Field(
        "trigger_near,invalidation_breach,take_profit_hit,status_worsened,health_drop,liq_proximity,liq_unknown_high_lev,wyckoff_event,data_stall,funding_extreme,oi_divergence,liq_cluster_near,setup_near,setup_triggered,setup_invalidated,intent_approaching,intent_zone_entered,intent_zone_entered_partial,intent_invalidated,universe_discovery,mdd_limit_warn,mdd_limit_critical,"
        "position_opened,position_closed,verdict_changed,stance_flipped,evidence_insufficient,periodic_pulse,full_alignment,flow_divergence,whale_entry",
        validation_alias=AliasChoices("FCE_ALERT_RULES_ENABLED", "ALERT_RULES_ENABLED"),
    )
    # WO-44 포지션 라이프사이클 알림.
    alert_pulse_interval_hours: float = Field(
        4.0,
        validation_alias=AliasChoices("FCE_ALERT_PULSE_INTERVAL_HOURS", "ALERT_PULSE_INTERVAL_HOURS"),
    )
    alert_closure_confirm_ticks: int = Field(
        2,
        validation_alias=AliasChoices("FCE_ALERT_CLOSURE_CONFIRM_TICKS", "ALERT_CLOSURE_CONFIRM_TICKS"),
    )
    alert_evidence_insufficient_hours: float = Field(
        2.0,
        validation_alias=AliasChoices("FCE_ALERT_EVIDENCE_INSUFFICIENT_HOURS", "ALERT_EVIDENCE_INSUFFICIENT_HOURS"),
    )
    notification_state_path: str = Field(
        "./notification_state.json",
        validation_alias=AliasChoices("FCE_NOTIFICATION_STATE_PATH", "NOTIFICATION_STATE_PATH"),
    )
    alert_trigger_near_pct: float = Field(
        1.5,
        validation_alias=AliasChoices("FCE_ALERT_TRIGGER_NEAR_PCT", "ALERT_TRIGGER_NEAR_PCT"),
    )
    alert_trigger_rearm_pct: float = Field(
        3.0,
        validation_alias=AliasChoices("FCE_ALERT_TRIGGER_REARM_PCT", "ALERT_TRIGGER_REARM_PCT"),
    )
    alert_liq_warn_pct: float = Field(
        8.0,
        validation_alias=AliasChoices("FCE_ALERT_LIQ_WARN_PCT", "ALERT_LIQ_WARN_PCT"),
    )
    alert_liq_critical_pct: float = Field(
        4.0,
        validation_alias=AliasChoices("FCE_ALERT_LIQ_CRITICAL_PCT", "ALERT_LIQ_CRITICAL_PCT"),
    )
    alert_health_drop_points: int = Field(
        15,
        validation_alias=AliasChoices("FCE_ALERT_HEALTH_DROP_POINTS", "ALERT_HEALTH_DROP_POINTS"),
    )
    alert_liq_unknown_high_lev_hours: float = Field(
        1.0,
        validation_alias=AliasChoices("FCE_ALERT_LIQ_UNKNOWN_HIGH_LEV_HOURS", "ALERT_LIQ_UNKNOWN_HIGH_LEV_HOURS"),
    )
    alert_wyckoff_min_confidence: int = Field(
        70,
        validation_alias=AliasChoices("FCE_ALERT_WYCKOFF_MIN_CONFIDENCE", "ALERT_WYCKOFF_MIN_CONFIDENCE"),
    )
    alert_funding_extreme_abs_rate: float = Field(
        0.01,
        validation_alias=AliasChoices("FCE_ALERT_FUNDING_EXTREME_ABS_RATE", "ALERT_FUNDING_EXTREME_ABS_RATE"),
    )
    alert_critical_cooldown_minutes: int = Field(
        30,
        validation_alias=AliasChoices("FCE_ALERT_CRITICAL_COOLDOWN_MINUTES", "ALERT_CRITICAL_COOLDOWN_MINUTES"),
    )
    alert_default_cooldown_minutes: int = Field(
        120,
        validation_alias=AliasChoices("FCE_ALERT_DEFAULT_COOLDOWN_MINUTES", "ALERT_DEFAULT_COOLDOWN_MINUTES"),
    )
    alert_response_window_hours: float = Field(
        6.0,
        validation_alias=AliasChoices("FCE_ALERT_RESPONSE_WINDOW_HOURS", "ALERT_RESPONSE_WINDOW_HOURS"),
    )
    alert_response_outcome_hours: float = Field(
        24.0,
        validation_alias=AliasChoices("FCE_ALERT_RESPONSE_OUTCOME_HOURS", "ALERT_RESPONSE_OUTCOME_HOURS"),
    )
    insight_auto_refresh_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_INSIGHT_AUTO_REFRESH_ENABLED", "INSIGHT_AUTO_REFRESH_ENABLED"),
    )
    insight_min_regeneration_interval_minutes: int = Field(
        10,
        validation_alias=AliasChoices(
            "FCE_INSIGHT_MIN_REGENERATION_INTERVAL_MINUTES",
            "INSIGHT_MIN_REGENERATION_INTERVAL_MINUTES",
        ),
    )
    param_autonomy_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_PARAM_AUTONOMY_ENABLED", "PARAM_AUTONOMY_ENABLED"),
    )
    performance_capital_base_usdt: float = Field(
        10000.0,
        validation_alias=AliasChoices("FCE_PERFORMANCE_CAPITAL_BASE_USDT", "PERFORMANCE_CAPITAL_BASE_USDT"),
    )
    performance_monthly_mdd_limit_pct: float = Field(
        0.0,
        validation_alias=AliasChoices("FCE_PERFORMANCE_MONTHLY_MDD_LIMIT_PCT", "PERFORMANCE_MONTHLY_MDD_LIMIT_PCT"),
    )
    paper_engine_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_PAPER_ENGINE_ENABLED", "PAPER_ENGINE_ENABLED"),
    )
    paper_margin_usdt: float = Field(
        100.0,
        validation_alias=AliasChoices("FCE_PAPER_MARGIN_USDT", "PAPER_MARGIN_USDT"),
    )
    paper_leverage: float = Field(
        3.0,
        validation_alias=AliasChoices("FCE_PAPER_LEVERAGE", "PAPER_LEVERAGE"),
    )
    paper_max_open_positions: int = Field(
        5,
        validation_alias=AliasChoices("FCE_PAPER_MAX_OPEN_POSITIONS", "PAPER_MAX_OPEN_POSITIONS"),
    )
    paper_min_evidence: int = Field(
        4,
        validation_alias=AliasChoices("FCE_PAPER_MIN_EVIDENCE", "PAPER_MIN_EVIDENCE"),
    )
    paper_min_checklist_passed: int = Field(
        5,
        validation_alias=AliasChoices("FCE_PAPER_MIN_CHECKLIST_PASSED", "PAPER_MIN_CHECKLIST_PASSED"),
    )
    paper_min_rr: float = Field(
        1.5,
        validation_alias=AliasChoices("FCE_PAPER_MIN_RR", "PAPER_MIN_RR"),
    )
    paper_candidate_bootstrap_enabled: bool = Field(
        True,
        validation_alias=AliasChoices(
            "FCE_PAPER_CANDIDATE_BOOTSTRAP_ENABLED",
            "PAPER_CANDIDATE_BOOTSTRAP_ENABLED",
        ),
    )
    paper_candidate_bootstrap_min_sample: int = Field(
        15,
        validation_alias=AliasChoices(
            "FCE_PAPER_CANDIDATE_BOOTSTRAP_MIN_SAMPLE",
            "PAPER_CANDIDATE_BOOTSTRAP_MIN_SAMPLE",
        ),
    )
    paper_candidate_bootstrap_min_win_1r_pct: float = Field(
        50.0,
        validation_alias=AliasChoices(
            "FCE_PAPER_CANDIDATE_BOOTSTRAP_MIN_WIN_1R_PCT",
            "PAPER_CANDIDATE_BOOTSTRAP_MIN_WIN_1R_PCT",
        ),
    )
    paper_candidate_bootstrap_relaxed_days: int = Field(
        14,
        ge=1,
        validation_alias=AliasChoices(
            "FCE_PAPER_CANDIDATE_BOOTSTRAP_RELAXED_DAYS",
            "PAPER_CANDIDATE_BOOTSTRAP_RELAXED_DAYS",
        ),
    )
    paper_candidate_bootstrap_relaxed_min_sample: int = Field(
        8,
        ge=1,
        validation_alias=AliasChoices(
            "FCE_PAPER_CANDIDATE_BOOTSTRAP_RELAXED_MIN_SAMPLE",
            "PAPER_CANDIDATE_BOOTSTRAP_RELAXED_MIN_SAMPLE",
        ),
    )
    paper_candidate_bootstrap_relaxed_min_win_1r_pct: float = Field(
        45.0,
        ge=0,
        le=100,
        validation_alias=AliasChoices(
            "FCE_PAPER_CANDIDATE_BOOTSTRAP_RELAXED_MIN_WIN_1R_PCT",
            "PAPER_CANDIDATE_BOOTSTRAP_RELAXED_MIN_WIN_1R_PCT",
        ),
    )
    paper_candidate_bootstrap_disable_validated_count: int = Field(
        3,
        validation_alias=AliasChoices(
            "FCE_PAPER_CANDIDATE_BOOTSTRAP_DISABLE_VALIDATED_COUNT",
            "PAPER_CANDIDATE_BOOTSTRAP_DISABLE_VALIDATED_COUNT",
        ),
    )
    paper_max_holding_bars: int = Field(
        30,
        validation_alias=AliasChoices("FCE_PAPER_MAX_HOLDING_BARS", "PAPER_MAX_HOLDING_BARS"),
    )
    paper_take_profit_atr_k1: float = Field(
        1.0,
        gt=0,
        validation_alias=AliasChoices("FCE_PAPER_TAKE_PROFIT_ATR_K1", "PAPER_TAKE_PROFIT_ATR_K1"),
    )
    paper_take_profit_atr_k2: float = Field(
        2.0,
        gt=0,
        validation_alias=AliasChoices("FCE_PAPER_TAKE_PROFIT_ATR_K2", "PAPER_TAKE_PROFIT_ATR_K2"),
    )
    paper_poor_mdd_pct: float = Field(
        10.0,
        validation_alias=AliasChoices("FCE_PAPER_POOR_MDD_PCT", "PAPER_POOR_MDD_PCT"),
    )
    paper_telegram_alerts_enabled: bool = Field(
        True,
        validation_alias=AliasChoices(
            "FCE_PAPER_TELEGRAM_ALERTS_ENABLED",
            "PAPER_TELEGRAM_ALERTS_ENABLED",
        ),
    )
    toss_stock_scout_enabled: bool = Field(
        False,
        validation_alias=AliasChoices("FCE_TOSS_STOCK_SCOUT_ENABLED", "TOSS_STOCK_SCOUT_ENABLED"),
    )
    toss_client_id: str = Field("", validation_alias=AliasChoices("FCE_TOSS_CLIENT_ID", "TOSS_CLIENT_ID"))
    toss_client_secret: str = Field("", validation_alias=AliasChoices("FCE_TOSS_CLIENT_SECRET", "TOSS_CLIENT_SECRET"))
    toss_base_url: str = Field(
        "https://openapi.tossinvest.com",
        validation_alias=AliasChoices("FCE_TOSS_BASE_URL", "TOSS_BASE_URL"),
    )
    toss_timeout_seconds: float = Field(
        10.0,
        gt=0,
        validation_alias=AliasChoices("FCE_TOSS_TIMEOUT_SECONDS", "TOSS_TIMEOUT_SECONDS"),
    )
    toss_poll_interval_seconds: int = Field(
        10,
        ge=10,
        validation_alias=AliasChoices("FCE_TOSS_POLL_INTERVAL_SECONDS", "TOSS_POLL_INTERVAL_SECONDS"),
    )
    toss_kr_watchlist_csv: str = Field(
        "",
        validation_alias=AliasChoices("FCE_TOSS_KR_WATCHLIST", "TOSS_KR_WATCHLIST"),
    )
    toss_us_watchlist_csv: str = Field(
        "",
        validation_alias=AliasChoices("FCE_TOSS_US_WATCHLIST", "TOSS_US_WATCHLIST"),
    )
    stock_paper_engine_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_STOCK_PAPER_ENGINE_ENABLED", "STOCK_PAPER_ENGINE_ENABLED"),
    )
    stock_live_trading_enabled: bool = Field(
        False,
        validation_alias=AliasChoices("FCE_STOCK_LIVE_TRADING_ENABLED", "STOCK_LIVE_TRADING_ENABLED"),
        description="Sealed until a separate WO approves a stock paper 4-week benchmark; true is invalid while LiveBroker is absent.",
    )
    stock_paper_initial_krw: float = Field(
        100_000_000.0,
        gt=0,
        validation_alias=AliasChoices("FCE_STOCK_PAPER_INITIAL_KRW", "STOCK_PAPER_INITIAL_KRW"),
    )
    stock_paper_initial_usd: float = Field(
        100_000.0,
        gt=0,
        validation_alias=AliasChoices("FCE_STOCK_PAPER_INITIAL_USD", "STOCK_PAPER_INITIAL_USD"),
    )
    stock_paper_max_minute_volume_ratio: float = Field(
        0.05,
        gt=0,
        le=0.25,
        validation_alias=AliasChoices("FCE_STOCK_PAPER_MAX_MINUTE_VOLUME_RATIO", "STOCK_PAPER_MAX_MINUTE_VOLUME_RATIO"),
    )
    stock_paper_kr_commission_rate: float = Field(
        0.00015,
        ge=0,
        validation_alias=AliasChoices("FCE_STOCK_PAPER_KR_COMMISSION_RATE", "STOCK_PAPER_KR_COMMISSION_RATE"),
    )
    stock_paper_us_commission_rate: float = Field(
        0.0007,
        ge=0,
        validation_alias=AliasChoices("FCE_STOCK_PAPER_US_COMMISSION_RATE", "STOCK_PAPER_US_COMMISSION_RATE"),
    )
    stock_paper_kr_sell_tax_rate: float = Field(
        0.0015,
        ge=0,
        validation_alias=AliasChoices("FCE_STOCK_PAPER_KR_SELL_TAX_RATE", "STOCK_PAPER_KR_SELL_TAX_RATE"),
    )
    polymarket_paper_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_POLYMARKET_PAPER_ENABLED", "POLYMARKET_PAPER_ENABLED"),
    )
    polymarket_poll_interval_seconds: int = Field(
        60,
        ge=30,
        validation_alias=AliasChoices("FCE_POLYMARKET_POLL_INTERVAL_SECONDS", "POLYMARKET_POLL_INTERVAL_SECONDS"),
    )
    polymarket_initial_usdc: float = Field(
        10_000.0,
        gt=0,
        validation_alias=AliasChoices("FCE_POLYMARKET_INITIAL_USDC", "POLYMARKET_INITIAL_USDC"),
    )
    polymarket_gamma_base_url: str = Field(
        "https://gamma-api.polymarket.com",
        validation_alias=AliasChoices("FCE_POLYMARKET_GAMMA_BASE_URL", "POLYMARKET_GAMMA_BASE_URL"),
    )
    polymarket_clob_base_url: str = Field(
        "https://clob.polymarket.com",
        validation_alias=AliasChoices("FCE_POLYMARKET_CLOB_BASE_URL", "POLYMARKET_CLOB_BASE_URL"),
    )
    polymarket_timeout_seconds: float = Field(
        10.0,
        gt=0,
        validation_alias=AliasChoices("FCE_POLYMARKET_TIMEOUT_SECONDS", "POLYMARKET_TIMEOUT_SECONDS"),
    )
    polymarket_market_limit: int = Field(
        100,
        ge=1,
        le=500,
        validation_alias=AliasChoices("FCE_POLYMARKET_MARKET_LIMIT", "POLYMARKET_MARKET_LIMIT"),
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    @model_validator(mode="after")
    def reject_unsealed_stock_live_trading(self) -> "Settings":
        if self.stock_live_trading_enabled:
            raise ValueError(
                "FCE_STOCK_LIVE_TRADING_ENABLED=true is sealed: stock paper must beat its benchmark for 4 weeks and a separate WO must implement LiveBroker"
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def symbol_list(self) -> list[str]:
        return [symbol.strip().upper() for symbol in self.default_symbols.split(",") if symbol.strip()]

    @property
    def stance_backtest_symbol_list(self) -> list[str]:
        return [symbol.strip().upper() for symbol in self.stance_backtest_symbols.split(",") if symbol.strip()]

    @property
    def telegram_allowed_chat_id_list(self) -> list[int]:
        chat_ids: list[int] = []
        raw_values = [self.telegram_chat_id, *self.telegram_allowed_chat_ids.split(",")]
        for raw in raw_values:
            value = raw.strip()
            if not value:
                continue
            try:
                chat_id = int(value)
            except ValueError:
                continue
            if chat_id not in chat_ids:
                chat_ids.append(chat_id)
        return chat_ids

    @property
    def alert_enabled_rule_set(self) -> set[str]:
        return {rule.strip() for rule in self.alert_rules_enabled.split(",") if rule.strip()}

    @property
    def universe_enabled_class_set(self) -> set[str]:
        return {item.strip().lower() for item in self.universe_classes_enabled.split(",") if item.strip()}

    @property
    def universe_blacklist_set(self) -> set[str]:
        return {symbol.strip().upper() for symbol in self.universe_blacklist.split(",") if symbol.strip()}

    @property
    def universe_crypto_allowlist_set(self) -> set[str]:
        return {ticker.strip().upper() for ticker in self.universe_crypto_allowlist.split(",") if ticker.strip()}

    @property
    def universe_stock_allowlist_set(self) -> set[str]:
        return {ticker.strip().upper() for ticker in self.universe_stock_allowlist.split(",") if ticker.strip()}

    @property
    def toss_kr_watchlist(self) -> list[str]:
        return [item.strip().upper() for item in self.toss_kr_watchlist_csv.split(",") if item.strip()][:400]

    @property
    def toss_us_watchlist(self) -> list[str]:
        return [item.strip().upper() for item in self.toss_us_watchlist_csv.split(",") if item.strip()][:400]


@lru_cache
def get_settings() -> Settings:
    return Settings()
