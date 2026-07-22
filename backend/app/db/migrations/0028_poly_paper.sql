CREATE TABLE IF NOT EXISTS poly_paper_track (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    currency TEXT NOT NULL CHECK(currency = 'USDC'),
    parameter_version TEXT NOT NULL,
    started_at TEXT,
    ends_at TEXT,
    clock_valid INTEGER NOT NULL DEFAULT 0,
    initial_cash REAL NOT NULL,
    cash REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'waiting',
    stop_reason TEXT,
    last_collection_at TEXT,
    last_collection_status TEXT,
    last_collection_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poly_markets (
    market_id TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    question TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('crypto', 'macro')),
    observed_at TEXT NOT NULL,
    end_at TEXT,
    active INTEGER NOT NULL,
    closed INTEGER NOT NULL,
    market_probability REAL,
    liquidity REAL NOT NULL DEFAULT 0,
    trade_eligible INTEGER NOT NULL DEFAULT 0,
    exclusion_reason TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_poly_markets_category_active
ON poly_markets(category, active, liquidity DESC);

CREATE TABLE IF NOT EXISTS poly_estimates (
    id TEXT PRIMARY KEY,
    judgment_id TEXT NOT NULL UNIQUE,
    market_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    category TEXT NOT NULL,
    market_probability REAL NOT NULL,
    estimated_probability REAL NOT NULL,
    confidence_low REAL NOT NULL,
    confidence_high REAL NOT NULL,
    estimate_quality TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('YES', 'NO')),
    gross_edge REAL NOT NULL,
    effective_price REAL,
    after_cost_edge REAL,
    trade_eligible INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL,
    FOREIGN KEY(market_id) REFERENCES poly_markets(market_id)
);
CREATE INDEX IF NOT EXISTS idx_poly_estimates_market_observed
ON poly_estimates(market_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS poly_orders (
    id TEXT PRIMARY KEY,
    market_id TEXT NOT NULL,
    estimate_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('YES', 'NO')),
    requested_notional REAL NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    FOREIGN KEY(market_id) REFERENCES poly_markets(market_id),
    FOREIGN KEY(estimate_id) REFERENCES poly_estimates(id)
);

CREATE TABLE IF NOT EXISTS poly_fills (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('YES', 'NO')),
    shares REAL NOT NULL,
    price REAL NOT NULL,
    notional REAL NOT NULL,
    filled_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES poly_orders(id)
);

CREATE TABLE IF NOT EXISTS poly_positions (
    market_id TEXT PRIMARY KEY,
    estimate_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('YES', 'NO')),
    shares REAL NOT NULL,
    average_price REAL NOT NULL,
    cost REAL NOT NULL,
    opened_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open', 'resolved')),
    resolved_at TEXT,
    outcome INTEGER,
    payout REAL,
    pnl REAL,
    payload TEXT NOT NULL,
    FOREIGN KEY(market_id) REFERENCES poly_markets(market_id),
    FOREIGN KEY(estimate_id) REFERENCES poly_estimates(id)
);

CREATE TABLE IF NOT EXISTS poly_resolutions (
    judgment_id TEXT PRIMARY KEY,
    estimate_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    outcome INTEGER NOT NULL CHECK(outcome IN (0, 1)),
    estimated_probability REAL NOT NULL,
    brier_score REAL NOT NULL,
    resolved_at TEXT NOT NULL,
    source TEXT NOT NULL,
    payload TEXT NOT NULL,
    FOREIGN KEY(estimate_id) REFERENCES poly_estimates(id),
    FOREIGN KEY(market_id) REFERENCES poly_markets(market_id)
);
CREATE INDEX IF NOT EXISTS idx_poly_resolutions_resolved
ON poly_resolutions(resolved_at DESC);
