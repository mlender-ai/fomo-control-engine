# Harmonic Pattern Engine

하모닉 엔진은 신규 진입 후보를 찾기 위한 기능이 아니다. 목적은 보유 포지션의 익절/반전 경계가 될 수 있는 PRZ(Potential Reversal Zone)를 계산해 액션 플랜에 공급하는 것이다.

## ZigZag

`structure/harmonic/zigzag.py`는 ATR 기반 ZigZag를 사용한다.

- 반전 임계값 기본값: `ATR x 2.0`
- 설정: `FCE_HARMONIC_ZIGZAG_ATR_MULTIPLIER`
- 최근 피벗 12개 안의 연속 스윙 체인만 평가한다.

레벨 엔진의 fractal swing은 구조 레벨용이고, 하모닉 ZigZag는 연속 XABCD 체인용이라 별도 모듈로 유지한다.

## Pattern Scope

v1 지원 패턴은 5개로 고정한다.

| Pattern | B retrace | C retrace | D / XA | BC projection | Notes |
|---|---:|---:|---:|---:|---|
| Gartley | 0.618 ± 0.03 | 0.382-0.886 | 0.786 ± 0.05 | 1.272-1.618 | D is XA retracement from A |
| Bat | 0.382-0.500 | 0.382-0.886 | 0.886 ± 0.05 | 1.618-2.618 | Deep D retracement |
| Butterfly | 0.786 ± 0.04 | 0.382-0.886 | 1.272 ± 0.08 | 1.618-2.240 | D extends beyond X |
| Crab | 0.382-0.618 | 0.382-0.886 | 1.618 ± 0.08 | 2.240-3.618 | Deep extension |
| AB=CD | 0.382-0.886 | n/a | n/a | 1.130-2.618 | CD/AB = 1.000 ± 0.08 |

`bullish` 패턴은 D가 저점인 상승 반전 PRZ이고, `bearish` 패턴은 D가 고점인 하락 반전 PRZ다.

## Status

- `completed`: XABCD 피벗이 모두 있고 D가 PRZ 안에 있음
- `forming`: XABC까지만 유효하고 D PRZ가 예상 구간으로 계산됨

## PRZ

PRZ는 관련 피보나치 투영값의 겹침 구간이다.

- D / XA retracement or extension
- BC projection
- AB=CD projection where applicable

출력:

```json
{"low": 104.12, "high": 105.04, "mid": 104.52}
```

## Confidence Formula

`confidence = ratio_fit + confluence + atr_significance`

- `ratio_fit` 0-50: 비율 오차가 작을수록 높다.
- `confluence` 0-30: PRZ가 구조 레벨 또는 POC/VAH/VAL과 합류하면 가산한다.
- `atr_significance` 0-20: 패턴 전체 크기가 ATR 대비 의미 있을수록 높다.

API 응답 임계값 기본값은 `55`이며 설정은 `FCE_HARMONIC_MIN_CONFIDENCE`다. 임계값 아래 패턴은 응답하지 않는다.

## Display Contract

- 유효한 `completed` 또는 `forming` 패턴이 있을 때만 X-A-B-C-D 연결선, 비율, PRZ를 차트에 그린다.
- `patterns=[]`이면 원시 ZigZag 피벗은 내부 계산 데이터로만 유지한다. P1, P2 같은 디버그 라벨이나 후보 연결선을 사용자 차트에 노출하지 않는다.
- 미검출 상태는 `유효 X-A-B-C-D 비율 없음 · PRZ 없음`으로 표시하며 방향 근거로 사용하지 않는다.

라이브 포지션 프로 화면은 `1D / 12H / 4H / 1H / 15M`의 확정 캔들을 독립 검사한다. 각 행은 같은 ZigZag·비율·confidence 설정을 사용한다. 다른 시간봉의 X-A-B-C-D를 현재 시간봉 위에 겹쳐 그리지 않으며, 사용자가 해당 행을 선택했을 때만 그 시간봉의 실제 캔들과 PRZ를 표시한다. 일봉 일반 응답이 분석 하한에 못 미치면 Bitget 공개 히스토리 캔들로 백필하되, 백필 실패는 `패턴 없음`이 아니라 `검사 불가`로 구분한다.

## Action Plan Integration

보유 방향과 반대 방향 PRZ만 익절 후보가 된다.

- Long position: 현재가 위의 `bearish` PRZ를 take_profit 후보로 사용
- Short position: 현재가 아래의 `bullish` PRZ를 take_profit 후보로 사용

예시 basis:

```text
Bat PRZ + 저항 R2/POC 합류 · 신뢰도 84
```
