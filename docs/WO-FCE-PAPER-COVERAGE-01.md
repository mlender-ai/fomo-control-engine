# WO-FCE-PAPER-COVERAGE-01 — 페이퍼 검증 커버리지 개통

우선순위: P0 (주식 4주 시계가 후보 0건으로 흐르고 있어 검증 표본이 생성되지 않음)
선행: WO-FCE-TOSS-PAPER-03, WO-FCE-POLY-PAPER-01

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `rg "PAPER-COVERAGE|exploration|coverage entry" docs backend dashboard` 결과 기존 구현 없음

## 진단 (코드 확정)
- `backend/app/toss/service.py::_build_ranked_candidates`는 시장 거래대금 순위와 Toss 거래대금 순위가 동시에 존재하고 격차 신호가 발생한 종목만 반환한다. 2026-07-22 운영 표본은 KR 중복 21종목·최대 격차 11, US 중복 16종목·최대 격차 8로 임계 20을 넘는 종목이 0개였다. 따라서 200종목 가격을 수집해도 PaperBroker 평가 대상은 0개였다.
- 같은 파일의 `_session_state`는 캘린더의 모든 중첩 `startTime/endTime`을 순회한다. US의 day/pre/regular/after를 모두 `open`으로 처리해 정규장 전용 체결 제약과 충돌한다.
- stock-v3 운영 분석의 최근 KR 후보 `034020`은 short_leaning·점수 55, US 후보 `INTC`는 short_leaning·점수 54·RR 0.39로 엄격 진입 0건은 정당했다. 그러나 엄격 신호만 기다리면 실행 모델 표본도 0건이라 체결 파이프라인 자체를 검증할 수 없다.
- Polymarket은 근거 완비 시장 3건에 실제 페이퍼 진입했지만, 비용 후 edge 5% 미만 추정은 전부 관측만 되어 CLOB 체결·정산 표본 생성 속도가 제한된다.

## 킬존 대조 / UI 위생 필요성
- 신규 차트·지표를 추가하지 않는다. 기존 GE의 주식·Polymarket 화면에서 엄격 신호와 탐색 체결을 혼동하지 않도록 모드·세션·시도 상태를 명시하는 데이터 오표시 방지 작업이다.
- 해자는 강제 수익률이 아니라 어떤 정책이 어떤 관측·호가에서 체결됐고 만기에 어떻게 채점됐는지 남는 append-only 원장이다.

## 작업

### 1. 주식 전체 유니버스 커버리지
- 주목도 격차 후보와 별개로 버전 고정 200종목을 순환 스캔한다.
- 한 주기 API 예산을 제한하고 현재가·경고·실제 1분봉·호가를 받은 종목만 분석한다.
- 엄격 게이트 통과 종목은 `strict_signal`, 미통과 종목 중 관측·체결 불변식이 완비된 상위 종목은 소액 `coverage`로 진입을 시도한다.
- coverage가 우회한 게이트와 당시 측정값을 주문 evidence에 전부 저장한다.

### 2. 정규장 판정
- KR은 `today.integrated.regularMarket`, US는 `today.regularMarket`만 체결 가능 시간으로 사용한다.
- day/pre/after는 수집 상태와 무관하게 PaperBroker 세션은 닫힌 것으로 기록한다.

### 3. 분리 회계
- 주문·체결·포지션에 `entry_mode`를 저장한다.
- 주식은 strict/coverage 가상 계정을 분리해 NAV·수익률·체결 수를 별도로 표시한다.
- 기존 통합 계좌는 하위 호환 표시용이며 엄격 전략 성적으로 사용하지 않는다.

### 4. Polymarket calibration coverage
- 근거 품질·정산 출처·CLOB 호가·유동성 불변식을 통과했지만 엄격 edge가 부족한 시장은 소액 `coverage_calibration` 포지션을 허용한다.
- 근거 없는 확률, macro 베이스레이트 미연결, 호가 없음은 계속 진입 금지한다.
- strict edge와 coverage 체결 모드를 주문·체결·포지션에 저장한다.

### 5. 최소 UI
- 각 시장의 실제 정규장 상태, strict/coverage 체결 수, 탐색 목표와 최근 시도를 노출한다.
- 탐색 체결을 엄격 신호 적중 성적으로 오인할 수 없도록 뱃지와 설명을 고정한다.

## 수용 기준
- [x] 현재 순위 격차 후보가 0개여도 정규장에는 전체 유니버스 coverage 후보가 분석된다.
- [x] US dayMarket·pre·after는 PaperBroker `session_closed`, regularMarket만 `open`이다.
- [x] 주식 coverage 주문도 실제 1분 고저·거래량·호가·VI·가격제한 불변식을 전부 통과해야만 체결된다.
- [x] strict/coverage 주문·체결·성과가 분리되어 표시된다.
- [x] Polymarket edge 부족은 coverage 가능하지만 근거 품질·정산 출처·CLOB·유동성 부족은 불가하다.
- [x] 실제 런타임에서 KR 정규장 진입 또는 구체 미체결 사유 원장이 생성되고, US는 다음 정규장 상태가 정확히 표시된다.
- [x] Polymarket 수집·추정·체결·정산 원장이 계속 갱신된다.
- [x] 실주문 surface 0건, 크립토 페이퍼 코드 diff 0.

## 금지
- 실제 주문·LiveBroker·지갑·인증·입출금 구현
- 장외·가짜 가격·가짜 거래량·가짜 호가 체결
- 근거 없는 Polymarket 확률 생성
- coverage 체결을 strict 신호 성적으로 합산
- 기존 적용 migration 수정

## 문서
- `docs/TossStockPaperTrading.md`
- `docs/PolymarketPaperTrading.md`
- `docs/WO-FCE-PAPER-COVERAGE-01.md`

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)
