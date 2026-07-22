# WO-FCE-CRYPTO-ETF-FLOW-01 — BTC·ETH 현물 ETF 자금 흐름

우선순위: P1 (포지션·스카우트의 수급 맥락 보강)
선행: WO-FCE-21 파생 데이터 수집

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `rg "ETF.*flow|etf_flow" docs/ backend/ dashboard/` 결과 확인

## 진단 (코드 확정)
- `backend/app/marketdata/coinglass.py`는 BTC·ETH 옵션을 예외 수집하지만 현물 ETF 순유입 엔드포인트는 호출하지 않는다.
- 스카우트와 라이브 포지션은 모두 `MoneyFlowCard`를 재사용하므로, 파생 컨텍스트의 `signals`에 정직한 공통 계약을 추가하면 두 화면을 중복 구현하지 않고 표시할 수 있다.
- CoinGlass V4 공식 엔드포인트는 BTC `/api/etf/bitcoin/flow-history`, ETH `/api/etf/ethereum/flow-history`이며 일별 순유입·유출과 ETF별 내역을 제공한다.

## 킬존 대조 및 위생 필요성
- `docs/FCE-MOAT-STRATEGY-01.md`의 기능 추가 킬존에 해당할 수 있으므로 방향 판정·자동 진입에는 연결하지 않는다.
- 이번 변경은 BTC·ETH 현물 ETF라는 외부 수급 근거가 기존 화면에서 누락된 데이터 오표시/관측 공백을 해소하는 위생 작업이다.
- 신규 감지기·점수·자동 승격을 만들지 않고 관측 카드만 추가한다.

## 작업
### 1. BTC·ETH 전용 수집
- CoinGlass 기존 어댑터에서 BTC·ETH에만 ETF flow-history를 1회 추가 호출한다.
- 최근 보고일 순유입, 최근 5개 보고일 합계, ETF별 기여를 정규화한다.
- 타 심볼에는 호출도 필드도 만들지 않는다.

### 2. 공통 파생 컨텍스트
- `signals.etf_flow`를 BTC·ETH에만 노출한다.
- 데이터 잠김·오류·빈 응답은 숫자를 채우지 않고 상태와 이유만 노출한다.

### 3. 스카우트·포지션 공통 UI
- 공용 `MoneyFlowCard`에 최종 보고일, 일간 순유입/유출, 5일 합계와 상위 ETF 기여를 표시한다.
- “일별 ETF 보고 · 실시간 체결 아님”을 상시 표기한다.

## 수용 기준
- [x] BTCUSDT·ETHUSDT만 ETF flow 요청 및 `signals.etf_flow` 노출
- [x] 기타 심볼에는 ETF flow 호출/필드/UI가 없음
- [x] 순유입·유출 부호, 최근 5개 보고일 합계, ETF별 기여 파싱 테스트 통과
- [x] 스카우트와 라이브 포지션의 공용 카드에서 동일 데이터 표시
- [x] 키/플랜 미연결 시 가짜 숫자 없이 연결 상태 표시

## 금지
- ETF flow를 방향 판정, 컨플루언스, 자동 진입 또는 후보 승격에 사용하지 않는다.
- 현물 ETF의 일별 보고값을 실시간 체결 또는 온체인 입금으로 표현하지 않는다.
- BTC·ETH 외 심볼로 범위를 확장하지 않는다.
- 실주문 경로를 변경하지 않는다.

## 문서
- `docs/Derivatives.md`

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
