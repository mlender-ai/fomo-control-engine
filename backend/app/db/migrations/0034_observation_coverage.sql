-- WO-FCE-OBSERVATION-INTEGRITY-01 Phase 1 — 관측 무결성 게이트.
--
-- 문제: 4주 검증을 시작한 지 3주가 지났지만 "온전히 관측된 날이 며칠인지 아무도 모른다".
-- 지난 2주간 워커 매달림(10.4h·11.7h), 토스 인증 자기잠금(20.8h), KST 자정 롤오버(매일 US 77%),
-- 맥 절전(재시작 5회)이 겹쳤고, 매번 "표본이 오염됐으니 다시 세자"가 반복됐다.
--
-- 원칙: **검증일은 커버리지 게이트를 통과한 날만 센다.** 통과 못 한 날은 유실일이다.
-- 커버리지는 세션 창을 15분 구간으로 쪼개 관측 흔적이 있는 구간 비율로 잰다(불규칙 주기에 강함).
CREATE TABLE IF NOT EXISTS observation_coverage (
    day TEXT NOT NULL,                          -- UTC 기준 ISO date
    track TEXT NOT NULL,                        -- crypto | stock_kr | stock_us | poly
    session_start_at TEXT,                      -- 그날의 세션 창(UTC). 비거래일이면 NULL
    session_end_at TEXT,
    expected_bins INTEGER NOT NULL DEFAULT 0,
    covered_bins INTEGER NOT NULL DEFAULT 0,
    coverage_pct REAL NOT NULL DEFAULT 0,
    observations INTEGER NOT NULL DEFAULT 0,
    longest_gap_seconds INTEGER NOT NULL DEFAULT 0,
    total_gap_seconds INTEGER NOT NULL DEFAULT 0,
    -- 맥 절전이 미국 정규장 후반(02:00~05:00 KST = 17:00~20:00 UTC)에 걸린 구간.
    -- 코드로 못 고치는 손실이라 별도 집계해 "수동 조치 필요"로 올린다(1-3).
    late_session_gap_seconds INTEGER NOT NULL DEFAULT 0,
    valid INTEGER NOT NULL DEFAULT 0,           -- 1 = 유효 관측일(검증일로 카운트)
    trading_day INTEGER NOT NULL DEFAULT 1,     -- 0 = 주말 등 비거래일(분모에서 제외)
    reason TEXT,
    computed_at TEXT NOT NULL,
    PRIMARY KEY (day, track)
);

CREATE INDEX IF NOT EXISTS idx_observation_coverage_track_day ON observation_coverage (track, day);
