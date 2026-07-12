CREATE TABLE IF NOT EXISTS paper_gate_funnel (
  symbol TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  bar_at TEXT NOT NULL,
  payload JSON NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (symbol, timeframe, bar_at)
);

CREATE INDEX IF NOT EXISTS idx_paper_gate_funnel_bar_at
  ON paper_gate_funnel (bar_at DESC);
