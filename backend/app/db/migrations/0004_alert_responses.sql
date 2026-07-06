CREATE TABLE IF NOT EXISTS alert_responses (
    id TEXT PRIMARY KEY,
    alert_id TEXT NOT NULL,
    position_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    response TEXT NOT NULL,
    detected_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_responses_alert
    ON alert_responses(alert_id);
CREATE INDEX IF NOT EXISTS idx_alert_responses_position_detected
    ON alert_responses(position_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_responses_rule_outcome
    ON alert_responses(rule_id, outcome);
