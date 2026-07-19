ALTER TABLE stock_paper_tracks ADD COLUMN benchmark_proxy_symbol TEXT NOT NULL DEFAULT '';
ALTER TABLE stock_paper_tracks ADD COLUMN benchmark_method TEXT NOT NULL DEFAULT 'unlevered_etf_proxy_close';

UPDATE stock_paper_tracks SET benchmark_proxy_symbol='237350' WHERE market='KR' AND benchmark_proxy_symbol='';
UPDATE stock_paper_tracks SET benchmark_proxy_symbol='QQQ' WHERE market='US' AND benchmark_proxy_symbol='';

CREATE TABLE IF NOT EXISTS stock_paper_marks (
    market TEXT NOT NULL CHECK(market IN ('KR', 'US')),
    symbol TEXT NOT NULL,
    price REAL NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(market, symbol)
);

CREATE TABLE IF NOT EXISTS stock_paper_fx_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    base_currency TEXT NOT NULL,
    quote_currency TEXT NOT NULL,
    rate REAL NOT NULL,
    observed_at TEXT NOT NULL,
    valid_from TEXT,
    valid_until TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stock_paper_fx_time ON stock_paper_fx_snapshots(base_currency, quote_currency, observed_at DESC);
