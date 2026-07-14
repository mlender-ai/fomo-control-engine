# Setup Alert Consistency

## Direction hierarchy

An individual setup is an attention trigger, not the final direction decision. The confluence briefing remains the primary directional judgment. Setup alerts must always show the setup direction and the briefing direction as separate claims.

`setup_near` and `setup_triggered` follow these states:

- Aligned: title includes `종합 정합` and keeps the normal severity.
- Conflicting: title is `충돌 셋업 · 양방향 근거`, both arguments remain visible, and the message states that the minority setup may be correct. Conflict alone must not be described as a definitive hold.
- Conflicted or insufficient briefing: message states `종합 판단 유보 중 · 셋업만 감지`.

A conflicting triggered setup is informational so it cannot become an automatic entry signal. When the setup direction is aligned with the higher-timeframe trend, it keeps its original severity and warns that the confluence briefing may be countertrend. Conflict alerts can be disabled with `FCE_SCOUT_CONFLICT_SETUP_ALERTS_ENABLED=false`; the setup lifecycle still updates even when its notification is suppressed.

## R:R hygiene

The default minimum invalidation distance is 0.8%, configured by `FCE_SETUP_MIN_INVALIDATION_DISTANCE_PCT`. A closer invalidation is treated as likely market noise:

- Public R:R is not calculated.
- The preview says `R:R 산출 불가 · 무효화 너무 가까움`.
- The setup alert cannot retain `action` severity.
- Strict paper entries and validation bootstrap entries fail the `invalidation_hygiene` gate.

Raw R:R is retained as `rr_ratio_raw` for detector-quality analysis. Public display is capped at `10+` by `FCE_SETUP_RR_DISPLAY_CAP`, while the unclamped value remains available to logs and scoring.

## Calibration fields

Scout setup judgments and scores retain these quality metrics:

- `invalidation_distance_pct`
- `invalidation_too_close`
- `rr_ratio_raw`
- `rr_above_display_cap`
- `briefing_htf_countertrend`

These fields are the aggregation contract for candidate scoring. They allow detector outliers and the rate at which the briefing opposes the higher-timeframe trend to be measured without suppressing minority setup evidence.
