# WO-FCE-LIVE-UI-HIERARCHY-01 — 라이브 포지션 UI 전수 정리

우선순위: P0 (기본 관제 화면에서 핵심 판단이 첫 화면 밖으로 밀리고 모바일 조작부가 과도하게 늘어남)
선행: WO-FCE-POSITION-DEEPDIVE-01, WO-FCE-UNIFIED-CHART-03

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `rg "UI 전수|정보 위계|LIVE-UI-HIERARCHY" docs/ backend/ dashboard/` 결과 확인

## 진단 (코드 확정)
- 1440×900 미니멀에서 `positionWhaleBanner`가 213px, 차트 워크스페이스 시작점이 y=503이다. `dashboard/components/live-position-cockpit.tsx`가 미니멀에도 프로용 고래 지갑·체결 상세 전체를 렌더한다.
- 미니멀 `compactGaugePanel`은 `compactChartWorkspace`의 stretch 정렬로 실제 내용보다 긴 1,139px까지 늘어난다. `dashboard/app/globals.css`의 `align-items: stretch`가 직접 원인이다.
- 390×844에서 `cockpitToolbar`가 282px이다. `dashboard/app/globals.css`의 모바일 규칙이 제목·동기화 시각·버튼 4개·모드 토글을 모두 세로로 쌓는다.
- 1440×900 프로에서 차트 시작점이 y=819다. 프로 포지션 카드 224px, 고래 상세, 별도 verdict가 연속으로 쌓이며 `ActionPlanPanel`의 파생 카드와 빈 `EvidenceRoomPanel`이 우측 정보를 중복한다.
- `PositionCandlestickChart.tsx`의 프로 레이어 선택은 11개 항목과 사용법 문장을 한 줄 우선순위로 노출한다.

## 작업
### 1. 미니멀 정보 예산 복구
- 미니멀 도구막대는 동기화와 모드 전환만 남기고 모바일에서 한 행으로 재배치한다.
- 고래 관측은 롱/숏 분포와 내 방향 정합만 남긴 요약 바로 축소한다.
- 계기판은 내용 높이만 사용하고 차트와 함께 강제 확장하지 않는다.

### 2. 프로 정보 위계 재구성
- 포지션 목록은 심볼·방향·배율·손익·상태만 유지하는 스캔 카드로 축소한다.
- 고래 지갑/최근 체결은 접힌 상세로 이동한다.
- 차트 레이어는 핵심 6개와 접힌 고급 레이어로 나눈다.
- 파생상품 근거 카드는 액션플랜 아래 접힌 상세로 이동하고, 선택 레이어가 없을 때 빈 검증실은 렌더하지 않는다.

### 3. 반응형·회귀 게이트
- 390/1024/1440 너비에서 가로 overflow, 잘린 조작부, 늘어난 빈 패널을 계측한다.
- 미니멀 예산 검사, 전체 제품 라우트 E2E, 시각 회귀 기준선을 갱신한다.

## 수용 기준
- [x] 1440px 미니멀 차트 워크스페이스 시작점 y=334.6, 고래 요약 높이=60px
- [x] 390px 미니멀 도구막대 높이=111.3px, document 가로 overflow 0
- [x] 미니멀 계기판 높이가 차트+Money Flow 전체 높이로 stretch되지 않음
- [x] 1440px 프로 차트 시작점 y=556.9
- [x] 프로 기본 화면에 레이어 버튼 6개, 고급 레이어·고래 상세·파생 근거는 기본 접힘
- [x] 선택 레이어가 없으면 빈 검증실 패널을 표시하지 않음
- [x] 전체 제품 라우트 E2E·시각 회귀·미니멀 예산·PWA·자산 검사 통과

## 금지
- 데이터 필드·판정 로직·주문 경로를 변경하지 않는다.
- 자동 승격·자동 진입·실주문을 추가하지 않는다.
- 정보를 숨기기만 하고 접근 경로를 없애지 않는다. 프로 상세는 접힘 영역에서 보존한다.
- 적용된 마이그레이션을 수정하지 않는다.

## 문서
- 갱신할 `docs/*.md`: `docs/UXPrinciples.md`, `docs/LivePositionUiAudit.md`

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
