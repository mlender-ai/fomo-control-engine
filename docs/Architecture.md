# Architecture

FOMO Control Engine은 자동매매 봇이 아니라 read-only 포지션 관제와 진입 전 스카우트 판단을 기록하는 로컬 엔진이다. 주문 실행 경로는 없다.

## Runtime Flow

```text
Exchange / Market Data
  -> Providers
  -> Indicator / Structure / Derivatives Engines
  -> Position / Scout / Review Services
  -> Repository
  -> FastAPI Routers / Worker / Telegram Bot
  -> Next.js Dashboard
```

## Backend Module Map

### API Layer

라우터는 요청 파싱, 서비스/핸들러 호출, 응답 직렬화만 담당한다. 판단 계산, 점수 계산, 알림 판정, DB 보존 정책은 라우터에 두지 않는다.

- `app/api/router_system.py`: health, system status, Bitget connection test
- `app/api/router_positions.py`: positions, live positions, snapshots, insights, exits
- `app/api/router_scout.py`: symbols, watchlist, scout scan, setup arming, simulator, scenarios
- `app/api/router_review.py`: trades, journal timeline, calibration, parameter suggestions
- `app/api/router_marketdata.py`: reports, market summary, research runs, liquidity/shadow/validation/memory legacy surfaces
- `app/api/deps.py`: shared runtime replacement hook for tests and local dependency swapping

### Service Layer

- `app/services/http_handlers.py`: legacy HTTP handler logic retained behind split routers. New work should migrate domain logic out of this file into focused services instead of growing it.
- `app/services/scout_handlers.py`: pre-entry scout handler logic behind `router_scout.py`.
- `app/services/runtime.py`: worker/bot-facing service facade. Worker, bot, and routes must share service functions instead of duplicating judgment logic.

### Domain Engines

- `app/exchange`: provider boundary and Bitget read-only integration
- `app/marketdata`: derivative providers and market data signal helpers
- `app/indicators`: RSI, MACD, Bollinger Bands, ATR, RVOL
- `app/structure/levels`: structural support/resistance level engine
- `app/structure/liquidity`: liquidity pools, sweep detection, BOS/CHoCH-style structure shifts, Wyckoff cross-checks
- `app/structure/wyckoff`: deterministic Wyckoff event/phase interpretation
- `app/structure/harmonic`: ZigZag and harmonic PRZ detection
- `app/positions`: health score, position state, action plans, chart analysis, insights, simulator
- `app/scout`: setup arming, setup alert candidates, pre-entry setup scoring
- `app/notify`: alert rules, Telegram sender, interactive bot, notification settings
- `app/review`: trade review, judgment scoring, calibration, alert-response review
- `app/derivatives`: derivatives API surface and context assembly
- `app/db`: repository, migrations, backup, retention
- `app/worker`: APScheduler jobs, heartbeat, daemon lifecycle

### Legacy / Auxiliary Domains

These modules are still imported or tested, so they are not archived in WO-FCE-25:

| Module | Currently Imported | Latest Role Through Phase C | WO-FCE-25 Decision |
|---|---:|---|---|
| `app/agents` | yes | deterministic research-run checklist and historical research run surface | keep, but do not call it “LLM agent” unless upgraded |
| `app/shadow` | yes | shadow profile extraction/comparison APIs and tests | keep |
| `app/validation` | yes | Monte Carlo, Bootstrap Sharpe CI, Walk Forward APIs and tests | keep |
| `app/memory` | yes | decision memory records from trades/shadow/validation | keep |
| `app/liquidity` | yes | report liquidity scoring and liquidation analysis endpoint | keep |

Archive rule: move a module to `_archive/` only after a separate approval that includes import status, product role, and replacement path.

## Layering Rules

1. `api/*` imports `services/*` or handler modules.
2. `services/*` may import `db`, `exchange`, `positions`, `scout`, `review`, `notify`, and domain engines.
3. Domain engines must not import `api`.
4. Worker and Telegram bot call `services/runtime.py`, not route modules.
5. URL paths and response schemas are API contracts. Router split work must pass tests without frontend changes.
6. New feature placement:
   - Position state or live cockpit logic: `app/positions` + `router_positions.py`
   - Pre-entry setup/scout logic: `app/scout` + `services/scout_handlers.py` + `router_scout.py`
   - Alerts/Telegram: `app/notify`
   - Calibration/review: `app/review` + `router_review.py`
   - Derivatives/OI/funding/liquidation data: `app/marketdata`, `app/derivatives`

## Import Cycle Guard

Run this before merging backend structural changes:

```bash
cd backend
python3 scripts/check_import_cycles.py
```

The script parses `app/` imports and fails if an internal import cycle is introduced.

## Bitget Integration Rule

The application depends on `MarketDataProvider`, not on Bitget directly. Choose the provider with `FCE_MARKET_DATA_PROVIDER`.

- `mock`: deterministic local snapshots for tests and UI development
- `bitget`: public/read-only futures market data and read-only position sync

Bitget endpoints used include public candles/ticker/funding/OI plus read-only position endpoints. Order placement is intentionally absent.

## Persistence

Default persistence is SQLite:

```text
FCE_DATABASE_URL=sqlite:///./fomo_control_engine.db
```

Schema changes must go through `app/db/migrations/`. Runtime maintenance is handled by backup and retention jobs in `app/db/maintenance.py`.

## Read Projection Latency Rule

- Dashboard `GET` projections must use repository state already refreshed by the worker when that state is sufficient for display. They must not repeat full external market snapshots per row.
- Optional projections such as public whale observations load independently. Their latency or failure must not block the core position or paper-engine workspace.
- Fresh external collection remains an explicit refresh/worker responsibility; read projections do not mutate trading state or place orders.
