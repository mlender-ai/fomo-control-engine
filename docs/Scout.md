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

`/scout SYMBOL`의 영속 상태는 `watchlist`가 정본이다. 아직 활성 진입 의도나 무장 셋업이
없더라도 웹과 Telegram 정기 펄스에는 `추적 조건 확인 중`으로 표시한다. 같은 심볼에 수동
추적과 엔진 셋업이 함께 있으면 상세 화면에서는 출처를 구분할 수 있지만 정기 펄스에서는
심볼당 한 번만 표시한다.

### Judgment ledger pilot

Candidate snapshots use the generic `entity_type` (`crypto`, `stock_kr`, `stock_us`) and preserve source, signal evidence, timestamp, and price. Outcomes are recorded independently at T+1, T+5, and T+20. The UI marks every aggregate with `N<30` as `표본 부족`.

### Local activation and external validation

Set the credential and watchlist variables in `backend/.env`, register the machine's public IP in Toss WTS, then opt in with `FCE_TOSS_STOCK_SCOUT_ENABLED=true`. Do not put credentials in the repository or GitHub Actions.

Real-data acceptance evidence (one-hour 429 recovery log, three live attention-gap candidates, screenshots, and a T+1 outcome cycle) can only be produced after those local values are present. Until then the API and UI return `credentials_required` with empty groups; they never substitute demo candidates.

## Bitget stock perpetual ↔ Toss underlying join (WO-FCE-BITGET-TOSS-MAP-01)

This join is not a union of the Bitget crypto universe and the Toss stock universe. It connects only a stock-underlying Bitget perpetual that is already present in the user's open positions or watchlist to the same verified Toss underlying. Bitget remains the execution and live-price source; Toss supplies read-only underlying candles, levels, and session context.

### Eligibility and identity gate

- The target universe is rebuilt from current Bitget open positions and the explicit watchlist. The full Bitget futures catalogue is never scanned through Toss.
- A contract is eligible only when the Bitget catalogue marks it as both `bitget_rwa` and `isRwa=YES`. BTC, ETH, HYPE, and every other pure crypto symbol are excluded before a Toss client can be constructed.
- A version-controlled identity record supplies the expected official name, listing exchange, and asset type. All three fields must match normalized Toss metadata. A ticker-string match alone has no authority.
- An identity match creates a `pending` candidate, not an active join. A mismatch becomes `rejected`; an ambiguous or unknown identity stays pending without join access. Only an explicit user approval changes a matching pending candidate to `verified`.
- Leveraged ETFs remain one underlying instrument. The leverage factor is displayed as context and is not used to scale prices or signals.

The stock scout page remains the full mapping management surface. The live-position cockpit also shows the selected contract's mapping state and allows an identity-matched pending candidate to be manually approved in context. Both surfaces show the Bitget contract and Toss identity evidence side by side; neither can infer verification from a prior ticker match. Rejected candidates can be regenerated only after the canonical identity or source metadata is corrected.

### Read-only chart decoration

For a verified map, the current Bitget mark is always the price of record. Toss daily OHLC is retained in raw form and also aligned to the Bitget mark by the contemporaneous basis ratio so its structural levels can share the existing chart scale. The chart exposes both source prices, the basis ratio and timestamp, Toss session freshness, security warnings, and any leverage warning. Toss does not provide US investor-category flow, so that signal is explicitly marked unavailable instead of being inferred from another source.

This decoration occurs after the existing Bitget analysis and gauges have been computed. It does not mutate cached analysis, Entry Score, paper-engine decisions, candidate promotion, or any order path. A pending/rejected map, missing Toss data, or a join error leaves the original Bitget chart intact.

### Live-position source hierarchy (WO-FCE-BITGET-TOSS-MAP-02)

The position cockpit is source-aware. For a stock-underlying perpetual, the summary panel names Bitget as the execution and live-price source and Toss as the read-only chart and structure source. It exposes the verification state, both source prices, basis, underlying session freshness, leverage context, and the explicit absence of US investor-category flow. A pending identity match can be approved from this panel and the chart is reloaded immediately.

Crypto whale telemetry is shown only for pure crypto contracts. It is not a substitute for equity-underlying ownership or flow data, so NBIS, SOXL, MSTR, and other eligible stock perpetuals never display the crypto whale banner or its position-card label.
