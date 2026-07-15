# Hyperliquid whale observation

This integration is read-only. It calls only Hyperliquid's unauthenticated `POST /info` endpoint and never signs or submits an order.

## Automatic discovery

- The worker reads the public dataset used by `https://app.hyperliquid.xyz/leaderboard` once per hour.
- It scans the full leaderboard and fills the watchlist automatically. Manual and bot-added wallets are preserved and consume slots before discovery wallets.
- Default discovery hygiene requires a 30-day profit of at least 100,000 USDT, ROI of at least 2%, account value of at least 1,000,000 USDT, and volume of at least 10,000,000 USDT.
- Monthly turnover above 250 times account value is excluded to reduce market-maker and high-frequency flow contamination.
- Discovery wallets that leave the selected set are deactivated. Their events, judgments, and candidate statistics are retained.
- The engine dashboard exposes current long/short notional, 24-hour signed flow, a 72-hour two-hour-bucket histogram, symbol exposure, and the latest large fills.

Manual `/whale add` registration remains available only as an override for a known public master or sub-account address. No API key, agent key, private key, or transaction hash is accepted as tracking input.

## Collection budget

- Maximum active wallets: 20.
- Default poll interval: 120 seconds.
- `clearinghouseState`: weight 2 per wallet.
- `userFillsByTime`: base weight 20 per wallet plus the official per-item response weight.
- With 20 wallets and no large fill response, the estimate is 220 weight/minute against the official 1,200 weight/minute IP budget.
- Fill polling starts at the wallet's last stored fill timestamp. First registration uses a bounded seven-day lookback. Worker failures use the common exponential backoff.

The relevant settings are `FCE_HYPERLIQUID_WHALE_TRACKING_ENABLED`, `FCE_HYPERLIQUID_WHALE_DISCOVERY_ENABLED`, `FCE_HYPERLIQUID_WHALE_DISCOVERY_INTERVAL_SECONDS`, `FCE_HYPERLIQUID_WHALE_POLL_INTERVAL_SECONDS`, `FCE_HYPERLIQUID_WHALE_MIN_SIZE_USD`, and `FCE_HYPERLIQUID_WHALE_MAX_WALLETS`.

## Data and decision boundaries

- Events are derived only after a fill appears in `userFillsByTime`.
- Chart markers are anchored to a closed FCE candle and never moved to a later candle.
- Coins that cannot map to a plain FCE `*USDT` symbol are stored but not rendered on an FCE chart.
- Every wallet starts as a candidate. Its events are observation data, not a follow signal.
- Only a promotion approved through the existing veto-window flow can make a wallet `validated`.
- Whale promotion requires all three gates: at least 28 elapsed validation days, `N >= 30`, and a net 1R confidence-interval lower bound of at least 55%.
- Leaderboard 30-day ROI/PnL is a discovery input, not validation evidence. FCE separately publishes follow-trade win rate, confidence interval, cumulative realized R, average R, and the 28-day progress for each wallet.
- The dashboard filters wallets into validating, review-ready, trusted, and excluded groups. A high leaderboard rank alone never enters the trusted group.
- Only validated wallets may contribute a low-weight onchain item to confluence, use an emphasized chart marker, or emit a validated warning alert.
- Labels are user-provided aliases and never claim verified ownership or identity.

Official API references: Hyperliquid info endpoint and rate-limit documentation.
