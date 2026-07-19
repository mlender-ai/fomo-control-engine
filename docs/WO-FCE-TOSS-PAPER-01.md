# WO-FCE-TOSS-PAPER-01 — 주식 페이퍼 트레이딩 확장

우선순위: P1 (체결 정직성은 P0)
선행: WO-FCE-TOSS-SCOUT-01, WO-FCE-BITGET-TOSS-MAP-01

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `rg "WO-FCE-TOSS-PAPER-01|PaperBroker|live_trading_enabled" docs backend dashboard` 결과 기존 구현 없음

## 진단 (코드 확정)
- 크립토 페이퍼는 `backend/app/paper/`에 USDT 단일 회계와 Bitget 확정 캔들 체결로 구현되어 있다. 주식 세션·호가단위·가격제한·VI·세금 규칙을 이 모델에 섞으면 기존 4주 검증 시계를 오염시킨다.
- Toss 수집기는 `backend/app/toss/service.py`에서 KR/US 시장을 분리해 200심볼 배치 현재가와 후보 캔들을 읽지만 주문 코드는 화이트리스트에 없다.
- 엔진 트레이딩 화면은 크립토 대결만 표시해 주식 트랙의 독립 시계와 미체결 사유를 확인할 수 없다.

## 작업
### 1. 버전 유니버스와 수집 예산
- `backend/app/stock_paper/universe/2026-q3.json`에 나스닥100 회사 100개와 코스피100 시드 100개를 고정한다.
- 분기 수동 갱신, 편출 종목 신규 진입 차단, Toss warning 런타임 게이트를 로더에서 검증한다.

### 2. 봉인된 Broker 경계와 정직한 PaperBroker
- `Broker` 추상 인터페이스와 `PaperBroker`만 구현한다. `LiveBroker`는 타입 계약만 두며 등록하거나 구현하지 않는다.
- 정규장, 갭 시가, 정수 수량/호가, 가격제한 잠김, VI·정지·warnings, 1분 거래량 5%, 반스프레드, 당분 고저 invariant를 한 체결 정책에서 강제한다.

### 3. 독립 회계·검증 트랙
- KRW/USD 원통화, 수수료·KR 매도세·환율 관측값을 fill에 저장한다.
- KR/US 각각 독립 시작일과 4주 종료일, 벤치마크, 미체결 사유를 저장한다. 크립토 테이블과 서비스는 변경하지 않는다.

### 4. Toss 어댑터·대시보드
- Toss 후보를 공통 `evaluate_entry` 게이트 입력으로 정규화하되 롱 시그널만 주문 후보로 만든다. 숏은 관측만 기록한다.
- GE에 `[크립토 트랙 | 주식 트랙]` 전환을 추가하고 시작일, 경과일, 엔진/지수, KRW/USD, 미체결 사유를 표시한다.

## 수용 기준
- [x] 체결 규칙 1~7 유닛 테스트 통과
- [x] 장외 주문이 다음 개장 시가로 체결
- [x] 가격제한 잠김 미체결 및 유동성 초과 부분 체결
- [x] 체결가 범위 invariant 실패 시 트랙 정지
- [x] `live_trading_enabled=true` 기동 실패, LiveBroker 구현체 0개
- [x] KR 매도 세금이 fill에 저장
- [x] 주식/크립토 검증 시계와 성적 완전 분리
- [x] 미체결 사유가 API와 대시보드에 실데이터로 표시
- [x] 200종목 배치 TPS 검산 문서 갱신

## 금지
- Toss 주문 API 호출, 실주문 구현, 공매도 체결, 장외 체결, 미관측 가격·유동성 추정.
- 기존 크립토 페이퍼 파라미터 및 검증 시작일 변경.

## 문서
- `docs/WO-FCE-TOSS-PAPER-01.md`
- `docs/TossStockPaperTrading.md`

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인
