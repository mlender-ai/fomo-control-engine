CREATE TABLE IF NOT EXISTS scout_snapshots (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    as_of TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scout_snapshots_symbol_asof
    ON scout_snapshots(symbol, as_of DESC);

CREATE TABLE IF NOT EXISTS armed_setups (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    source TEXT NOT NULL,
    setup_type TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger_price REAL,
    updated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_armed_setups_symbol_status
    ON armed_setups(symbol, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_armed_setups_status_updated
    ON armed_setups(status, updated_at DESC);
