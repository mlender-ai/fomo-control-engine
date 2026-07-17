# Paper Trading Policy

The paper engine is a deterministic benchmark. It never calls an exchange order endpoint and never changes the read-only safety boundary.

## Time axis

Every decision is evaluated once per confirmed candle. The engine stores the last evaluated candle by symbol and timeframe. Repeated worker ticks on the same candle are no-ops. Trigger fills use the confirmed trigger candle close; candle highs and lows may only establish that a take-profit level was touched.

## Entry

All gates must pass:

1. A confirmed directional stance flip is present, the stance is not transitioning, and at least 4 same-direction evidence items exist.
2. At least 5 of 6 simulator checklist items pass. Risk/reward is at least 1.5 and invalidation occurs before estimated liquidation.
3. At least one active same-direction signature is `validated` and its current published regime/statistical scope has a net win-at-1R confidence-interval lower bound of at least 50%.
4. Market data is fresh. Stock and index entries additionally require an explicit earnings-calendar result outside D-1 through D+1; unavailable earnings data fails closed.
5. Fewer than 5 paper positions are open.

Each trade uses 100 USDT margin and 3x leverage. These values are configurable but identical for every new paper trade.

## Exit priority

The first matching rule wins:

1. Confirmed close breaches invalidation, or the breakeven stop after partial profit.
2. First take-profit is touched: close 50% at that candle close and move the stop to entry.
3. A confirmed opposite stance flip closes the remainder.
4. After TP1 has realized a partial profit, high take-profit pressure for two consecutive confirmed candles closes only the remainder. Pressure can never close a pre-TP or losing position.
5. Thirty confirmed holding candles close the remainder.

## Costs

Every entry and exit deducts taker fee plus asset-class slippage from executed notional. Gross PnL and costs are stored separately; all scoreboards use net PnL. No trigger receives a better price than the confirmed candle close.

## Audit trail and comparison

The entry snapshot stores the evidence stack, stance, checklist, action levels, and validated signature statistics. Entry is registered in the judgment ledger. Closing produces a judgment score and tags losing trades with the signature, regime when available, and exit reason.

Legacy pressure exits that occurred before TP1 are retained in the journal as `policy_invalid:pre_tp_pressure_exit` audit records and excluded from benchmark performance. They are not rewritten as wins or losses.

The engine and user are compared over the same date window using return ratios, win rate, profit factor, maximum drawdown, and trade count. Absolute USDT amounts are not compared because sizing and symbol universes differ. The fixed notice is: **Conditions differ; this compares directional and timing judgment, not absolute capital performance.**

An `engine_leading` notice requires both higher rolling four-week net return and no greater maximum drawdown. It is informational only and cannot enable live trading.
