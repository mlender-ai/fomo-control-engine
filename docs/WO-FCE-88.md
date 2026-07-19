# WO-FCE-88 — 실히스토리 stance 백테스트 하니스

우선순위: P0 (합성 80.8%를 실제 OHLCV 결과로 재판정해야 방향 엔진 성적이 검증됨)
선행: WO-FCE-54 방향 엔진 v2

## 착수 전 확인 (AGENTS.md 불변 규칙 2)

- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `rg "WO-(FCE-)?88" docs backend dashboard` 결과 기존 구현 없음

## 진단 (코드 확정)

- `backend/app/analyst/stance_history.py`는 최대 200개 라이브 캔들 안에서 확정봉 prefix를 재생하지만 화면 리본용 72포인트만 반환하며 결과를 채점하지 않는다.
- `backend/app/exchange/bitget/provider.py`의 `get_ohlcv`는 최신 `/candles` 1페이지만 읽고, `/history-candles` 페이지 수집기가 없다.
- `docs/DirectionalEngine.md`의 80.8%는 합성 라벨 리플레이이며 실제 가격 히스토리 성적이 아님을 명시한다.
- `backtest_stats`는 signature별 결과와 payload를 이미 영속화하므로 새 통계 저장 시스템은 만들 필요가 없다.

## 작업

### 1. 실제 히스토리 수집

- Bitget 공개 `/api/v2/mix/market/history-candles`를 페이지 처리한다.
- 중복 제거·시간 정렬 후 미확정 마지막 봉을 제거한다.
- BTCUSDT·ETHUSDT·SOXLUSDT 4시간봉을 기본 검증 집합으로 고정한다.

### 2. 무룩어헤드 stance 재생·채점

- 각 확정 시점에는 해당 시점까지의 prefix만 `build_chart_analysis → build_confluence`에 전달한다.
- 히스테리시스 상태는 매 봉 순서대로 전진한다.
- 4시간봉 6개 뒤(24시간) 종가를 결과로 사용하고 표본 중첩을 막기 위해 6봉 stride로 채점한다.
- 롱/숏 방향 수익에서 왕복 수수료·슬리피지를 뺀 net 결과가 양수일 때만 적중으로 본다.

### 3. 통계 원장·API·화면

- 합성 성적과 다른 `directional_v2_real_history_24h` signature로 `BacktestStat`에 저장한다.
- 적중률·95% CI·N·기간·원본 해시·비용·결측·파생 히스토리 미포함 한계를 함께 발행한다.
- N<30 또는 데이터 품질 하한 미달이면 결과 수치는 보존하되 결론은 유보한다.
- 엔진 상태 화면에 3심볼 실데이터 카드와 수동 갱신을 제공한다.
- 매일 저부하 작업으로 갱신한다.

## 수용 기준

- [x] BTCUSDT·ETHUSDT·SOXLUSDT 실제 Bitget 4h 성적이 CI와 함께 발행
- [x] 미래 캔들을 바꿔도 그 이전 stance가 바뀌지 않는 테스트
- [x] 미확정 봉 제거·페이지 중복 제거 테스트
- [x] 24시간 비중첩 채점·비용 차감 테스트
- [x] N<30 결론 유보와 데이터 한계 문구 테스트
- [x] 엔진 상태 화면에서 실제/합성 결과가 혼합되지 않음

## 금지

- 합성 80.8%와 실히스토리 적중률 통합 표기 금지
- 미확정 캔들·미래 가격을 판정 입력에 사용 금지
- 파생 과거 데이터 부재를 0 또는 추정치로 채우기 금지
- 결과가 나쁘다는 이유로 숨기거나 파라미터를 자동 승격하기 금지
- 실주문 경로 변경 금지

## 문서

- `docs/FceMoatStrategy.md`
- `docs/DirectionalEngine.md`
- `docs/Backtest.md`

## 완료 정의 (공통)

- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)

## 최초 실행 결과 (2026-07-20)

- BTCUSDT: 51.2% · 95% CI 34.9~67.4% · N=43
- ETHUSDT: 47.8% · 95% CI 32.6~63.0% · N=46
- SOXLUSDT: 41.7% · 95% CI 25.0~58.3% · N=36 (stance 재생 커버리지 86.0%)
- 공통: 2026-05-10~07-19, 확정봉만, gap 0, invalid OHLC 0, 데이터 품질 100/100
- 판정: 합성 80.8%를 실제 일반화 성적으로 취급할 수 없음. 결과는 자동 승격이나 실주문에 사용하지 않는다.
