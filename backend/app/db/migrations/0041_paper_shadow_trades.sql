-- FOMO LAB ENG-02 — 그림자 원장.
--
-- 그림자는 본 트랙과 **같은 봉 · 같은 분석**을 받고 정책 **하나만** 다르게 돈다(`app/paper/shadows.py`).
-- 결과는 여기 따로 쌓는다 — `paper_trades` 에 섞으면 본 트랙의 표본 · 성적 · 용량이 오염된다(whale_follow_trades 와 같은 이유).
-- 스키마는 paper_trades + 그림자 번호. 같은 `PaperTrade` 모델을 쓰므로 진입 · 청산 함수를 수정 없이 재사용한다.
-- 행을 지우지 않는다 — 실패한 실험의 거래도 사실이다.

CREATE TABLE IF NOT EXISTS paper_shadow_trades (
    id TEXT PRIMARY KEY,
    shadow INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    status TEXT NOT NULL,
    entry_bar_at TEXT NOT NULL,
    exit_at TEXT,
    updated_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_shadow_trades_shadow_status
    ON paper_shadow_trades(shadow, status, symbol);
