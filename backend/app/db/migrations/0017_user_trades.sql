CREATE TABLE IF NOT EXISTS user_trades (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_at TEXT NOT NULL,
    exit_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_trades_exit_at
    ON user_trades(exit_at DESC);

CREATE INDEX IF NOT EXISTS idx_user_trades_symbol_exit_at
    ON user_trades(symbol, exit_at DESC);

CREATE TABLE IF NOT EXISTS user_account_fills (
    trade_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_account_fills_timestamp
    ON user_account_fills(timestamp ASC);
