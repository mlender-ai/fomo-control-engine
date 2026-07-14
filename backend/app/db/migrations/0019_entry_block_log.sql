CREATE TABLE IF NOT EXISTS entry_block_log (
  id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  bar_at TEXT NOT NULL,
  direction TEXT NOT NULL,
  failed_gate TEXT NOT NULL,
  detail TEXT NOT NULL,
  payload JSON NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entry_block_log_bar_at
  ON entry_block_log (bar_at DESC);

CREATE INDEX IF NOT EXISTS idx_entry_block_log_gate_bar
  ON entry_block_log (failed_gate, bar_at DESC);
