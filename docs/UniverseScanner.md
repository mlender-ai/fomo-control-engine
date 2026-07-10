# Universe Scanner

WO-FCE-35 scans symbols outside the user's current watchlist, open positions, and active entry intents. It is a discovery channel, not a recommendation channel.

## Universe

- Crypto: Bitget USDT-M catalog, up to `FCE_UNIVERSE_CRYPTO_SYMBOL_LIMIT` symbols.
- Stock/index puffs: Bitget catalog symbols tagged `stock` or `index`, up to `FCE_UNIVERSE_STOCK_SYMBOL_LIMIT`.
- Excluded: open positions, watchlist symbols, armed setups, active entry intents, `FCE_UNIVERSE_BLACKLIST`.

### 큐레이션 허용 리스트 (2026-07-10)

거래량 순위만으로는 마이크로캡 잡주·잡코인이 발견 채널에 올라온다. base ticker 허용 리스트로 유니버스를 큐레이션한다 (리스트 안에서만 거래량 랭킹·라운드로빈):

- `FCE_UNIVERSE_CRYPTO_ALLOWLIST` — 기본: 시총 10위권(스테이블 제외) `BTC,ETH,XRP,BNB,SOL,DOGE,ADA,TRX,LINK,HYPE`. **정적 스냅샷** — 시총 순위 변동 시 env로 갱신.
- `FCE_UNIVERSE_STOCK_ALLOWLIST` — 기본: 미국 상장 시총 상위(메가캡 ~50) + 최근 핫한 기업(AI 인프라·퀀텀·우주·원전·크립토 프록시 ~15). 카탈로그에 없는 티커는 자연 무시되므로 넉넉히 적어도 안전.
- 빈 문자열이면 해당 클래스 큐레이션 비활성(전체 카탈로그). 지수(index)는 6종 전부 메이저라 허용 리스트 없음.
- 리스트 밖 심볼은 `excluded[].reason = "not_in_allowlist"`로 기록된다.

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
