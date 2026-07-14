# Paper Exit Realism

## Exit targets

- Paper TP1 is `entry +/- ATR(14) * FCE_PAPER_TAKE_PROFIT_ATR_K1` (default `1.0`).
- TP1 closes half of the remaining quantity at the target price and moves the stop to entry.
- TP2 is `ATR * k2` (default `2.0`) or the nearer favorable action-plan target when that target remains beyond TP1.
- Entry R:R is recalculated from the actual ATR TP1 and the existing structural invalidation. The distant action-plan R:R cannot pass the paper entry gate.
- Fees and slippage are charged on entry, TP1, and the final exit.

## Time decay

`FCE_PAPER_MAX_HOLDING_BARS` is a review threshold, not an unconditional liquidation timer.

- A position is extended when the confirmed stance still supports its direction.
- A position closes as `time_decay` when the threshold has passed and the stance is neutral, conflicted, or transitioning.
- `time_decay` and legacy `time_stop` exits retain realized PnL but are neutral and excluded from win-rate calculations.
- Time-decay frequency is an entry-quality diagnostic. It is never counted as a winning signature outcome.

## Scoreboard windows

- `competition`: benchmark anchor onward. This is the only window used for the engine-versus-user verdict.
- `recent_28d`: rolling 28-day display window. It includes account trades closed before the benchmark anchor and is not used for the verdict.
- A verdict remains `insufficient_samples` until both sides have at least 10 scored, non-neutral exits.

## User-fill reconstruction audit

All closed positions reconstructed inside the account-fill retention window are persisted. The benchmark count is a filtered view, not a storage filter. Each sync compares reconstructed open symbol, direction, and quantity against Bitget's current open-position endpoint. Any mismatch is exposed in `user_fill_sync.diagnostics.live_position_reconciliation`.

## Reachability audit

`audit_atr_target_reachability` replays confirmed candles without future leakage. At every eligible entry bar it fixes ATR from the prefix available at that bar, then checks whether long and short ATR targets are reached within the configured holding window.
