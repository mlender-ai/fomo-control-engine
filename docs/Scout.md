# Scout data sources

## Toss Securities stock observation (WO-FCE-TOSS-SCOUT-01)

The KR/US stock surfaces are read-only observations. They do not share an execution path with Bitget, Entry Score, candidate promotion, or the paper engine. The UI must always show `Toss 데이터 · 주문 실행 없음` and describe results as observations rather than recommendations.

The adapter follows the server-owned Toss OpenAPI 1.2.4 document at `https://openapi.tossinvest.com/openapi-docs/latest/openapi.json`. Token issuance uses form-encoded `grant_type`, `client_id`, and `client_secret`; market calls use only the bearer token.

### Safety boundary

- Credentials exist only in local environment variables: `FCE_TOSS_CLIENT_ID`, `FCE_TOSS_CLIENT_SECRET`.
- Collection is opt-in with `FCE_TOSS_STOCK_SCOUT_ENABLED=false` by default. CI therefore never contacts Toss.
- `TossReadOnlyClient` rejects every path outside its compiled market-data whitelist before HTTP dispatch. It has no account, asset, order, order-info, or conditional-order method.
- A 401 refreshes credentials once; token issuance and market-call 429/5xx paths retry with bounded exponential jitter; edge blocking reports an allowed-IP action; server maintenance pauses collection for 15 minutes per market.
- Raw observations and collection timestamps are stored separately from resampled candles and judgment outcomes. Missing data remains missing.

### Independent signals

Candidates are grouped by signal rather than collapsed into one score:

1. `attention_gap`: high market trading-value rank and lower Toss execution rank observed together.
2. `retail_overheat`: Toss execution attention rises without the market rank following.
3. `investor_flow`: index investor flow and symbol activity are simultaneous observations, never a causal claim.
4. `momentum`, `orderbook_change`, and price-limit proximity remain separate evidence.

Liquidation and investment-risk warnings exclude a candidate. Investment warnings, short-term overheating, and VI remain visible badges.

### Polling TPS budget

The configured universe is capped at 400 symbols and prices are batched by 200.

| Group | Limit | Scheduled peak | Utilization |
|---|---:|---:|---:|
| MARKET_DATA | 10 TPS | prices 2/10s + candidate orderbook/trades/limits 60/15s = 4.20 TPS | 42.0% |
| MARKET_DATA_CHART | 5 TPS | 20 candidates/15s = 1.33 TPS | 26.7% |
| STOCK | 5 TPS | promotion checks plus daily refresh, bounded below 2.0 TPS | <40% |
| RANKING | 5 TPS | 6 calls/60s = 0.10 TPS | 2.0% |
| MARKET_INDICATOR | 5 TPS | 2 calls/300s = 0.007 TPS | 0.1% |
| MARKET_INFO | 3 TPS | KR/US calendars once per minute = 0.033 TPS | 1.1% |

The adapter's token buckets remain the final enforcement boundary even if a caller changes scheduling.

The local worker runs a cheap session/price tick every 10 seconds. Ranking families refresh at most once per 60 seconds, KOSPI/KOSDAQ investor observations at most once per five minutes, and candidate orderbook/trade/limit/candle evidence at most once per 15 seconds. Warning checks are cached for one day. Price and stock metadata use 200-symbol batches, and 1-minute candidate candles are persisted before 5m/15m/1h/4h resampling. KR may expand from the six ranking families; US remains limited to the manual watchlist.

### Judgment ledger pilot

Candidate snapshots use the generic `entity_type` (`crypto`, `stock_kr`, `stock_us`) and preserve source, signal evidence, timestamp, and price. Outcomes are recorded independently at T+1, T+5, and T+20. The UI marks every aggregate with `N<30` as `표본 부족`.

### Local activation and external validation

Set the credential and watchlist variables in `backend/.env`, register the machine's public IP in Toss WTS, then opt in with `FCE_TOSS_STOCK_SCOUT_ENABLED=true`. Do not put credentials in the repository or GitHub Actions.

Real-data acceptance evidence (one-hour 429 recovery log, three live attention-gap candidates, screenshots, and a T+1 outcome cycle) can only be produced after those local values are present. Until then the API and UI return `credentials_required` with empty groups; they never substitute demo candidates.
