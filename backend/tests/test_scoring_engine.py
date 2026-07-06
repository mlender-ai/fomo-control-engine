from app.scoring.engine import (
    calculate_entry_score,
    score_fomo,
    score_momentum,
    score_risk,
    score_volume,
    state_label,
)


def test_entry_score_uses_inverse_risk_weight() -> None:
    score = calculate_entry_score(
        structure_score=79,
        volume_score=88,
        liquidity_score=91,
        momentum_score=72,
        risk_score=31,
    )

    assert score == 82


def test_score_helpers_are_bounded() -> None:
    indicators = {
        "relative_volume": 3.0,
        "last_close": 120.0,
        "previous_close": 100.0,
        "macd_histogram": 1.2,
        "rsi": 78.0,
        "bollinger_lower": 90.0,
        "bollinger_upper": 115.0,
        "atr": 8.0,
    }

    assert 0 <= score_volume(indicators) <= 100
    assert 0 <= score_momentum(indicators) <= 100
    assert 0 <= score_risk(120.0, indicators) <= 100
    assert 0 <= score_fomo(62, indicators, change_24h=9.5, funding_rate=0.02) <= 100


def test_state_label_prioritizes_fomo_warning_when_score_is_not_strong() -> None:
    assert state_label(entry_score=63, fomo_index=82) == "FOMO 경고"
    assert state_label(entry_score=88, fomo_index=82) == "강한 진입 후보군"
