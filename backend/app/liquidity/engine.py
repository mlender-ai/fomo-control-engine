from app.db.models import MarketSnapshot


def analyze_liquidity(snapshot: MarketSnapshot) -> dict:
    funding_neutral = abs(snapshot.funding_rate) < 0.01
    oi_positive = snapshot.open_interest_change > 0
    upper_liquidity = 58 + min(25, max(0, snapshot.open_interest_change * 1.7))
    lower_liquidity = 55 + min(20, max(0, -snapshot.open_interest_change * 1.5))
    funding_penalty = 12 if snapshot.funding_rate > 0.018 else 0
    score = int(
        max(
            0,
            min(
                100,
                (upper_liquidity * 0.55 + lower_liquidity * 0.25 + (80 if funding_neutral else 48) * 0.2) - funding_penalty,
            ),
        )
    )

    return {
        "liquidity_score": score,
        "upper_liquidity": int(max(0, min(100, upper_liquidity))),
        "lower_liquidity": int(max(0, min(100, lower_liquidity))),
        "dominant_direction": "upside_liquidity" if upper_liquidity >= lower_liquidity else "downside_liquidity",
        "open_interest_change": "increasing" if oi_positive else "decreasing",
        "funding_rate_state": "neutral" if funding_neutral else "heated" if snapshot.funding_rate > 0 else "negative",
    }
