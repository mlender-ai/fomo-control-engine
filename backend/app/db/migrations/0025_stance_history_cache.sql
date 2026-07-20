CREATE TABLE IF NOT EXISTS stance_history_candles (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    quote_volume REAL,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(symbol, timeframe, opened_at)
);
CREATE INDEX IF NOT EXISTS idx_stance_history_symbol_time
    ON stance_history_candles(symbol, timeframe, opened_at DESC);
