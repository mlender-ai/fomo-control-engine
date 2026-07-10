"""WO-FCE-56 — 캔들 앵커드 히스테리시스: 시간축 규약 회귀 테스트.

상태(EMA·persist·candles_in_state)는 **확정 캔들에서만** 전진한다. 호출 빈도(워커 90s·
스카우트 15m·온디맨드 연타)는 상태에 영향을 주지 못하고, 미마감 캔들 관측은 preview로만
노출된다. 같은 확정 캔들 = 같은 상태(재현성).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.analyst.confluence import build_confluence

T0 = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)  # 캔들 오픈 (4h 경계)
BAR = timedelta(minutes=240)


def _analysis(long_s: float, short_s: float, last_candle_at: datetime) -> dict:
    up = long_s >= short_s
    iso = last_candle_at.isoformat()
    return {
        "mark_price": 100.0,
        "as_of": iso,
        "data_quality": {"last_candle_at": iso},
        "price_levels": {
            "support": [{"price": 96.0, "score": max(1, min(99, long_s)), "touches": 4, "last_touch_at": iso}],
            "resistance": [{"price": 104.0, "score": max(1, min(99, short_s)), "touches": 4, "last_touch_at": iso}],
        },
        "wyckoff": {
            "side": "accumulation" if up else "distribution",
            "accumulation_score": max(1, min(99, long_s)),
            "distribution_score": max(1, min(99, short_s)),
        },
        "wyckoff_phase": {"side": "accumulation" if up else "distribution"},
        "wyckoff_markers": [],
        "liquidity": {"sweeps": [], "htf_range_sweeps": []},
        "volume_profile": {"poc_price": 99.0 if up else 101.0},
        "derivatives": {"signals": {}},
        "wyckoff_mtf": {"htf_phase": "undetermined", "htf_trend": "neutral", "alignment": "neutral"},
    }


def _state_core(state: dict) -> dict:
    """재현성 비교 대상 = 상태 본체. preview는 관측(비상태)이라 제외 — docs §시간축."""
    return {k: v for k, v in state.items() if k != "preview"}


def _bootstrap_long_state() -> dict:
    """T0 캔들이 마감된 시점에 롱 상태 부트스트랩 (앵커 = T0)."""
    c = build_confluence(symbol="X", timeframe="4h", analysis=_analysis(88, 52, T0), generated_at=T0 + BAR)
    assert c["stance_state"]["last_bar_at"] == T0.isoformat()
    return c["stance_state"]


def test_same_confirmed_candle_repeated_calls_state_identical() -> None:
    """재현성: 동일 확정 캔들에서 N회 연속 호출 → 상태 바이트 동일."""
    prior = _bootstrap_long_state()
    t1 = T0 + BAR  # 다음 캔들 오픈 (진행 중)
    states = []
    state = prior
    for k in range(1, 6):  # 90초 간격 5회
        c = build_confluence(symbol="X", timeframe="4h", analysis=_analysis(60, 80, t1), generated_at=t1 + timedelta(seconds=90 * k), prior_state=state)
        state = c["stance_state"]
        states.append(_state_core(state))
    assert all(s == states[0] for s in states)
    assert _state_core(state) == {**_state_core(prior), "flipped": False}


def test_90s_polling_cannot_satisfy_persist_within_one_candle() -> None:
    """3분 관성 붕괴 재현→수리: 90초 간격 20회 + 확정 캔들 1개 → persist 미충족·무flip.
    (구현 결함 하에서는 flip_persist=2가 3분에 충족돼 flip이 발생했다.)"""
    state = _bootstrap_long_state()
    t1 = T0 + BAR
    flips = 0
    for k in range(1, 21):
        c = build_confluence(symbol="X", timeframe="4h", analysis=_analysis(50, 90, t1), generated_at=t1 + timedelta(seconds=90 * k), prior_state=state)
        state = c["stance_state"]
        flips += 1 if state["flipped"] else 0

    assert flips == 0
    assert state["stance"] == "long_leaning"  # 관성 유지
    assert int(state["pending_count"] or 0) == 0  # persist가 호출로 증가하지 않음


def test_unclosed_candle_spike_moves_preview_not_state() -> None:
    """상태/프리뷰 분리: 미마감 캔들 스파이크 → 상태 무변, preview만 변동."""
    prior = _bootstrap_long_state()
    t1 = T0 + BAR
    c = build_confluence(symbol="X", timeframe="4h", analysis=_analysis(50, 92, t1), generated_at=t1 + timedelta(minutes=30), prior_state=prior)
    state = c["stance_state"]

    assert _state_core(state) == {**_state_core(prior), "flipped": False}  # 상태 무변
    assert state["preview"]["raw_stance"] == "short_leaning"  # 프리뷰는 최신 관측
    assert c["stance"] == "long_leaning"  # held 그대로


def test_two_confirmed_candles_reversal_flips() -> None:
    """전환 감지 보존: 확정 캔들 2개 연속 우세 역전 → flip 발생."""
    state = _bootstrap_long_state()
    flips = 0
    for i in (1, 2):  # T1, T2 캔들이 각각 마감된 시점의 관측
        candle = T0 + BAR * i
        c = build_confluence(symbol="X", timeframe="4h", analysis=_analysis(48, 92, candle), generated_at=candle + BAR, prior_state=state)
        state = c["stance_state"]
        flips += 1 if state["flipped"] else 0

    assert state["stance"] == "short_leaning"
    assert flips == 1  # 정확히 한 번, 두 번째 확정 캔들에서


def test_bootstrap_never_flips() -> None:
    """부트스트랩 flip 오발화 방지: prior 없음(또는 무효)이면 flipped는 항상 False."""
    c = build_confluence(symbol="X", timeframe="4h", analysis=_analysis(48, 92, T0), generated_at=T0 + BAR, prior_state=None)
    assert c["stance_state"]["flipped"] is False
    # 무효 prior(과거 스키마 잔재)도 동일.
    c2 = build_confluence(symbol="X", timeframe="4h", analysis=_analysis(48, 92, T0), generated_at=T0 + BAR, prior_state={"stance": "unknown_value"})
    assert c2["stance_state"]["flipped"] is False


def test_multi_candle_gap_advances_once_but_counts_gap() -> None:
    """갭 규약: 여러 캔들 갭이면 관측은 1스텝 전진, candles_in_state에 갭 반영 (docs §시간축)."""
    state = _bootstrap_long_state()
    candles_before = int(state["candles_in_state"])
    # 3캔들 뒤의 확정 캔들에서 같은 방향 관측 (미스캔 구간).
    candle = T0 + BAR * 3
    c = build_confluence(symbol="X", timeframe="4h", analysis=_analysis(88, 52, candle), generated_at=candle + BAR, prior_state=state)
    state = c["stance_state"]

    assert state["stance"] == "long_leaning"
    assert int(state["candles_in_state"]) == candles_before + 3  # 갭 3캔들 반영
    assert state["last_bar_at"] == candle.isoformat()


def test_stale_prior_older_bar_freezes() -> None:
    """앵커 역행(더 오래된 캔들 데이터가 뒤늦게 도착) → 상태 무변 (시계 역주행 방지)."""
    state = _bootstrap_long_state()  # 앵커 = T0
    older = T0 - BAR
    c = build_confluence(symbol="X", timeframe="4h", analysis=_analysis(50, 92, older), generated_at=T0 + BAR, prior_state=state)
    assert _state_core(c["stance_state"]) == {**_state_core(state), "flipped": False}
