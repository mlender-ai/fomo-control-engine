import math
import random
from statistics import mean, pstdev

from app.db.models import Trade, ValidationRun, ValidationRunRequest


def run_validation(trades: list[Trade], request: ValidationRunRequest) -> ValidationRun:
    filtered = _filter_trades(trades, request)
    returns = [trade.pnl_percent / 100 for trade in filtered]
    summary = _summary(returns, filtered)
    mc = monte_carlo(
        returns,
        request.validation.get("monte_carlo", {}).get("n_simulations", 1000),
        request.validation.get("monte_carlo", {}).get("seed", 42),
    )
    boot = bootstrap_sharpe(
        returns,
        request.validation.get("bootstrap", {}).get("n_bootstrap", 1000),
        request.validation.get("bootstrap", {}).get("confidence", 0.95),
        request.validation.get("bootstrap", {}).get("seed", 42),
    )
    wf = walk_forward(returns, request.validation.get("walk_forward", {}).get("n_windows", 5))
    warnings = _warnings(summary, boot, wf, mc) + _schema_warnings(request)
    return ValidationRun(
        strategy_type=request.strategy_type,
        symbol=request.symbol.upper(),
        timeframe=request.timeframe,
        start_time=request.start,
        end_time=request.end,
        params=request.params,
        summary=summary,
        results={
            "monte_carlo": mc,
            "bootstrap": boot,
            "walk_forward": wf,
            "score_bucket_performance": _bucket(filtered, "entry_score"),
            "fomo_bucket_performance": {},
            "risk_bucket_performance": {},
        },
        warnings=warnings,
    )


def monte_carlo(returns: list[float], n_simulations: int = 1000, seed: int = 42) -> dict:
    if not returns:
        return {
            "n_simulations": n_simulations,
            "p_value_sharpe": 1.0,
            "median_total_return": 0,
        }
    rng = random.Random(seed)
    totals = []
    observed = sum(returns)
    for _ in range(n_simulations):
        sample = [rng.choice(returns) for _ in returns]
        totals.append(sum(sample))
    better = sum(1 for total in totals if total >= observed)
    return {
        "n_simulations": n_simulations,
        "p_value_sharpe": round(better / n_simulations, 4),
        "median_total_return": round(sorted(totals)[len(totals) // 2], 4),
    }


def bootstrap_sharpe(
    returns: list[float],
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    seed: int = 42,
) -> dict:
    if len(returns) < 2:
        return {"sharpe_ci": [0, 0], "confidence": confidence}
    rng = random.Random(seed)
    sharpes = []
    for _ in range(n_bootstrap):
        sample = [rng.choice(returns) for _ in returns]
        sharpes.append(_sharpe(sample))
    sharpes.sort()
    low_index = int(((1 - confidence) / 2) * len(sharpes))
    high_index = int((1 - (1 - confidence) / 2) * len(sharpes)) - 1
    return {
        "sharpe_ci": [
            round(sharpes[low_index], 4),
            round(sharpes[max(low_index, high_index)], 4),
        ],
        "confidence": confidence,
    }


def walk_forward(returns: list[float], n_windows: int = 5) -> dict:
    if not returns:
        return {"windows": [], "consistency_rate": 0}
    size = max(1, math.ceil(len(returns) / n_windows))
    windows = []
    for index in range(0, len(returns), size):
        chunk = returns[index : index + size]
        windows.append(
            {
                "index": len(windows) + 1,
                "trades": len(chunk),
                "total_return": round(sum(chunk), 4),
                "positive": sum(chunk) > 0,
            }
        )
    return {
        "windows": windows,
        "consistency_rate": round(sum(1 for window in windows if window["positive"]) / len(windows), 2),
    }


def _filter_trades(trades: list[Trade], request: ValidationRunRequest) -> list[Trade]:
    result = [trade for trade in trades if trade.symbol == request.symbol.upper()]
    entry_min = request.params.get("entry_score_min")
    if entry_min is not None:
        result = [trade for trade in result if (trade.entry_score or 0) >= entry_min]
    return result


def _summary(returns: list[float], trades: list[Trade]) -> dict:
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "total_trades": len(returns),
        "win_rate": round(len(wins) / len(returns), 4) if returns else 0,
        "avg_win": round(mean(wins), 4) if wins else 0,
        "avg_loss": round(mean(losses), 4) if losses else 0,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else 0,
        "max_drawdown": round(_max_drawdown(returns), 4),
        "sharpe": round(_sharpe(returns), 4),
        "sortino": round(_sortino(returns), 4),
        "calmar": round(
            (sum(returns) / abs(_max_drawdown(returns))) if _max_drawdown(returns) else 0,
            4,
        ),
        "recovery_factor": round(
            (sum(returns) / abs(_max_drawdown(returns))) if _max_drawdown(returns) else 0,
            4,
        ),
        "expectancy": round(mean(returns), 4) if returns else 0,
    }


def _warnings(summary: dict, boot: dict, wf: dict, mc: dict) -> list[str]:
    warnings = []
    if summary["total_trades"] < 30:
        warnings.append("sample size is limited")
    if summary["profit_factor"] > 3 and summary["total_trades"] < 50:
        warnings.append("possible overfitting")
    if wf["consistency_rate"] < 0.5:
        warnings.append("walk forward stability is weak")
    if boot["sharpe_ci"][0] < 0:
        warnings.append("Sharpe confidence interval crosses below zero")
    if mc["p_value_sharpe"] > 0.2:
        warnings.append("performance is not clearly separated from random sampling")
    if summary["max_drawdown"] < -0.3:
        warnings.append("max drawdown risk is high")
    return warnings


def _schema_warnings(request: ValidationRunRequest) -> list[str]:
    warnings = []
    if request.params.get("fomo_index_max") is not None:
        warnings.append("fomo_index_max is not applied because completed trades do not store entry FOMO Index yet")
    if request.params.get("risk_score_max") is not None:
        warnings.append("risk_score_max is not applied because completed trades do not store entry Risk Score yet")
    return warnings


def _bucket(trades: list[Trade], attr: str) -> dict:
    buckets: dict[str, list[float]] = {}
    for trade in trades:
        value = getattr(trade, attr) or 0
        key = f"{int(value // 10) * 10}-{int(value // 10) * 10 + 9}"
        buckets.setdefault(key, []).append(trade.pnl_percent)
    return {key: {"count": len(values), "avg_pnl": round(mean(values), 2)} for key, values in buckets.items()}


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0
    deviation = pstdev(returns)
    return mean(returns) / deviation if deviation else 0


def _sortino(returns: list[float]) -> float:
    downside = [value for value in returns if value < 0]
    if not downside:
        return 0
    deviation = pstdev(downside)
    return mean(returns) / deviation if deviation else 0


def _max_drawdown(returns: list[float]) -> float:
    equity = 1.0
    peak = 1.0
    drawdown = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        drawdown = min(drawdown, (equity - peak) / peak)
    return drawdown
