-- WO-FCE-PAPER-OBSERVABILITY-01 (D3): 조용한 스킵의 관측화.
-- last_success_at은 조기 반환(비활성/미구성)에도 갱신되어 "돌고 있다"는 착시를 준다.
-- last_effective_run_at은 엔진이 실제로 평가를 수행한 마지막 시각만 기록한다.
-- 두 값의 괴리가 "잡은 success지만 한 번도 평가하지 않았다"를 드러낸다.
ALTER TABLE worker_heartbeat ADD COLUMN last_effective_run_at TEXT;
