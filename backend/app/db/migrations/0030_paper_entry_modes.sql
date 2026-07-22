ALTER TABLE stock_paper_orders ADD COLUMN entry_mode TEXT NOT NULL DEFAULT 'strict_signal';
ALTER TABLE stock_paper_fills ADD COLUMN entry_mode TEXT NOT NULL DEFAULT 'strict_signal';
ALTER TABLE stock_paper_tracks ADD COLUMN last_market_state TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE stock_paper_tracks ADD COLUMN last_market_observed_at TEXT;

CREATE TABLE IF NOT EXISTS stock_paper_mode_accounts (
    market TEXT NOT NULL CHECK(market IN ('KR', 'US')),
    entry_mode TEXT NOT NULL CHECK(entry_mode IN ('strict_signal', 'coverage')),
    currency TEXT NOT NULL CHECK(currency IN ('KRW', 'USD')),
    initial_cash REAL NOT NULL,
    cash REAL NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(market, entry_mode)
);

CREATE TABLE IF NOT EXISTS stock_paper_mode_positions (
    market TEXT NOT NULL CHECK(market IN ('KR', 'US')),
    symbol TEXT NOT NULL,
    entry_mode TEXT NOT NULL CHECK(entry_mode IN ('strict_signal', 'coverage')),
    quantity INTEGER NOT NULL,
    average_price REAL NOT NULL,
    currency TEXT NOT NULL CHECK(currency IN ('KRW', 'USD')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(market, symbol, entry_mode)
);

ALTER TABLE poly_orders ADD COLUMN entry_mode TEXT NOT NULL DEFAULT 'strict_edge';
ALTER TABLE poly_fills ADD COLUMN entry_mode TEXT NOT NULL DEFAULT 'strict_edge';
ALTER TABLE poly_positions ADD COLUMN entry_mode TEXT NOT NULL DEFAULT 'strict_edge';
