-- WO-FCE-WHALE-FOLLOW-01 Phase 6-2 — 고래 추종 페이퍼 트랙 원장.
--
-- ## 왜 별도 테이블인가 (C3)
--
-- 추종 트랙은 크립토 트랙과 **진입 트리거만 다르고** 사이징·잠금·출구는 같다. 같은
-- 원장에 섞으면 두 가지가 동시에 망가진다:
--
-- 1. 크립토 트랙의 표본·판정이 오염된다. `sample_viability` 가 `paper_trades` 를 세므로
--    고래 진입이 크립토 트랙의 진입률·표본 수로 잡힌다
-- 2. 어느 트리거가 우위를 냈는지 분해할 수 없다. 변수를 하나만 바꿔야 기여가 측정된다
--
-- 트랙별 독립 판정이 `COMPLETION_DEFINITION.md` 규정이다.
--
-- ## 스키마는 paper_trades 와 같다
--
-- 같은 `PaperTrade` 모델을 그대로 쓴다. 그래야 `policy.open_trade` · `plan_position_size` ·
-- `evaluate_exit` · `apply_exit_decision` 을 **수정 없이** 재사용할 수 있다(C5).
-- 자격 종류(관찰/승격)·고래 식별·체결→진입 지연은 payload 안에 들어간다.

CREATE TABLE IF NOT EXISTS whale_follow_trades (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    status TEXT NOT NULL,
    entry_bar_at TEXT NOT NULL,
    exit_at TEXT,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_whale_follow_trades_status_symbol
    ON whale_follow_trades(status, symbol, updated_at DESC);

-- 지갑별 조회 — 자격 종류별 성과 분리 집계(6-4 항목 3)에 쓴다.
CREATE INDEX IF NOT EXISTS idx_whale_follow_trades_entry
    ON whale_follow_trades(entry_bar_at DESC);
