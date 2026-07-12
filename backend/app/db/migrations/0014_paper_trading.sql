CREATE TABLE IF NOT EXISTS paper_trades (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    status TEXT NOT NULL,
    entry_bar_at TEXT NOT NULL,
    exit_at TEXT,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_status_symbol
    ON paper_trades(status, symbol, updated_at DESC);

CREATE TABLE IF NOT EXISTS paper_engine_states (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, timeframe)
);
