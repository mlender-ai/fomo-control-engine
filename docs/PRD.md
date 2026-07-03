# FOMO Control Engine PRD

## Product Definition

FOMO Control Engine is a personal trading decision engine. It helps the user decide whether a planned entry is supported by data, then records entry, monitoring, exit, and review decisions.

## V0.4 Goals

- Generate reports for BTCUSDT, ETHUSDT, SOLUSDT, and other major tickers.
- Calculate Entry Opportunity Score from deterministic sub-scores.
- Show score breakdown and plain Korean report in a dashboard.
- Let the user save manual entries.
- Monitor open positions against fresh reports.
- Save exits and generate review text.
- Persist reports, positions, monitoring logs, and completed trades.
- Allow mock/live Bitget market data switching.
- Read actual Bitget futures positions through private read-only API.
- Sync actual exchange positions into internal position tracking without closing them automatically.
- Show API provider, public API, private API, and data quality state in the dashboard.
- Run a deterministic multi-agent research review over one fixed market snapshot.
- Store each research run, agent output, final label, and report tree.
- Extract a Shadow Account profile from completed trades when the sample is large enough.
- Compare actual trades against Shadow rules to expose FOMO losses and noise trades.
- Analyze liquidation cluster proxies without turning them into an automatic entry signal.
- Run basic validation checks with Monte Carlo, Bootstrap Sharpe CI, and Walk Forward windows.
- Surface Research Runs, Decision Memory, Shadow Journal, and Validation Lab in the dashboard.

## Non-Goals

- No automatic trading.
- No order execution.
- No guaranteed signal language.
- No machine learning prediction in v0.1.
- No automatic trading in v0.4.
- No order, close-position, batch-order, trigger-order, or withdrawal endpoint integration.
- No autonomous execution agent.
- No news, social, on-chain, harmonic, Elliott wave, or complex backtesting scope in v0.4.

## Success Criteria

- User checks a report before entry.
- Position state is monitored objectively.
- Exit reasons and review notes are stored.
- Repeated mistakes become visible through trade history.
- Research runs remain reproducible from stored snapshot and score JSON.
- Shadow extraction refuses insufficient samples instead of inventing behavioral rules.
- Validation warnings clearly state data and sample limitations.
