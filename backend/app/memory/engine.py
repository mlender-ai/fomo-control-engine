from uuid import UUID

from app.db.models import DecisionMemory, ShadowProfile, Trade


def memory_from_trade(trade: Trade) -> DecisionMemory:
    memory_type = "winning_pattern" if trade.pnl_percent >= 0 else "losing_pattern"
    if trade.entry_score is not None and trade.entry_score < 65 and trade.pnl_percent < 0:
        memory_type = "fomo_mistake"
    return DecisionMemory(
        symbol=trade.symbol,
        memory_type=memory_type,
        source_trade_id=trade.id,
        summary=f"{trade.symbol} {trade.direction.value} trade closed at {trade.pnl_percent:.2f}% with entry score {trade.entry_score}.",
        evidence={
            "pnl_percent": trade.pnl_percent,
            "entry_score": trade.entry_score,
            "exit_score": trade.exit_score,
            "holding_minutes": trade.holding_minutes,
            "exit_reason": trade.exit_reason,
        },
        weight=1.2 if memory_type == "fomo_mistake" else 1.0,
    )


def memory_from_shadow(profile: ShadowProfile) -> DecisionMemory:
    return DecisionMemory(
        symbol=None,
        memory_type="risk_rule",
        summary=f"Shadow profile found {len(profile.rules)} rule(s) from {profile.total_trades} completed trades.",
        evidence={
            "shadow_id": profile.shadow_id,
            "rules": [rule.model_dump(mode="json") for rule in profile.rules],
            "fomo_patterns": profile.fomo_patterns,
            "common_mistakes": profile.common_mistakes,
        },
        weight=1.0,
    )


def memory_from_validation(run_id: UUID, symbol: str, summary: dict, warnings: list[str]) -> DecisionMemory:
    return DecisionMemory(
        symbol=symbol,
        memory_type="monitoring_lesson",
        source_research_run_id=None,
        summary=f"Validation run {run_id} produced win rate {summary.get('win_rate', 0):.2f} with {len(warnings)} warning(s).",
        evidence={
            "validation_run_id": str(run_id),
            "summary": summary,
            "warnings": warnings,
        },
        weight=1.0,
    )
