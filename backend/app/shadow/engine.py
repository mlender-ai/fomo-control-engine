from uuid import uuid4

from app.db.models import (
    ShadowAttribution,
    ShadowExtractRequest,
    ShadowProfile,
    ShadowRule,
    Trade,
)


class ShadowSampleError(ValueError):
    pass


def extract_shadow_profile(trades: list[Trade], request: ShadowExtractRequest) -> ShadowProfile:
    completed = sorted(trades, key=lambda trade: trade.created_at)
    profitable = [trade for trade in completed if trade.pnl_percent > 0]
    losing = [trade for trade in completed if trade.pnl_percent <= 0]
    if len(completed) < request.min_trades or len(profitable) < request.min_profitable_trades:
        raise ShadowSampleError(f"샘플이 부족합니다. 최소 완료 거래 {request.min_trades}건, 수익 거래 {request.min_profitable_trades}건이 필요합니다.")

    high_score_winners = [trade for trade in profitable if (trade.entry_score or 0) >= 75]
    low_fomo_winners = [trade for trade in profitable if trade.entry_score is not None and (trade.exit_score or trade.entry_score) >= trade.entry_score - 10]
    rules = [
        _rule(
            "R1",
            "Entry Score가 75 이상인 거래에서 성과가 상대적으로 좋았습니다.",
            {"entry_score_min": 75},
            high_score_winners,
            completed,
        ),
        _rule(
            "R2",
            "진입 후 점수가 크게 무너지지 않은 거래가 좋은 샘플에 많았습니다.",
            {"score_drop_min": -10},
            low_fomo_winners,
            completed,
        ),
    ]
    rules = [rule for rule in rules if rule.support_count > 0]
    fomo_trades = [trade for trade in completed if (trade.entry_score or 100) < 65 and trade.pnl_percent < 0]
    attribution = ShadowAttribution(
        noise_trades_pnl=round(
            sum(trade.pnl_amount for trade in completed if not _matches_any_rule(trade, rules)),
            2,
        ),
        fomo_trades_pnl=round(sum(trade.pnl_amount for trade in fomo_trades), 2),
        late_exit_pnl=round(
            sum(
                trade.pnl_amount
                for trade in losing
                if trade.exit_score is not None and trade.entry_score is not None and trade.exit_score < trade.entry_score - 20
            ),
            2,
        ),
        counterfactual_trades=[
            {
                "trade_id": str(trade.id),
                "symbol": trade.symbol,
                "pnl_amount": trade.pnl_amount,
                "reason": "low_entry_score_loss",
            }
            for trade in fomo_trades[:5]
        ],
    )
    profile_text = (
        f"최근 완료 거래 {len(completed)}건을 분석했습니다. 수익 거래는 {len(profitable)}건, 손실 거래는 {len(losing)}건입니다. "
        "이 분석은 매매 조언이 아니라 과거 행동 패턴 복기입니다."
    )
    return ShadowProfile(
        shadow_id=f"shadow_{uuid4().hex[:8]}",
        total_trades=len(completed),
        profitable_trades=len(profitable),
        losing_trades=len(losing),
        date_range=(completed[0].created_at, completed[-1].created_at),
        profile_text=profile_text,
        rules=rules,
        fomo_patterns=[
            {
                "pattern": "low_entry_score_loss",
                "count": len(fomo_trades),
                "pnl": attribution.fomo_trades_pnl,
            }
        ],
        common_mistakes=[
            {"mistake": "entry_score_below_65", "count": len(fomo_trades)},
            {
                "mistake": "late_exit_after_score_drop",
                "count": len([trade for trade in losing if trade.exit_score and trade.entry_score and trade.exit_score < trade.entry_score - 20]),
            },
        ],
        attribution=attribution,
    )


def compare_shadow_profile(profile: ShadowProfile, trades: list[Trade]) -> dict:
    real_total = round(sum(trade.pnl_amount for trade in trades), 2)
    filtered = [trade for trade in trades if _matches_any_rule(trade, profile.rules)]
    shadow_total = round(sum(trade.pnl_amount for trade in filtered), 2)
    return {
        "real_total_pnl": real_total,
        "shadow_filtered_pnl": shadow_total,
        "delta_pnl": round(shadow_total - real_total, 2),
        "attribution": profile.attribution.model_dump(mode="json"),
    }


def _rule(
    rule_id: str,
    text: str,
    conditions: dict,
    sample: list[Trade],
    all_trades: list[Trade],
) -> ShadowRule:
    avg_pnl = sum(trade.pnl_percent for trade in sample) / len(sample) if sample else 0
    avg_holding = int(sum(trade.holding_minutes for trade in sample) / len(sample)) if sample else 0
    return ShadowRule(
        rule_id=rule_id,
        human_text=text,
        entry_conditions=conditions,
        support_count=len(sample),
        coverage_rate=round(len(sample) / len(all_trades), 2) if all_trades else 0,
        avg_pnl=round(avg_pnl, 2),
        avg_holding_minutes=avg_holding,
        sample_trade_ids=[str(trade.id) for trade in sample[:10]],
    )


def _matches_any_rule(trade: Trade, rules: list[ShadowRule]) -> bool:
    for rule in rules:
        conditions = rule.entry_conditions
        if "entry_score_min" in conditions and (trade.entry_score or 0) >= conditions["entry_score_min"]:
            return True
        if "score_drop_min" in conditions and trade.entry_score is not None and trade.exit_score is not None:
            if trade.exit_score - trade.entry_score >= conditions["score_drop_min"]:
                return True
    return False
