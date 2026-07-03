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
- No exchange liquidation stream
- No on-chain or cross-venue aggregation
- No trade execution trigger
