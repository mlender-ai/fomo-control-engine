CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    entry_score INTEGER NOT NULL,
    fomo_index INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_symbol_created
    ON reports(symbol, created_at DESC);

CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    status TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_positions_status_opened
    ON positions(status, opened_at DESC);

CREATE TABLE IF NOT EXISTS monitoring_logs (
    id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_monitoring_position_created
    ON monitoring_logs(position_id, created_at DESC);

CREATE TABLE IF NOT EXISTS position_snapshots (
    id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_position_snapshots_position_created
    ON position_snapshots(position_id, created_at DESC);

CREATE TABLE IF NOT EXISTS position_insights (
    id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    snapshot_id TEXT,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_position_insights_position_created
    ON position_insights(position_id, created_at DESC);

CREATE TABLE IF NOT EXISTS position_events (
    id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_position_events_position_created
    ON position_events(position_id, created_at DESC);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    rule_id TEXT NOT NULL,
    position_id TEXT,
    symbol TEXT NOT NULL,
    severity TEXT NOT NULL,
    fired_at TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    acked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_position_fired
    ON alerts(position_id, fired_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_rule_fired
    ON alerts(rule_id, fired_at DESC);

CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_created
    ON trades(created_at DESC);

CREATE TABLE IF NOT EXISTS judgment_ledger (
    id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    judgment_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_judgment_ledger_judgment_id
    ON judgment_ledger(judgment_id);
CREATE INDEX IF NOT EXISTS idx_judgment_ledger_position_asof
    ON judgment_ledger(position_id, as_of DESC);

CREATE TABLE IF NOT EXISTS judgment_scores (
    id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    trade_id TEXT,
    judgment_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_judgment_scores_position_created
    ON judgment_scores(position_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_judgment_scores_trade_created
    ON judgment_scores(trade_id, created_at DESC);

CREATE TABLE IF NOT EXISTS calibration_suggestions (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calibration_suggestions_status_created
    ON calibration_suggestions(status, created_at DESC);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    provider TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_snapshots_symbol_created
    ON market_snapshots(symbol, created_at DESC);

CREATE TABLE IF NOT EXISTS derivative_snapshots (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    provider TEXT NOT NULL,
    tier TEXT NOT NULL,
    as_of TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_derivative_snapshots_symbol_asof
    ON derivative_snapshots(symbol, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_derivative_snapshots_provider_asof
    ON derivative_snapshots(provider, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_derivative_snapshots_symbol_created
    ON derivative_snapshots(symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_derivative_snapshots_provider_created
    ON derivative_snapshots(provider, created_at DESC);

CREATE TABLE IF NOT EXISTS database_maintenance_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_database_maintenance_events_type_created
    ON database_maintenance_events(event_type, created_at DESC);

CREATE TABLE IF NOT EXISTS research_runs (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    report_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_runs_symbol_created
    ON research_runs(symbol, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_outputs (
    id TEXT PRIMARY KEY,
    research_run_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_outputs_run_created
    ON agent_outputs(research_run_id, created_at ASC);

CREATE TABLE IF NOT EXISTS shadow_profiles (
    shadow_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decision_memories (
    id TEXT PRIMARY KEY,
    symbol TEXT,
    memory_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decision_memories_symbol_created
    ON decision_memories(symbol, created_at DESC);

CREATE TABLE IF NOT EXISTS validation_runs (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    strategy_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_validation_runs_created
    ON validation_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY,
    added_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS symbol_catalog (
    symbol TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entry_scenarios (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    linked_position_id TEXT,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entry_scenarios_symbol
    ON entry_scenarios(symbol, direction, created_at DESC);

CREATE TABLE IF NOT EXISTS worker_heartbeat (
    job_name TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    runs INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    total_failures INTEGER NOT NULL DEFAULT 0,
    skipped INTEGER NOT NULL DEFAULT 0,
    base_interval_seconds INTEGER NOT NULL DEFAULT 0,
    current_interval_seconds INTEGER NOT NULL DEFAULT 0,
    last_started_at TEXT,
    last_success_at TEXT,
    last_error_at TEXT,
    last_error TEXT,
    next_run_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bitget_trade_fills (
    symbol TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY(symbol, trade_id)
);
CREATE INDEX IF NOT EXISTS idx_bitget_trade_fills_symbol_timestamp
    ON bitget_trade_fills(symbol, timestamp DESC);

CREATE TABLE IF NOT EXISTS bitget_trade_fill_fetch_state (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY(symbol, timeframe)
);
