# WO-FCE-UNIFIED-CHART-02 — 청산 차트 단일화·라이브 가독성 개선

우선순위: P1 (동일 데이터의 중복 화면과 무설명 강조선이 실시간 판단을 방해함)
선행: WO-FCE-UNIFIED-CHART-01

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-UNIFIED-CHART-02" docs/ backend/ dashboard/` 결과 확인

## 진단 (코드 확정)
- `dashboard/components/symbol-analysis-view.tsx:209`가 통합 차트 아래에 `LiquidationHeatmapPanel`을 다시 렌더해 동일한 Bitget 실현 청산 데이터를 두 번 보여준다.
- `dashboard/components/position/PositionCandlestickChart.tsx:1210`의 상위 밀집 구간은 노란 점선 사각형만 그려 의미·순위·금액을 차트에서 식별할 수 없다.
- `dashboard/components/position/PositionCandlestickChart.tsx:56`의 저장 키와 기본 농도 55%는 기존 20% 저장값을 계속 복원해 밀집 밴드 가시성이 낮다.
- 통합 차트는 5초마다 실현 청산을 갱신하지만 화면에는 갱신 주기와 마지막 수신 시각이 없어 라이브 상태를 판독하기 어렵다.

## 작업
### 1. 청산 화면 단일화
- `SymbolAnalysisView`의 독립 `LiquidationHeatmapPanel`을 제거하고 통합 캔들 차트를 단일 정본으로 사용한다.
- 미사용 레거시 컴포넌트·클라이언트 타입/API·전용 CSS를 함께 제거한다.

### 2. 밀집 구간 의미와 대비 강화
- 노란 점선 사각형을 순위·실현 금액이 표시되는 상위 밀집 밴드로 대체한다.
- 상위 3개 구간을 차트 위 칩으로도 노출하고 클릭 시 해당 가격을 강조한다.
- 기본 농도를 높이고 저장 키를 갱신해 과거 20% 값이 새 기본값을 덮지 않게 한다.

### 3. 정직한 라이브 상태 표시
- `LIVE · 청산 5초` 상태와 마지막 API 수신 시각을 표시한다.
- 마지막 캔들은 `최근 확정`으로 명시해 미확정 캔들처럼 오인하지 않게 한다.
- 현재가 펄스와 마지막 확정 캔들 마커로 차트의 갱신 지점을 식별한다.

## 수용 기준
- [x] 화면에 청산 차트가 통합 차트 1개만 존재하고 `realized-liquidation-heatmap`은 렌더되지 않는다.
- [x] 데이터가 있으면 상위 밀집 구간에 `밀집 1/2/3`, 가격, 실현 금액이 표시되고, 없으면 명시적 빈 상태가 표시되며 노란 점선 설명 공백이 없다.
- [x] 신규 사용자 기본 농도는 68% 이상이며 기존 20% 저장값은 새 버전에서 재사용하지 않는다.
- [x] `LIVE · 청산 5초`, 마지막 수신 시각, `최근 확정` 캔들 의미가 화면에 표시된다.
- [x] 프론트 게이트·Playwright 스모크/비주얼 테스트와 자산 검사가 통과한다.

## 금지
- 미확정 캔들을 확정 캔들처럼 표시하지 않는다.
- 실현 청산을 미래 예상 청산가 또는 진입 신호로 표현하지 않는다.
- 자동 승격·룩어헤드·미검증 방향 판정·실주문 경로를 추가하지 않는다.

## 문서
- 갱신할 `docs/*.md`: `docs/Derivatives.md`, `docs/LiquidationIntelligence.md`

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
