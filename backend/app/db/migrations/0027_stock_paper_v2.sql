ALTER TABLE stock_paper_tracks ADD COLUMN clock_valid INTEGER NOT NULL DEFAULT 0;
ALTER TABLE stock_paper_tracks ADD COLUMN clock_invalidation_reason TEXT;
ALTER TABLE stock_paper_tracks ADD COLUMN parameter_version TEXT NOT NULL DEFAULT 'stock-v1';
UPDATE stock_paper_tracks SET clock_valid=0,
clock_invalidation_reason='Toss 인증 실패 및 진입 파이프라인 순환 봉쇄 구간은 검증 표본에서 제외';

CREATE TABLE IF NOT EXISTS stock_paper_analysis_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL CHECK(market IN ('KR', 'US')),
    symbol TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    parameter_version TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stock_paper_analysis_latest
ON stock_paper_analysis_snapshots(market, symbol, observed_at DESC);

CREATE TABLE IF NOT EXISTS stock_paper_entry_rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL CHECK(market IN ('KR', 'US')),
    symbol TEXT NOT NULL,
    ts TEXT NOT NULL,
    gate TEXT NOT NULL,
    measured_value TEXT,
    threshold TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stock_paper_entry_rejections_gate
ON stock_paper_entry_rejections(market, gate, ts DESC);

CREATE TABLE IF NOT EXISTS toss_auth_diagnostics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    configured INTEGER NOT NULL,
    base_url TEXT NOT NULL,
    payload TEXT NOT NULL
);
