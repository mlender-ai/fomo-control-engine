# Asset Classes and Sessions

FOMO Control Engine treats Bitget stock puffs as normal `USDT-FUTURES` symbols.
No separate exchange integration is required.

## Classification

Contract catalog rows are classified in this order:

1. Bitget metadata:
   - `isRwa=YES` means a stock/index puff.
   - known index tickers such as `QQQ`, `SPY`, `DIA`, `IWM` are classified as `index`.
   - the remaining RWA contracts are classified as `stock`.
2. Explicit ticker allowlists for known stock/index puff symbols.
3. Standard `USDT`/`USDC`/`USD` perps without RWA metadata are classified as `crypto`.
4. Anything else stays `unknown` and is logged. Unknown symbols are not guessed.

The catalog preserves `source_category`, `funding_rate_interval_hours`, and raw Bitget metadata used by the classifier.

## US Session Model

`stock` and `index` symbols use the US equities calendar in `app/marketdata/sessions.py`.

- Regular session: US/Eastern 09:30-16:00.
- Extended session: US weekdays outside regular hours.
- Closed session: US weekends and hardcoded market holidays.

Closed-session candles are excluded from level, Wyckoff, harmonic, liquidity, and volume-baseline analysis. Crypto symbols remain 24/7.

Holiday dates are intentionally hardcoded and must be reviewed yearly.

## Funding Interval

Funding interval is read from Bitget contract/funding metadata (`fundInterval` / `fundingRateInterval`) and stored with derivative metrics. Funding percentile samples are compared against the same interval when available, avoiding accidental 4h/8h baseline mixing.
