from app.db.models import Direction, Position, Report, ScoreBreakdown
from app.positions.engine import build_position_state, calculate_liquidation_distance, make_insight, make_snapshot


def _report(entry_score: int = 86, risk: int = 18) -> Report:
    return Report(
        symbol="BTCUSDT",
        timeframe="4h",
        price=110.0,
        change_24h=2.4,
        entry_score=entry_score,
        scores=ScoreBreakdown(
            structure=90,
            volume=88,
            liquidity=84,
            momentum=86,
            risk=risk,
            fomo=32,
        ),
        state_label="강한 진입 후보군",
        raw_json={
            "indicators": {
                "rsi": 58,
                "macd_histogram": 0.4,
                "last_close": 110.0,
                "previous_close": 108.0,
                "bollinger_upper": 116.0,
                "bollinger_lower": 101.0,
                "relative_volume": 1.35,
                "atr": 2.8,
            },
            "structure": {
                "trend": {
                    "direction": "neutral_to_bullish",
                    "higher_low": True,
                    "break_of_structure": True,
                },
                "wyckoff": {
                    "accumulation_score": 64,
                    "distribution_score": 22,
                    "phase_hint": "markup_candidate",
                    "spring_candidate": False,
                    "sos_confirmed": True,
                },
            },
            "liquidity": {
                "open_interest_change": "stable",
                "funding_rate_state": "neutral",
            },
        },
        report="deterministic report",
        provider="mock",
    )


def test_position_state_classifies_healthy_when_thesis_and_risk_are_intact() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100.0,
        quantity=0.5,
        leverage=2,
        current_price=110.0,
        liquidation_price=80.0,
        entry_score=81,
        entry_memo="상승 구조 돌파 후 지지 확인",
    )

    state = build_position_state(position, _report(), [])

    assert state["status"] == "healthy"
    assert state["status_label"] == "진입 논리 유지"
    assert state["pnl_percent"] == 20.0
    assert state["liquidation_distance_pct"] == 27.27
    components = state["score_json"]["health_components"]
    assert state["severity_rank"] == 0
    assert components["survival"] >= 90
    assert components["pnl_state"] >= 90
    assert components["thesis_integrity"] >= 70


def test_position_state_marks_critical_when_liquidation_distance_is_tight() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100.0,
        quantity=0.5,
        leverage=10,
        current_price=110.0,
        liquidation_price=107.0,
        entry_score=84,
    )

    state = build_position_state(position, _report(), [])

    assert calculate_liquidation_distance(position, 110.0) == 2.73
    assert state["status"] == "critical"
    assert state["risk_score"] >= 90


def test_position_health_v2_directly_penalizes_large_unrealized_loss() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.short,
        entry_price=100.0,
        quantity=0.5,
        leverage=10,
        current_price=109.7,
        liquidation_price=None,
        entry_score=84,
    )

    state = build_position_state(position, _report(), [])
    components = state["score_json"]["health_components"]

    assert state["pnl_percent"] == -97.0
    assert state["status"] == "critical"
    assert state["health_score"] <= 25
    assert state["severity_rank"] == 4
    assert components["formula_version"] == "health_v2"
    assert components["pnl_state"] == 0
    assert components["survival"] <= 30
    assert components["direction_alignment"] < 50


def test_position_health_v2_caps_large_loss_with_tight_liquidation_distance() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100.0,
        quantity=0.5,
        leverage=10,
        current_price=94.2,
        liquidation_price=90.432,
        entry_score=84,
    )

    state = build_position_state(position, _report(), [])
    components = state["score_json"]["health_components"]

    assert state["pnl_percent"] == -58.0
    assert state["liquidation_distance_pct"] == 4.0
    assert state["health_score"] <= 20
    assert state["status"] == "critical"
    assert components["pnl_state"] <= 10
    assert components["survival"] <= 10


def test_position_health_v2_keeps_profitable_position_with_buffer_healthy() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100.0,
        quantity=0.5,
        leverage=1,
        current_price=117.0,
        liquidation_price=87.75,
        entry_score=84,
    )

    state = build_position_state(position, _report(), [])

    assert state["pnl_percent"] == 17.0
    assert state["liquidation_distance_pct"] == 25.0
    assert state["health_score"] >= 65
    assert state["status"] == "healthy"


def test_short_position_thesis_drops_when_price_breaks_upward_resistance() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.short,
        entry_price=100.0,
        quantity=0.5,
        leverage=3,
        current_price=106.0,
        liquidation_price=130.0,
        entry_score=84,
        entry_direction_score=72,
    )

    state = build_position_state(position, _report(), [])
    components = state["score_json"]["health_components"]
    position_analysis = state["analysis"]["position_analysis"]

    assert position_analysis["current_direction_score"] < position_analysis["entry_direction_score"]
    assert position_analysis["thesis_delta"] < -20
    assert components["thesis_integrity"] < 50
    assert state["status"] == "thesis_weakening"


def test_position_pnl_uses_exchange_margin_when_available() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100.0,
        quantity=0.5,
        leverage=10,
        current_price=120.0,
        unrealized_pl=-25.0,
        margin_size=100.0,
        entry_score=84,
    )

    state = build_position_state(position, _report(), [])

    assert state["pnl_percent"] == -25.0
    assert state["pnl_source"] == "exchange"
    assert state["score_json"]["health_components"]["pnl_state"] == 31


def test_position_pnl_falls_back_to_computed_when_margin_missing() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100.0,
        quantity=0.5,
        leverage=10,
        current_price=120.0,
        unrealized_pl=-25.0,
        margin_size=None,
        entry_score=84,
    )

    state = build_position_state(position, _report(), [])

    assert state["pnl_percent"] == 200.0
    assert state["pnl_source"] == "computed"


def test_high_leverage_position_without_liquidation_price_is_explicit() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100.0,
        quantity=0.5,
        leverage=10,
        current_price=110.0,
        liquidation_price=None,
        entry_score=84,
    )

    state = build_position_state(position, _report(), [])

    assert state["status"] in {"watch", "thesis_weakening", "critical"}
    assert "LIQUIDATION_PRICE_MISSING_HIGH_LEVERAGE" in state["analysis"]["reason_codes"]


def test_position_insight_explains_snapshot_without_recalculating_scores() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100.0,
        quantity=0.5,
        current_price=110.0,
        liquidation_price=80.0,
        entry_score=81,
    )
    state = build_position_state(position, _report(), [])
    snapshot = make_snapshot(position, state)
    insight = make_insight(position, snapshot)

    assert insight.health_score == snapshot.health_score
    assert insight.input_json == snapshot.analysis_json
    assert "매수/매도 지시가 아닙니다" not in insight.insight_text
