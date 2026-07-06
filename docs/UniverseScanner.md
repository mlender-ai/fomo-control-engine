# Universe Scanner

WO-FCE-35 scans symbols outside the user's current watchlist, open positions, and active entry intents. It is a discovery channel, not a recommendation channel.

## Universe

- Crypto: Bitget USDT-M catalog, up to `FCE_UNIVERSE_CRYPTO_SYMBOL_LIMIT` symbols.
- Stock/index puffs: Bitget catalog symbols tagged `stock` or `index`, up to `FCE_UNIVERSE_STOCK_SYMBOL_LIMIT`.
- Excluded: open positions, watchlist symbols, armed setups, active entry intents, `FCE_UNIVERSE_BLACKLIST`.

## Rate Budget

Default interval: `FCE_WORKER_UNIVERSE_SCAN_INTERVAL_SECONDS=1800`.

Default batch: `FCE_UNIVERSE_ROUND_ROBIN_BATCH_SIZE=12`.

Estimated public requests per symbol:

```text
candles 1 + market snapshot 1 + derivatives/context 1 = 3 requests
```

Default per tick:

```text
12 symbols * 3 requests = 36 requests / 30 minutes
```

If the universe has more symbols than the batch size, the worker uses round-robin. For 80 symbols at the default batch size, full coverage is roughly 7 ticks or 210 minutes. Increase N only with this budget updated.

## Alert Gate

A discovery alert fires only when all quality gates pass:

- signal confidence >= `FCE_UNIVERSE_MIN_CONFIDENCE` (default 70)
- class-level backtest sample >= `FCE_UNIVERSE_BACKTEST_MIN_SAMPLE` (default 30)
- class-level win@1R >= `FCE_UNIVERSE_BACKTEST_MIN_WIN_1R_PCT` (default 55%)
- no live/backtest divergence flag
- 24h quote volume >= `FCE_UNIVERSE_MIN_QUOTE_VOLUME_24H`
- stock/index symbols are not blocked by an earnings window

Throttle gates:

- daily alert cap: `FCE_UNIVERSE_DAILY_ALERT_LIMIT` (default 3)
- per-symbol cooldown: `FCE_UNIVERSE_SYMBOL_COOLDOWN_HOURS` (default 48)

Quality-passing discoveries blocked by throttles are stored in the Scout discovery panel but not sent.

## Language Guard

Messages use "발견" or "포착". They do not use recommendation language or order instructions.
