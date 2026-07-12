ALTER TABLE entry_intents RENAME TO entry_intents_legacy;

CREATE TABLE entry_intents (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT,
    status TEXT NOT NULL,
    zone_lower REAL,
    zone_upper REAL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'zone'
);

INSERT INTO entry_intents
    (id, symbol, timeframe, direction, status, zone_lower, zone_upper, expires_at, updated_at, created_at, payload, kind)
SELECT id, symbol, timeframe, direction, status, zone_lower, zone_upper, expires_at, updated_at, created_at, payload, 'zone'
FROM entry_intents_legacy;

DROP TABLE entry_intents_legacy;

CREATE INDEX IF NOT EXISTS idx_entry_intents_symbol_status
    ON entry_intents(symbol, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_entry_intents_status_expires
    ON entry_intents(status, expires_at ASC);
CREATE INDEX IF NOT EXISTS idx_entry_intents_kind_status
    ON entry_intents(kind, status, updated_at DESC);
