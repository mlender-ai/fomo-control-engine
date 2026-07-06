from functools import lru_cache
from pydantic import AliasChoices, Field
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
        48,
        validation_alias=AliasChoices("FCE_BITGET_TRADE_FILL_LOOKBACK_HOURS", "BITGET_TRADE_FILL_LOOKBACK_HOURS"),
    )
    bitget_trade_fill_cache_ttl_seconds: int = Field(
        60,
        validation_alias=AliasChoices(
            "FCE_BITGET_TRADE_FILL_CACHE_TTL_SECONDS",
            "BITGET_TRADE_FILL_CACHE_TTL_SECONDS",
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
        6,
        validation_alias=AliasChoices("FCE_COINGLASS_REQUESTS_PER_SYMBOL", "COINGLASS_REQUESTS_PER_SYMBOL"),
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
        7,
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
    scout_watchlist_symbol_limit: int = Field(
        30,
        validation_alias=AliasChoices("FCE_SCOUT_WATCHLIST_SYMBOL_LIMIT", "SCOUT_WATCHLIST_SYMBOL_LIMIT"),
    )
    scout_auto_arm_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_SCOUT_AUTO_ARM_ENABLED", "SCOUT_AUTO_ARM_ENABLED"),
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
    universe_scanner_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FCE_UNIVERSE_SCANNER_ENABLED", "UNIVERSE_SCANNER_ENABLED"),
    )
    worker_universe_scan_interval_seconds: int = Field(
        1800,
        validation_alias=AliasChoices(
            "FCE_WORKER_UNIVERSE_SCAN_INTERVAL_SECONDS",
            "WORKER_UNIVERSE_SCAN_INTERVAL_SECONDS",
        ),
    )
    universe_crypto_symbol_limit: int = Field(
        40,
        validation_alias=AliasChoices("FCE_UNIVERSE_CRYPTO_SYMBOL_LIMIT", "UNIVERSE_CRYPTO_SYMBOL_LIMIT"),
    )
    universe_stock_symbol_limit: int = Field(
        40,
        validation_alias=AliasChoices("FCE_UNIVERSE_STOCK_SYMBOL_LIMIT", "UNIVERSE_STOCK_SYMBOL_LIMIT"),
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
        "trigger_near,invalidation_breach,take_profit_hit,status_worsened,health_drop,liq_proximity,liq_unknown_high_lev,wyckoff_event,data_stall,funding_extreme,oi_divergence,liq_cluster_near,setup_near,setup_triggered,setup_invalidated,intent_approaching,intent_zone_entered,intent_zone_entered_partial,intent_invalidated,universe_discovery",
        validation_alias=AliasChoices("FCE_ALERT_RULES_ENABLED", "ALERT_RULES_ENABLED"),
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

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def symbol_list(self) -> list[str]:
        return [symbol.strip().upper() for symbol in self.default_symbols.split(",") if symbol.strip()]

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
