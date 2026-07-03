# FOMO Control Engine

FOMO Control Engine is a personal trading decision engine. It does not place trades or promise signals. It scores whether a planned entry is supported by market structure, volume, liquidity, momentum, and risk data, then turns the structured result into a plain-language report.

## Current v0.4 Scope

- FastAPI backend with report, position, monitoring, exit, and review endpoints
- Deterministic Entry Opportunity Score calculation
- Mock and live Bitget read-only market data providers
- Bitget private read-only position lookup and sync
- SQLite persistence for reports, positions, monitoring logs, trades, research runs, agent outputs, shadow profiles, decision memories, and validation runs
- Agentic Research runs with deterministic market snapshots, Bull/Bear debate, Risk Guardian, and FOMO Gatekeeper outputs
- Shadow Account extraction from completed trades
- Liquidation Intelligence proxy analysis from score/OI/funding context
- Validation Lab with Monte Carlo, Bootstrap Sharpe CI, and Walk Forward checks
- Next.js dashboard for market summary, ticker detail, open positions, trade journal, research runs, shadow journal, and validation lab
- pytest coverage for scoring, reports, mock provider, persistence, position flow, research runs, shadow extraction, liquidity analysis, validation, and memory

## Run Locally

Backend:

```bash
cd backend
python3 -m pip install -e .
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8875
```

Dashboard:

```bash
cd dashboard
npm install
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8875 npm run dev -- -H 127.0.0.1 -p 8876
```

Open [http://127.0.0.1:8876](http://127.0.0.1:8876).

## Configuration

Create `backend/.env` from `backend/.env.example`.

Default local mode:

```bash
MARKET_DATA_PROVIDER=mock
DATABASE_URL=sqlite:///./fomo_control_engine.db
```

Live Bitget public market data mode:

```bash
MARKET_DATA_PROVIDER=bitget
BITGET_PRODUCT_TYPE=USDT-FUTURES
BITGET_MARGIN_COIN=USDT
BITGET_API_KEY=
BITGET_API_SECRET=
BITGET_API_PASSPHRASE=
```

The current Bitget integration uses public read-only futures market endpoints for candles, ticker, current funding rate, and open interest. Private API usage is limited to read-only futures positions. No order endpoint is implemented.

Useful checks:

```bash
curl http://127.0.0.1:8875/api/system/status
curl -X POST http://127.0.0.1:8875/api/system/bitget/test-connection
curl -X POST http://127.0.0.1:8875/api/reports \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","timeframe":"4h"}'
curl http://127.0.0.1:8875/api/account/bitget/positions
curl -X POST http://127.0.0.1:8875/api/account/bitget/sync-positions
curl -X POST http://127.0.0.1:8875/api/research-runs \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","timeframe":"4h","mode":"entry_review"}'
curl -X POST http://127.0.0.1:8875/api/liquidity/analyze \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","timeframe":"4h"}'
curl -X POST http://127.0.0.1:8875/api/validation/run \
  -H "Content-Type: application/json" \
  -d '{"strategy_type":"entry_score_threshold","symbol":"BTCUSDT","timeframe":"4h"}'
```

## Tests

```bash
cd backend
python3 -m pytest

cd ../dashboard
npm run typecheck
npm run build
```

## Safety Principles

- V0.4 is read-only for exchange integrations.
- Bitget API keys must be read-only and provided through environment variables.
- No automatic buy or sell execution is included.
- Scores are deterministic. LLM usage, when added later, must only transform computed JSON into natural language.
- Agent outputs explain a fixed score snapshot. They do not recalculate price, score, or order intent.
