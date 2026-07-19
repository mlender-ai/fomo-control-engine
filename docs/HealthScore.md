# Health Score v2

Health Score는 "지금 이 포지션이 계획대로 살아있는가"를 재는 결정론 점수다. 시장 전체 진입 기회 점수인 Entry Score를 포지션 건강도 산식에 사용하지 않는다.

## 공식

```text
health_score =
  survival * 0.30
  + pnl_state * 0.20
  + thesis_integrity * 0.20
  + structure * 0.20
  + flow * 0.10
```

최종 값은 반올림 후 0~100으로 제한한다.

## 컴포넌트

### survival

청산가 거리 기준 생존 점수다.

| 청산가 거리 | 점수 |
|---:|---:|
| 30% 이상 | 100 |
| 15% | 70 |
| 10% | 40 |
| 5% | 10 |
| 3% 미만 | 0 |

구간 사이는 선형 보간한다.

청산가가 없으면 포지션 레버리지에 따라 상한 점수를 직접 사용한다.

| 조건 | survival |
|---|---:|
| leverage >= 10x | 30 |
| leverage >= 5x | 45 |
| 그 외 | 60 |

### pnl_state

ROE% 기준 손익 상태 점수다.

| ROE% | 점수 |
|---:|---:|
| +20% 이상 | 90~100 |
| 0% ~ +20% | 70~90 |
| -10% ~ 0% | 50~70 |
| -30% ~ -10% | 25~50 |
| -50% ~ -30% | 10~25 |
| -50% 미만 | 0~10 |

구간 사이는 선형 보간한다. 피크 대비 수익 반납률이 50%를 초과하면 15점을 추가 감점한다.

### thesis_integrity

진입 당시 방향 점수(`entry_direction_score`)와 현재 방향 점수(`current_direction_score`)의 변화로 계산한다.

| 변화폭 | 점수 규칙 |
|---:|---|
| +10 이상 | `82 + min(18, delta * 0.9)` |
| 0 ~ +10 | `72 + delta` |
| -15 ~ 0 | `72 + delta * 1.5` |
| -30 ~ -15 | `50 + (delta + 15) * 1.4` |
| -30 미만 | `25 + (delta + 30) * 0.8` |

### structure

포지션 방향을 인지한 구조 점수다.

- Long: 현재 방향 점수 70% + 기존 구조 점수 30%
- Short: 현재 방향 점수 70% + `(100 - 기존 구조 점수)` 30%

WO-FCE-04의 구조적 S/R 레벨 엔진 전까지는 기존 구조 점수가 롱 편향이라는 한계가 있으므로, 숏 포지션은 반전 구조 점수를 임시로 사용한다.

### flow

WO-FCE-21부터 파생 수급 데이터가 있으면 `formula_version = health_v2_derivatives`를 사용한다. 기본값은 55다.

파생 데이터 우선 산식:

| 항목 | 조건 | 점수 변화 |
|---|---|---:|
| OI/가격 다이버전스 | 롱 + `price_up_oi_up` | +18 |
| OI/가격 다이버전스 | 롱 + `price_up_oi_down` | +6 |
| OI/가격 다이버전스 | 롱 + `price_down_oi_up` | -20 |
| OI/가격 다이버전스 | 롱 + `price_down_oi_down` | -8 |
| OI/가격 다이버전스 | 숏 + `price_down_oi_up` | +18 |
| OI/가격 다이버전스 | 숏 + `price_down_oi_down` | +6 |
| OI/가격 다이버전스 | 숏 + `price_up_oi_up` | -20 |
| OI/가격 다이버전스 | 숏 + `price_up_oi_down` | -8 |
| Funding | `neutral` | +5 |
| Funding | 포지션 방향에 우호적인 `overheated` | +6 |
| Funding | 포지션 방향에 불리한 `overheated` | -12 |
| Funding | 포지션 방향에 우호적인 `extreme` | +10 |
| Funding | 포지션 방향에 불리한 `extreme` | -20 |
| Crowding | score >= 70이고 보유 방향으로 쏠림 | -15 |
| Crowding | score >= 70이고 반대 방향으로 쏠림 | +8 |
| Crowding | score >= 40이고 보유 방향으로 쏠림 | -6 |
| Crowding | score < 40 | +4 |

