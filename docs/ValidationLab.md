# Validation Lab

Validation Lab checks whether stored trade outcomes are strong enough to trust. It is not a full backtest engine in v0.4.

## API

- `POST /api/validation/run`
- `GET /api/validation/runs`
- `GET /api/validation/runs/{run_id}`

Example request:

```json
{
  "strategy_type": "entry_score_threshold",
  "symbol": "BTCUSDT",
  "timeframe": "4h",
  "params": {
    "entry_score_min": 75,
    "risk_score_max": 60,
    "fomo_index_max": 70
  },
  "validation": {
    "monte_carlo": {
      "n_simulations": 500,
      "seed": 42
    },
    "bootstrap": {
      "n_bootstrap": 500,
      "confidence": 0.95,
      "seed": 42
    },
    "walk_forward": {
      "n_windows": 5
    }
  }
}
```

## Checks

- Monte Carlo resampling over realized trade returns
- Bootstrap Sharpe confidence interval
- Walk Forward consistency across windows
- Entry Score bucket performance
- sample-size and overfitting warnings

## Determinism

Monte Carlo and Bootstrap use explicit seeds. Re-running the same request over the same trade set should produce the same result.

## Current Schema Limit

Completed trades currently store `entry_score` and `exit_score`, but not entry FOMO Index or entry Risk Score. If a validation request includes `fomo_index_max` or `risk_score_max`, v0.4 records a warning and does not silently apply a fake proxy.

## Stored Record

`validation_runs` stores:

- request strategy type
- symbol and timeframe
- params
- summary metrics
- full validation result JSON
- warnings

Validation runs also create Decision Memory records so future research reviews can include validation caveats.
