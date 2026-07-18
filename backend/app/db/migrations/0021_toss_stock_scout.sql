CREATE TABLE IF NOT EXISTS toss_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_toss_quotes_symbol_time ON toss_quotes(market, symbol, observed_at DESC);

CREATE TABLE IF NOT EXISTS toss_candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    UNIQUE(market, symbol, timeframe, opened_at)
);

CREATE TABLE IF NOT EXISTS toss_rankings_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    ranking_kind TEXT NOT NULL,
    ranking_basis TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS toss_investor_flow (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    index_code TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS toss_warnings (
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(market, symbol)
);

CREATE TABLE IF NOT EXISTS scout_judgment_snapshots (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('crypto', 'stock_kr', 'stock_us')),
    symbol TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    price REAL NOT NULL,
    evidence TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scout_judgment_signal ON scout_judgment_snapshots(entity_type, signal_type, observed_at DESC);

CREATE TABLE IF NOT EXISTS scout_judgment_outcomes (
    judgment_id TEXT NOT NULL,
    horizon_days INTEGER NOT NULL CHECK(horizon_days IN (1, 5, 20)),
    observed_at TEXT NOT NULL,
    price REAL NOT NULL,
    return_pct REAL NOT NULL,
    PRIMARY KEY(judgment_id, horizon_days),
    FOREIGN KEY(judgment_id) REFERENCES scout_judgment_snapshots(id)
);

