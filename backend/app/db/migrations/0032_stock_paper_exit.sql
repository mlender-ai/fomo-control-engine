-- WO-FCE-STOCK-EXIT-01: 주식 페이퍼 청산 경로 신설.
-- 기존엔 매도 주문을 생성하는 코드가 없어 진입만 있고 출구가 없었다. 청산 판단에는
-- 진입 시점의 무효화선·목표(exit_plan)와 보유 기간(opened_at)이 필요하다.
ALTER TABLE stock_paper_positions ADD COLUMN opened_at TEXT;
ALTER TABLE stock_paper_positions ADD COLUMN exit_plan TEXT;
-- 청산 경로 부재 구간의 고아 포지션은 엔진 성과가 아니다(C4). 통계에서 제외하되 원장엔 남긴다.
ALTER TABLE stock_paper_positions ADD COLUMN excluded_from_stats INTEGER NOT NULL DEFAULT 0;
ALTER TABLE stock_paper_positions ADD COLUMN exclusion_reason TEXT;

ALTER TABLE stock_paper_mode_positions ADD COLUMN opened_at TEXT;
ALTER TABLE stock_paper_mode_positions ADD COLUMN excluded_from_stats INTEGER NOT NULL DEFAULT 0;
ALTER TABLE stock_paper_mode_positions ADD COLUMN exclusion_reason TEXT;
