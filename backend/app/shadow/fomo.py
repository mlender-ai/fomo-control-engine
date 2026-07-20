"""Entry-time FOMO observation and loss attribution (WO-FCE-90)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.db.models import Direction, Trade


FOMO_INDEX_THRESHOLD = 65.0
WEIGHTS = {"chase": 40.0, "speed": 20.0, "unscouted": 15.0, "stance": 25.0}


def build_entry_fomo_snapshot(
    *,
    direction: Direction,
    entry_price: float,
    plan_price: float | None,
    report_created_at: datetime | None,
    entered_at: datetime,
    scout_originated: bool | None,
    held_stance: str | None,
    entry_state_label: str | None,
) -> dict[str, Any]:
    chase_pct = _chase_pct(direction, entry_price, plan_price)
    report_minutes = _minutes(report_created_at, entered_at)
    alignment = _stance_alignment(direction, held_stance)
    severities = {
        "chase": min(1.0, max(0.0, (chase_pct or 0.0) / 3.0)) if chase_pct is not None else None,
        "speed": min(1.0, max(0.0, (60.0 - report_minutes) / 60.0)) if report_minutes is not None else None,
        "unscouted": 0.0 if scout_originated is True else 1.0 if scout_originated is False else None,
        "stance": 0.0 if alignment == "aligned" else 1.0 if alignment == "against" else 0.5 if alignment == "conflicted" else None,
    }
    available_weight = sum(WEIGHTS[key] for key, value in severities.items() if value is not None)
    contribution = {key: round(WEIGHTS[key] * value, 2) if value is not None else None for key, value in severities.items()}
    index = round(sum(value or 0.0 for value in contribution.values()) / available_weight * 100, 1) if available_weight else None
    complete = available_weight == sum(WEIGHTS.values())
    return {
        "plan_price": plan_price,
        "chase_pct": chase_pct,
        "report_to_entry_minutes": report_minutes,
        "scout_originated": scout_originated,
        "stance_alignment": alignment,
        "held_stance": held_stance,
        "entry_state_label": entry_state_label,
        "fomo_index": index,
        "threshold": FOMO_INDEX_THRESHOLD,
        "components": {
            key: {
                "available": severities[key] is not None,
                "severity": round(severities[key], 3) if severities[key] is not None else None,
                "weight": weight,
                "contribution": contribution[key],
            }
            for key, weight in WEIGHTS.items()
        },
        "component_coverage_pct": round(available_weight / sum(WEIGHTS.values()) * 100, 1),
        "complete": complete,
        "captured_at": entered_at.isoformat(),
        "policy": "entry_time_only_no_backfill",
    }


def monthly_fomo_attribution(
    trades: list[Trade],
    *,
    now: datetime | None = None,
    min_trades: int = 10,
    threshold: float = FOMO_INDEX_THRESHOLD,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    month = current.strftime("%Y-%m")
    monthly = [trade for trade in trades if trade.created_at.strftime("%Y-%m") == month]
    eligible = [trade for trade in monthly if trade.fomo_index is not None and bool(trade.fomo_components.get("complete"))]
    fomo_losses = [trade for trade in eligible if float(trade.fomo_index or 0) >= threshold and trade.pnl_amount < 0]
    eligible_losses = [trade for trade in eligible if trade.pnl_amount < 0]
    fomo_cost = abs(sum(trade.pnl_amount for trade in fomo_losses))
    total_loss = abs(sum(trade.pnl_amount for trade in eligible_losses))
    legacy_proxy = [trade for trade in monthly if (trade.entry_score or 100) < 65 and trade.pnl_amount < 0]
    publishable = len(eligible) >= min_trades
    recent = max(eligible, key=lambda trade: trade.created_at, default=None)
    return {
        "month": month,
        "threshold": threshold,
        "eligible_trades": len(eligible),
        "excluded_legacy_trades": len(monthly) - len(eligible),
        "fomo_loss_trades": len(fomo_losses),
        "fomo_cost_usdt": round(fomo_cost, 2) if publishable else None,
        "loss_share_pct": round(fomo_cost / total_loss * 100, 1) if publishable and total_loss else None,
        "sample_floor": min_trades,
        "sample_sufficient": publishable,
        "statement": (
            f"FOMO 비용 {fomo_cost:.2f} USDT (거래 {len(fomo_losses)}건, 관측 가능 손실 대비 {round(fomo_cost / total_loss * 100, 1) if total_loss else 0.0}%)"
            if publishable
            else f"표본 부족 — 결론 유보 (FOMO 스냅샷 N={len(eligible)}, 최소 {min_trades})"
        ),
        "recent_entry": (
            {"trade_id": str(recent.id), "symbol": recent.symbol, "fomo_index": recent.fomo_index, "components": recent.fomo_components} if recent else None
        ),
        "legacy_proxy_comparison": {
            "method": "entry_score_below_65_and_loss",
            "count": len(legacy_proxy),
            "pnl_usdt": round(sum(trade.pnl_amount for trade in legacy_proxy), 2),
            "continuity_audit_only": True,
        },
        "policy": "신규 진입 시점 스냅샷만 포함 · 과거 거래 소급 추정 없음",
    }


def _chase_pct(direction: Direction, entry_price: float, plan_price: float | None) -> float | None:
    if plan_price is None or plan_price <= 0:
        return None
    multiplier = 1.0 if direction == Direction.long else -1.0
    return round((entry_price / plan_price - 1.0) * 100 * multiplier, 3)


def _minutes(start: datetime | None, end: datetime) -> float | None:
    if start is None:
        return None
    return round(max(0.0, (end - start).total_seconds() / 60.0), 2)


def _stance_alignment(direction: Direction, held_stance: str | None) -> str:
    if held_stance == "conflicted":
        return "conflicted"
    expected = "long_leaning" if direction == Direction.long else "short_leaning"
    opposite = "short_leaning" if direction == Direction.long else "long_leaning"
    if held_stance == expected:
        return "aligned"
    if held_stance == opposite:
        return "against"
    return "unknown"
