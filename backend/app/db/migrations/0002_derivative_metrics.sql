CREATE TABLE IF NOT EXISTS deriv_metrics (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    tier TEXT NOT NULL,
    as_of TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deriv_metrics_symbol_asof
    ON deriv_metrics(symbol, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_deriv_metrics_source_asof
    ON deriv_metrics(source, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_deriv_metrics_symbol_source_asof
    ON deriv_metrics(symbol, source, as_of DESC);

CREATE TABLE IF NOT EXISTS liquidation_events (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    interval TEXT NOT NULL,
    bucket_start TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_liquidation_events_symbol_bucket
    ON liquidation_events(symbol, bucket_start DESC);
CREATE INDEX IF NOT EXISTS idx_liquidation_events_source_bucket
    ON liquidation_events(source, bucket_start DESC);
