from app.db.models import ScoreBreakdown


def score_volume(indicators: dict) -> int:
    relative_volume = indicators["relative_volume"]
    score = 46
    score += 25 if relative_volume >= 1.6 else 14 if relative_volume >= 1.25 else 0
    score += 12 if indicators["last_close"] > indicators["previous_close"] else -5
    score += 9 if indicators["macd_histogram"] > -0.2 else 0
    return max(0, min(100, int(score)))


def score_momentum(indicators: dict) -> int:
    rsi = indicators["rsi"]
    score = 50
    if 28 <= rsi <= 45:
        score += 20
    elif 45 < rsi <= 62:
        score += 12
    elif rsi > 72:
        score -= 22
    score += 12 if indicators["last_close"] > indicators["bollinger_lower"] else 0
    score += 10 if indicators["macd_histogram"] > 0 else 3
    return max(0, min(100, int(score)))


def score_risk(snapshot_price: float, indicators: dict) -> int:
    atr_percent = (indicators["atr"] / snapshot_price) * 100 if snapshot_price else 0
    risk = 28
    risk += 18 if atr_percent > 5 else 8 if atr_percent > 3 else 0
    risk += 15 if indicators["rsi"] > 70 else 0
    risk += 12 if indicators["last_close"] > indicators["bollinger_upper"] else 0
    risk += 10 if indicators["relative_volume"] > 2.4 else 0
    return max(0, min(100, int(risk)))


def score_fomo(entry_score: int, indicators: dict, change_24h: float, funding_rate: float) -> int:
    fomo = 22
    fomo += 28 if change_24h > 8 else 18 if change_24h > 5 else 10 if change_24h > 2.5 else 0
    fomo += 20 if indicators["relative_volume"] > 1.9 else 10 if indicators["relative_volume"] > 1.45 else 0
    fomo += 15 if funding_rate > 0.018 else 5 if funding_rate > 0.01 else 0
    fomo += 24 if indicators["rsi"] > 72 else 12 if indicators["rsi"] > 62 else 0
    fomo += 18 if entry_score < 70 and change_24h > 2 else 0
    return max(0, min(100, int(fomo)))


def calculate_entry_score(
    structure_score: int,
    volume_score: int,
    liquidity_score: int,
    momentum_score: int,
    risk_score: int,
) -> int:
    score = (
        structure_score * 0.30
        + volume_score * 0.25
        + liquidity_score * 0.20
        + momentum_score * 0.15
        + (100 - risk_score) * 0.10
    )
    return int(round(score))


def state_label(entry_score: int, fomo_index: int) -> str:
    if fomo_index >= 75 and entry_score < 70:
        return "FOMO 경고"
    if entry_score >= 85:
        return "강한 진입 후보군"
    if entry_score >= 75:
        return "진입 후보군"
    if entry_score >= 65:
        return "관찰 가치 있음"
    if entry_score >= 50:
        return "관망 우선"
    return "진입 근거 부족"


def build_breakdown(snapshot_price: float, change_24h: float, funding_rate: float, structure: dict, liquidity: dict, indicators: dict) -> tuple[int, ScoreBreakdown]:
    volume = score_volume(indicators)
    momentum = score_momentum(indicators)
    risk = score_risk(snapshot_price, indicators)
    entry = calculate_entry_score(
        structure["structure_score"],
        volume,
        liquidity["liquidity_score"],
        momentum,
        risk,
    )
    fomo = score_fomo(entry, indicators, change_24h, funding_rate)
    return entry, ScoreBreakdown(
        structure=structure["structure_score"],
        volume=volume,
        liquidity=liquidity["liquidity_score"],
        momentum=momentum,
        risk=risk,
        fomo=fomo,
    )
