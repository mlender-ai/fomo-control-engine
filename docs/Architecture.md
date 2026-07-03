# Architecture

```text
MarketDataProvider
  -> Indicator Engine
  -> Wyckoff Structure Engine
  -> Liquidity Engine
  -> Scoring Engine
  -> Report Engine
  -> Agentic Research Orchestrator
  -> Shadow / Validation / Decision Memory
  -> FastAPI
  -> Next.js Dashboard
  -> Entry / Monitoring / Exit / Review
```

## Backend

- `app/exchange/base.py`: exchange data boundary
- `app/exchange/mock.py`: local mock provider
- `app/exchange/bitget/signer.py`: HMAC SHA256 request signing
- `app/exchange/bitget/client.py`: async public/private GET client
- `app/exchange/bitget/provider.py`: live Bitget market data and read-only positions provider
- `app/exchange/factory.py`: `mock` / `bitget` provider switch
- `app/db/repository.py`: memory and SQLite repositories
- `app/indicators`: RSI, MACD, Bollinger Bands, ATR, RVOL
- `app/structure/wyckoff`: probabilistic market structure interpretation
- `app/liquidity`: OI/Funding/liquidity score logic
- `app/scoring`: deterministic Entry Opportunity Score and FOMO Index
- `app/report`: JSON-to-natural-language report rendering
- `app/agents`: deterministic agent contracts and research-run orchestration
- `app/shadow`: completed-trade pattern extraction and Shadow Account comparison
- `app/validation`: Monte Carlo, Bootstrap Sharpe CI, and Walk Forward checks
- `app/memory`: decision memory creation from trades, shadow profiles, and validation runs
- `app/monitoring`: position state comparison
- `app/review`: trade review rendering

## Dashboard

- Home: market summary, top candidates, warnings, latest report
- Ticker detail: score breakdown, report, raw indicators
- Positions: manual position entry, monitoring, exit
- Research: agentic research runs, Bull/Bear confidence, recent decision memory
- Shadow: extracted Shadow Account profile and profile history
- Validation: validation run history and warnings
- Journal: completed trades and review text

## Bitget Integration Rule

The application depends on `MarketDataProvider`, not on Bitget directly. Choose the provider with `FCE_MARKET_DATA_PROVIDER`.

- `mock`: deterministic local snapshots for tests and UI development
- `bitget`: public read-only futures market data from Bitget

Bitget endpoints used in v0.4:

- `GET /api/v2/mix/market/candles`
- `GET /api/v2/mix/market/ticker`
- `GET /api/v2/mix/market/current-fund-rate`
- `GET /api/v2/mix/market/open-interest`
- `GET /api/v2/mix/position/all-position`

Order placement is intentionally absent.

## Persistence

Default persistence is SQLite. v0.4 persists reports, positions, monitoring logs, trades, market snapshots, research runs, agent outputs, shadow profiles, decision memories, and validation runs.

```text
FCE_DATABASE_URL=sqlite:///./fomo_control_engine.db
```

Tests use `memory://` or temporary SQLite files so score and API flow tests are isolated.
