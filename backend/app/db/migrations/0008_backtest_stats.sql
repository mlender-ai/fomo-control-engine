CREATE TABLE IF NOT EXISTS backtest_stats (
    id TEXT PRIMARY KEY,
    signature_key TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    scope TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    sample_size INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL,
    UNIQUE(signature_key, symbol, timeframe, scope)
);
CREATE INDEX IF NOT EXISTS idx_backtest_stats_symbol
    ON backtest_stats(symbol, timeframe, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_stats_signature
    ON backtest_stats(signature_key, generated_at DESC);

