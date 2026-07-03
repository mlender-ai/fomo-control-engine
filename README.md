# FOMO Control Engine

FOMO Control Engine is a personal live position intelligence cockpit. It does not place trades or promise signals. It tracks the user's real open positions, calculates deterministic position state JSON, and explains whether the original thesis, risk, chart structure, and exit review data still make sense.

## Current MVP Scope

- FastAPI backend with live position, snapshot, insight, event, memo, exit-record, and trade review endpoints
- Deterministic Entry Opportunity Score calculation
- Mock and live Bitget read-only market data providers
- Bitget private read-only position lookup and sync
- SQLite persistence for reports, positions, monitoring logs, trades, live position snapshots, insights, and events
- Position Health Score with thesis, chart, risk, momentum/volume, liquidity/funding components
- Position State labels: healthy, watch, risk rising, thesis weakening, critical, unknown
- Deterministic Korean AI-style insight text generated from stored JSON only
- Astryx-based Next.js terminal dashboard focused on Live Positions, Trade History, and Settings
- Older market/research/shadow/validation routes remain available but are hidden from the main MVP navigation
- pytest coverage for scoring, reports, mock provider, persistence, position flow, live position APIs, research runs, shadow extraction, liquidity analysis, validation, and memory

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

Production-style dashboard check:

```bash
cd dashboard
npm run build
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8875 npm run start -- -H 127.0.0.1 -p 8876
```

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
curl http://127.0.0.1:8875/api/live/positions
curl -X POST http://127.0.0.1:8875/api/live/positions/sync
curl -X POST http://127.0.0.1:8875/api/reports \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","timeframe":"4h"}'
curl http://127.0.0.1:8875/api/account/bitget/positions
curl -X POST http://127.0.0.1:8875/api/account/bitget/sync-positions
```

Live position detail endpoints:

```bash
curl http://127.0.0.1:8875/api/live/positions/{position_id}
curl -X POST http://127.0.0.1:8875/api/live/positions/{position_id}/analyze
curl -X POST http://127.0.0.1:8875/api/live/positions/{position_id}/insight
curl http://127.0.0.1:8875/api/live/positions/{position_id}/events
curl http://127.0.0.1:8875/api/live/positions/{position_id}/snapshots
```

## Tests

```bash
cd backend
python3 -m pytest

cd ../dashboard
npm run lint
npm run typecheck
npm run build
```

Astryx component reference:

```bash
cd dashboard
npm run astryx -- component --list --detail brief
```

## Safety Principles

- V0.4 is read-only for exchange integrations.
- Bitget API keys must be read-only and provided through environment variables.
- No automatic buy or sell execution is included.
- `record-exit` only writes an internal trade review record. It does not submit an exchange order.
- Scores are deterministic. LLM usage, when added later, must only transform computed JSON into natural language.
- Position insight output explains a fixed score snapshot. It does not recalculate price, score, or order intent.

## Design Notes

- Dashboard implementation uses Astryx packages for accessible shell, navigation, command palette, badges, status dots, tables, keyboard hints, and theme structure.
- The visual direction is terminal-style information density and multi-panel scanning. It does not copy Bloomberg Terminal branding, proprietary screens, logo, colors, text, or layout.
- Keyboard workflow: `Cmd/Ctrl+K` or `/` opens the command palette. `G` then `P` routes to Live Positions, `G` then `T` routes to Trade History, and `G` then `,` routes to Settings.

See:

- `docs/UXUIDesignSystem.md`
- `docs/AstryxIntegration.md`
- `docs/TerminalUX.md`
