ALTER TABLE trades ADD COLUMN plan_price REAL;
ALTER TABLE trades ADD COLUMN chase_pct REAL;
ALTER TABLE trades ADD COLUMN report_to_entry_minutes REAL;
ALTER TABLE trades ADD COLUMN scout_originated INTEGER;
ALTER TABLE trades ADD COLUMN stance_alignment TEXT;
ALTER TABLE trades ADD COLUMN entry_state_label TEXT;
ALTER TABLE trades ADD COLUMN fomo_index REAL;

CREATE INDEX IF NOT EXISTS idx_trades_fomo_created
    ON trades(fomo_index, created_at DESC);
