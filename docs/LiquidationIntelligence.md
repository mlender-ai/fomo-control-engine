# Liquidation Intelligence

Liquidation Intelligence v0.1 estimates liquidation cluster pressure from the existing liquidity score context. It is a proxy layer, not an exchange liquidation feed.

## API

- `POST /api/liquidity/analyze`

Request:

```json
{
  "symbol": "BTCUSDT",
  "timeframe": "4h"
}
```

## Inputs

- report price
- liquidity sub-score
- upper and lower liquidity hints from the report JSON
- funding and open-interest context already captured by the market provider

## Output

- upper cluster candidates
- lower cluster candidates
- dominant magnet
- asymmetry score
- cascade risk labels

## Safety Rule

The liquidation result is not added directly into Entry Opportunity Score in v0.4. It is displayed as separate context so a user can avoid treating a liquidation magnet as a guaranteed target.

## Limits

- No real order-book depth
- No continuously connected exchange liquidation WebSocket
- No on-chain or cross-venue aggregation
- No trade execution trigger

## WO-FCE-78 Realized History

The realized-liquidation data path reads Bitget's public three-day liquidation REST history. It observes price, side, amount, and event time after liquidation has occurred. It does not change this module's forward-looking proxy and is not added to Entry Score or direction scoring.

The two surfaces must stay distinct:

- `Liquidation Intelligence`: possible future cluster proxy / optional Coinglass model, always labeled estimated.
- `Realized Liquidation Heatmap`: historical Bitget liquidation events, always labeled realized and not predictive.

The realized layer combines confirmed OHLC candles, timestamped event cells, and horizontal period-total realized-density bands. It also compares observed intensity above and below the current price. These additions improve visual orientation only; they do not convert historical liquidations into a forecast or scoring input.

## WO-FCE-UNIFIED-CHART-01 Shared-axis view

The main pro chart now has a separate `청산밀집(실현)` layer. It reuses the main candle viewport, price scale, plan lines, EMA series, current-price flag, and crosshair instead of introducing another chart axis. The default combination is `플랜 + 청산밀집(실현)`; ordinary clicks can compose more layers, while shift-click keeps the two-layer comparison cap.

The default inline controls expose trust context, range, `누적 잔존/발생 시점`, and opacity. Direction and size quartile remain available under the closed `세부 필터`; above/below totals, long/short totals, and last-event summaries are intentionally omitted from the default surface so the observed price zones remain primary. `N`, low-sample status, `과거 발생 · 예측 아님`, and the latest confirmed candle remain visible. Crosshair readout shows the unmodified source bucket total.

## WO-FCE-UNIFIED-CHART-02 Single live surface

The shared-axis pro chart is now the only realized-liquidation chart surface. The duplicate standalone card was removed together with its frontend client and styles, so candles, current price, action plan, and realized density cannot drift across two independent viewports.

- Former yellow dotted outlines are replaced by ranked solid bands and matching `밀집 1/2/3` controls. Each control states the observed price, estimated realized USD, and event count; these bands are density summaries, not action-plan guardrails.
- Default opacity is 68%, and a versioned preference key prevents an old 20% value from silently restoring the low-visibility state.
- `LIVE · 청산 5초` reports the polling cadence and latest successful receive time. `최근 확정` identifies the newest confirmed OHLC candle. The UI does not imply tick streaming or promote an unconfirmed candle.
- A pulsing current-price marker and latest-confirmed marker improve visual motion while preserving the read-only, confirmed-candle, non-predictive rules.
