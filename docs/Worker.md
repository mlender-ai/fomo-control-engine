# Background Worker

WO-FCE-16 moves live position observation from the browser polling loop into the FastAPI process. The dashboard is now a display surface; state production happens server-side while the backend is running.

## Runtime

- Scheduler: APScheduler `AsyncIOScheduler`
- Process model: same FastAPI process, no separate daemon
- Control: `FCE_WORKER_ENABLED=true`
- Heartbeat API: `GET /api/system/worker`
- Log file: `logs/worker.log` with 10MB x 5 rotation

Each job uses a shared wrapper:

- one lock per job; a tick is skipped if the previous tick is still running
- exceptions are isolated per job
- after 3 consecutive failures the interval doubles, capped by `FCE_WORKER_BACKOFF_MAX_MULTIPLIER`
- a successful run restores the configured interval
- heartbeat is persisted in SQLite `worker_heartbeat`

## Jobs

| Job | Default | Purpose |
|---|---:|---|
| `sync_positions` | 90s | Bitget read-only position sync, snapshot storage, health/status recalculation |
| `refresh_market_data` | 300s | Refresh candles and report cache for currently held symbols |
| `collect_derivatives` | 300s | Collect Bitget public OI, funding, taker long/short ratio, and optional Coinglass Tier 2 data for held/watchlist symbols |
| `regen_stale_insights` | 120s | Regenerate stale insights when existing staleness rules fire and min interval allows |
| `database_retention` | 04:00 daily in `FCE_DB_MAINTENANCE_TIMEZONE` | Retention cleanup and closed-position snapshot downsampling |
| `database_backup` | 04:30 daily in `FCE_DB_MAINTENANCE_TIMEZONE` | Online SQLite gzip backup and restore smoke check |
| `detect_closures` | sync hook | Expose closure bookkeeping status after sync without duplicating route logic |
| `evaluate_alerts` | sync hook | Alert hook for WO-FCE-17 |
| `daily_summary` | sync hook | Daily Telegram summary check |
| `scout_scan` | 900s, on | Watchlist scan, scout snapshot storage, automatic setup arming, setup alert evaluation |
| `refresh_calibration_cache` | 1800s | Build read-only calibration, weekly, and performance view caches. HTTP GET never generates suggestions or applies autonomy. |
| `refresh_symbol_catalog` | 86400s | Refresh the Bitget symbol catalog at startup and daily; failures are exposed through heartbeat and `/api/symbols`. |
| `telegram_bot` | long polling task | Interactive Telegram bot supervisor |

## SQLite Concurrency

SQLite connections are configured with:

- `PRAGMA journal_mode=WAL`
- `PRAGMA busy_timeout=5000`
- `PRAGMA synchronous=NORMAL`

Repository writes and heartbeat writes share a process-wide lock to avoid worker/API write contention in the local single-user app.

## Database Maintenance

Phase B adds file-based migrations, daily SQLite backups, and retention cleanup. Daily maintenance uses `FCE_DB_MAINTENANCE_TIMEZONE`, defaulting to `Asia/Seoul`.

- Migrations run from `backend/app/db/migrations/*.sql` during SQLite repository initialization.
- Backups are enabled with `FCE_DB_BACKUP_ENABLED=true` and written to `FCE_DB_BACKUP_DIR` as `fce_YYYYMMDD.db.gz`.
- Backups are kept for `FCE_DB_BACKUP_KEEP_DAYS` days and smoke-tested by opening a restored temp DB.
- Retention keeps derivative snapshots for `FCE_DB_RETENTION_DAYS` days.
- Closed-position snapshots older than `FCE_DB_CLOSED_SNAPSHOT_RETENTION_DAYS` are downsampled to `FCE_DB_SNAPSHOT_DOWNSAMPLE_MINUTES`; open positions are not downsampled.
- Trade fills are retained for `FCE_DB_TRADE_FILL_RETENTION_DAYS` (default 2 days, matching the 24-48h flow window); alert logs for `FCE_DB_ALERT_RETENTION_DAYS`; worker heartbeat rows for `FCE_DB_WORKER_HEARTBEAT_RETENTION_DAYS`.
- Judgment ledger, judgment scores, scenarios, reviews, and trades are permanent and are not touched by retention.
- Maintenance events are visible at `GET /api/system/database`.

## Derivative Data

Tier 1 is Bitget public data and needs no API key:

- Open interest: `/api/v2/mix/market/open-interest`
- Funding: `/api/v2/mix/market/current-fund-rate`
- Account long/short ratio: `/api/v2/mix/market/account-long-short`

Tier 2 Coinglass is represented as a locked provider unless `FCE_COINGLASS_API_KEY` is configured. No liquidation cluster values are fabricated when the key is missing.
See `docs/Derivatives.md` for signal formulas, storage tables, and Coinglass rate-budget math.

Read current flow with:

```text
GET /api/derivatives/{symbol}
POST /api/derivatives/refresh
```

## Scout Automation

WO-FCE-24 turns the scout from pull-only to push monitoring.

- `FCE_WORKER_SCOUT_SCAN_ENABLED=true` by default.
- Default interval: `FCE_WORKER_SCOUT_SCAN_INTERVAL_SECONDS=900`.
- Watchlist budget: max `FCE_SCOUT_WATCHLIST_SYMBOL_LIMIT=30` symbols per tick.
- Bitget request budget formula: `symbols × (candles 1 + ticker 1 + derivatives 1) / 15m`.
- If the watchlist exceeds 30 symbols, the scan result reports `round_robin_required=true`; the UI must show this as a warning rather than silently over-scanning.

Automatic setup arming is deterministic and can be disabled with `FCE_SCOUT_AUTO_ARM_ENABLED=false`.

- Harmonic PRZ: confidence >= 70 and distance <= 3%.
- Structure level: score >= 70 and distance <= 2%.
- Wyckoff Spring/UTAD/SOS/SOW style event: confidence >= 70.
- Crowding + level confluence: crowding score >= 80 and qualified level proximity.

Setup alerts reuse the WO-FCE-17 state machine:

- `setup_near`: armed setup distance <= 1.5%.
- `setup_triggered`: trigger price touched or event confirmed.
- `setup_invalidated`: setup premise broken before usable trigger.

Setup messages always use preview and review language. They never say to enter a trade.

## Bitget Read-Only Request Budget

The worker never calls order endpoints.

With one open symbol and default intervals:

- `sync_positions`: private read-only positions once every 90s, plus market analysis for each open position
- `refresh_market_data`: public OHLCV/report refresh once every 300s per open symbol
- `collect_derivatives`: public OI, funding, long/short ratio, ticker, and optional Coinglass requests once every 300s per tracked symbol
- `regen_stale_insights`: checks every 120s and only regenerates when stale; LLM regeneration keeps the 10-minute per-symbol guard

Approximate baseline for `N` open symbols:

```text
private position reads: 40/hour
public market snapshots from sync: 40 * N/hour
public market snapshots from cache refresh: 12 * N/hour
public derivative reads: 48 * N/hour
Coinglass Tier 2 reads: up to floor((30 req/min * 5 min) / 6 req per symbol) = 25 symbols per tick by default
stale insight checks: 30 * N/hour, but regeneration is rate-limited
```

Bitget provider-level backoff and cache TTLs still apply. If Bitget rate limits a job, the worker wrapper records the failure and backs off after repeated failures.
