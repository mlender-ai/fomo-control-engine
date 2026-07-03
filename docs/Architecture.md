# Architecture

```text
MarketDataProvider
  -> Indicator Engine
  -> Wyckoff Structure Engine
  -> Liquidity Engine
  -> Scoring Engine
  -> Report Engine
  -> FastAPI
  -> Next.js Dashboard
  -> Entry / Monitoring / Exit / Review
```

## Backend

- `app/exchange/base.py`: exchange data boundary
- `app/exchange/mock.py`: local mock provider
- `app/exchange/bitget/client.py`: read-only Bitget integration placeholder
- `app/indicators`: RSI, MACD, Bollinger Bands, ATR, RVOL
- `app/structure/wyckoff`: probabilistic market structure interpretation
- `app/liquidity`: OI/Funding/liquidity score logic
- `app/scoring`: deterministic Entry Opportunity Score and FOMO Index
- `app/report`: JSON-to-natural-language report rendering
- `app/monitoring`: position state comparison
- `app/review`: trade review rendering

## Dashboard

- Home: market summary, top candidates, warnings, latest report
- Ticker detail: score breakdown, report, raw indicators
- Positions: manual position entry, monitoring, exit
- Journal: completed trades and review text

## Bitget Integration Rule

The application depends on `MarketDataProvider`, not on Bitget directly. Bitget can be wired later by implementing `get_snapshot()` inside `BitgetReadOnlyClient` and choosing that provider in the API composition layer.

