CREATE TABLE IF NOT EXISTS whale_wallets (
    address TEXT PRIMARY KEY,
    active INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS whale_events (
    id TEXT PRIMARY KEY,
    wallet_address TEXT NOT NULL,
    symbol TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_at TEXT NOT NULL,
    size_usd REAL NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_whale_events_symbol_at
    ON whale_events(symbol, event_at DESC);

CREATE INDEX IF NOT EXISTS idx_whale_events_wallet_at
    ON whale_events(wallet_address, event_at DESC);

CREATE TABLE IF NOT EXISTS whale_position_states (
    wallet_address TEXT NOT NULL,
    coin TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (wallet_address, coin)
);
