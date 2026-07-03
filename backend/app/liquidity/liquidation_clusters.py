from app.db.models import LiquidationAnalysis, LiquidationCluster, Report
from app.liquidity.cascade_risk import cascade_risk_label


def analyze_liquidation(report: Report) -> LiquidationAnalysis:
    price = report.price
    liquidity = report.raw_json.get("liquidity", {})
    upper_strength = float(liquidity.get("upper_liquidity", max(40, report.scores.liquidity)))
    lower_strength = float(liquidity.get("lower_liquidity", max(35, 100 - report.scores.liquidity)))
    upper_distance = 2.5 + max(0, 80 - upper_strength) / 20
    lower_distance = -(2.5 + max(0, 80 - lower_strength) / 20)
    upper = LiquidationCluster(
        price=round(price * (1 + upper_distance / 100), 6),
        side="short_liquidation",
        magnitude=round(upper_strength, 2),
        distance_pct=round(upper_distance, 2),
        priority=_priority(upper_strength),
    )
    lower = LiquidationCluster(
        price=round(price * (1 + lower_distance / 100), 6),
        side="long_liquidation",
        magnitude=round(lower_strength, 2),
        distance_pct=round(lower_distance, 2),
        priority=_priority(lower_strength),
    )
    asymmetry = int(max(0, min(100, 50 + (upper_strength - lower_strength) / 2)))
    dominant = "upside" if upper_strength > lower_strength + 8 else "downside" if lower_strength > upper_strength + 8 else "balanced"
    risk_up = cascade_risk_label(upper.magnitude, upper.distance_pct)
    risk_down = cascade_risk_label(lower.magnitude, lower.distance_pct)
    return LiquidationAnalysis(
        symbol=report.symbol,
        timeframe=report.timeframe,
        current_price=price,
        liquidity_score=report.scores.liquidity,
        upper_clusters=[upper],
        lower_clusters=[lower],
        dominant_magnet=dominant,
        asymmetry_score=asymmetry,
        cascade_risk_up=risk_up,
        cascade_risk_down=risk_down,
        cascade_risk=f"{risk_down}_downside" if risk_down != "low" else f"{risk_up}_upside",
    )


def _priority(value: float) -> str:
    if value >= 75:
        return "high"
    if value >= 55:
        return "medium"
    return "low"
