from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from math import sqrt
from statistics import mean, median, pstdev
from typing import Any
from uuid import UUID

from app.backtest.statistics import bootstrap_ci_from_counts
from app.db.models import Position, PositionSnapshot, Trade

SAMPLE_FLOOR = 10
CALMAR_MIN_DAYS = 180
KELLY_MIN_SAMPLE = 10
PERFORMANCE_DISCLAIMER = "계좌 성과 지표는 종료 거래 기준입니다. 표본과 자본 기준을 함께 확인하세요."
KELLY_DISCLAIMER = "켈리는 동일 반복 베팅 통계 가정의 이론값입니다. 권장 사이즈가 아닙니다."


@dataclass(frozen=True)
class PerformanceConfig:
    capital_base_usdt: float = 10_000.0
    monthly_mdd_limit_pct: float | None = None
    sample_floor: int = SAMPLE_FLOOR


def build_performance_report(
    trades: list[Trade],
    *,
    positions: list[Position] | None = None,
    latest_snapshots: dict[UUID, PositionSnapshot] | None = None,
    config: PerformanceConfig | None = None,
) -> dict[str, Any]:
    cfg = config or PerformanceConfig()
    ordered = sorted(trades, key=lambda item: item.created_at)
    positions_by_id = {position.id: position for position in positions or []}
    curve = equity_curve(ordered, latest_snapshots or {}, capital_base_usdt=cfg.capital_base_usdt)
    overall = performance_metrics(ordered, curve=curve, config=cfg)
    breakdowns = {
        "asset_class": _group_metrics(ordered, curve, config=cfg, key_fn=lambda trade: _asset_class(trade, positions_by_id)),
        "direction": _group_metrics(ordered, curve, config=cfg, key_fn=lambda trade: trade.direction.value),
        "month": _group_metrics(ordered, curve, config=cfg, key_fn=lambda trade: trade.created_at.strftime("%Y-%m")),
        "setup_linked": _group_metrics(ordered, curve, config=cfg, key_fn=lambda trade: _setup_bucket(trade, positions_by_id)),
    }
    mdd_guard = monthly_mdd_guard(overall, cfg.monthly_mdd_limit_pct)
    return {
        "as_of": _now().isoformat(),
        "sample_floor": cfg.sample_floor,
        "capital_base_usdt": cfg.capital_base_usdt,
        "disclaimer": PERFORMANCE_DISCLAIMER,
        "overall": overall,
        "equity_curve": curve,
        "breakdowns": breakdowns,
        "mdd_guard": mdd_guard,
        "scoreboard_cross_view": _scoreboard_cross_view(ordered),
    }


