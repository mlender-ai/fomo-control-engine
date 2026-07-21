# WO-FCE-WHALE-RELIABILITY-AUDIT-02 — 고래 원시 종목 조회·필터 신뢰성 감사

우선순위: P0 (확정 체결과 공개 포지션이 존재해도 원시 종목 조회에서 누락되거나 전체 시장 데이터로 둔갑하는 데이터 오표시)
선행: WO-FCE-ONCHAIN-FLOW-AUDIT-01

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-WHALE-RELIABILITY-AUDIT-02" docs/ backend/ dashboard/` 결과 없음

## 킬존 대조와 위생 필요성
- `docs/FCE-MOAT-STRATEGY-01.md` §2.1의 신규 차트·지표 기능이 아니다.
- append-only 원장에 있는 `XYZ:SNDK` 체결이 정규 `symbol` 열이 비었다는 이유로 차트와 종목 활동에서 누락되고, 종목 검색은 전역 20건을 다시 필터링해 실제 체결을 빈 상태로 오표시했다.
- 선택 종목이 갱신 중 사라지면 종목 이름은 유지한 채 전체 시장 합계를 보여줬다. 따라서 존재하는 관측을 숨기거나 다른 범위로 표시하는 위생 장애 복구다.

## 진단 (코드 확정)
- `backend/app/db/repository/marketdata.py`의 `list_whale_events(symbol=...)`는 DB `symbol`만 비교해 `symbol=''`, `coin='XYZ:SNDK'` 이벤트를 반환하지 않았다.
- `backend/app/onchain/service.py`의 종목 활동·지원 여부·검증 합의도 일부 경로에서 `symbol`만 읽어 원시 종목 포지션과 이벤트를 제외했다.
- 대시보드는 전역 라운드로빈 `recent_events` 20건을 선택 종목으로 다시 필터링했다. 21번째 종목의 체결은 72시간 원장과 흐름에 있어도 선택 시 보이지 않았다.
- 선택 키가 `flow_by_instrument`에서 사라질 때 `?? data.flow` 폴백으로 전체 시장 합계가 선택 종목 값처럼 표시됐다.

## 작업
### 1. 원시 종목 조회 정합성
- 메모리·SQLite 저장소의 종목 조회를 `symbol` 우선, 없으면 원장 payload의 `coin`으로 통일한다.
- 종목 활동, 차트 지원 여부, 검증 합의에서 동일한 instrument 정규화 함수를 재사용한다.

### 2. 종목별 최근 체결 완전성
- 72시간 burst를 한 번 생성하고 전역 라운드로빈 20건과 종목별 최신 10건을 별도로 제공한다.
- 원본 `whale_events`의 수정·삭제 없이 표시 계층만 확장한다.

### 3. 정직한 필터 상태
- 선택 종목 데이터가 없으면 전체 합계로 폴백하지 않고 0/빈 타임라인을 표시한다.
- 존재하지 않는 검색어는 현재 선택을 유지하고 명시적인 “찾지 못함” 상태를 표시한다.

## 수용 기준
- [x] SQLite와 메모리 저장소 모두 `XYZ:SNDK` 원시 종목 조회가 가능하다.
- [x] 원시 종목의 공개 포지션과 최근 체결이 `symbol_activity`에 함께 표시된다.
- [x] 전역 20건 밖의 21번째 종목도 종목별 최근 체결에서 조회된다.
- [x] 선택 종목 데이터 부재 시 전체 합계로 둔갑하지 않는다.
- [x] 잘못된 검색어가 조용히 무시되지 않고 상태 메시지를 남긴다.
- [x] 백엔드 전체 게이트와 프론트 lint/typecheck/E2E를 통과한다.

## 금지
- 원본 체결 원장 삭제·수정 금지.
- candidate 고래를 방향 판정·자동 진입에 사용 금지.
- 자동 승격·룩어헤드·실주문 경로 변경 금지.

## 문서
- 갱신: `docs/HyperliquidWhales.md`, 본 WO.

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [ ] origin/main 반영 + CI success 확인 (불변 규칙 1·3)

## 검증 결과
- 백엔드: ruff, format, import cycle, mypy baseline, 전체 테스트 598건, 품질 baseline 통과.
- 프론트: lint 0 errors(기존 warning 13), typecheck 통과.
- E2E: 최초 샌드박스 실행은 Chromium Mach 포트 권한으로 실행 전 실패, 동일 커밋을 정상 브라우저 권한으로 재실행해 23건 전부 통과. 첫 원격 CI에서 차트 백그라운드 갱신 중간 프레임을 캡처하는 기존 스냅샷 레이스를 발견해, 로딩 배지 소멸을 기다리는 결정론 게이트를 추가했다.
