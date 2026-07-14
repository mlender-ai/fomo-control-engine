# Hyperliquid whale observation

This integration is read-only. It calls only Hyperliquid's unauthenticated `POST /info` endpoint and never signs or submits an order.

## Collection budget

- Maximum active wallets: 20.
- Default poll interval: 120 seconds.
- `clearinghouseState`: weight 2 per wallet.
- `userFillsByTime`: base weight 20 per wallet plus the official per-item response weight.
- With 20 wallets and no large fill response, the estimate is 220 weight/minute against the official 1,200 weight/minute IP budget.
- Fill polling starts at the wallet's last stored fill timestamp. First registration uses a bounded seven-day lookback. Worker failures use the common exponential backoff.

The relevant settings are `FCE_HYPERLIQUID_WHALE_TRACKING_ENABLED`, `FCE_HYPERLIQUID_WHALE_POLL_INTERVAL_SECONDS`, `FCE_HYPERLIQUID_WHALE_MIN_SIZE_USD`, and `FCE_HYPERLIQUID_WHALE_MAX_WALLETS`.

## Data and decision boundaries

- Events are derived only after a fill appears in `userFillsByTime`.
- Chart markers are anchored to a closed FCE candle and never moved to a later candle.
- Coins that cannot map to a plain FCE `*USDT` symbol are stored but not rendered on an FCE chart.
- Every wallet starts as a candidate. Its events are observation data, not a follow signal.
- Only a promotion approved through the existing veto-window flow can make a wallet `validated`.
- Whale promotion requires `N >= 30` and a net 1R confidence-interval lower bound of at least 55%.
- Only validated wallets may contribute a low-weight onchain item to confluence, use an emphasized chart marker, or emit a validated warning alert.
- Labels are user-provided aliases and never claim verified ownership or identity.

Official API references: Hyperliquid info endpoint and rate-limit documentation.
