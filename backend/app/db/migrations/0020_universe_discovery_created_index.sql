CREATE INDEX IF NOT EXISTS idx_universe_discoveries_created_at
    ON universe_discoveries(created_at DESC);
