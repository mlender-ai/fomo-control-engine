# WO-FCE-80 — 풋·콜 가시성 및 CVD 대기 상태 교정

우선순위: P1 (실데이터가 수집돼도 핵심 옵션 지표가 보이지 않고, CVD 미지원 상태가 무한 대기로 오해됨)
선행: WO-FCE-79

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-80" docs/ backend/ dashboard/` 결과 없음

## 진단 (코드 확정)
- `dashboard/components/position/CompactChartWorkspace.tsx`는 OCC 풋·콜 값을 Money Flow footer의 작은 글씨로만 표시해 최소 화면에서 발견하기 어렵다.
- 실제 SOXL compact chart API에는 OI P/C 1.8425와 계약량 P/C 2.0819가 정상 포함된다.
- Bitget SOXL 선물 체결 7,984건이 4시간 캔들 버킷 하나로 합쳐져 CVD 점이 1개만 생성된다. UI는 두 개 이상의 델타를 요구해 계속 `구간 시계열 축적 중`으로 표시한다.
- SOXL은 Bitget 현물 마켓 매핑이 없어 현물 CVD가 생성되지 않는다. 이 상태는 축적 대기가 아니라 명시적 미지원이다.

## 작업
### 1. 풋·콜 독립 지표
- Money Flow 상단에 OCC OI/계약량 P/C를 숫자와 콜·풋 계약 수로 바로 표시한다.

### 2. 체결 이벤트 CVD
- 4시간 캔들 버킷이 하나뿐이어도 최근 실제 체결을 시간 순서의 최대 24개 이벤트 구간으로 집계해 CVD 시계열을 제공한다.
- 기존 가격·OI·판정 로직은 확정 캔들 기준을 유지하며 이벤트 CVD는 관측 시각화에만 사용한다.

### 3. 상태 문구 교정
- 현물 마켓 미지원은 `현물 마켓 미지원`으로, 단일 CVD 점은 `현재 구간`으로 표시해 무한 대기처럼 보이지 않게 한다.

## 수용 기준
- [x] SOXL Money Flow에서 OI P/C와 계약량 P/C가 footer 탐색 없이 보인다.
- [x] SOXL 선물 CVD가 실제 체결 기반 막대 시계열로 표시된다.
- [x] SOXL 현물 CVD는 `현물 마켓 미지원`으로 표시된다.
- [x] 기존 분류·룩어헤드 테스트와 프론트엔드 E2E가 통과한다.

## 금지
- 옵션 P/C와 미검증 CVD를 자동 진입·방향 점수에 반영하지 않는다.
- 미확정 캔들 또는 미래 데이터를 판정에 사용하지 않는다.
- 실주문 코드를 추가하지 않는다.

## 문서
- 갱신할 `docs/*.md`: `docs/MoneyFlow.md`, `docs/WO-FCE-80.md`

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [ ] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
