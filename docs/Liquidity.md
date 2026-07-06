# Liquidity Engine

WO-FCE-28 adds a deterministic liquidity layer for pools, sweeps, structure shifts, and Wyckoff cross-checks. The engine records observable OHLCV and trade-flow facts only. It does not infer hidden intent and it does not make win-rate claims.

## Pool Detection

Pool detection uses fractal swing points from recent candles.

- `eqh`: two or more swing highs whose prices are within `max(ATR * 0.1, price * 0.0008)`.
- `eql`: two or more swing lows whose prices are within the same tolerance.
- `old_high`: a recent swing high that has not been traded through by later candles.
- `old_low`: a recent swing low that has not been traded through by later candles.

Each pool emits:

```json
{
  "price": 100.0,
  "kind": "eqh",
  "touch_count": 3,
  "first_seen": "2026-07-06T00:00:00+00:00",
  "swept": false,
  "swept_at": null
}
```

Memory is capped at six pools per type. When more candidates exist, the engine keeps the most recent and most-tested pools first.

## Sweep Detection

A sweep is confirmed only when all conditions are true:

1. A candle wick penetrates a pool or the previous 1D candle range.
2. The candle body closes back inside the boundary within 0-3 candles.
3. Relative volume on the sweep candle is at least `1.5x`.

If price behavior matches but volume is below `1.5x`, the event is emitted as `unconfirmed` under `rejected_sweeps` and is not registered as a judgment.

## Sweep Grade

Depth is measured as penetration distance divided by ATR.

- `Weak`: depth < 0.3 ATR
- `Mid`: 0.3 ATR <= depth <= 0.8 ATR
- `Strong`: depth > 0.8 ATR

## Confidence Formula

Sweep confidence is deterministic:

```text
confidence =
  depth_significance   0-35
+ volume_confirmation  0-35
+ return_speed         0-20
+ pool_quality         0-10
```

`volume_confirmation` is zero when relative volume is below `1.5x`. If trade-fill delta is available and aligns with the expected follow-through direction, the volume component can receive a small bonus within the same 35-point cap.

## HTF Range Sweep

The engine treats the previous 1D candle high and low as temporary pools. A wick through that high or low followed by a body close back inside the range is emitted as `htf_range_sweep` through the same pipeline. This is not a separate branded module.

## Structure Shift

The structure module tracks:

- `BOS`: body close through the most recent swing boundary in the current swing direction.
- `CHoCH`: body close through the opposite swing boundary after a prior swing trend.
- `premium`, `discount`, `equilibrium`: location inside the Wyckoff range when available, otherwise the recent range.

## Wyckoff Cross-Check

Liquidity sweeps are integrated into Wyckoff instead of rendered as duplicate primary events:

- sell-side sweep near a range low can confirm `spring_candidate`.
- buy-side sweep near a range high can confirm `utad_candidate`.

When confirmed, the Wyckoff event receives a `liquidity_confirmation` component:

- `Weak`: +10
- `Mid`: +12
- `Strong`: +15

The total Wyckoff confidence remains capped at 100.

## Judgment Ledger

Only confirmed sweeps are registered as `liquidity_sweep` judgments.

- sell-side sweep implies upward follow-through.
- buy-side sweep implies downward follow-through.

Those judgments are scored by the existing directional-event scorer, so calibration can later measure sweep accuracy by grade and confidence bucket.
