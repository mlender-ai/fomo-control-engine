CREATE TABLE IF NOT EXISTS engine_params (
    id TEXT PRIMARY KEY,
    param TEXT NOT NULL,
    status TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    suggestion_id TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_engine_params_param_status
    ON engine_params(param, status, approved_at DESC);
CREATE INDEX IF NOT EXISTS idx_engine_params_suggestion
    ON engine_params(suggestion_id);
