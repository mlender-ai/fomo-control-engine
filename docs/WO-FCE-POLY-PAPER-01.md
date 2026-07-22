# WO-FCE-POLY-PAPER-01 — Polymarket 페이퍼 트레이딩

우선순위: P2 (주식·크립토 검증과 분리된 판단 원장 확장)
선행: WO-FCE-89 (Judgment Ledger 커버리지)

## 착수 전 확인 (AGENTS.md 불변 규칙 2)
- [x] `git branch -a` 에 같은 WO 브랜치 없음
- [x] `git log --oneline -15` + `git status` 에 기존 구현 없음
- [x] `grep -r "WO-FCE-POLY-PAPER-01" docs/ backend/ dashboard/` 결과 없음

## 진단 (코드 확정)
- `backend/app`에는 예측시장 어댑터·확률 판정·정산 체결 모델이 없다.
- 현재 크립토/주식 엔진은 캔들·구조·stance를 입력으로 사용하므로 사건 확률을 판정하는 Polymarket에 재사용하면 의미가 달라진다.
- 공용 `judgment_ledger`와 `judgment_scores`는 출처·claim·metrics를 확장할 수 있어 확률 추정과 Brier score를 기록하는 검증 레일로 재사용할 수 있다.

## 킬존 대조 / UI 위생 필요성
- 범용 예측시장 차트나 거래 UI를 복제하지 않는다.
- 화면은 시장 확률과 FCE 추정의 출처·시각·품질·비용 차감 edge를 함께 보여 주어, 근거 없는 숫자나 크립토/주식 성적 합산을 막는 데이터 오표시 방지용 최소 UI다.
- 해자 기여는 기능 수가 아니라 만기 정답으로 채점된 확률 원장의 축적이다.

## 작업

### 1. 읽기 전용 시장 어댑터
- Gamma 공개 시장 목록/단건 메타와 CLOB 공개 호가장만 구현한다.
- 인증 헤더, 지갑, 주문 생성·취소·조회 구현은 만들지 않는다.
- 크립토/거시만 관측하고, 카테고리·정산 출처·만기·유동성이 불명확하면 관측 전용으로 분리한다.

### 2. 독립 확률 판정
- 기존 confluence·DirectionalEngine·구조 레벨을 import하지 않는다.
- 크립토 가격 임계 시장은 관측된 현물 가격·확정 4시간봉 실현 변동성·남은 시간을 명시한 0-drift lognormal 베이스레이트로 계산한다.
- 모든 추정은 베이스레이트, 출처·관측시각이 있는 근거, 신뢰구간, 품질을 보존한다. 지원하지 않는 질문/거시 근거 부족은 `low`로 진입 제외하며 숫자를 만들지 않는다.

### 3. 체결 정직성 / 독립 회계
- USDC 전용 페이퍼 계좌, 호가 깊이 VWAP, 유동성 상한, 관측 호가 범위 invariant를 적용한다.
- 호가 깊이·프로토콜 taker fee를 차감한 edge와 capped Kelly를 모두 통과한 경우에만 YES/NO 페이퍼 포지션을 연다.
- 기본은 만기 보유이며 주식·크립토 검증 시계/성적과 합산하지 않는다.

### 4. Judgment Ledger / 정산
- 각 확률 추정을 공용 원장에 `entity_type=polymarket`으로 append-only 기록한다.
- 공식 메타가 확정한 결과로 Brier score `(p-y)^2`를 기록한다.
- 캘리브레이션 버킷과 평균 Brier를 별도 집계하고 N<30은 항상 `표본 부족`으로 표시한다.

### 5. 최소 UI
- 엔진 트레이딩에 `Polymarket` 서브탭을 추가한다.
- 독립 검증 시계, USDC 계좌, 시장/FCE 확률, 비용 차감 edge, 근거 펼침, 포지션, Brier/캘리브레이션을 표시한다.

## 수용 기준
- [x] `poly_paper`가 기존 크립토/주식 판정 엔진을 import하지 않는다.
- [x] 근거·출처·시각이 없으면 추정 숫자와 페이퍼 진입이 생성되지 않는다.
- [x] 호가 VWAP·유동성 상한·체결가 invariant 테스트가 통과한다.
- [x] 추정→정산→Brier score 1사이클과 calibration 집계 테스트가 통과한다.
- [x] 공용 원장에 `polymarket` 판단이 기록되고 기존 트랙 성적과 합산되지 않는다.
- [x] 공개 어댑터에 주문·지갑·인증 구현이 없다.
- [x] UI에 N<30 표본 부족과 근거 출처가 노출된다.

## 금지
- 실주문·지갑·인증·입출금·LiveBroker 구현
- 기존 캔들 판정 엔진 재사용
- 정치·스포츠·문화 시장 진입
- 근거 없는 LLM 숫자 생성
- 투자 조언 문구, 크립토/주식 검증 시계 변경

## 문서
- `docs/PolymarketPaperTrading.md`
- `.env.example`

## 완료 정의 (공통)
- [x] HARNESS.md 게이트 통과
- [x] docs 갱신
- [x] origin/main 반영 + CI success 확인 (불변 규칙 1·3)

## 검증 기록 (2026-07-22)

- 백엔드: Ruff·format·import-cycle 통과, mypy 부채 `166/174`, `615 passed`, 총 커버리지 `77.41%`, 품질 기준선 `total 76.09% / core 88.07%`.
- 프론트: lint error 0, typecheck·프로덕션 빌드·로컬 자산 검사 통과, Playwright `24 passed`.
- 공개 실데이터: Crypto/Macro를 `30/10`으로 균형 노출하고, 출처·시각이 완비된 크립토 확률 추정과 독립 USDC PaperBroker 체결 3건을 확인했다.
- 런타임 감사: Bitget 동기 스냅샷은 worker thread에서 계산하고, estimate append 후 체결 전 중단된 후보는 다음 수집에서 재가격·재시도한다. 기존 적용 스키마는 변경하지 않고 `0029_poly_fill_fee.sql`로 수수료 컬럼을 전진 추가했다.
- 안전성: `live_orders_enabled=false`, 공개 클라이언트 surface는 시장 목록·단건·호가장 읽기 3개뿐이며 지갑·인증·실주문 구현이 없다.
