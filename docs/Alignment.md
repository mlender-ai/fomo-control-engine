# Full Alignment

Full alignment is a measured observation, not an entry instruction.

## Eligible modules

Only modules with a `validated` signature lifecycle and a non-neutral current
direction are eligible. Each module contributes at most one vote. Candidate,
degraded, and quarantined modules are excluded.

## Decision

An alignment is unanimous only when all conditions hold:

1. At least four eligible modules agree on one direction.
2. No eligible module dissents.
3. The higher timeframe bias matches that direction.
4. The directional state is not transitioning.
5. The candle used by the directional state is confirmed.

`score = sum(confluence evidence.score for agreeing modules)`

`evidence.score` is reused without modification. It already includes the
engine base weight, confidence, calibration CI lower-bound factor, recency,
and HTF adjustment documented in `DirectionalEngine.md`. Full alignment adds
no new weights.

## Honesty guard

`full_alignment` is registered as a candidate signature. Until N >= 30 the UI
shows `표본 축적 중`. When N >= 30 and win@1R CI lower bound is below 50%, the
UI shows `만장일치의 예측력 미검증`. Historical results are descriptive and
do not guarantee future outcomes.
