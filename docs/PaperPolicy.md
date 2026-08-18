# 크립토 페이퍼 진입 정책

## 원칙 — 검증 대상을 검증 조건으로 삼을 수 없다

페이퍼 진입의 목적은 **시그니처를 채점할 표본을 만드는 것**이다. 그런데 "검증된
시그니처"를 진입 조건으로 요구하면 표본이 영원히 생기지 않는다. 검증 대상이 검증 조건이
되는 순환 봉쇄다. 주식은 `stock-v2` 에서 이미 이 원리로 수리했고, 크립토는 `crypto-v2`
에서 같은 수리를 한다.

같은 원리가 stance 게이트에도 적용된다. **판정 대상은 판정 조건이 될 수 없다** —
`flipped`(전환 직후인가)는 사후 채점 대상이지 진입 자격이 아니다.

## crypto-v1 → crypto-v2 diff (WO-FCE-CORE-DEFECTS-01 Phase 1)

정책 파일: `backend/app/paper/params/crypto-v2.json` (신규). 파일이 없으면 `PaperPolicy`
기본값(= v1 동작)이 그대로 쓰인다 — **옵트인이며 파일 삭제로 즉시 롤백된다.**

| 게이트 | v1 | v2 | 판정 |
| --- | --- | --- | --- |
| `confirmed_flip` | `flipped is True` AND `transitioning is not True` AND 방향 일치 AND 확정봉 | 방향 일치 AND `transitioning is not True` AND 확정봉 | **변경** |
| `validated_signature` | 검증 시그니처 AND `ci_low >= min_ci` | 기록만(항상 통과) | **변경** |
| `evidence` | `>= min_evidence` | 동일 | diff 0 |
| `checklist` | `passed >= min_passed` AND `total >= min_total` | 동일 | diff 0 |
| `invalidation_hygiene` | 동일 | 동일 | diff 0 |
| `risk_reward` | `rr >= min_rr` | 동일 | diff 0 |
| `liquidation_safety` | 동일 | 동일 | diff 0 |
| `earnings_clear` | 동일 | 동일 | diff 0 |
| `data_fresh` | 동일 | 동일 | diff 0 |

diff 0 은 `tests/test_crypto_entry_gate_phase1.py::test_retained_gates_have_zero_diff`
가 10개 입력 조합에 대해 강제한다. 임계값(`min_rr`·`min_evidence`·`min_checklist_*`)은
단 하나도 바뀌지 않았다.

### 왜 `flipped` 요구가 결함이었나 — 실측

화면 실측: **이번 주 flip 14회 → 진입 0회**, 최다 탈락 "스탠스 전환 미확정" 190회.
퍼널 실측(600건): `confirmed_flip` 통과 **6.0%**.

WO 원문은 "`flipped is True` 와 `transitioning is not True` 가 겹치는 창이 구조적으로
거의 없다"고 봤으나 **그것은 정확하지 않다.** 상태머신(`analyst/confluence.py`)은 flip
완료 시 `build(cand, transitioning=False, ..., flipped=True)` 로 **둘을 동시에 세운다** —
모순이 아니다.

진짜 제약은 다른 것이다. `flipped=True` 는 **flip 완료 봉 1봉만의 펄스**다:

- 이후 봉은 `cand == prior_stance` → `flipped=False`
- 같은 확정 캔들 재평가는 봉 앵커 동결로 `flipped=False`

즉 진입 기회가 flip 순간 **1봉**으로 제한됐고, 그 1봉 안에서 나머지 8개 게이트까지 전부
동시에 통과해야 했다. flip 14회에 진입 0회는 엄격함이 아니라 **평가 불능**이다.

`stable_direction` 은 진입 기회를 "flip 순간"에서 "방향이 안정적으로 유지되는 구간"으로
넓힌다. 방향 일치·안정·확정봉 요구는 그대로이며, 룩어헤드 금지(확정 캔들만 사용)도 존치한다.

### 반사실 재생 (동일 600건 실데이터)

| | 전 게이트 통과 |
| --- | --- |
| v1 (실측) | **1건** |
| v2 (반사실 상한 추정) | **127건** |

수리 후 남는 병목 — 이것들은 **정당한 품질 거부이므로 완화하지 않는다**:

| 게이트 | 탈락률 |
| --- | --- |
| `checklist` | 62.5% |
| `risk_reward` | 46.8% |
| `liquidation_safety` | 32.5% |
| `action_levels` | 27.8% |
| `invalidation_hygiene` | 26.7% |
| `evidence` | 26.5% |

