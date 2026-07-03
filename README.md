# FOMO Control Engine

FOMO Control Engine is a personal trading decision engine. It does not place trades or promise signals. It scores whether a planned entry is supported by market structure, volume, liquidity, momentum, and risk data, then turns the structured result into a plain-language report.

## Current v0.1 Scope

- FastAPI backend with report, position, monitoring, exit, and review endpoints
- Deterministic Entry Opportunity Score calculation
- Mock market data provider behind a Bitget-ready exchange boundary
- Next.js dashboard for market summary, ticker detail, open positions, and trade journal
- Documentation for PRD, architecture, and scoring logic

## Run Locally

Backend:

```bash
cd backend
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8875
```

Dashboard:

```bash
cd dashboard
npm install
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8875 npm run dev -- --host 127.0.0.1 --port 8876
```

Open [http://127.0.0.1:8876](http://127.0.0.1:8876).

## Safety Principles

- V1 is read-only for exchange integrations.
- Bitget API keys must be read-only and provided through environment variables.
- No automatic buy or sell execution is included.
- Scores are deterministic. LLM usage, when added later, must only transform computed JSON into natural language.

