-- WO-FCE-57: 심볼·타임프레임별 캔들 앵커드 방향 히스테리시스 상태.
-- 스카우트 스냅샷 수명과 분리해 포지션·온디맨드 판정도 같은 prior를 사용한다.
CREATE TABLE IF NOT EXISTS directional_states (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    state JSON NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_directional_states_updated_at
    ON directional_states(updated_at DESC);
