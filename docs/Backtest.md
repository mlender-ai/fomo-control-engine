# Historical Setup Backtest

WO-FCE-34 adds deterministic historical setup replay. It is descriptive context, not a prediction engine.

## Fixed Policy

- Disclaimer: `과거 통계 · 미래 보장 아님 · 수수료/슬리피지 미반영`
- Entry price: confirmation candle close.
- No lookahead: detectors receive only candles up to the replay index.
- Fractal/zigzag pivots are not treated as actionable until the detector can confirm them from the replay window.
- Outcome horizon: default 48 bars.
- Risk unit: invalidation distance if an eligible structural level exists; otherwise `ATR * 1.5`.
- Same-candle target and invalidation: conservative loss.
- N must always be displayed. `N < 10` is `표본 부족 — 결론 유보`.
- Live calibration and historical backtest stats are never merged into one hit-rate number.

## Signature Schema

Each setup is normalized to:

```text
engine:event_type:strength_class:direction:asset_class:timeframe
```

Examples:

```text
liquidity:sweep_low:Strong:long:crypto:4h
wyckoff:spring_confirmed:conf>=70:long:stock:4h
harmonic:prz_touch:conf>=70:short:crypto:4h
levels:level_touch:score>=70:long:index:1h
```

The same conversion code is used for live analysis and replay output.

## Rate Budget

The first implementation replays over the candle window already returned by the market data provider. It does not add a new paginated Bitget history collector yet. For 20 symbols, precompute cost is the existing scout scan OHLCV request per symbol plus local CPU replay. A later expansion can add paginated two-year history with a low-priority worker queue and per-symbol cache.