def performance_metrics(
    trades: list[Trade],
    *,
    curve: list[dict[str, Any]] | None = None,
    config: PerformanceConfig | None = None,
) -> dict[str, Any]:
    cfg = config or PerformanceConfig()
    ordered = sorted(trades, key=lambda item: item.created_at)
    n = len(ordered)
    gross_profit = sum(trade.pnl_amount for trade in ordered if trade.pnl_amount > 0)
    gross_loss = abs(sum(trade.pnl_amount for trade in ordered if trade.pnl_amount < 0))
    net_profit = gross_profit - gross_loss
    wins = [trade for trade in ordered if trade.pnl_amount > 0]
    losses = [trade for trade in ordered if trade.pnl_amount < 0]
    equity = curve or equity_curve(ordered, {}, capital_base_usdt=cfg.capital_base_usdt)
    drawdown = max_drawdown(equity)
    daily_returns = _daily_returns(ordered, cfg.capital_base_usdt)
    span_days = _span_days(ordered)
    annual_return_pct = _annualized_return_pct(net_profit, cfg.capital_base_usdt, span_days)
    sample_sufficient = n >= cfg.sample_floor
    warnings = []
    if not sample_sufficient:
        warnings.append(f"표본 부족 — 결론 유보 (N={n}, 최소 {cfg.sample_floor})")
    if span_days < CALMAR_MIN_DAYS:
        warnings.append("칼마는 6개월 미만 표본에서 발행하지 않습니다.")

    profit_factor = None
    if sample_sufficient:
        if gross_loss > 0:
            profit_factor = round(gross_profit / gross_loss, 3)
        elif gross_profit > 0:
            profit_factor = None
            warnings.append("손실 거래가 없어 PF는 무한대로 발행하지 않습니다.")

    win_rate_pct = round(len(wins) / n * 100, 2) if n else None
    avg_win = mean([trade.pnl_amount for trade in wins]) if wins else 0.0
    avg_loss = abs(mean([trade.pnl_amount for trade in losses])) if losses else 0.0
    payoff_ratio = round(avg_win / avg_loss, 3) if avg_loss > 0 else None
    sharpe = _sharpe(daily_returns) if sample_sufficient else None
    sortino = _sortino(daily_returns) if sample_sufficient else None
    calmar = None
    if sample_sufficient and span_days >= CALMAR_MIN_DAYS and drawdown["max_drawdown_pct"]:
        calmar = round((annual_return_pct or 0.0) / abs(float(drawdown["max_drawdown_pct"])), 3)

    recovery_factor = None
    if sample_sufficient and drawdown["max_drawdown_usdt"]:
        recovery_factor = round(net_profit / abs(float(drawdown["max_drawdown_usdt"])), 3)

    avg_r = _average_r(ordered) if sample_sufficient else None
    ruin = risk_of_ruin(
        win_rate_pct=win_rate_pct,
        payoff_ratio=payoff_ratio,
        average_bet_fraction=_average_bet_fraction(ordered, cfg.capital_base_usdt),
        sample_size=n,
        sample_floor=cfg.sample_floor,
    )

    return {
        "sample_size": n,
        "sample_sufficient": sample_sufficient,
        "sample_warning": None if sample_sufficient else f"표본 부족 — 결론 유보 (N={n})",
        "gross_profit_usdt": round(gross_profit, 4),
        "gross_loss_usdt": round(gross_loss, 4),
        "net_profit_usdt": round(net_profit, 4),
        "profit_factor": profit_factor,
        "profit_factor_refs": {"watch": 1.5, "strong": 2.0},
        "win_rate_pct": win_rate_pct,
        # WO-36 표기 표준: 전 표면 CI 병기 — 계좌 승률도 예외 아님.
        "win_rate_ci": list(bootstrap_ci_from_counts(len(wins), n)) if n else None,
        "avg_win_usdt": round(avg_win, 4) if wins else None,
        "avg_loss_usdt": round(avg_loss, 4) if losses else None,
        "payoff_ratio": payoff_ratio,
        "avg_r": avg_r,
        "avg_r_method": "review_v2.realized_r 또는 pnl_percent/100 프록시",
        "max_drawdown_pct": drawdown["max_drawdown_pct"],
        "max_drawdown_usdt": drawdown["max_drawdown_usdt"],
        "max_drawdown_period": drawdown["period"],
        "longest_recovery_days": drawdown["longest_recovery_days"],
        "annualized_return_pct": annual_return_pct if sample_sufficient else None,
        "calmar": calmar,
        "calmar_published": bool(calmar is not None),
        "sharpe": sharpe,
        "sortino": sortino,
        "sortino_preferred": True,
        "recovery_factor": recovery_factor,
        "risk_of_ruin": ruin,
        "warnings": warnings,
    }


def equity_curve(
    trades: list[Trade],
    snapshots: dict[UUID, PositionSnapshot],
    *,
    capital_base_usdt: float,
) -> list[dict[str, Any]]:
    capital = max(float(capital_base_usdt), 1.0)
    cumulative = 0.0
    points = [
        {
            "ts": _first_ts(trades).isoformat(),
            "equity_usdt": round(capital, 4),
            "realized_pnl_usdt": 0.0,
            "unrealized_pnl_usdt": 0.0,
            "drawdown_pct": 0.0,
            "source": "initial",
        }
    ]
    peak = capital
    for trade in sorted(trades, key=lambda item: item.created_at):
        cumulative += trade.pnl_amount
        equity = capital + cumulative
        peak = max(peak, equity)
        points.append(
            {
                "ts": trade.created_at.isoformat(),
                "equity_usdt": round(equity, 4),
                "realized_pnl_usdt": round(cumulative, 4),
                "unrealized_pnl_usdt": 0.0,
                "drawdown_pct": round(((equity - peak) / peak) * 100, 4) if peak else 0.0,
                "source": "closed_trade",
                "trade_id": str(trade.id),
                "symbol": trade.symbol,
            }
        )
    if snapshots:
        unrealized = sum(float(snapshot.pnl_amount or 0.0) for snapshot in snapshots.values())
        equity = capital + cumulative + unrealized
        peak = max(peak, equity)
        points.append(
            {
                "ts": _now().isoformat(),
                "equity_usdt": round(equity, 4),
                "realized_pnl_usdt": round(cumulative, 4),
                "unrealized_pnl_usdt": round(unrealized, 4),
                "drawdown_pct": round(((equity - peak) / peak) * 100, 4) if peak else 0.0,
                "source": "open_unrealized",
            }
        )
    return points


