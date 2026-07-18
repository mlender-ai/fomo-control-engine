# WO-FCE-UNIFIED-CHART-03 — 청산 컨트롤 위계·반응형 정리

우선순위: P1 (중간 데스크톱 폭에서 핵심 밀집 카드가 잘리고 동급 정보 과밀로 차트 판독을 방해함)
선행: WO-FCE-UNIFIED-CHART-02

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-UNIFIED-CHART-03" docs/ backend/ dashboard/` 결과 확인

## 진단 (코드 확정)
- `dashboard/app/globals.css`의 `.unifiedHeatmapOverview`는 좌측 최소 280px와 우측 최소 514px 이상을 동시에 요구해 2열 상세 화면의 차트 열이 좁아지면 마지막 밀집 카드가 `overflow: hidden` 경계에서 잘린다.
- 총액 요약 3개, 데이터 기준, 구간 설명 카드, 밀집 카드 3개가 같은 시각 우선순위로 한 줄에 놓여 핵심 가격보다 보조 통계가 먼저 보인다.
- 방향·규모·기간·표현 필터와 농도 제어가 모두 기본 노출되어 차트보다 컨트롤이 강하게 보인다.
- `persist/event` 원문은 사용자 행동보다 구현 용어를 드러낸다.

## 작업
### 1. 정보 위계 재구성
- 신뢰 상태(`과거 발생 · 예측 아님`, N, 5초 갱신, 최근 확정)를 헤더 1순위로 둔다.
- 기간·표현만 기본 제어로 남기고 방향·규모는 접힌 세부 필터로 이동한다.
- 보조 총액·마지막 이벤트·중복 기준 문구를 기본 화면에서 제거한다.

### 2. 밀집 구간 반응형 정리
- 설명과 상위 3개 카드를 분리하고 카드 그리드를 `repeat(3, minmax(0, 1fr))`로 제한한다.
- 가격을 1순위, 실현 추정액·건수를 2순위로 표시한다.
- 1440px 2열 상세 화면과 390px 모바일에서 부모 경계·가로 뷰포트를 넘지 않게 한다.

### 3. 언어 정리
- `persist/event` 표시명을 `누적 잔존/발생 시점`으로 바꾸고 의미를 툴팁에 명시한다.
- 실현 청산은 과거 관측이며 미래 유동성이나 진입 신호가 아니라는 문구를 유지한다.

## 수용 기준
- [x] 1440px 상세 2열 화면에서 모든 밀집 카드의 오른쪽 경계가 청산 컨트롤 안에 있다.
- [x] 기본 화면에는 보조 총액 3개와 방향·규모 버튼이 노출되지 않는다.
- [x] 상위 밀집 카드가 가격 → 실현 추정액·건수 순으로 읽힌다.
- [x] 세부 필터를 열면 방향·규모 필터를 사용할 수 있다.
- [x] 390px 모바일 가로 넘침이 없고 프론트/E2E/비주얼 게이트가 통과한다.

## 금지
- 미확정 캔들·미래 청산을 확정 관측처럼 표시하지 않는다.
- 실현 청산을 방향 판정·자동 진입·Entry Score에 추가하지 않는다.
- 실주문 경로, 자동 승격, 적용된 마이그레이션 변경을 만들지 않는다.

## 문서
- 갱신할 `docs/*.md`: `docs/Derivatives.md`, `docs/LiquidationIntelligence.md`

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)

## 검증
- `npm run lint` — 오류 0, 기존 경고 15개
- `npm run typecheck`
- `npm run build`
- `FCE_E2E_DATABASE_URL=sqlite:////tmp/fce-e2e-wo-unified-chart-03.db npm run test:e2e` — 18 passed
- `npm run check:minimal-budget`
- `npm run scan:design-tokens`
- `npm run check:local-assets` — 14 routes, 29 assets 2xx
- `npm run check:pwa`
