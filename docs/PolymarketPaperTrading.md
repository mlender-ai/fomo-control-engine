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
- 비용 차감 edge가 `poly-v2` 엄격 임계 이상이면 capped Kelly로 크기를 계산한다.
- 기본 청산은 공식 만기 결과다. 중도 청산 최적화는 범위 밖이다.

### poly-v2 캘리브레이션 커버리지

`strict_edge`는 기존 비용 후 edge 5% 기준을 그대로 사용한다. `coverage_calibration`은
엄격 edge가 부족해도 근거 품질, 베이스레이트, 정산 출처, 실제 CLOB ask와 유동성이 모두
있는 시장에만 원금 0.5%를 배정한다. 근거 없는 확률, macro 베이스레이트 미연결,
orderbook/fee schedule 부재는 계속 관측 전용이다.

두 모드는 주문·체결·포지션에 저장하고 화면에서 별도 뱃지와 포지션 수로 표시한다.
coverage의 목적은 수익률을 강제로 만드는 것이 아니라 Brier score와 calibration 표본을
더 빨리 확보하는 것이다. 엄격 edge 성과와 합산하지 않는다.

2026-07-22 실제 공개 CLOB 런타임에서 poly-v2 신규 포지션 4건을 확인했다. 이 중
`BTC 55K dip YES`는 strict edge, `BTC 150K NO`, `BTC 50K dip YES`, `BTC 75K July YES`는
coverage calibration이며 모두 실제 ask 깊이, 수수료, 호가 유동성 상한을 적용했다.

## 판단 원장과 정산

확률 추정은 공용 `judgment_ledger`에 `source_type=polymarket`, `entity_type=polymarket`으로 append-only 기록한다. 정산되면 공용 `judgment_scores.metrics.brier_score`와 Polymarket 정산 원장에 같은 값을 남긴다.

캘리브레이션은 예측 확률 10% 단위 버킷별 평균 예측과 실제 YES 비율을 비교한다. N<30에서는 적중/우수 단정을 하지 않고 `표본 부족`만 표시한다.

## 운영

- `FCE_POLYMARKET_PAPER_ENABLED`: 트랙/UI 활성화
- `FCE_POLYMARKET_POLL_INTERVAL_SECONDS`: 공개 시장 수집 주기(기본 60초)
- `FCE_POLYMARKET_INITIAL_USDC`: 독립 가상 원금
- `FCE_POLYMARKET_GAMMA_BASE_URL`, `FCE_POLYMARKET_CLOB_BASE_URL`: 공개 API 기준 URL

수집이 실패하면 마지막 원장을 보존하고 오류 상태만 갱신한다. 첫 정상 공개 시장 수집 시점에 독립 4주 검증 시계가 시작된다.

확률 원장을 쓴 뒤 PaperBroker 주문 원장을 쓰기 전에 프로세스가 중단되면, 다음 수집은 정상 추정 간격을 기다리지 않고 해당 후보를 다시 가격 계산해 체결 가능 여부를 재검증한다. 이 재시도도 새 판단으로 append-only 기록되며 이전 추정을 덮어쓰지 않는다.

## 유니버스 위생 · 지표 분모 (WO-FCE-ALERT-WHITELIST-02)

### 제외 사유 분류 — 거부가 아닌 것들

`poly_paper/service.py::classify_exclusion`

| 분류 | 사유 | 성격 |
|---|---|---|
| `universe_exit` | `resolved_or_expired`, `resolution_time_invalid`, `market_inactive` | **판정 대상이 아니다.** 만료·종료 시장은 유니버스에서 나간다 |
| `out_of_scope` | `unsupported_crypto_question`, `clob_token_missing` | **파싱 대상이 아니다.** 질문 형식이 지원 범위 밖 |
| `capacity_full` | `position_capacity`, `coverage_capacity`, `insufficient_cash` | **설계된 정상 동작.** 5개 보유 중이면 6번째를 안 잡는 게 맞다 |
| `rejected` | `after_cost_edge_low`, `liquidity_below_minimum` 등 | **실제 판정 미달** — 이것만 거부다 |

