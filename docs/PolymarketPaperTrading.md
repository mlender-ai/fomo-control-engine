# Polymarket 페이퍼 트레이딩

Polymarket 트랙은 크립토·주식 페이퍼와 별개인 예측시장 확률 검증 트랙이다. 대표 성과는 수익률이 아니라 만기 결과로 채점한 Brier score와 calibration이다.

## 경계

- 공개 읽기: Gamma 시장 목록/단건, CLOB 호가장
- 지원 유니버스: `crypto`, `macro`
- 페이퍼 회계: USDC 독립 계좌
- 실주문·지갑·인증: 구현 없음
- 기존 confluence·stance·구조 레벨: 사용 안 함

공식 API 계약은 Polymarket의 [Market Data overview](https://docs.polymarket.com/market-data/overview), [Fetching Markets](https://docs.polymarket.com/market-data/fetching-markets), [Orderbook](https://docs.polymarket.com/trading/orderbook), [Fees](https://docs.polymarket.com/trading/fees), [Resolution](https://docs.polymarket.com/concepts/resolution)을 기준으로 한다. 유니버스는 일반 상위 시장 목록을 잘라 쓰지 않고, 공식 권장 방식대로 `/events`를 `tag_slug`로 조회한 뒤 포함된 활성 시장을 펼쳐 중복 제거한다.

## 확률 추정 정직성

추정 하나는 반드시 다음을 갖는다.

1. 베이스레이트 모델과 입력
2. `claim`, `source`, `observed_at`이 있는 근거
3. 추정 확률과 신뢰구간
4. 품질(`high|medium|low`)과 제외 사유

초기 버전은 BTC·ETH·SOL·XRP 가격 임계 질문만 0-drift lognormal 베이스레이트로 계산한다. 현물 가격, 확정 4시간봉 로그수익률 실현 변동성, 만기까지 시간을 입력으로 쓴다. 만기 시점 상회/하회와 만기 전 최초 도달은 서로 다른 사건이므로 terminal 분포와 barrier first-passage 분포로 분리한다. 거시 시장은 관측 목록에 보이지만 검증된 베이스레이트/근거 공급기가 없으므로 추정값 자체를 발행하지 않고 진입하지 않는다. 지원하지 않는 질문에도 임의 숫자를 채우지 않는다.

## 페이퍼 체결

- 선택한 YES/NO 토큰의 실제 공개 ask 깊이를 걷어 VWAP을 계산한다.
- `feesEnabled` 시장은 공식 `fee = shares × rate × p × (1-p)`를 호가별로 반영한다. fee schedule이 없으면 관측 전용으로 제외한다.
- 한 번의 체결은 관측 ask 유동성의 설정 비율을 넘지 않는다.
- 체결가는 소비한 호가의 최소·최대 범위를 벗어나면 트랙을 정지한다.
- 비용 차감 edge가 양수이고 `poly-v1` 임계 이상일 때만 capped Kelly로 크기를 계산한다.
- 기본 청산은 공식 만기 결과다. 중도 청산 최적화는 범위 밖이다.

## 판단 원장과 정산

확률 추정은 공용 `judgment_ledger`에 `source_type=polymarket`, `entity_type=polymarket`으로 append-only 기록한다. 정산되면 공용 `judgment_scores.metrics.brier_score`와 Polymarket 정산 원장에 같은 값을 남긴다.

캘리브레이션은 예측 확률 10% 단위 버킷별 평균 예측과 실제 YES 비율을 비교한다. N<30에서는 적중/우수 단정을 하지 않고 `표본 부족`만 표시한다.

## 운영

- `FCE_POLYMARKET_PAPER_ENABLED`: 트랙/UI 활성화
- `FCE_POLYMARKET_POLL_INTERVAL_SECONDS`: 공개 시장 수집 주기
- `FCE_POLYMARKET_INITIAL_USDC`: 독립 가상 원금
- `FCE_POLYMARKET_GAMMA_BASE_URL`, `FCE_POLYMARKET_CLOB_BASE_URL`: 공개 API 기준 URL

수집이 실패하면 마지막 원장을 보존하고 오류 상태만 갱신한다. 첫 정상 공개 시장 수집 시점에 독립 4주 검증 시계가 시작된다.

확률 원장을 쓴 뒤 PaperBroker 주문 원장을 쓰기 전에 프로세스가 중단되면, 다음 수집은 정상 추정 간격을 기다리지 않고 해당 후보를 다시 가격 계산해 체결 가능 여부를 재검증한다. 이 재시도도 새 판단으로 append-only 기록되며 이전 추정을 덮어쓰지 않는다.