### 원장 기록 — 판정 대상은 남긴다

게이트에서 뺀 값은 `paper_gate_funnel.policy_observations` 에 남는다:
`flipped` · `transitioning` · `stance` · `validated_signature_observed` ·
`signature_ci_low_pct_observed` · `policy_version` · 두 게이트 모드.

이로써 "전환 직후 진입 vs 안정 후 진입 중 어느 쪽 성적이 나은가", "미검증 시그니처로 들어간
거래의 결과는 어땠나"를 **사후에 채점**할 수 있다. 조건에서 뺐다고 관측까지 버리면 그 질문에
영원히 답할 수 없다.


---

## 재진입 잠금 (WO-FCE-RISK-SIZING-01 Phase 3 · 2026-08-18)

같은 확정봉 안에서 청산하고 곧바로 같은 가격·같은 방향으로 다시 들어가는 일이
실측 9건 있었다. 새 판단이 아니라 왕복이다.

```python
policy.py  reentry_locked(entry_bar_at=..., direction=..., last_exit_bar_at=..., ...)
```

| 필드 | 기본값 | 채택값 |
| --- | --- | --- |
| `reentry_lock_mode` | `"off"` (기존 동작) | **`"same_bar"`** |
| `reentry_lock_bars` | `0` | `0` |
| `reentry_lock_same_direction_only` | `True` | `True` |

`params/crypto-v2.json` 옵트인. **파일을 지우면 잠금이 사라진다.**

두 진입 경로(정규 · validation bootstrap) 모두에 걸린다 — 한쪽만 막으면 왕복이 남는다.
차단된 건은 `paper_gate_funnel.reentry_block` 에 사유가 남는다(C10).

**품질 게이트가 아니라 표본 독립성 게이트다.** `min_rr`·`min_evidence`·체크리스트 임계는
건드리지 않는다(C1).

근거와 반사실 대조표: [`validation/SAMPLE_INDEPENDENCE.md`](validation/SAMPLE_INDEPENDENCE.md)

### 왜 더 긴 잠금을 쓰지 않는가

5봉 양방향 잠금이 표면상 가장 좋다(PF 2.08). **표본의 58%를 버린 결과**이고,
동일방향/양방향 두 변형이 잠금 길이에 대해 **반대 방향으로 움직인다** — 기전이 아니라
잡음 적합이다. `same_bar` 만 gross 우위가 음수라는 독립 근거를 갖는다.


---

## 순 기준 RR — 병행 기록만 (Phase 3-4 · 2026-08-18)

`target_plan` 에 세 값을 함께 남긴다:

```json
{ "rr_basis": "gross", "gross_rr_ratio": 1.5, "net_rr_ratio": 1.2035,
  "roundtrip_cost_distance": 0.1836, "rr_ratio": 1.5 }
```

`rr_basis` 가 `"net"` 이면 게이트가 `net_rr_ratio` 를 쓴다. **기본값·채택값은 `"gross"` 다.**

### ⚠️ 전환하지 않은 이유 — RR 게이트가 항등식이다

```
staged_reward  = ATR × k1 × 0.5 + ATR × k2 × 0.5 = ATR × 1.5
execution_risk = min(structural_risk, ATR)

structural_risk ≥ ATR 이면  RR = ATR×1.5 / ATR = 1.5   ← 산술적으로 항상
```

실측 24건 중 **20건(83%)의 RR 이 정확히 1.5000** 이고 **RR < 1.5 는 0건**이다.
`rr_ratio >= min_rr(1.5)` 게이트는 **한 번도 무언가를 거른 적이 없다.**

비용을 빼면 그 20건이 전부 1.5 아래로 떨어진다 — 순 기준 전환은 필터를 조이는 것이
아니라 **통과 24건 → 4건(83% 감축)** 으로 만든다. 표본 확보가 임계 경로인 상황에서
이것은 사용자 결정 사항이다.

> 진짜 수리 대상은 기준(gross/net)이 아니라 **RR 이 자기 자신을 확인하는 구조**다.
> `execution_risk` 가 ATR 로 캡되고 보상도 ATR 배수라 비율이 상수가 된다.
> 이 항목은 `DIRECTIONAL-INTEGRITY`(진입 품질) 소관으로 이관한다.

근거: [`validation/EXECUTION_MODEL.md`](validation/EXECUTION_MODEL.md)
