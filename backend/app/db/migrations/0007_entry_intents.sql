CREATE TABLE IF NOT EXISTS entry_intents (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    status TEXT NOT NULL,
    zone_lower REAL NOT NULL,
    zone_upper REAL NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entry_intents_symbol_status
    ON entry_intents(symbol, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_entry_intents_status_expires
    ON entry_intents(status, expires_at ASC);
