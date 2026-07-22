# WO-FCE-WHALE-ALERT-BATCH-03 — 리더보드 고래 3분 다중체결 알림 묶음

우선순위: P0 — 동일 고래의 분할·다종목 체결이 Telegram 개별 푸시로 폭주해 실제 관측 맥락을 가린다.
선행: WO-FCE-WHALE-RELIABILITY-AUDIT-02

## 착수 전 확인 (AGENTS.md 불변 규칙 2)

- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 3분 알림 묶음 구현 없음
- [x] `rg "WO-FCE-WHALE-ALERT-BATCH-03" docs backend dashboard` 결과 없음

## 진단 (코드 확정)

- `backend/app/notify/alerts.py`의 기존 `evaluate_whale_events`는 한 폴링에서 받은 `open`·`flip` 이벤트마다 즉시 `_fire_if_allowed`를 호출했다. 같은 지갑이 여러 종목을 동시에 체결하면 휴대전화에 체결 수만큼 푸시가 발생했다.
- `increase`는 진입 익스포저가 늘어난 확정 체결인데도 알림 입력에서 제외되어, 다중체결 묶음이 실제 집행을 일부 누락했다.
- `backend/app/worker/manager.py`와 실서비스 `/api/onchain/whales`의 `rate_budget.poll_interval_seconds`를 확인한 결과 수집 런타임은 이미 30초다. 수집 주기 변경은 필요하지 않다.
- 기존 `NotificationState`에는 3분 창을 폴링과 재시작 사이에 유지할 버퍼가 없었다.

## 킬존 대조와 위생 필요성

이 작업은 신규 차트·지표·감지기 기능이 아니다. 동일 원시 체결을 여러 푸시로 중복 노출해 사용자가 한 고래의 연속 집행을 서로 독립된 판단처럼 오독하는 문제와 알림 폭주를 막는 표시 위생 작업이다. 원시 체결 원장과 검증·방향 판정은 변경하지 않는다.

## 작업

### 1. 지갑별 고정 3분 체결 창

- `open`·`increase`·`flip` 확정 체결을 지갑 주소별로 버퍼링한다.
- 첫 체결의 이벤트 시각부터 180초인 고정 창을 사용하며, 30초 폴링에서 창이 닫힌 뒤 확정한다.
- 한 건뿐이면 Telegram 알림만 폐기하고 원시 `WhaleEvent` 원장은 그대로 유지한다.

### 2. 단일 묶음 메시지

- 두 건 이상일 때 한 지갑당 한 메시지만 발송한다.
- 같은 종목·방향·행동은 건수, 관측 명목금액 합계, 수량 가중 평균 체결가로 한 줄에 합친다.
- 서로 다른 종목 또는 롱·숏 전환은 같은 메시지 안의 별도 줄로 보존한다.

### 3. 재시작·중복 안전성

- 대기 이벤트를 기존 notification state 파일에 영속화한다.
- 이벤트 ID/fill ID로 같은 폴링 입력의 중복 큐잉을 막는다.
- 설정 예시에 180초 묶음 창을 명시한다.

## 수용 기준

- [x] 단일 체결은 3분 뒤에도 Telegram 발송 0건
- [x] 같은 지갑의 3분 이내 2건 이상은 메시지 1건
- [x] 같은 체결을 반복 입력해도 묶음 내 중복 0건
- [x] `increase` 체결도 다중체결에 포함
- [x] 3분 밖의 두 단일 체결은 서로 묶이지 않음
- [x] 프로세스 재시작 후 대기 묶음 복구
- [x] 원시 고래 이벤트 저장·검증·방향 판정 경로 무변경

## 금지

- 원시 체결 병합·삭제 금지
- 후보 고래를 검증 고래로 자동 승격 금지
- 미검증 고래를 방향 판정·자동 진입에 사용 금지
- 실주문 경로 변경 금지

## 문서

- `docs/HyperliquidWhales.md`
- `docs/WO-FCE-WHALE-ALERT-BATCH-03.md`
- `backend/.env.example`

## 완료 정의 (공통)

- [x] HARNESS.md 백엔드 게이트 통과 (`601 passed`, coverage 77.38%, core 88.07%, mypy 166/174)
- [x] 관련 문서 갱신
- [x] origin/main 반영 + CI success 확인 (run `29879799059`)
