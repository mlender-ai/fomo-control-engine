# WO-FCE-92 — 주식 페이퍼 체결 시점 차트 감사

우선순위: P1 (체결 원장에 저장된 시각·가격을 시장 캔들과 함께 읽지 못해 복기 정합성을 직접 확인할 수 없음)
선행: WO-FCE-TOSS-PAPER-01

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-92" docs/ backend/ dashboard/` 결과 확인

## 킬존 대조와 위생 필요성
- `docs/FCE-MOAT-STRATEGY-01.md` §2에서 범용 차트·UI 기능은 킬존이다. 이 WO는 신규 분석 지표나 차트 기능 경쟁이 아니다.
- 현재 `stock_paper_fills.filled_at/price`는 원장 목록에만 있고 실제 Toss 캔들과 시간축으로 대조할 수 없다. 사용자가 엔진의 진입 시점·가격을 오인하거나 체결 invariant를 화면에서 감사하지 못하는 **데이터 오표시·가독 불능 방지 위생 작업**으로 한정한다.
- 표시 대상은 이미 저장된 실제 페이퍼 fill과 실제 Toss 캔들뿐이다. fill 또는 캔들이 없으면 빈칸으로 남긴다.

## 진단 (코드 확정)
- `backend/app/stock_paper/store.py`는 fill의 `filled_at`, `price`, `side`를 append-only로 보존한다.
- `backend/app/toss/store.py`는 종목별 실제 OHLCV를 `toss_candles`에 보존하지만 체결 원장과 조인하는 읽기 경로가 없다.
- `dashboard/components/engine-trading-shell.tsx`의 주식 트랙은 최근 체결을 텍스트 목록으로만 보여 주므로 진입이 어느 캔들에서 발생했는지 확인할 수 없다.

## 작업
### 1. 체결 감사 읽기 모델
- 종목·시장별 실제 fill과 체결 시점 주변 Toss 캔들을 읽는 read-only API를 추가한다.
- 1분봉이 없을 때만 일봉으로 폴백하고, 사용한 시간봉·출처를 응답에 명시한다.

### 2. 진입·청산 마커
- 주식 트랙에 최근 체결 종목 선택기와 캔들 차트를 추가한다.
- 매수 fill은 `진입`, 매도 fill은 `청산`으로 표시하고 정확한 체결 시각·가격·수량을 원장과 함께 노출한다.
- 실제 fill 또는 실제 캔들이 없으면 이유를 포함한 빈 상태를 표시한다.

### 3. 회귀 검증
- 저장소/API 테스트로 임의 종목이나 가짜 데이터가 노출되지 않는지 확인한다.
- 데스크톱·모바일에서 진입 마커, 시간, 가격, 가로 넘침을 브라우저로 확인한다.

## 수용 기준
- [x] 실제 주식 페이퍼 매수 fill의 시각·가격이 해당 종목 Toss 캔들 위 `진입` 마커로 표시됨
- [x] 매도 fill은 `청산`으로 구분되고 정확한 시각·가격·수량이 표시됨
- [x] 캔들·fill 부재 시 합성값 없이 명시적 빈 상태가 표시됨
- [x] 데스크톱·모바일 가로 넘침 0, 콘솔 오류 0
- [x] 기존 크립토 페이퍼·실주문 봉인 경로 변경 0

## 금지
- 가짜 fill·가짜 캔들·보간 가격 생성 금지
- 자동 승격·룩어헤드·신규 감지기 추가 금지
- 실주문 및 주문 인터페이스 변경 금지
- 기존 적용 마이그레이션 수정 금지

## 문서
- 갱신할 `docs/*.md`: `docs/WO-FCE-92.md`, `docs/TossStockPaperTrading.md`

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)

## HANDOFF
- 목표: 실제 주식 페이퍼 체결 시점·가격을 Toss 캔들 위에서 감사 가능하게 표시
- 한 일: 체결 주변 캔들 read-only API, 진입·청산 마커 차트, 정확한 fill 원장, 빈 상태, 반응형 UI와 테스트 추가
- 안 한 일/막힌 곳: 실데이터 fill 0건이므로 운영 화면은 정직한 빈 상태이며, E2E·저장소 테스트에서 실제 fill 스키마 1사이클을 검증
- 다음 액션: 실제 PaperBroker fill 발생 후 운영 차트의 소스·시간봉·마커를 확인
- 검증: 백엔드 572 passed, E2E 21 passed, 빌드·asset 2xx, 데스크톱/모바일 overflow 0·console error 0
- 머지: `8b6aa618` origin/main 반영, CI `29715876139` success
