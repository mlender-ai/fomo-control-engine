from app.db.models import Direction, Position, PositionHealthComponents, Report, ScoreBreakdown
from app.positions.engine import (
    build_position_state,
    calculate_liquidation_distance,
    health_score_integrity,
    make_insight,
    make_snapshot,
)


def test_health_score_integrity_explains_large_loss_cap() -> None:
    components = PositionHealthComponents(
        survival=100,
        pnl_state=0,
        thesis_integrity=100,
        structure=69,
        flow=73,
        chart_structure=69,
        risk_safety=100,
        momentum_volume=73,
        liquidity_funding=73,
        pnl_protection=0,
        liquidation_buffer=100,
        direction_alignment=74,
        formula_version="health_v2_derivatives",
    )

    integrity = health_score_integrity(components)

    assert integrity == {
        "weighted_score_before_cap": 71,
        "cap_reason": "pnl_state_zero",
        "cap_value": 25,
        "final_score": 25,
        "formula_version": "health_v2_derivatives",
        "score_consistent": True,
    }


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


def _report_with_derivatives(derivatives: dict) -> Report:
    report = _report()
    report.raw_json["liquidity"]["derivatives"] = derivatives
    return report


def _derivatives_payload(
    *,
    divergence: str,
    funding: float,
    funding_state: str,
    ratio: float,
    crowding: float,
) -> dict:
    return {
        "as_of": "2026-07-06T01:00:00+00:00",
        "latest": {
            "provider": "bitget",
            "as_of": "2026-07-06T01:00:00+00:00",
            "funding_rate": funding,
            "open_interest_change_pct": 4.0,
            "long_short_ratio": ratio,
        },
        "signals": {
            "oi_price_divergence": {
                "state": divergence,
                "label": divergence,
                "price_change_pct": 3.0,
                "oi_change_pct": 4.0,
            },
            "funding_state": {
                "state": funding_state,
                "label": funding_state,
                "funding": funding,
                "percentile": 95,
            },
            "crowding_score": {"score": crowding, "label": "쏠림"},
        },
    }


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


def test_derivative_flow_scores_long_alignment_and_adverse_crowding() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100.0,
        quantity=0.5,
        leverage=3,
        current_price=105.0,
        liquidation_price=80.0,
        entry_score=84,
    )
    aligned = build_position_state(
        position,
        _report_with_derivatives(
            _derivatives_payload(
                divergence="price_up_oi_up",
                funding=0.0,
                funding_state="neutral",
                ratio=1.0,
                crowding=30,
            )
        ),
        [],
    )
    adverse = build_position_state(
        position,
        _report_with_derivatives(
            _derivatives_payload(
                divergence="price_down_oi_up",
                funding=0.0012,
                funding_state="extreme",
                ratio=1.45,
                crowding=82,
            )
        ),
        [],
    )

    aligned_flow = aligned["score_json"]["health_components"]["flow"]
    adverse_flow = adverse["score_json"]["health_components"]["flow"]
    assert aligned["score_json"]["health_components"]["formula_version"] == "health_v2_derivatives"
    assert adverse["score_json"]["health_components"]["formula_version"] == "health_v2_derivatives"
    assert aligned_flow >= 80
    assert adverse_flow <= 10
    assert aligned_flow - adverse_flow >= 70


def test_derivative_flow_scores_short_alignment_and_adverse_crowding() -> None:
    position = Position(
        symbol="BTCUSDT",
        direction=Direction.short,
        entry_price=100.0,
        quantity=0.5,
        leverage=3,
        current_price=95.0,
        liquidation_price=125.0,
        entry_score=84,
    )
    aligned = build_position_state(
        position,
        _report_with_derivatives(
            _derivatives_payload(
                divergence="price_down_oi_up",
                funding=0.0012,
                funding_state="extreme",
                ratio=1.45,
                crowding=82,
            )
        ),
        [],
    )
    adverse = build_position_state(
        position,
        _report_with_derivatives(
            _derivatives_payload(
                divergence="price_up_oi_up",
                funding=-0.0012,
                funding_state="extreme",
                ratio=0.74,
                crowding=82,
            )
        ),
        [],
    )

    aligned_flow = aligned["score_json"]["health_components"]["flow"]
    adverse_flow = adverse["score_json"]["health_components"]["flow"]
    assert aligned_flow >= 85
    assert adverse_flow <= 10
    assert aligned_flow - adverse_flow >= 75


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
