-- WO-FCE-RISK-SIZING-01 Phase 4-1 — 결정 시점 호가 깊이 관측.
--
-- 문제: `policy.slippage_pct = 0.03` 은 출처가 없고 체결 기록과 대조된 적도 없다. 그런데
-- Phase 3-5 민감도표대로 이 값 하나가 netR 의 부호를 가른다(−0.995 vs −0.013). 엔진의
-- 흑자 여부가 미검증 상수에 달려 있다.
--
-- 실주문은 봉인이라 불가능하지만(C12), 결정 시점의 호가 깊이에 명목가를 얹으면 예상
-- 체결가가 나온다. 문제는 **호가가 저장되지 않는다**는 것이다 — 크립토 봉 미보존
-- (STOP_EXECUTION.md §3)·와이코프 국면 미저장(POSITION-VIEW-01)과 같은 계열의 결함이며,
-- 사후 재구성이 원리적으로 불가능하다. 그래서 결정 시점에 남긴다.
--
-- 관측치는 가정값 0.03% 를 **대체하지 않고 나란히** 기록된다(C7). 대체 기준은
-- app/paper/slippage.py::ASSUMPTION_REPLACEMENT 에 결과 확인 전 고정돼 있다.
--
-- 리텐션: DB 가 이미 10GB 다. `db_depth_observation_retention_days`(기본 45일)로
-- maintenance 경로에서 정리한다 — 표본 대체 기준(30건)을 채우고도 남는 창이다.
CREATE TABLE IF NOT EXISTS execution_depth_observations (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    decision TEXT NOT NULL,                     -- entry | exit
    observed_at TEXT NOT NULL,                  -- 결정 시점 (사후 재구성 불가)
    direction TEXT,
    trade_id TEXT,
    mid_price REAL,
    spread_pct REAL,
    -- 대표 프로브(명목 300 매수)의 관측 슬리피지. 상세는 payload 에 전량 보관한다.
    observed_slippage_pct REAL,
    assumed_slippage_pct REAL NOT NULL,
    unavailable_reason TEXT,                    -- 깊이를 못 얻은 사유 (침묵 금지)
    payload JSON NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_execution_depth_observed_at
  ON execution_depth_observations (observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_execution_depth_symbol_observed
  ON execution_depth_observations (symbol, observed_at DESC);
