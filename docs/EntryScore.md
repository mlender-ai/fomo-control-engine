# Entry Opportunity Score

Entry Opportunity Score is not a buy signal. It measures whether the current entry idea is supported by data.

## Weights

```text
Structure: 30
Volume: 25
Liquidity: 20
Momentum: 15
Risk: 10
```

Risk is inverted in the final score because higher risk is worse.

```text
Entry Score =
Structure * 0.30
+ Volume * 0.25
+ Liquidity * 0.20
+ Momentum * 0.15
+ (100 - Risk) * 0.10
```

## Interpretation

- 0-49: entry basis is weak, FOMO risk is likely.
- 50-64: some rebound signals, but structure is weak.
- 65-74: worth watching, additional confirmation required.
- 75-84: entry candidate, structure and volume provide support.
- 85-100: strong candidate, but risk still must be checked manually.

