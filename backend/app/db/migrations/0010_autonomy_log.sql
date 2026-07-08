-- WO-FCE-37 자율 검증 루프: 시그니처 상태 전이 원장 (append-only).
CREATE TABLE IF NOT EXISTS autonomy_log (
    id TEXT PRIMARY KEY,
    signature_key TEXT NOT NULL,
    new_state TEXT NOT NULL,
    transition TEXT NOT NULL,
    autonomous INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_autonomy_log_signature_created
    ON autonomy_log(signature_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_autonomy_log_state_created
    ON autonomy_log(new_state, created_at DESC);
