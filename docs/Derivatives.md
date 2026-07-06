# Derivatives Data Layer

WO-FCE-20 adds a two-tier read-only derivatives layer.

## Sources

Tier 1 is always available when the Bitget market provider is active:

- Open interest: Bitget `/api/v2/mix/market/open-interest`
- Current funding: Bitget `/api/v2/mix/market/current-fund-rate`
- Account long/short ratio: Bitget `/api/v2/mix/market/account-long-short`

Tier 2 is locked unless `FCE_COINGLASS_API_KEY` is configured:

- Aggregated open interest: Coinglass `/api/futures/open-interest/exchange-list`
- Aggregated liquidation history: Coinglass `/api/futures/liquidation/aggregated-history`
- Top trader account ratio: Coinglass `/api/futures/top-long-short-account-ratio/history`
- OI-weighted funding: Coinglass `/api/futures/funding-rate/oi-weight-history`
- Liquidation price clusters: Coinglass `/api/futures/liquidation/aggregated-heatmap/model2`

If a Coinglass endpoint returns an auth, plan, or rate-limit error, only that feature is marked `locked` or `error`; Bitget collection continues.

## Storage

- `deriv_metrics`: symbol time series for OI, funding, taker long/short ratio, top trader ratio, and OI-weighted funding.
- `liquidation_events`: Coinglass liquidation history buckets with long and short liquidation USD values.
- `derivative_snapshots`: compatibility snapshot for existing dashboard and bot responses.

Retention:

- `deriv_metrics`: raw rows for 90 days, then 1-day downsample.
- `liquidation_events`: 30 days.
- Judgment, review, and calibration data are never deleted by derivative retention.

## Signals

All signals are deterministic.

### OI Price Divergence

Inputs:

- `price_change_pct_24h`
- `oi_change_pct`

Classification:

- `price_up_oi_up`: price and OI both rise.
- `price_up_oi_down`: price rises while OI falls; short-covering rally is possible.
- `price_down_oi_up`: price falls while OI rises; new short pressure is possible.
- `price_down_oi_down`: price and OI both fall; position reduction or long liquidation is possible.

If either input is missing, the signal is `null`.

### Funding State

Funding state uses the absolute funding percentile inside stored history, not a fixed funding-rate threshold.

- sample size `< 20`: `null`, label `표본 부족`
- percentile `< 70`: `neutral`
- percentile `70-89.99`: `overheated`
- percentile `>= 90`: `extreme`

### Crowding Score

Formula:

```text
crowding_score =
  funding_percentile * 0.40
+ long_short_pressure * 0.35
+ oi_pressure * 0.25
```

Components:

- `funding_percentile`: absolute funding percentile, 0-100.
- `long_short_pressure`: distance of long/short ratio from 1.0, capped at 100.
- `oi_pressure`: `abs(oi_change_pct) * 10`, capped at 100.

The score is `null` when funding sample coverage is insufficient.

### Liquidation Clusters

Price-level clusters are emitted only from Coinglass heatmap/model data. The engine does not reconstruct a liquidation heatmap from aggregated liquidation history because that history has time and amount but no price level.

## WO-FCE-21 Integration

Derivative data is injected into existing product surfaces instead of creating a separate dashboard.

### Health Score

`health_components.flow` uses derivative signals when available:

- OI/price divergence alignment with the position direction.
- Funding state and whether the funding sign is favorable or adverse to the held direction.
- Crowding score and whether the crowded side matches the held direction.

If derivative signal coverage is insufficient, the engine falls back to the legacy relative-volume/MACD flow score and keeps `formula_version = health_v2`.

### Action Plan

Watch triggers can include deterministic derivative conditions:

- extreme funding adverse to the held direction,
- OI expansion with price stalling or moving against the position,
- estimated liquidation cluster proximity.

Coinglass liquidation clusters may be added as take-profit candidates only as `청산 밀집대 추정`; they are never labeled as confirmed support/resistance.

### UI, Bot, Alerts

- The chart flow layer renders OI and funding subpanels. Coinglass liquidation clusters render only when Tier 2 source status is `ok`.
- The action-plan evidence module shows source, `as_of`, OI change, funding state, and Coinglass lock/availability.
- Telegram `/flow SYMBOL` uses the same service payload as the dashboard.
- Alerts added in WO-FCE-21 are `funding_extreme`, `oi_divergence`, and `liq_cluster_near`. They inherit the existing cooldown and quiet-hour state machine, and none of them are `critical`.

Every derivative number exposed by API/UI must include source and basis time. Liquidation cluster labels must include `추정`.

## Coinglass Rate Budget

Default config:

```text
FCE_DERIVATIVE_TRACKING_INTERVAL_SECONDS=300
FCE_COINGLASS_RATE_LIMIT_PER_MINUTE=30
FCE_COINGLASS_REQUESTS_PER_SYMBOL=6
```

Budget:

```text
requests_per_tick = 30 * (300 / 60) = 150
max_symbols_per_tick = floor(150 / 6) = 25
```

Therefore 20 symbols fit in one 5-minute tick. If tracked symbols exceed the tick budget, the worker uses round-robin selection.
