from app.db.models import MarketSnapshot


def analyze_structure(snapshot: MarketSnapshot, indicators: dict) -> dict:
    candles = snapshot.candles
    recent_lows = [candle.low for candle in candles[-12:]]
    prior_lows = [candle.low for candle in candles[-28:-12]]
    recent_highs = [candle.high for candle in candles[-12:]]
    prior_highs = [candle.high for candle in candles[-28:-12]]
    higher_low = min(recent_lows) > min(prior_lows)
    break_of_structure = max(recent_highs) > max(prior_highs)
    downtrend_weakening = candles[-1].close > indicators["twenty_close"]
    spring_candidate = candles[-1].low < min(prior_lows) and candles[-1].close > min(prior_lows)

    score = 45
    score += 16 if higher_low else -5
    score += 14 if break_of_structure else 0
    score += 12 if downtrend_weakening else -4
    score += 10 if spring_candidate else 0
    score += 8 if indicators["relative_volume"] > 1.4 and candles[-1].close > candles[-1].open else 0
    score = max(0, min(100, score))

    return {
        "structure_score": score,
        "wyckoff": {
            "accumulation_score": max(15, min(95, score + (8 if spring_candidate else 0))),
            "distribution_score": max(5, min(90, 100 - score - (8 if higher_low else 0))),
            "phase_hint": "early_accumulation" if score >= 70 else "neutral_range",
            "spring_candidate": spring_candidate,
            "sos_confirmed": break_of_structure and indicators["relative_volume"] > 1.25,
        },
        "trend": {
            "direction": "neutral_to_bullish" if downtrend_weakening else "bearish_to_neutral",
            "higher_low": higher_low,
            "break_of_structure": break_of_structure,
        },
    }

