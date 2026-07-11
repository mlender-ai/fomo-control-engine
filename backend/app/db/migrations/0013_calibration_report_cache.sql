CREATE TABLE IF NOT EXISTS calibration_report_cache (
    report_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    computed_at TEXT NOT NULL
);

