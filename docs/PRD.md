# FOMO Control Engine PRD

## Product Definition

FOMO Control Engine is a personal trading decision engine. It helps the user decide whether a planned entry is supported by data, then records entry, monitoring, exit, and review decisions.

## V0.2 Goals

- Generate reports for BTCUSDT, ETHUSDT, SOLUSDT, and other major tickers.
- Calculate Entry Opportunity Score from deterministic sub-scores.
- Show score breakdown and plain Korean report in a dashboard.
- Let the user save manual entries.
- Monitor open positions against fresh reports.
- Save exits and generate review text.
- Persist reports, positions, monitoring logs, and completed trades.
- Allow mock/live Bitget market data switching.

## Non-Goals

- No automatic trading.
- No order execution.
- No guaranteed signal language.
- No machine learning prediction in v0.1.
- No automatic trading in v0.2.

## Success Criteria

- User checks a report before entry.
- Position state is monitored objectively.
- Exit reasons and review notes are stored.
- Repeated mistakes become visible through trade history.
