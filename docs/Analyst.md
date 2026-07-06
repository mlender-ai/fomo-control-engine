# Analyst Briefing

WO-FCE-30의 애널리스트 브리핑은 주문 신호가 아니다. 기존 엔진 출력만 합성해 방향별 근거, 반대 근거, 조건부 시나리오를 설명한다.

## Evidence Schema

모든 엔진 신호는 다음 공통 스키마로 정규화한다.

```json
{
  "engine": "liquidity",
  "claim": "저점 Strong 스윕 확인",
  "direction": "long",
  "weight": 18,
  "confidence": 82,
  "score": 14.76,
  "as_of": "2026-07-06T00:00:00Z"
}
```

`score = base_weight * confidence / 100 * calibration_factor`

기본 가중치:

| Engine | Weight |
|---|---:|
| liquidity | 18 |
| wyckoff | 18 |
| harmonic | 16 |
| level | 12 |
| derivatives | 11 |
| mtf | 10 |
| volume | 9 |
| structure | 8 |

## Calibration Adjustment

캘리브레이션 표본이 충분할 때만 가중치를 보정한다.

- 표본 하한: `N >= 20`
- 보정 계수: `accuracy_pct / 70`
- 하한/상한: `0.60 ~ 1.25`
- 표본 부족 시 `calibration.applied = false`

즉, 실제 적중률이 70%인 근거는 기본 가중치를 유지한다. 70%보다 낮으면 감점되고, 높으면 최대 1.25배까지만 보정된다.

## Stance Rules

- `insufficient`: 방향성 근거가 3개 미만이거나, 반대 근거가 0개일 때
- `conflicted`: 롱/숏 점수차가 강한 쪽 점수의 25% 미만일 때
- `long_leaning`: 롱 점수가 숏 점수를 충분히 앞설 때
- `short_leaning`: 숏 점수가 롱 점수를 충분히 앞설 때

반대 근거가 없으면 방향 브리핑을 출력하지 않는다. 이 규칙은 예스맨식 브리핑을 막기 위한 구조적 가드다.

## Text Guardrails

- 금지: 직접 진입·매수·매도를 지시하는 명령형 문구
- 허용: “유지 근거”, “약화 근거”, “이탈 시 재평가”, “부분 익절 검토”
- 모든 숫자는 입력 JSON의 기존 엔진 출력 또는 액션 플랜 값만 인용한다.
- 근거별 실측 적중률은 표본이 있는 경우에만 `N`과 함께 표기한다.

## Judgment

브리핑 스탠스는 `analyst_briefing` judgment로 저장된다.

- `long_leaning`: 이후 가격 상승 경로가 맞으면 correct
- `short_leaning`: 이후 가격 하락 경로가 맞으면 correct
- `conflicted`: 이후 가격 변화가 작으면 correct, 큰 방향성이 나오면 wrong
- `insufficient`: 채점 보류

이 결과는 캘리브레이션 화면의 “브리핑 성적표”와 주간 리포트에 집계된다.
