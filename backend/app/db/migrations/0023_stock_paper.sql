CREATE TABLE IF NOT EXISTS stock_paper_tracks (
    market TEXT PRIMARY KEY CHECK(market IN ('KR', 'US')),
    currency TEXT NOT NULL CHECK(currency IN ('KRW', 'USD')),
    benchmark_index TEXT NOT NULL,
    universe_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    initial_cash REAL NOT NULL,
    cash REAL NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running', 'stopped', 'completed')),
    stop_reason TEXT,
    benchmark_start REAL,
    benchmark_current REAL,
    benchmark_observed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_paper_orders (
    id TEXT PRIMARY KEY,
    market TEXT NOT NULL CHECK(market IN ('KR', 'US')),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
    status TEXT NOT NULL,
    signal_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reason TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stock_paper_orders_market_status ON stock_paper_orders(market, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS stock_paper_fills (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    market TEXT NOT NULL CHECK(market IN ('KR', 'US')),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('buy', 'sell')),
    filled_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES stock_paper_orders(id)
);
CREATE INDEX IF NOT EXISTS idx_stock_paper_fills_market_time ON stock_paper_fills(market, filled_at DESC);

CREATE TABLE IF NOT EXISTS stock_paper_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL CHECK(market IN ('KR', 'US')),
    symbol TEXT,
    order_id TEXT,
    event_type TEXT NOT NULL,
    reason TEXT,
    observed_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stock_paper_events_reason ON stock_paper_events(market, reason, observed_at DESC);

CREATE TABLE IF NOT EXISTS stock_paper_positions (
    market TEXT NOT NULL CHECK(market IN ('KR', 'US')),
    symbol TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    average_price REAL NOT NULL,
    currency TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(market, symbol)
);
