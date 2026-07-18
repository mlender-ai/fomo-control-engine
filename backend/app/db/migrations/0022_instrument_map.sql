CREATE TABLE IF NOT EXISTS instrument_map (
    bitget_symbol TEXT PRIMARY KEY,
    bitget_type TEXT NOT NULL CHECK(bitget_type IN ('usdt_futures', 'spot')),
    toss_symbol TEXT NOT NULL,
    toss_market TEXT NOT NULL CHECK(toss_market IN ('US', 'KR')),
    verification_status TEXT NOT NULL CHECK(verification_status IN ('verified', 'pending', 'rejected')),
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_instrument_map_status
    ON instrument_map(verification_status, updated_at DESC);
