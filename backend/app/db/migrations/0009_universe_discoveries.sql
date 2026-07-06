CREATE TABLE IF NOT EXISTS universe_discoveries (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    signature_key TEXT NOT NULL,
    status TEXT NOT NULL,
    gate_passed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_universe_discoveries_symbol
    ON universe_discoveries(symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_universe_discoveries_status
    ON universe_discoveries(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_universe_discoveries_signature
    ON universe_discoveries(signature_key, created_at DESC);
