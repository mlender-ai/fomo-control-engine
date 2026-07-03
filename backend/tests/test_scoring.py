from app.scoring.engine import calculate_entry_score


def test_entry_score_uses_inverse_risk_weight() -> None:
    score = calculate_entry_score(
        structure_score=79,
        volume_score=88,
        liquidity_score=91,
        momentum_score=72,
        risk_score=31,
    )

    assert score == 82