def max_drawdown(points: list[dict[str, Any]]) -> dict[str, Any]:
    peak_value: float | None = None
    peak_ts: str | None = None
    trough_ts: str | None = None
    max_dd_pct = 0.0
    max_dd_usdt = 0.0
    in_drawdown_since: datetime | None = None
    longest_recovery_days = 0
    for point in points:
        equity = _float(point.get("equity_usdt"))
        if equity is None:
            continue
        ts = _parse_dt(point.get("ts")) or _now()
        if peak_value is None or equity >= peak_value:
            if in_drawdown_since is not None:
                longest_recovery_days = max(longest_recovery_days, (ts - in_drawdown_since).days)
                in_drawdown_since = None
            peak_value = equity
            peak_ts = point.get("ts")
            continue
        if in_drawdown_since is None:
            in_drawdown_since = ts
        dd_usdt = equity - peak_value
        dd_pct = (dd_usdt / peak_value) * 100 if peak_value else 0.0
        if dd_pct < max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_usdt = dd_usdt
            trough_ts = point.get("ts")
    if in_drawdown_since is not None:
        longest_recovery_days = max(longest_recovery_days, (_now() - in_drawdown_since).days)
    return {
        "max_drawdown_pct": round(max_dd_pct, 4),
        "max_drawdown_usdt": round(max_dd_usdt, 4),
        "period": {"peak_at": peak_ts, "trough_at": trough_ts},
        "longest_recovery_days": longest_recovery_days,
    }


def monthly_mdd_guard(metrics: dict[str, Any], limit_pct: float | None) -> dict[str, Any]:
    if limit_pct is None or limit_pct <= 0:
        return {"configured": False, "status": "none", "limit_pct": None, "usage_pct": None}
    mdd = abs(float(metrics.get("max_drawdown_pct") or 0.0))
    usage = (mdd / limit_pct) * 100 if limit_pct else 0.0
    status = "critical" if usage >= 100 else "warn" if usage >= 80 else "ok"
    return {
        "configured": True,
        "status": status,
        "limit_pct": round(limit_pct, 4),
        "current_mdd_pct": round(mdd, 4),
        "usage_pct": round(usage, 2),
    }


def risk_of_ruin(
    *,
    win_rate_pct: float | None,
    payoff_ratio: float | None,
    average_bet_fraction: float | None,
    sample_size: int,
    sample_floor: int = SAMPLE_FLOOR,
) -> dict[str, Any]:
    if sample_size < sample_floor or win_rate_pct is None or payoff_ratio is None or payoff_ratio <= 0:
        return {
            "published": False,
            "probability_pct": None,
            "assumption": "표본 부족 또는 손익비 미산출 — 파산확률 유보",
        }
    p = max(0.0, min(1.0, win_rate_pct / 100))
    q = 1 - p
    edge = p - (q / payoff_ratio)
    bet_fraction = max(float(average_bet_fraction or 0.0), 0.0001)
    if edge <= 0:
        probability = 100.0
    else:
        capital_units = max(1.0, 1.0 / bet_fraction)
        probability = min(100.0, max(0.0, (((1 - edge) / (1 + edge)) ** capital_units) * 100))
    return {
        "published": True,
        "probability_pct": round(probability, 4),
        "edge": round(edge, 6),
        "average_bet_fraction_pct": round(bet_fraction * 100, 4),
        "assumption": "동일 베팅비율·동일 승률·동일 손익비 반복 가정",
    }


