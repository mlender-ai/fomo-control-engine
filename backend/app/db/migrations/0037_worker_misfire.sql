-- WO-FCE-WORKER-HANG-02 Phase 2-2: 건너뛴 발화(misfire)의 영속화.
--
-- APScheduler 의 `misfire_grace_time` 기본값 1초와 이벤트 루프 지연(실측 평균 11.66초)이
-- 만나 거의 모든 발화가 소실됐다. 그런데 그 소실은 **어디에도 기록되지 않았다** —
-- APScheduler 는 WARNING 을 찍지만 잡 이름이 없어(`WorkerManager._run_scheduled_job`)
-- 어느 잡이 굶는지 알 수 없었다. 그래서 두 달 숨었다.
--
-- `skipped`(이전 틱이 아직 도는 중)와 **별도 칼럼**이다. 원인이 다르고 처방이 다르다:
-- skipped 는 잡이 느린 것이고 misfired 는 스케줄러가 아예 실행하지 않은 것이다.
-- 한 칸에 합치면 misfire 가 정상 스킵에 묻힌다.
--
-- `misfire_grace_seconds` 를 함께 남기는 이유: 정책이 조용히 1초로 되돌아갔는지
-- **조회로 확인**할 수 있어야 한다. 코드만 보고 판단하면 이번 사고를 반복한다.
ALTER TABLE worker_heartbeat ADD COLUMN misfired INTEGER NOT NULL DEFAULT 0;
ALTER TABLE worker_heartbeat ADD COLUMN last_misfire_at TEXT;
ALTER TABLE worker_heartbeat ADD COLUMN misfire_grace_seconds INTEGER NOT NULL DEFAULT 0;
