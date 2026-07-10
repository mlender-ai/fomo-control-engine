from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from app.analyst.briefing import build_analyst_briefing
from app.analyst.confluence import build_confluence
from app.db.models import JudgmentScore, utc_now


def _recent_iso(hours: float) -> str:
    """generated_at(=utc_now) 대비 상대 시각. WO-51 시간 감쇠 도입 후 픽스처가
    실행 시점 드리프트로 인해 임의 감쇠되지 않도록 항상 '최근'으로 고정한다."""
    return (utc_now() - timedelta(hours=hours)).isoformat()


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
            "time": _recent_iso(2),
        }
    )
    analysis["wyckoff"] = {"side": "distribution", "phase": "Phase C"}
    analysis["wyckoff_phase"] = {"side": "distribution", "phase": "Phase C"}
    analysis["wyckoff_markers"] = [{"label": "UTAD", "confidence": 92, "side": "distribution", "time": _recent_iso(2), "price": 108.5}]
    analysis["volume_xray"] = {"delta_ratio": -0.42, "relative_volume": 1.8}
    analysis["wyckoff_mtf"] = {"alignment": "conflicting", "htf_phase": "distribution", "htf_trend": "bearish"}
    analysis["derivatives"] = {
        "signals": {
            "as_of": _recent_iso(2),
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


def test_recency_decays_stale_level_direction_contribution() -> None:
    """WO-51: 3주 전 마지막 터치 저항의 방향 기여가 신선 증거 대비 유의하게 감쇠하되,
    존재(floor)는 유지된다."""
    now = utc_now()
    fresh = _balanced_analysis()
    fresh["price_levels"]["resistance"][0]["last_touch_at"] = _recent_iso(6)
    stale = _balanced_analysis()
    stale["price_levels"]["resistance"][0]["last_touch_at"] = _recent_iso(24 * 21)  # 3주 전

    fresh_c = build_confluence(symbol="T", timeframe="4h", analysis=fresh, generated_at=now)
    stale_c = build_confluence(symbol="T", timeframe="4h", analysis=stale, generated_at=now)
    fresh_level = next(item for item in fresh_c["short_evidence"] if item["engine"] == "level")
    stale_level = next(item for item in stale_c["short_evidence"] if item["engine"] == "level")

    assert stale_level["recency_factor"] < fresh_level["recency_factor"]
    assert stale_level["score"] < fresh_level["score"] * 0.6  # 유의 감쇠
    assert stale_level["score"] > 0  # 존재는 유지 (structure floor)
    assert stale_level["decay_class"] == "structure"


def test_event_evidence_decays_faster_than_structure() -> None:
    """WO-51: 같은 나이라면 이벤트성(스윕)이 구조성(레벨)보다 빨리 감쇠한다."""
    now = utc_now()
    analysis = _balanced_analysis()
    aged = _recent_iso(48)  # 이틀 전
    analysis["price_levels"]["support"][0]["last_touch_at"] = aged
    analysis["liquidity"]["sweeps"][0]["time"] = aged

    confluence = build_confluence(symbol="T", timeframe="4h", analysis=analysis, generated_at=now)
    level = next(item for item in confluence["long_evidence"] if item["engine"] == "level")
    sweep = next(item for item in confluence["long_evidence"] if item["engine"] == "liquidity")

    assert sweep["recency_factor"] < level["recency_factor"]


def test_gate_thresholds_scale_invariant_under_uniform_decay() -> None:
    """WO-51 회귀 안전: 모든 증거가 동일 클래스·동일 나이면 recency가 균일하게 곱해져
    long/short 비율이 보존된다 → stance·composite(비율 기반)는 스케일 불변."""
    now = utc_now()
    fresh = build_confluence(symbol="T", timeframe="4h", analysis=_structure_only_analysis(_recent_iso(2)), generated_at=now)
    aged = build_confluence(symbol="T", timeframe="4h", analysis=_structure_only_analysis(_recent_iso(200)), generated_at=now)

    assert fresh["stance"] == aged["stance"]
    assert fresh["stance"] != "insufficient"
    assert fresh["composite_score"] == aged["composite_score"]
    # 절대 score는 감쇠로 줄어야 한다(감쇠가 실제로 작동함을 확인).
    assert aged["long_score"] < fresh["long_score"]


def test_epoch_integer_timestamp_is_parsed_for_decay() -> None:
    """WO-51: 와이코프 마커·하모닉 time은 epoch 정수 — 감쇠에 반영되어야 한다."""
    now = utc_now()
    recent_epoch = int(now.timestamp()) - 3600  # 1시간 전
    ancient_epoch = int(now.timestamp()) - 3600 * 24 * 30  # 30일 전
    recent = _balanced_analysis()
    recent["wyckoff_markers"][0]["time"] = recent_epoch
    ancient = _balanced_analysis()
    ancient["wyckoff_markers"][0]["time"] = ancient_epoch

    recent_c = build_confluence(symbol="T", timeframe="4h", analysis=recent, generated_at=now)
    ancient_c = build_confluence(symbol="T", timeframe="4h", analysis=ancient, generated_at=now)
    recent_marker = next(item for item in recent_c["long_evidence"] if item["engine"] == "wyckoff" and "Spring" in item["claim"])
    ancient_marker = next(item for item in ancient_c["long_evidence"] if item["engine"] == "wyckoff" and "Spring" in item["claim"])

    assert ancient_marker["recency_factor"] < recent_marker["recency_factor"]
    assert ancient_marker["is_stale"] is True  # 정수 epoch도 staleness 판정됨


def test_htf_uptrend_prevents_lone_short_residue_from_shorting() -> None:
    """WO-52 (SOXL 유형): HTF 상승 기준선 하에서 하위 잔존 short 증거(저항·고점스윕·하모닉)가
    단독으로 종합 하방을 만들지 못한다. 역행 증거는 감쇠만 될 뿐 0으로 죽지 않는다."""
    now = utc_now()
    confluence = build_confluence(symbol="SOXL", timeframe="4h", analysis=_htf_uptrend_short_residue(), generated_at=now)

    assert confluence["htf_context"]["bias"] == "long"
    assert confluence["stance"] != "short_leaning"  # 종합 하방 불가
    assert confluence["stance"] == "long_leaning"
    short_ev = confluence["short_evidence"]
    assert short_ev, "역행 증거가 목록에서 사라지면 안 된다 (전환 감지 보존)"
    for item in short_ev:
        assert item["score"] > 0  # 0으로 죽지 않음
        assert item["htf_factor"] < 1.0  # 감쇠는 됨
    for item in confluence["long_evidence"]:
        if item["engine"] != "mtf":
            assert item["htf_factor"] > 1.0  # 정렬 증거 증폭


def test_htf_absent_does_not_force_strong_direction() -> None:
    """WO-52 금지 규정: HTF 기준선 부재 시 하위만으로 강한 방향 확정 금지 → 종합 신뢰도 상한."""
    now = utc_now()
    analysis = _htf_uptrend_short_residue()
    analysis.pop("wyckoff_mtf")  # HTF 제거

    confluence = build_confluence(symbol="SOXL", timeframe="4h", analysis=analysis, generated_at=now)
    assert confluence["htf_context"]["available"] is False
    if confluence["stance"] in {"long_leaning", "short_leaning"}:
        assert confluence["composite_score"] <= 75.0


def test_htf_conflict_does_not_amplify_counter_evidence() -> None:
    """WO-52: 엔진이 alignment=conflicting(HTF-하위 정면 충돌)을 선언하면 앵커 배수를
    적용하지 않는다 — 되돌림이 아니라 전환/충돌이므로 증폭·감쇠 대신 _stance 가드에 맡긴다."""
    now = utc_now()
    analysis = _htf_uptrend_short_residue()
    analysis["wyckoff_mtf"] = {"htf_phase": "accumulation", "htf_trend": "bullish", "alignment": "conflicting"}

    confluence = build_confluence(symbol="SOXL", timeframe="4h", analysis=analysis, generated_at=now)
    for item in confluence["short_evidence"] + confluence["long_evidence"]:
        if item["engine"] != "mtf":
            assert item["htf_factor"] == 1.0


def test_htf_context_present_in_output() -> None:
    now = utc_now()
    confluence = build_confluence(symbol="T", timeframe="4h", analysis=_balanced_analysis(), generated_at=now)
    ctx = confluence["htf_context"]
    assert set(ctx) >= {"bias", "strength", "alignment", "available"}
    assert ctx["bias"] in {"long", "short", "neutral"}
    assert 0.0 <= ctx["strength"] <= 1.0


def test_hysteresis_absorbs_balance_zone_oscillation() -> None:
    """WO-53 (실증 재현): 균형 구간에서 raw가 4분마다 롱↔숏 깜빡여도 held stance는
    반전하지 않는다 (분단위 flip 0)."""
    base = utc_now().replace(microsecond=0)
    seq = [(85, 55), (55, 85), (85, 55), (55, 85), (85, 55), (55, 85)]
    prior = None
    flips = 0
    raw_seen = set()
    for i, (ls, ss) in enumerate(seq):
        t = base + timedelta(minutes=4 * i)
        c = build_confluence(symbol="X", timeframe="4h", analysis=_directional_analysis(ls, ss, t), generated_at=t, prior_state=prior)
        state = c["stance_state"]
        flips += 1 if state["flipped"] else 0
        raw_seen.add(c["raw_stance"])
        prior = state

    assert {"long_leaning", "short_leaning"} <= raw_seen  # raw는 실제로 진동했다
    assert flips == 0  # 그러나 held는 무반전
    assert prior["stance"] == "long_leaning"  # 최초 방향 유지


def test_hysteresis_flips_on_sustained_reversal() -> None:
    """WO-53: 진짜 전환(지속적 우세 역전)은 문턱 통과 후 flip — 전환 감지 보존(둔감 방지)."""
    base = utc_now().replace(microsecond=0)
    seq = [(88, 52), (88, 52), (52, 90), (50, 92), (48, 94)]  # 롱 유지 후 지속 숏
    prior = None
    flips = 0
    for i, (ls, ss) in enumerate(seq):
        t = base + timedelta(minutes=4 * i)
        c = build_confluence(symbol="X", timeframe="4h", analysis=_directional_analysis(ls, ss, t), generated_at=t, prior_state=prior)
        prior = c["stance_state"]
        flips += 1 if prior["flipped"] else 0

    assert prior["stance"] == "short_leaning"  # 결국 전환됨
    assert flips == 1  # 정확히 한 번, 스팸 아님


def test_hysteresis_exposes_transitioning_before_flip() -> None:
    """WO-53: flip 직전 transitioning 서브상태 + 진행도 노출 (사용자의 '추격' 구간)."""
    base = utc_now().replace(microsecond=0)
    long_state = build_confluence(symbol="X", timeframe="4h", analysis=_directional_analysis(88, 52, base), generated_at=base)["stance_state"]
    t1 = base + timedelta(minutes=4)
    c = build_confluence(symbol="X", timeframe="4h", analysis=_directional_analysis(52, 90, t1), generated_at=t1, prior_state=long_state)
    state = c["stance_state"]

    assert state["stance"] == "long_leaning"  # 아직 유지
    assert state["transitioning"] is True
    assert state["target"] == "short_leaning"
    assert 0.0 < state["flip_threshold_progress"] < 1.0
    assert state["flipped"] is False


def test_hysteresis_insufficient_bypasses_hold() -> None:
    """WO-53/56: 증거 부족은 방향 게이트가 이긴다 — 단, 확정 캔들 전진에서 적용된다.
    (같은 캔들 내 일시 데이터 결손은 상태를 지우지 않고 동결 — WO-56 시간축 규약)"""
    base = utc_now().replace(microsecond=0)
    long_state = build_confluence(symbol="X", timeframe="4h", analysis=_directional_analysis(88, 52, base), generated_at=base)["stance_state"]
    # 다음 확정 캔들에서 증거 부족 관측 (as_of가 새 캔들, 그 캔들은 마감됨).
    thin = {
        "mark_price": 100.0,
        "as_of": base.isoformat(),
        "data_quality": {"last_candle_at": base.isoformat()},
        "price_levels": {"support": [], "resistance": []},
        "wyckoff_mtf": {},
    }
    c = build_confluence(symbol="X", timeframe="4h", analysis=thin, generated_at=base + timedelta(minutes=244), prior_state=long_state)

    assert c["stance"] == "insufficient"
    assert c["stance_state"]["transitioning"] is False


def test_hysteresis_same_bar_data_glitch_freezes_state() -> None:
    """WO-56: 앵커 미상(타임스탬프 결손) 관측은 held 상태를 지우지 못한다 — 동결 + 프리뷰만."""
    base = utc_now().replace(microsecond=0)
    long_state = build_confluence(symbol="X", timeframe="4h", analysis=_directional_analysis(88, 52, base), generated_at=base)["stance_state"]
    thin = {"mark_price": 100.0, "price_levels": {"support": [], "resistance": []}, "wyckoff_mtf": {}}
    c = build_confluence(symbol="X", timeframe="4h", analysis=thin, generated_at=base + timedelta(minutes=4), prior_state=long_state)

    assert c["stance"] == "long_leaning"  # held 유지
    assert c["stance_state"]["preview"]["raw_stance"] == "insufficient"  # 프리뷰는 정직


def test_hysteresis_params_clamped_to_hard_bounds() -> None:
    """WO-53/39: 튜닝 오버라이드는 hard bound 내로 클램프 (추세 차단 방지 상한)."""
    base = utc_now().replace(microsecond=0)
    analysis = _directional_analysis(88, 52, base)
    c = build_confluence(symbol="X", timeframe="4h", analysis=analysis, generated_at=base, hysteresis_params={"flip_margin": 999.0, "flip_persist": 99})
    policy = c["calibration_policy"]["hysteresis_policy"]
    assert "0.6" in policy  # flip 문턱 상한
    assert "5회" in policy  # persist 상한 5


def _directional_analysis(long_s: float, short_s: float, t: object) -> dict:
    """방향 점수를 직접 제어하는 최소 분석 — 히스테리시스 시퀀스 검증용.
    지지(long)·저항(short)·국면·POC가 우세 방향을 따른다. HTF는 중립(기준선 개입 배제)."""
    up = long_s >= short_s
    iso = t.isoformat()
    return {
        "mark_price": 100.0,
        "as_of": iso,
        "data_quality": {"last_candle_at": iso},
        "price_levels": {
            "support": [{"price": 96.0, "score": long_s, "touches": 4, "last_touch_at": iso}],
            "resistance": [{"price": 104.0, "score": short_s, "touches": 4, "last_touch_at": iso}],
        },
        "wyckoff": {
            "side": "accumulation" if up else "distribution",
            "accumulation_score": long_s,
            "distribution_score": short_s,
        },
        "wyckoff_phase": {"side": "accumulation" if up else "distribution"},
        "wyckoff_markers": [],
        "liquidity": {"sweeps": [], "htf_range_sweeps": []},
        "volume_profile": {"poc_price": 99.0 if up else 101.0},
        "derivatives": {"signals": {}},
        "wyckoff_mtf": {"htf_phase": "undetermined", "htf_trend": "neutral", "alignment": "neutral"},
    }


def _htf_uptrend_short_residue() -> dict:
    """HTF 상승 + 하위 잔존 short 증거(저항·고점스윕·하모닉). SOXL 반등 유형."""
    return {
        "mark_price": 100.0,
        "timeframe": "4h",
        "as_of": _recent_iso(1),
        "data_quality": {"last_candle_at": _recent_iso(1)},
        "price_levels": {
            "support": [{"price": 94.0, "score": 70, "touches": 3, "last_touch_at": _recent_iso(8)}],
            "resistance": [{"price": 102.0, "score": 85, "touches": 4, "last_touch_at": _recent_iso(30)}],
        },
        "wyckoff": {"side": "accumulation", "accumulation_score": 68},
        "wyckoff_phase": {"side": "accumulation", "phase": "Phase C"},
        "wyckoff_markers": [],
        "liquidity": {
            "sweeps": [{"confirmed": True, "side": "buy_side", "grade": "Strong", "confidence": 80, "label": "고점 스윕", "time": _recent_iso(20)}],
            "htf_range_sweeps": [],
        },
        "harmonic_patterns": [{"direction": "bearish", "label": "AB=CD", "confidence": 70, "prz": {"mid": 102.0}, "detected_at": _recent_iso(18)}],
        "volume_profile": {"poc_price": 99.0},
        "derivatives": {"signals": {}},
        "wyckoff_mtf": {"htf_phase": "accumulation", "htf_trend": "bullish", "alignment": "aligned"},
    }


def _structure_only_analysis(touch_iso: str) -> dict:
    """구조성 증거만(레벨·POC·국면), 모두 동일 시각 — 스케일 불변 검증용."""
    return {
        "mark_price": 100.0,
        "timeframe": "4h",
        "as_of": touch_iso,
        "data_quality": {"last_candle_at": touch_iso},
        "price_levels": {
            "support": [{"price": 96.0, "score": 86, "touches": 5, "last_touch_at": touch_iso}],
            "resistance": [{"price": 108.0, "score": 60, "touches": 3, "last_touch_at": touch_iso}],
        },
        "wyckoff": {"side": "accumulation", "accumulation_score": 70},
        "wyckoff_phase": {"side": "accumulation", "phase": "Phase C"},
        "wyckoff_markers": [],
        "liquidity": {"sweeps": [], "htf_range_sweeps": []},
        "harmonic_patterns": [],
        "volume_profile": {"poc_price": 98.0},
        "derivatives": {"signals": {}},
    }


def _balanced_analysis() -> dict:
    analysis = _long_only_analysis()
    analysis["price_levels"]["resistance"] = [
        {
            "price": 108.0,
            "score": 72,
            "touches": 4,
            "sources": ["swing"],
            "label": "저항 R1",
            "last_touch_at": _recent_iso(6),
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
                    "last_touch_at": _recent_iso(6),
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
                "time": _recent_iso(2),
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
                    "time": _recent_iso(2),
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