### 만료 시장 제거

`_apply_market_gates`가 `closed` 또는 `end_at <= now`를 `resolved_or_expired`로 판정하고, 평가 루프는 이런 시장을 **진입 전에 걸러낸다**. 재평가 비용도 쓰지 않고 거부 카운트에도 넣지 않는다.

수리 전에는 만료 시장이 유니버스를 차지한 채 매 사이클 "거부"만 쌓아 실제 평가 가능 시장을 밀어냈다(2026-08-01 실측: `resolution_time_invalid` 39건 → 평가 0).

### 지표 분모 정의

```
거부율 분모 = 실제 판정 대상 = 전체 관측 − (만료 + 범위외 + 한도도달)
```

`rejected_summary` 이벤트와 엔진 반환 payload는 `rejected`(진짜 거부)·`capacity_waiting`·`out_of_scope`·`universe_exits`를 **분리해서** 싣는다. **최다 거부 게이트는 진짜 거부에서만 고른다** — "마켓 한도"가 최다 거부로 뜨던 오표기를 수리했다.

`max_open_markets` 도달로 진입이 없는 것은 정상이며, 성과 리포트에 `한도 도달 대기 N건`으로 표기한다.

## 미실현 산출·만기 분포 (WO-FCE-OBSERVATION-INTEGRITY-01 Phase 4)

### 실측 — 폴리는 이번 검증에서 정산 표본을 만들 수 없다

2026-08-05 기준 보유 9건 중 **8건이 2027-01-01 만기**로, 검증 종료(08-19)보다 5개월 뒤다.

| 항목 | 값 |
| --- | --- |
| 보유(open) | 8건 |
| 최근접 만기 | 2027-01-01 |
| **검증 기간 내 정산 예정** | **0건** |
| 관측 유니버스 중 08-19 이전 만기 시장 | 4,847개 |

정산 로직 자체는 정상이다 — 08-01 만기 1건(`Will Bitcoin reach $75,000 in July?`)이
`poly_resolutions` 에 정상 기록됐다(pnl −42.5). 문제는 **선정된 시장의 만기가 검증 창 밖**이라는 것.
유니버스에는 단기 만기 시장이 4,847개나 있으므로 선정 기준의 문제이지 데이터 부족이 아니다.
**유니버스 선정 기준 변경은 이 WO 범위 밖**이며, 위 실측을 별도 WO 판단 근거로 남긴다.

### 미실현 산출 규격

정산 전이라도 현재 시장가 기준 미실현을 낸다. **정산 손익과 절대 합산하지 않는다**(C3) —
미실현은 확정이 아니고, 합쳐 표기하면 없는 성적을 있는 것처럼 보이게 한다.

- 현재가: YES 포지션 = `market_probability`, **NO 포지션 = `1 − market_probability`**.
  NO 를 YES 확률로 평가하면 손익 부호가 뒤집힌다.
- 미실현 가치 = `shares × 현재가`, 미실현 손익 = `가치 − cost`.
- 시장 확률이 없으면 미실현도 **없다**(`null`) — 지어내지 않는다.
- 정산된 포지션에는 미실현을 붙이지 않는다(이중 계상 방지).

응답: `unrealized {basis, is_settled:false, note, open_positions, cost, value, pnl, return_pct}`,
`expiry {open_positions, nearest_end_at, settling_within_validation, label, sample_possible}`.

포지션별로 `unrealized_value`·`unrealized_pnl`·`unrealized_return_pct`·`settles_within_validation`.

화면은 `미실현 (미확정)` 라벨과 함께 표기하고, `sample_possible=false` 이면
"검증 기간 안에 정산되는 보유 시장이 없어 이번 검증에서 정산 표본을 만들 수 없습니다"를 상시 노출한다.
