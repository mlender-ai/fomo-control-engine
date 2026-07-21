# WO-FCE-SCOUT-RECOVERY-01 — 스카우트 추적 누락·주식 워커 복구

우선순위: P0 — 텔레그램 명령 무응답과 추적 목록 누락은 관측 공백을 만든다.
선행: WO-FCE-TOSS-PAPER-02, WO-FCE-LOCAL-RECOVERY-01

## 착수 전 확인 (AGENTS.md 불변 규칙 2)

- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `rg "SCOUT-RECOVERY|scout tracking" docs/ backend/ dashboard/` 결과 확인

## 진단 (코드 확정)

- `watchlist`에는 HYPEUSDT·NBISUSDT·IBMUSDT가 남아 있었지만
  `scout_handlers._tracking_sources()`는 활성 진입 의도와 무장 셋업만 읽었다. 따라서 활성
  셋업이 있던 HYPEUSDT만 정기 상태 펄스의 `추적 중` 블록에 노출됐다.
- 동일 심볼의 수동 관심목록과 엔진 셋업이 함께 존재하면 펄스에서 중복 표시될 수 있었다.
- 주식 스카우트 워커는 분석 결과 안의 `datetime`을 그대로 `json.dumps()`하여
  `TypeError: Object of type datetime is not JSON serializable`로 실패했다.
- 2026-07-21 11:33 KST 이후 백엔드가 중단되어 15:29 KST `/scout nbis` 업데이트를
  수신할 프로세스가 없었다.

## 작업

### 1. 추적 목록 정합성

- 관심목록 자체를 수동 추적 소스로 포함한다.
- 활성 의도·셋업이 아직 없어도 `추적 조건 확인 중` 상태로 웹·텔레그램에 노출한다.
- 정기 펄스에서는 동일 심볼의 수동·엔진 추적을 한 번만 표시한다.

### 2. 주식 분석 스냅샷 직렬화

- `datetime`·UUID·Enum만 명시적으로 JSON 값으로 변환하고 알 수 없는 타입은 실패시킨다.
- 직렬화 회귀 테스트를 추가한다.

### 3. 운영 복구

- 백엔드와 내장 워커·텔레그램 폴링을 재기동한다.
- API에서 저장된 세 심볼과 워커 heartbeat를 확인한다.

## 수용 기준

- [x] 평범한 관심목록 종목도 `/api/scout/scan`의 `tracked`에 포함
- [x] HYPEUSDT·NBISUSDT·IBMUSDT가 추적 상태에 표시
- [x] 동일 심볼 수동·엔진 중복 펄스 0건
- [x] `datetime` 포함 주식 분석 스냅샷 저장 성공
- [x] 백엔드 health와 Telegram bot heartbeat 정상
- [x] 관련 테스트와 HARNESS.md 게이트 통과

## 금지

- 실주문 경로 변경 금지
- 신규 감지기 추가 금지
- 추적 표시 문제를 해결하기 위한 진입 게이트 완화 금지
- 시크릿 파일 수정 금지

## 문서

- `docs/Scout.md`
- `docs/TossStockPaperTrading.md`
- `docs/LocalRecovery.md`

## 완료 정의 (공통)

- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
