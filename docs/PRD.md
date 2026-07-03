# FOMO Control Engine PRD

## Product Definition

FOMO Control Engine is a personal trading decision engine. It helps the user decide whether a planned entry is supported by data, then records entry, monitoring, exit, and review decisions.

## V1 Goals

- Generate reports for BTCUSDT, ETHUSDT, SOLUSDT, and other major tickers.
- Calculate Entry Opportunity Score from deterministic sub-scores.
- Show score breakdown and plain Korean report in a dashboard.
- Let the user save manual entries.
- Monitor open positions against fresh reports.
- Save exits and generate review text.

## Non-Goals

- No automatic trading.
- No order execution.
- No guaranteed signal language.
- No machine learning prediction in v0.1.

## Success Criteria

- User checks a report before entry.
- Position state is monitored objectively.
- Exit reasons and review notes are stored.
- Repeated mistakes become visible through trade history.