def kelly_reference_from_historical(historical_backtest: Any, direction: str | None = None) -> dict[str, Any]:
    if not isinstance(historical_backtest, dict):
        return _kelly_unavailable("백테스트 통계 없음")
    stats = historical_backtest.get("stats")
    if not isinstance(stats, list):
        return _kelly_unavailable("백테스트 통계 없음")
    candidates = []
    for stat in stats:
        if not isinstance(stat, dict):
            continue
        signature = stat.get("signature") if isinstance(stat.get("signature"), dict) else {}
        if direction and signature.get("direction") not in {direction, "neutral", None}:
            continue
        # 켈리 근거는 validated 시그니처만 — 강등/격리 통계로 사이징 참고치 산출 금지.
        if _signature_state(stat) != "validated":
            continue
        n = int(stat.get("sample_size") or 0)
        if n < KELLY_MIN_SAMPLE:
            continue
        ci = stat.get("win_1r_ci")
        win_source = ci[0] if isinstance(ci, list) and len(ci) == 2 else stat.get("win_1r_pct")
        win_pct = _float(win_source)
        payoff = _float(stat.get("median_rr"))
        if win_pct is None or payoff is None or payoff <= 0:
            continue
        kelly = _kelly_fraction(win_pct / 100, payoff)
        candidates.append(
            {
                "signature_key": stat.get("signature_key"),
                "label": stat.get("label") or signature.get("label") or "동일 시그니처",
                "sample_size": n,
                "win_rate_ci_low_pct": round(win_pct, 2),
                "median_rr": round(payoff, 3),
                "kelly_fraction_pct": round(kelly * 100, 3),
                "half_kelly_fraction_pct": round(max(kelly, 0.0) * 50, 3),
                "state": _signature_state(stat),
            }
        )
    if not candidates:
        return _kelly_unavailable("검증된 시그니처 통계 부족")
    best = max(candidates, key=lambda item: item["half_kelly_fraction_pct"])
    return {
        "available": True,
        "published": True,
        "disclaimer": KELLY_DISCLAIMER,
        "basis": "백테스트 net 승률 CI 하한 + 중앙 손익비",
        **best,
    }


def attach_kelly_to_simulation(result: dict[str, Any], historical_backtest: Any) -> dict[str, Any]:
    reference = kelly_reference_from_historical(historical_backtest, str(result.get("direction") or ""))
    margin = _float(result.get("margin_usdt"))
    if reference.get("available") and margin is not None:
        # 실제 계좌 잔고를 모르므로 입력 증거금과 하프 켈리 비율만 비교 문장화한다.
        reference["input_margin_usdt"] = margin
        reference["position_sizing_note"] = "계좌 기준 자본을 설정하면 입력 증거금과 하프 켈리 참고 상한을 비교할 수 있습니다."
    result["kelly_reference"] = reference
    return result


def _group_metrics(
    trades: list[Trade],
    curve: list[dict[str, Any]],
    *,
    config: PerformanceConfig,
    key_fn,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[Trade]] = defaultdict(list)
    for trade in trades:
        groups[str(key_fn(trade) or "unknown")].append(trade)
    return {
        key: performance_metrics(items, curve=equity_curve(items, {}, capital_base_usdt=config.capital_base_usdt), config=config)
        for key, items in sorted(groups.items())
    }


def _scoreboard_cross_view(trades: list[Trade]) -> dict[str, Any]:
    total = len(trades)
    engine_right_user_cost = 0
    engine_wrong = 0
    setup_linked = 0
    for trade in trades:
        scorecard = trade.judgment_scorecard if isinstance(trade.judgment_scorecard, dict) else {}
        correct = int(scorecard.get("correct") or 0)
        wrong = int(scorecard.get("wrong") or 0)
        if correct > wrong and trade.pnl_amount < 0:
            engine_right_user_cost += 1
        if wrong > correct:
            engine_wrong += 1
        position_meta = trade.review_v2.get("position") if isinstance(trade.review_v2, dict) else None
        if isinstance(position_meta, dict) and position_meta.get("scenario_id"):
            setup_linked += 1
    return {
        "total_trades": total,
        "engine_right_but_account_lost": engine_right_user_cost,
        "engine_wrong_dominant": engine_wrong,
        "setup_linked_trades": setup_linked,
        "note": "엔진 판단·알림 대응·계좌 결과를 같은 거래 단위로 대조하는 요약입니다.",
    }


