from __future__ import annotations

from uuid import uuid4

from app.analyst.briefing import build_analyst_briefing
from app.analyst.confluence import build_confluence
from app.db.models import JudgmentScore


def test_briefing_requires_counter_evidence_for_directional_stance() -> None:
    briefing = build_analyst_briefing(symbol="TESTUSDT", timeframe="4h", analysis=_long_only_analysis(), action_plan=_action_plan())
    confluence = briefing["confluence"]

    assert confluence["stance"] == "insufficient"
    assert confluence["counter_evidence"] == []
    assert "진입하세요" not in briefing["text"]


def test_briefing_outputs_evidence_counter_scenario_and_hit_rates() -> None:
    analysis = _balanced_analysis()
    analysis["historical_backtest"] = {
        "sample_floor": 10,
        "stats": [
            {
                "label": "유동성 저점 스윕",
                "sample_size": 14,
                "win_1r_pct": 64.3,
            }
        ],
    }
    briefing = build_analyst_briefing(
        symbol="TESTUSDT",
        timeframe="4h",
        analysis=analysis,
        action_plan=_action_plan(),
        calibration_scores=_calibration_scores("liquidity_sweep", total=22, correct=15),
    )
    confluence = briefing["confluence"]

    assert confluence["stance"] in {"long_leaning", "conflicted"}
    assert confluence["counter_evidence"]
    assert briefing["scenario"]
    assert any(line.startswith("라이브") and "N=" in line for line in briefing["hit_rates"])
    assert any(line.startswith("백테스트") and "N=14" in line for line in briefing["hit_rates"])
    assert "반대 근거" in briefing["text"]
    assert "판단은 사용자 몫" in briefing["text"]


def test_conflicted_case_does_not_force_direction() -> None:
    analysis = _balanced_analysis()
    analysis["liquidity"]["sweeps"].append(
        {
            "confirmed": True,
            "side": "buy_side",
            "grade": "Strong",
            "confidence": 95,
            "expected_move": "down",
            "label": "고점 Strong 스윕",
            "time": "2026-07-06T00:00:00+00:00",
        }
    )
    analysis["wyckoff"] = {"side": "distribution", "phase": "Phase C"}
    analysis["wyckoff_phase"] = {"side": "distribution", "phase": "Phase C"}
    analysis["wyckoff_markers"] = [
        {"label": "UTAD", "confidence": 92, "side": "distribution", "time": 1, "price": 108.5}
    ]
    analysis["volume_xray"] = {"delta_ratio": -0.42, "relative_volume": 1.8}
    analysis["wyckoff_mtf"] = {"alignment": "conflicting", "htf_phase": "distribution", "htf_trend": "bearish"}
    analysis["derivatives"] = {
        "signals": {
            "as_of": "2026-07-06T00:00:00+00:00",
            "oi_price_divergence": {"state": "price_up_oi_up", "label": "가격 상승 + OI 증가"},
            "funding_state": {"state": "extreme", "funding": -0.001, "label": "펀딩 음수 극단"},
            "crowding_score": {"score": 80},
        }
    }

    confluence = build_confluence(symbol="TESTUSDT", timeframe="4h", analysis=analysis)

    assert confluence["stance"] == "conflicted"
    assert confluence["counter_evidence"]


def test_calibration_weight_adjustment_only_applies_after_sample_floor() -> None:
    analysis = _balanced_analysis()

    low_sample = build_confluence(
        symbol="TESTUSDT",
        timeframe="4h",
        analysis=analysis,
        calibration_scores=_calibration_scores("liquidity_sweep", total=19, correct=19),
    )
    enough_sample = build_confluence(
        symbol="TESTUSDT",
        timeframe="4h",
        analysis=analysis,
        calibration_scores=_calibration_scores("liquidity_sweep", total=20, correct=20),
    )

    low_liquidity = next(item for item in low_sample["long_evidence"] if item["engine"] == "liquidity")
    adjusted_liquidity = next(item for item in enough_sample["long_evidence"] if item["engine"] == "liquidity")

    assert low_liquidity["calibration"]["applied"] is False
    assert adjusted_liquidity["calibration"]["applied"] is True
    assert adjusted_liquidity["score"] > low_liquidity["score"]


def _balanced_analysis() -> dict:
    analysis = _long_only_analysis()
    analysis["price_levels"]["resistance"] = [
        {
            "price": 108.0,
            "score": 72,
            "touches": 4,
            "sources": ["swing"],
            "label": "저항 R1",
            "last_touch_at": "2026-07-06T00:00:00+00:00",
        }
    ]
    analysis["harmonic_prz"] = [
        {
            "direction": "bearish",
            "pattern": "AB=CD",
            "mid": 108.2,
            "confidence": 66,
            "basis": "하락 반전 후보 구간(PRZ)",
        }
    ]
    return analysis


def _long_only_analysis() -> dict:
    return {
        "mark_price": 100.0,
        "timeframe": "4h",
        "price_levels": {
            "support": [
                {
                    "price": 96.0,
                    "score": 86,
                    "touches": 5,
                    "sources": ["swing", "liquidity_pool"],
                    "label": "지지 S1",
                    "last_touch_at": "2026-07-06T00:00:00+00:00",
                }
            ],
            "resistance": [],
        },
        "wyckoff": {
            "side": "accumulation",
            "phase": "Phase C",
            "liquidity_crosscheck": {
                "confirmations": [{"wyckoff_event": "spring_candidate", "sweep_grade": "Strong"}],
            },
        },
        "wyckoff_phase": {"side": "accumulation", "phase": "Phase C"},
        "wyckoff_markers": [
            {
                "label": "Spring",
                "confidence": 74,
                "side": "accumulation",
                "time": 1,
                "price": 96.2,
            }
        ],
        "liquidity": {
            "sweeps": [
                {
                    "confirmed": True,
                    "side": "sell_side",
                    "grade": "Strong",
                    "confidence": 82,
                    "expected_move": "up",
                    "label": "저점 Strong 스윕",
                    "time": "2026-07-06T00:00:00+00:00",
                }
            ],
            "htf_range_sweeps": [],
            "dealing_range": {"zone": "discount", "label": "디스카운트"},
            "structure_shift": {"event": "BOS", "direction": "up", "label": "상방 구조 돌파"},
        },
        "harmonic_prz": [],
        "volume_profile": {"poc_price": 98.0},
        "volume_xray": {"delta_ratio": 0.28, "relative_volume": 1.8},
        "derivatives": {"signals": {}},
        "wyckoff_mtf": {"alignment": "aligned", "htf_phase": "accumulation", "htf_trend": "bullish"},
    }


def _action_plan() -> dict:
    return {
        "invalidation": {
            "price": 96.0,
            "basis": "지지 S1",
            "distance_pct": -4.0,
            "action": "이탈 시 손절 검토",
        },
        "take_profit": [
            {
                "price": 108.0,
                "basis": "저항 R1",
                "distance_pct": 8.0,
                "action": "부분 익절 검토",
            }
        ],
        "watch_triggers": [],
    }


def _calibration_scores(judgment_type: str, *, total: int, correct: int) -> list[JudgmentScore]:
    scores = []
    for index in range(total):
        scores.append(
            JudgmentScore(
                judgment_id=f"{judgment_type}:{index}",
                position_id=uuid4(),
                trade_id=None,
                judgment_type=judgment_type,
                claim={},
                confidence=70,
                outcome="correct" if index < correct else "wrong",
                detail="test",
                metrics={},
            )
        )
    return scores
