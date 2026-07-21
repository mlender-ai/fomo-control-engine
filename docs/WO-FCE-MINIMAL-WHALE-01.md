# WO-FCE-MINIMAL-WHALE-01 — 미니멀 차트 고래 체결 실시간 오버레이

우선순위: P0 (이미 수집한 확정 체결이 미니멀 차트에서 누락·지연되는 데이터 오표시)
선행: WO-FCE-BITGET-TOSS-MAP-02, HyperliquidWhales

## 착수 전 확인 (AGENTS.md 불변 규칙 2)

- [x] `git branch -a`에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status`에 기존 구현 없음
- [x] `rg "WO-FCE-MINIMAL-WHALE-01" docs backend dashboard` 결과 없음

## 킬존 대조와 위생 필요성

`docs/FCE-MOAT-STRATEGY-01.md` §2의 범용 차트 기능 경쟁에는 진입하지 않는다. 현재 백엔드가
Hyperliquid 확정 체결을 이미 제공하지만 미니멀 `compressed` 렌더 분기가 온체인 오버레이를
건너뛰고, 마커 선택이 금액 우선이라 최신 체결이 사라지며, 차트 자체도 자동 갱신되지 않는다.
이는 신규 지표가 아니라 **관측 데이터 누락·신선도 오표시를 제거하는 위생 수리**다.

## 진단 (코드 확정)

- `PositionCandlestickChart.renderTaOverlay`: `compressed` 분기에서 `onchainOverlayNodes` 미호출.
- `onchainMarkerNodes`: 일반 체결을 원형으로 렌더해 롱/숏 방향이 즉시 구분되지 않음.
- `chart_onchain_context`: 크기순 상위 8그룹을 선택해 최근 체결이 작은 경우 차트에서 누락.
- `LivePositionCockpit`: 고래 대시보드는 30초 갱신하지만 선택 차트는 선택/모드 전환 때만 갱신.
- `Settings.hyperliquid_whale_poll_interval_seconds`: 기본 120초로 UI 갱신보다 느림.

## 작업

### 1. 확정 체결 마커 정합

- 최근 8개 체결 그룹을 시간 우선으로 선택한다.
- 완성 봉 이후 발생한 확정 체결은 우측 LIVE 마커로 분리하고 실제 체결 시각·가격을 보존한다.
- 롱은 위쪽 삼각형, 숏은 아래쪽 삼각형으로 표시한다.
- 진입·증액은 채움, 감액·청산은 비움으로 구분한다.

### 2. 미니멀 실시간 갱신

- 미니멀 순수 크립토 선택 차트를 30초마다 조용히 갱신한다.
- Hyperliquid 수집 기본 주기를 공식 IP 가중치 예산 안의 30초로 맞춘다.
- 주식 기초자산 선물에는 크립토 고래 마커를 표시하지 않는다.

### 3. 버그·회귀 감사

- 현재 미완성 봉 이벤트, 최신 8그룹 선택, 삼각형 방향, 미니멀 표시, 주식 미노출을 테스트한다.
- 오류 시 기존 차트를 보존하고 코어 포지션 관제를 막지 않는다.

## 수용 기준

- [x] 미니멀 ETH/BTC 차트에 롱·숏 삼각 마커 노출
- [x] LIVE 마커 툴팁에 실제 체결 시각·가격·규모·검증 상태 노출
- [x] 최신 8그룹 선택 및 30초 갱신
- [x] 주식 선물 고래 마커 0개
- [x] 미검증 고래가 방향 판정·자동 진입에 사용되지 않음
- [x] 관련 backend/frontend/E2E 테스트 통과

## 금지

- 미검증 고래의 방향 판정·자동 진입 사용
- 캔들 미래값으로 체결을 재배치하거나 가짜 체결가 생성
- 실주문 경로 추가
- 신규 감지기 추가

## 문서

- 갱신: `docs/HyperliquidWhales.md`, `docs/WO-FCE-MINIMAL-WHALE-01.md`

## 완료 정의 (공통)

- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인

## 검증 기록

- 백엔드: `588 passed, 2 deselected`, 전체 커버리지 77.10%, 품질 코어 88.07%.
- 프론트: ESLint error 0, TypeScript 통과, 프로덕션 빌드 통과.
- E2E: 22개 전부 통과. 롱/숏 삼각형과 주식 선물 미노출을 독립 검증.
- 운영: `/api/onchain/whales`가 30초, 최대 880/1200 weight/min 예산을 노출.
- 배포 화면: ETH 미니멀 차트에서 삼각형 8개(롱 6, 숏 2) DOM·시각 확인.
