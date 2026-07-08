# Performance Metrics

WO-FCE-38 adds account-level analytics. These metrics describe the trader/account record, not a setup signal. All outputs must show sample size `N`; when `N < 10`, conclusion-style interpretation is withheld.

## Data Basis

- Source: closed `Trade` records.
- Equity curve: configured base capital + cumulative realized PnL + latest open unrealized PnL snapshot.
- Default base capital: `FCE_PERFORMANCE_CAPITAL_BASE_USDT=10000`.
- Risk-free rate: `0`.

## Metrics

### Profit Factor

`PF = gross_profit / abs(gross_loss)`

Reference marks are shown at `1.5` and `2.0`. If there are no loss trades, FCE does not publish infinity.

### Maximum Drawdown

`MDD = min((equity - previous_peak) / previous_peak)`

Both percent and USDT drawdown are displayed. Longest recovery days count the longest period from drawdown start until a new equity high.

### Calmar Ratio

`Calmar = annualized_return_pct / abs(MDD_pct)`

FCE withholds Calmar when the trade span is under 180 days.

### Sharpe and Sortino

Daily PnL is converted to daily return using the configured base capital.

`Sharpe = mean(daily_return) / stdev(daily_return) * sqrt(365)`

`Sortino = mean(daily_return) / downside_stdev(daily_return) * sqrt(365)`

Sortino is preferred in the UI because it focuses on downside volatility.

### Recovery Factor

`Recovery Factor = net_profit / abs(max_drawdown_usdt)`

### Win Rate and Average R

Win rate is account trade win rate, not setup/signature win rate.

`Average R` uses `review_v2.realized_r` when available. If unavailable, it falls back to `pnl_percent / 100` and marks the method as a proxy.

### Risk of Ruin

FCE uses a fixed repeated-bet approximation:

`edge = p - ((1 - p) / payoff_ratio)`

If `edge <= 0`, risk of ruin is `100%`. Otherwise:

`risk = ((1 - edge) / (1 + edge)) ^ capital_units`

where `capital_units = 1 / average_bet_fraction`.

Assumption: identical bet fraction, win rate, and payoff ratio repeat. This is not a prediction.

## Kelly Reference

The simulator can show a Kelly reference block only when a validated historical signature has enough samples.

- Win rate uses the backtest `win@1R` CI lower bound.
- Payoff uses median RR.
- Displayed value is half Kelly.
- Candidate signatures and insufficient samples do not publish a value.

Mandatory copy:

`켈리는 동일 반복 베팅 통계 가정의 이론값입니다. 권장 사이즈가 아닙니다.`

## Monthly MDD Guard

Optional setting:

`FCE_PERFORMANCE_MONTHLY_MDD_LIMIT_PCT`

When configured, FCE reports usage:

- `<80%`: ok
- `80% to <100%`: warn
- `>=100%`: critical

The guard is read-only. It sends facts and does not enforce trading actions.
