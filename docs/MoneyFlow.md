# Money Flow 2.0

WO-FCE-69 compares spot and futures taker flow on the same confirmed-candle timeline. It is an observation layer, not an entry or exit instruction.

## Sources

### Tier 1: Bitget public data

- Spot fills: `GET /api/v2/spot/market/fills-history`
- Futures fills: `GET /api/v2/mix/market/fills-history`
- Open interest and funding: the existing Bitget derivatives collector

Buy and sell volume use the public fill `side` field. CVD is the cumulative sum of `buy_volume - sell_volume` in candle buckets. A perpetual symbol is mapped to the same Bitget spot symbol. Symbols without a spot market are returned as unavailable; no synthetic spot flow is generated.

Every Tier 1 result is labeled **Bitget single-exchange proxy**. It is not an all-market aggregate.

### Tier 2: Coinglass

When `FCE_COINGLASS_API_KEY` is configured, optional feature probes request aggregated spot/futures taker volume. If both are available, their CVD replaces the Bitget CVD while price and OI context continue to use the available market snapshot. BTC and ETH additionally probe put/call ratio and options OI. Other symbols are explicitly unsupported.

An authentication, plan, or endpoint error locks only that feature. It does not disable Tier 1 collection.

## Window And Direction

The current observation uses up to the most recent 24 confirmed 4-hour candle buckets (96 hours). Missing historical fills remain missing and are never synthesized. Direction thresholds are not fixed constants. For each input, the engine calculates the 40th percentile of the absolute values observed during the latest 30 days:

```text
threshold(field) = percentile_40(abs(field[latest 30 days]))

direction(value) =
  up    when value >= threshold
  down  when value <= -threshold
  flat  otherwise
```

At least 10 complete stored observations are required. Before that, the result is `mixed` with `provisional=true` and the UI says that distribution samples are accumulating.

Evidence confidence is calculated rather than assigned. Each field required by the selected state contributes `min(abs(value) / p40_threshold, 1)`; a required flat field contributes `1 - min(abs(value) / p40_threshold, 1)`. The mean component significance is stored on a 0-100 scale.

## States

| State | Deterministic condition | UI label |
|---|---|---|
| `spot_led` | price up and spot CVD up | 현물 유입 동반 상승 |
| `futures_led` | price up, futures CVD up, spot CVD down/flat, and OI up | 선물 단독 견인 - 레버리지 상승 경계 |
| `spot_absorb` | price down/flat and spot CVD up | 하락 구간 현물 매집 관찰 |
| `delever` | price down and OI down | 레버리지 청산 진행 |
| `mixed` | no rule matches | 혼조 - 판정 유보 |

Only confirmed observations are promoted to alerts and evidence. `futures_led` does not assert a fake rebound. It records that leverage led the rise while spot confirmation was absent.

## Consumers

- Position and scout compact workspaces show one money-flow card with two CVD sparklines, source, and basis time.
- The professional evidence rail shows the same card and source.
- The one-line derivatives module uses the state label.
- Analyst confluence includes `futures_led` as counter-evidence against a bullish stance.
- `flow_divergence` alerts fire once on entry into `futures_led` and re-arm only after the state exits.
- `futures_led_rally` is registered as a candidate signature and written to the judgment ledger. It remains a candidate until forward samples are sufficient; no historical win rate is fabricated from unavailable spot-fill history.

## Rate Budget

The six core Coinglass requests plus up to four optional money-flow/options probes require a worst-case budget of 10 requests per symbol. Optional unsupported probes do not consume the local request counter.
