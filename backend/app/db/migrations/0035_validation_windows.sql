-- WO-FCE-WINDOW-ANCHOR-01 Phase 1 — 검증 창 앵커.
--
-- 문제: 검증 완료 판정(유효일 / 표본 / 국면)에 **창 개념이 없었다.** `effective_days` 는
-- 트랙의 역사상 모든 유효일 개수였고, 표본 SQL 8종 전부 시각 조건이 없었다. 그래서
-- 페이퍼 재시작(`start_paper_benchmark(reset=True)`)을 실행해도 스코어보드만 0으로 돌아가고
-- 검증 카운터는 어제 값 그대로 남는다 — 두 화면이 서로 다른 창을 말하는 분열 상태가 된다.
--
-- 원칙: **창은 필터지 삭제가 아니다.** 앵커 이전 행은 하나도 지우지 않는다. 계수에서 빠질 뿐
-- 조회는 그대로 가능하며, 제외된 건수도 노출한다(침묵 금지).
--
-- 이력 보존: 창마다 새 행을 넣는다(append-only). `window_seq` 최대값이 현재 창이고,
-- 과거 창의 앵커는 그대로 남아 "그때 무엇을 어디부터 셌는가"를 나중에도 확인할 수 있다.
CREATE TABLE IF NOT EXISTS validation_windows (
    track TEXT NOT NULL,                        -- crypto | stock_kr | stock_us | poly
    window_seq INTEGER NOT NULL,                -- 창 회차. 1부터 시작하며 재시작마다 +1
    anchored_at TEXT NOT NULL,                  -- 창 시작 시각 (UTC ISO datetime) — 표본 필터 하한
    anchor_day TEXT NOT NULL,                   -- 창 시작일 (UTC ISO date) — 관측일 필터 하한
    reason TEXT,                                -- 왜 창을 열었는가 (재시작 사유 등)
    created_at TEXT NOT NULL,
    PRIMARY KEY (track, window_seq)
);

CREATE INDEX IF NOT EXISTS idx_validation_windows_track_seq ON validation_windows (track, window_seq DESC);
