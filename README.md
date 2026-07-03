# FOMO Control Engine

FOMO Control Engine is a personal trading decision engine. It does not place trades or promise signals. It scores whether a planned entry is supported by market structure, volume, liquidity, momentum, and risk data, then turns the structured result into a plain-language report.

## Current v0.2 Scope

- FastAPI backend with report, position, monitoring, exit, and review endpoints
- Deterministic Entry Opportunity Score calculation
- Mock and live Bitget read-only market data providers
- SQLite persistence for reports, positions, monitoring logs, and trades
- Next.js dashboard for market summary, ticker detail, open positions, and trade journal
- pytest coverage for scoring, reports, mock provider, persistence, and position flow
- Documentation for PRD, architecture, and scoring logic

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
FCE_MARKET_DATA_PROVIDER=mock
FCE_DATABASE_URL=sqlite:///./fomo_control_engine.db
```

Live Bitget public market data mode:

```bash
FCE_MARKET_DATA_PROVIDER=bitget
FCE_BITGET_PRODUCT_TYPE=usdt-futures
```

The current Bitget integration uses public read-only futures market endpoints for candles, ticker, current funding rate, and open interest. No order endpoint is implemented.

## Tests

```bash
cd backend
python3 -m pytest

cd ../dashboard
npm run typecheck
npm run build
```

## Safety Principles

- V0.2 is read-only for exchange integrations.
- Bitget API keys must be read-only and provided through environment variables.
- No automatic buy or sell execution is included.
- Scores are deterministic. LLM usage, when added later, must only transform computed JSON into natural language.
