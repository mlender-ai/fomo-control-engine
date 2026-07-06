# FCE Calibration

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

The dedicated `/calibration` dashboard contains four modules:

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
- Timezone: `telegram_quiet_hours_timezone`, default `Asia/Seoul`

The weekly report includes the recent scorecard, confidence curve, highlights, and pending suggestions.
