# Derivatives Data Layer

WO-FCE-20 adds a two-tier read-only derivatives layer.

## Sources

Tier 1 is always available when the Bitget market provider is active:

- Open interest: Bitget `/api/v2/mix/market/open-interest`
- Current funding: Bitget `/api/v2/mix/market/current-fund-rate`
- Account long/short ratio: Bitget `/api/v2/mix/market/account-long-short`
- Realized liquidation history: Bitget `/api/v3/market/liquidations` (public, latest 3 days)

Tier 2 is locked unless `FCE_COINGLASS_API_KEY` is configured:

- Aggregated open interest: Coinglass `/api/futures/open-interest/exchange-list`
- Aggregated liquidation history: Coinglass `/api/futures/liquidation/aggregated-history`
- Top trader account ratio: Coinglass `/api/futures/top-long-short-account-ratio/history`
- OI-weighted funding: Coinglass `/api/futures/funding-rate/oi-weight-history`
- Liquidation price clusters: Coinglass `/api/futures/liquidation/aggregated-heatmap/model2`
- Aggregated spot/futures CVD and BTC/ETH options are optional feature probes described in `MoneyFlow.md`.

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

### Realized Liquidation Heatmap (WO-FCE-78)

Bitget's public liquidation history includes observed `price`, `side`, `amount`, and `ts`, so the engine can render a time × price heatmap without a Coinglass key. This is a historical heatmap of liquidations that already happened, not a map of positions expected to liquidate in the future.

- `buy` means a long position was liquidated; `sell` means a short position was liquidated, following Bitget's endpoint contract.
- Event identity is deterministic across refreshes, so pagination does not duplicate stored observations.
- Color intensity uses `price × amount` on a log scale. Bitget's REST page does not specify the amount unit, so every such value is labeled `estimated`; event price, side, amount, and timestamp remain direct observations.
- The legacy endpoint supports 24-hour and 72-hour windows. Since WO-FCE-UNIFIED-CHART-02, the product UI consumes the candle-aligned unified endpoint below instead of rendering a second standalone chart.
- WO-FCE-86 established the confirmed-OHLC overlay and horizontal **period-total realized-density bands**. The standalone rendering was retired after the shared-axis view reached parity; the bands still summarize liquidations that already occurred and are not standing orders, predicted leverage levels, or future liquidity.
- This observation is excluded from Entry Score, directional confluence, automatic entry, and signature promotion.

API:

```text
GET  /api/derivatives/{symbol}/liquidation-heatmap?window_hours=72
POST /api/derivatives/{symbol}/liquidation-heatmap/refresh?window_hours=72
```

The forward-looking Coinglass `aggregated-heatmap/model2` remains a separate optional model. A realized Bitget hotspot must never be relabeled as an expected liquidation cluster.

### Unified chart raster (WO-FCE-UNIFIED-CHART-01/02)

The pro chart requests observed events as a candle-aligned time × price grid and paints that raster on the main chart coordinate system. WO-FCE-UNIFIED-CHART-02 made this the only liquidation chart surface and removed the duplicate WO-FCE-78/86 frontend card, client API, and styles.

```text
GET /api/liq/heatmap?symbol=ETHUSDT&tf=4h&range=3D&side=all&size=all&mode=persist&price_bins=120
```

- `event` paints value only in the event bucket. `persist` repeats the same raw event value until a later **confirmed** candle first trades through the observed event price. Persistence is a historical display transform, not an unfilled-order or forecast claim.
- The response keeps the full raw event list and unmodified USD-estimated bucket values. Log normalization and opacity are rendering metadata only.
- Bitget currently supplies no leverage field in the collected row. The UI therefore labels the filter `규모` and uses explicit quartile membership: `Q2+` starts at the 25th percentile, `Q3+` at the median, and `Q4` at the 75th percentile. The response publishes the actual thresholds and `leverage_available=false`. If a future source provides leverage for every event, metadata switches to `filter_basis=leverage`; no leverage is inferred.
- `source=coinglass_est` returns the same adapter shape with `source_status=locked` until an existing collector is connected. It is a separate disabled layer and is never mixed with Bitget realized totals.
- The UI polls this observation at no more than five-second intervals while its layer is active. It remains excluded from Entry Score, directional confluence, paper-engine entry gates, and live orders.
- The UI exposes `LIVE · 청산 5초`, last receive time, and the latest `최근 확정` candle time. `LIVE` describes observation polling only; it does not claim an unconfirmed candle or streaming order-book feed.
- The top three realized-density zones use ranked solid bands (`밀집 1/2/3`) with price, estimated realized USD, and event count. They replace the unexplained yellow dotted outlines. Clicking a zone highlights its price; it does not create an action-plan guardrail.
- The rendering default is 68% opacity under the versioned `fce.unifiedHeatmap.opacity.v2` preference. Raw bucket totals are unchanged.

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

Every derivative number exposed by API/UI must include source and basis time. Liquidation cluster labels must include `추정`; Bitget history labels must include `실현 청산`.

## Coinglass Rate Budget

Default config:

```text
FCE_DERIVATIVE_TRACKING_INTERVAL_SECONDS=300
FCE_COINGLASS_RATE_LIMIT_PER_MINUTE=30
FCE_COINGLASS_REQUESTS_PER_SYMBOL=10
```

Budget:

```text
requests_per_tick = 30 * (300 / 60) = 150
max_symbols_per_tick = floor(150 / 10) = 15
```

Therefore 15 symbols fit in one 5-minute tick at the worst-case feature budget. If tracked symbols exceed the tick budget, the worker uses round-robin selection. Unsupported optional probes are not counted as consumed requests.