def _asset_class(trade: Trade, positions_by_id: dict[UUID, Position]) -> str:
    position = positions_by_id.get(trade.position_id)
    symbol = (position.symbol if position else trade.symbol).upper()
    detected_class = getattr(position, "asset_class", None) if position else None
    if detected_class:
        return str(detected_class)
    stock_roots = {"TSLA", "NVDA", "MSTR", "PLTR", "QQQ", "SPY", "COIN", "AAPL", "MSFT", "META"}
    root = symbol.removesuffix("USDT")
    if root in stock_roots:
        return "stock"
    return "crypto" if symbol.endswith("USDT") else "unknown"


def _setup_bucket(trade: Trade, positions_by_id: dict[UUID, Position]) -> str:
    position = positions_by_id.get(trade.position_id)
    if position and position.scenario_id is not None:
        return "setup_linked"
    if isinstance(trade.review_v2, dict):
        position_payload = trade.review_v2.get("position")
        if isinstance(position_payload, dict) and position_payload.get("scenario_id"):
            return "setup_linked"
    return "direct_or_unknown"


def _daily_returns(trades: list[Trade], capital_base_usdt: float) -> list[float]:
    capital = max(float(capital_base_usdt), 1.0)
    daily: dict[str, float] = defaultdict(float)
    for trade in trades:
        daily[trade.created_at.strftime("%Y-%m-%d")] += trade.pnl_amount
    return [value / capital for _, value in sorted(daily.items())]


def _sharpe(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    sigma = pstdev(returns)
    if sigma == 0:
        return None
    return round((mean(returns) / sigma) * sqrt(365), 3)


def _sortino(returns: list[float]) -> float | None:
    if len(returns) < 2:
        return None
    # 표준 하방편차: 전체 수익률에 대해 0(MAR) 하회분의 RMS.
    # 손실값들끼리의 표준편차(pstdev)는 균일 손실에서 0이 되어 소르티노를 미발행하고 값도 왜곡한다.
    downside_sq = [min(0.0, value) ** 2 for value in returns]
    sigma = sqrt(mean(downside_sq))
    if sigma == 0:
        return None
    return round((mean(returns) / sigma) * sqrt(365), 3)


def _average_r(trades: list[Trade]) -> float | None:
    values = []
    for trade in trades:
        rv2 = trade.review_v2 if isinstance(trade.review_v2, dict) else {}
        value = _float(rv2.get("realized_r"))
        if value is None:
            value = trade.pnl_percent / 100
        values.append(value)
    return round(mean(values), 3) if values else None


def _average_bet_fraction(trades: list[Trade], capital_base_usdt: float) -> float | None:
    if not trades:
        return None
    capital = max(float(capital_base_usdt), 1.0)
    return median([abs(trade.pnl_amount) / capital for trade in trades])


def _annualized_return_pct(net_profit: float, capital_base_usdt: float, span_days: int) -> float | None:
    capital = max(float(capital_base_usdt), 1.0)
    if span_days <= 0:
        return None
    total_return = net_profit / capital
    if total_return <= -1:
        return -100.0
    return round(((1 + total_return) ** (365 / max(span_days, 1)) - 1) * 100, 3)


def _span_days(trades: list[Trade]) -> int:
    if len(trades) < 2:
        return 0
    ordered = sorted(trades, key=lambda item: item.created_at)
    return max(1, (ordered[-1].created_at - ordered[0].created_at).days)


def _first_ts(trades: list[Trade]) -> datetime:
    if not trades:
        return _now()
    return min(trade.created_at for trade in trades)


def _signature_state(stat: dict[str, Any]) -> str:
    # WO-37 서비스 계층이 부착하는 필드명은 lifecycle_state.
    state = stat.get("lifecycle_state") or stat.get("signature_state") or stat.get("state")
    if state:
        return str(state)
    n = int(stat.get("sample_size") or 0)
    return "validated" if n >= 30 else "candidate"


def _kelly_fraction(win_probability: float, payoff_ratio: float) -> float:
    p = max(0.0, min(1.0, win_probability))
    q = 1 - p
    if payoff_ratio <= 0:
        return 0.0
    return max(0.0, p - (q / payoff_ratio))


def _kelly_unavailable(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "published": False,
        "reason": reason,
        "disclaimer": KELLY_DISCLAIMER,
    }


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)
