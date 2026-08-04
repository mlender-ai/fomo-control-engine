-- WO-FCE-BREACH-ALERT-FIX-01: 청산 경로 부재 구간의 고아 포지션을 실제로 표시한다.
--
-- 0032 는 excluded_from_stats/exclusion_reason 컬럼을 만들고 "고아는 엔진 성과가 아니다"라고
-- 규정했지만 **기존 행에 값을 채우지 않았다**. 2026-08-04 실측: 보유 6건 전부
-- excluded_from_stats=0, exclusion_reason=NULL, opened_at=NULL, exit_plan=NULL 이었다.
--
-- 그 결과 데드락이 생겼다:
--   · exit_plan·opened_at 이 NULL → 스탑·목표 없음, _holding_days=0 → 청산 트리거 영구 미도달
--   · 그런데 coverage 슬롯은 계속 점유 → coverage_slots_used(3) >= 목표(3) → 진입 영구 0
--   · 진입 0 + 청산 0 이 서로를 지탱한다.
--
-- 소급 청산은 하지 않는다(금지사항). 지금 만든 규칙을 과거 포지션에 적용하는 것은 라이브가
-- 아니라 백테스트다. 통계·슬롯 계산에서만 제외하고 원장 행은 그대로 남긴다.

UPDATE stock_paper_positions
SET excluded_from_stats = 1,
    exclusion_reason = 'void_no_exit_path'
WHERE quantity > 0 AND opened_at IS NULL AND exit_plan IS NULL AND excluded_from_stats = 0;

UPDATE stock_paper_mode_positions
SET excluded_from_stats = 1,
    exclusion_reason = 'void_no_exit_path'
WHERE quantity > 0 AND opened_at IS NULL AND excluded_from_stats = 0;
