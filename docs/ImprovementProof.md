# ImprovementProof — 엔진 개선 증명 판정 규칙 (WO-FCE-45)

사용자 질문: "복기 영역을 통해 엔진이 개선되고 있나? 잘 모르겠어."
이 문서는 "개선"을 주장할 수 있는 최소 기준과, 주장할 수 없을 때의 정직한 표기를 고정한다.

구현: `app/review/improvement.py` · API: `GET /api/review/improvement` ·
주간 임베드: `weekly_report.improvement_digest` (WO-49 렌더·발송)

## 원칙 — 개선 연출 금지

1. **유의 기준을 통과한 사실만 "개선"으로 주장한다.** 미달이면 "이번 주 유의미한 개선 없음"을
   명시 발송한다 — 침묵이나 애매한 문구로 개선을 암시하지 않는다.
2. **효과 없음·악화·판별 불가는 그대로 보고한다.** 조치별 효과표는 결과가 나쁜 조치를
   숨기지 않는다 (자율 규칙 채점의 연장 — WO-37 메타 무결성과 같은 철학).
3. **모든 수치에 N과 비교 조건을 병기한다** (WO-36 표기 표준의 연장).

## 소표본 개선 판정 기준 (설계 난제의 답)

전/후 창 각각 **N ≥ 15** 미만이면 무조건 `판별 불가` — 그 아래에선 어떤 방향 주장도 하지 않는다.

N 충족 시, 부트스트랩 95% CI(WO-36과 동일 결정론 엔진)로 판정:

| 조건 | 판정 |
|---|---|
| Δ > 0 **and** 두 창 CI 비겹침 | **개선 (유의)** — 유일하게 "개선"으로 주장 가능한 등급 |
| Δ ≥ +5%p, CI 겹침 | 개선 신호 (유의 아님) — 개선 주장 금지, 관찰 항목 |
| \|Δ\| < 5%p | 효과 없음 |
| Δ ≤ −5%p, CI 겹침 | 악화 신호 (유의 아님) |
| Δ < 0 **and** CI 비겹침 | **악화 (유의)** — 그대로 보고 |

- CI 비겹침 기준은 보수적이다(대략 p<0.05보다 엄격). 소표본에서 개선을 과대 주장하는
  것보다 놓치는 쪽을 택한다 — 표본이 쌓이면 CI가 수축해 자연히 판정 가능해진다.
- ±5%p "신호" 임계는 잡음 컷 — 유의 아님 꼬리표 없이 노출되지 않는다.

## 조치별 효과표

각 조치(파라미터 채택 `engine_params` · 자율 강등 `autonomy_log`)에 대해
전/후 **14일 창**의 스코프 내 적중률을 대조한다.

- 파라미터 스코프: `min_invalidation_level_score`→무효화, `wyckoff_event_min_confidence`→와이코프,
  `harmonic_*`→하모닉 PRZ, `alert_trigger_near_pct`→감시 트리거. 미등록 파라미터는 전체 스코프 + 명시.
- 시그니처 강등: 스코프 = 해당 시그니처가 남긴 판단. 대개 표본이 작아 `판별 불가`가 정직한
  답이며, 구조 조치(발견/가중 노출 차단)라는 사실과 강등 시점 근거를 `structural_note`로 병기.

## 레짐 통제 주간 비교

전체 적중률 변화는 (a) 엔진 조치 (b) 시장 레짐 변화 (c) 표본 구성 변화가 섞인다.
최소 통제: **동일 레짐 내 전후 비교**.

- 판단 생성 시점에 `claim.regime`을 태깅한다 (WO-45부터 — `build_judgment_entries`).
- 이번 주 지배 레짐(태깅 표본의 최빈값)으로 양쪽 주를 필터 → 각 N ≥ 15면 통제 비교,
  `basis = "동일 레짐(uptrend) 기준"`.
- **통제 불가 경로 (명시 의무)**:
  - 태그 없음(과거 표본): `"판별 불가 — 판단에 레짐 태그 없음 (이번 주부터 기록 시작)"`
  - 동일 레짐 표본 부족: `"판별 불가 — 동일 레짐(X) 표본 부족 (전주 N=a, 이번 주 N=b)"`
  - 이 경우 raw Δ는 참고로 제공하되 basis에 `"전체 (레짐 통제 불가)"`를 박는다.
- (c) 표본 구성 변화는 현 단계에서 통제하지 않는다 — 한계로 명시 (판단 유형 분포가 주간
  요약의 judgment_types에 노출되므로 사용자가 육안 대조 가능).

## 주간 다이제스트 스키마 (WO-49 소비, schema_version=1)

```json
{
  "schema_version": 1,
  "tested": 1204, "accuracy_pct": 69.8, "accuracy_ci": [67.2, 72.3],
  "delta_pct": 2.1, "delta_basis": "동일 레짐(uptrend) 기준", "delta_verdict": "개선 (유의)",
  "regime_control": {"controlled": true, "regime": "uptrend", "reason": null},
  "actions": [{"kind": "param_adoption", "action": "harmonic_min_confidence 70→80",
               "scope": "하모닉 PRZ", "before": {...}, "after": {...},
               "delta_pct": ..., "verdict": "...", "verdict_reason": "..."}],
  "quarantined": [], "experiments": [{"id","title","days_running"}],
  "weakest": {"judgment_type": "harmonic_prz", "accuracy_pct": 13.3, "tested": 30},
  "improvement_claim": false,
  "headline": "이번 주 유의미한 개선 없음",
  "sparkline": [{"week_start","tested","accuracy_pct","accuracy_ci"} × 12]
}
```

- `improvement_claim`은 **주간 Δ가 "개선 (유의)"이거나 이번 주 조치 중 "개선 (유의)"가
  있을 때만** true. 그 외 headline은 정확히 `"이번 주 유의미한 개선 없음"`.
- 스파크라인 빈 주는 `accuracy_pct: null` (0%로 채우지 않는다 — 추세 조작 금지).
- 회귀 고정: `tests/test_improvement_proof.py` — 무조치·플랫 주간 개선 문구 미생성,
  약한 신호(+8%p, CI 겹침) 개선 주장 금지, 효과 없음/악화 정직 표기.