롱/숏 쏠림 방향은 계정 롱숏비가 1.2 이상이면 long, 0.83 이하이면 short로 본다. 중립 구간은 쏠림 방향 가감점을 주지 않는다.

파생 신호가 없으면 기존 폴백 산식(`formula_version = health_v2`)을 사용한다.

- 가격 변화가 포지션 방향과 같으면 +12, 반대면 -12
- MACD histogram 부호가 포지션 방향과 같으면 +12, 반대면 -12
- 상대 거래량 >= 1.8: 방향 정렬 시 +10, 역행 시 -8
- 상대 거래량 >= 1.2: 방향 정렬 시 +6, 역행 시 -5
- 상대 거래량 < 0.8: -6
- 펀딩이 포지션 방향에 우호적이면 +4
- 펀딩 과열/혼잡 상태면 -6
- OI 증가가 포지션 방향 가격 변화와 같으면 +5, 반대면 -5

## 방향 점수

`direction_aware_score(direction, structure, indicators)`는 long/short를 분리해 계산한다.

Long:
- bullish 78, neutral_to_bullish 68, neutral 55, bearish_to_neutral 45, bearish 25
- higher_low +10
- break_of_structure +8
- spring_candidate +6
- sos_confirmed +8
- 종가가 볼린저 하단 아래면 -18
- distribution_score가 accumulation_score보다 20 초과 높으면 -10

Short:
- bearish 78, bearish_to_neutral 68, neutral 55, neutral_to_bullish 35, bullish 22
- lower_high +10
- higher_low -10
- 상승 break_of_structure -12
- 종가가 볼린저 상단 위면 -18
- distribution_score가 accumulation_score보다 10 초과 높으면 +8
- spring_candidate -6
- sos_confirmed -8

## 최종 cap 규칙

가중 평균만으로 극단 손실을 덮지 못하게 아래 cap을 적용한다.

| 조건 | 최종 health 상한 |
|---|---:|
| pnl_state == 0 | 25 |
| pnl_state <= 10 and survival <= 30 | 25 |
| pnl_state <= 10 and survival <= 10 | 20 |

따라서 -75% 이하 포지션이 60점 이상이 되는 경로는 없다.

## 산식 감사 필드

라이브 포지션 응답의 `score_json.health_integrity`는 표시 점수를 역산할 수 있게 아래 값을 함께 보존한다.

- `weighted_score_before_cap`: 5개 컴포넌트 가중합을 반올림한 원점수
- `cap_reason` / `cap_value`: 극단 손실·생존 조건으로 적용된 상한과 사유
- `final_score`: 화면의 Health Score와 동일한 최종 값
- `formula_version`: 수급 데이터 포함 여부를 나타내는 산식 버전
- `score_consistent`: 같은 컴포넌트 재계산 결과와 최종 점수의 일치 여부

compact 폴링 응답은 `score_as_of`, `price_as_of`, `basis_pnl_percent`,
`current_pnl_percent`, `basis_consistent`를 추가한다. 실시간 가격/PnL과 마지막 분석
스냅샷의 점수 기준이 달라지면 `basis_consistent=false`로 표시하며, 저장된 전체
액션 플랜은 두 기준이 일치할 때만 compact 화면의 정본으로 재사용한다.

Health Score와 Risk Score는 별도 축이다. 예를 들어 ETH 숏의 청산 거리가 충분해
Risk가 낮더라도 거래소 ROE가 -75% 이하라면 Health는 cap 25, 상태는 critical일 수
있다. UI는 이를 `건강도 N/100`으로 명시해 Risk 점수와 혼동하지 않는다.

## 상태 심각도

상태는 숫자 rank를 함께 내려준다.

| 상태 | rank |
|---|---:|
| healthy | 0 |
| unknown | 0 |
| watch | 1 |
| risk_rising | 2 |
| thesis_weakening | 3 |
| critical | 4 |

추가 경계:
- PnL <= -75% 또는 청산가 거리 < 5%: `critical`
- PnL <= -50%: 최소 `risk_rising`
- PnL <= -30%: 최소 `watch`
- 청산가가 없고 open 포지션이며 leverage >= 5x: 최소 `watch`
