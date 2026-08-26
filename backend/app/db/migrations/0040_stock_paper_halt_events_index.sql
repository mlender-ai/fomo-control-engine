-- WO-FCE-STOCK-STATUS-01 3-2 — 정지 이력 조회 인덱스.
--
-- ## 왜 필요한가
--
-- 정지 이력은 `event_type IN ('track_stopped','invariant_failure')` 로 찾는다. 그런데
-- `stock_paper_events` 는 2026-08-25 기준 **2,087만 행**이고 그중 두 종류는 합쳐서
-- 한 자릿수다. 인덱스가 없으면 그 몇 행을 찾으려고 2,087만 행을 훑는다 — 실측에서
-- 대시보드 조회가 5초 예산을 넘겼다.
--
-- ## 부분 인덱스인 이유
--
-- 전체 인덱스를 걸면 `unfilled` 2,087만 행까지 색인해 쓰기마다 비용이 붙는다.
-- 실제로 필요한 것은 사건 두 종류뿐이므로 그것만 색인한다. `0038` 이 같은 이유로
-- 부분 인덱스를 쓴 선례가 있다.

CREATE INDEX IF NOT EXISTS idx_stock_paper_events_halt
    ON stock_paper_events(event_type, id DESC)
    WHERE event_type IN ('track_stopped', 'invariant_failure');
