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

The separate realized-liquidation panel reads Bitget's public three-day liquidation REST history. It observes price, side, amount, and event time after liquidation has occurred. It does not change this module's forward-looking proxy and is not added to Entry Score or direction scoring.

The two surfaces must stay distinct:

- `Liquidation Intelligence`: possible future cluster proxy / optional Coinglass model, always labeled estimated.
- `Realized Liquidation Heatmap`: historical Bitget liquidation events, always labeled realized and not predictive.

The realized panel combines confirmed OHLC candles, timestamped event cells, and horizontal period-total realized-density bands. It also compares observed intensity above and below the current price. These additions improve visual orientation only; they do not convert historical liquidations into a forecast or scoring input.

## WO-FCE-UNIFIED-CHART-01 Shared-axis view

The main pro chart now has a separate `청산밀집(실현)` layer. It reuses the main candle viewport, price scale, plan lines, EMA series, current-price flag, and crosshair instead of introducing another chart axis. The default combination is `플랜 + 청산밀집(실현)`; ordinary clicks can compose more layers, while shift-click keeps the two-layer comparison cap.

The inline controls expose side, size quartile, range, `persist/event`, and opacity. `N`, low-sample status, `실제 청산 · 예상 아님`, above/below totals, long/short totals, and last event remain visible next to the chart. Crosshair readout shows the unmodified source bucket total. The old realized card remains in place as the C5 validation fallback.
