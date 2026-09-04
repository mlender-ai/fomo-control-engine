# FCE Calibration

판단 표본의 운영 커버리지 정본은 [`JudgmentCoverage.md`](JudgmentCoverage.md)다. 주간 calibration 리포트는 최근 7일의 원장 커버리지 한 줄을 포함하며, 미분류 판단 유형이 있으면 `attention`으로 노출한다. `autonomy_logs`는 거버넌스 정본, `judgment_ledger`/`judgment_scores`는 판단·결과 정본으로 역할을 분리한다.

WO-FCE-22 calibration turns stored judgment scores into a deterministic scorecard. It does not change trading parameters automatically.

## Sample Floor

- `sample_floor = 10`
- Any bucket with fewer than 10 tested judgments returns `sample_state = insufficient_sample`.
- Accuracy can still be displayed, but the conclusion is `표본 부족`.

## Outcome Summary

Each scorecard bucket reports:

- `total`: all judgments in the bucket
- `tested`: judgments excluding `untested`
- `correct`, `wrong`, `whipsaw`, `untested`
- `accuracy_pct = correct / tested * 100`
- `conclusion`:
  - `표본 부족`: tested < 10
  - `유효`: accuracy >= 70
  - `관찰 필요`: accuracy >= 50 and < 70
  - `개선 필요`: accuracy < 50

## Confidence Curve

Judgments with a `confidence` value are grouped into 10-point buckets.

- `calibration_gap_pct = actual_accuracy_pct - bucket_midpoint`
- `overconfident`: gap <= -20 and tested >= 10
- `underconfident`: gap >= +20 and tested >= 10
- `aligned`: otherwise, with tested >= 10
- `insufficient_sample`: tested < 10

## Suggestions

Suggestions are generated only when a proposal bucket has at least 15 tested samples. The stricter floor prevents the engine from changing live thresholds based on thin samples.

- `min_invalidation_level_score`: low accuracy for invalidation levels with score 40-55.
- `confidence_floor_review`: overconfident Wyckoff or harmonic confidence buckets.
- `alert_trigger_near_pct`: noisy trigger-near alerts.
- `harmonic_ratio_tolerance_multiplier`: low-accuracy harmonic PRZ judgments.

Approving a suggestion records an `engine_params` version and applies that explicit override at runtime. No suggestion is ever applied automatically.

Each judgment stores the active `param_version` snapshot so later reports can compare pre-change and post-change results.

## Interim Scoring

Closed trades are still the primary scoring source. Open positions are also scored by the `interim_scoring` worker job so untested judgments do not wait indefinitely for a position close.

- Default interval: once per day.
- Interim scores use a synthetic score context and do not fabricate a closed trade.
- Judgment scoring only uses candles and snapshots at or after the judgment `as_of`.

## Product Surface

WO-FCE-91 removed the duplicate `/calibration` route. The canonical `/engine?tab=status`
surface contains the calibration and validation modules alongside their operational context:

- Judgment scorecard
- Confidence curve
- Level quality
- Parameter suggestions

WO-FCE-23 adds a fifth module, alert response scorecard:

- `alert_response_window_hours = 6`: response detection window after an alert.
- `alert_response_outcome_hours = 24`: deterministic result comparison window after the detected response.
- Responses are inferred from read-only position deltas: `closed_full`, `reduced`, `added`, `held`, `stop_moved`.
- Outcomes are `response_good`, `response_costly`, or `inconclusive`.
- All alert-response language is explicitly retrospective. It does not say the user should have acted differently.
- Similar-alert history is shown in Telegram only when N >= 5.

Alert response scoring never updates alert thresholds automatically; it can only become future evidence for the suggestion/approval loop.

WO-FCE-24 adds a sixth module, pre-entry setup scorecard:

- Scout setup judgments use the sentinel position id `00000000-0000-0000-0000-000000000000` because no real position exists yet.
- Armed setups are recorded as `judgment_type = scout_setup`.
- Unentered setups are scored after trigger/invalidated resolution using only scout snapshots after the setup resolution time.
- The module separates harmonic PRZ, structure level, Wyckoff event, crowding+level, and manual price setups.
- The score is retrospective evidence. It does not convert setup alerts into entry instructions.

## Weekly Report

The weekly report covers the latest 7 days of judgment scores by `JudgmentScore.created_at`.

Default Telegram schedule:

- Day: Sunday (`6`, Python weekday)
- Time: `20:00`
- Timezone: `telegram_local_timezone`, default `Asia/Seoul` (옛 `telegram_quiet_hours_timezone` 환경변수는 별칭으로 유지)

The weekly report includes the recent scorecard, confidence curve, highlights, and pending suggestions.

## 파라미터 루프의 시야 경계 (WO-FCE-FULL-AUDIT-01 Phase 3)

> **다이얼을 돌리는 루프는 다이얼이 없다는 것을 알아챌 수 없다.**

4주 실사 결과 복기 루프는 **정상 작동했다**: 판단 원장 138,092건 · 채점 89,408건 ·
제안 8건(채택 3 · `dwell_blocked` 5) · `engine_params` 5버전 · `autonomy_log` 10건.
`dwell_blocked` 5건은 자기 완화 방향이라 dwell 창에 걸린 것으로 설계대로다.

그런데 같은 기간 확정된 **구조 결함 5종의 포착 건수는 0건**이다.

| 결함 | 성격 | 캘리브레이션이 볼 수 있나 |
| --- | --- | --- |
| 와이코프가 선행 추세를 인자로 안 받음 | 함수 시그니처 | 아니오 |
| 커버리지 게이트 부재 | 없는 개념 | 아니오 |
| 재진입 잠금 부재 | 없는 규칙 | 아니오 |
| `distance_pct` 가 옛 스냅샷 값 | 데이터 흐름 | 아니오 |
| 이벤트 루프 블로킹 | 실행 모델 | 아니오 |

전부 **임계값이 아니라 구조**다. 캘리브레이션은 존재하는 파라미터의 값을 조정하는 장치이므로,
"이 파라미터가 애초에 없다" 또는 "입력이 틀린 값이다"를 제안으로 만들 수 없다.

**따라서 자율 루프가 조용하다는 것은 "문제가 없다"는 뜻이 아니다.** 구조 결함 탐지는
별도 수단(실사·재판정 하네스·정적 감사)이 담당해야 하며, 이 루프에 기대하면 안 된다.

실사 전문: [`validation/FULL_AUDIT_01.md`](validation/FULL_AUDIT_01.md)
