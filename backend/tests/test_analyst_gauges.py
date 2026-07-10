"""WO-FCE-55 Part A — 2게이지 판정 테스트.

방향 게이지(재설계 stance 번역)와 익절 압력 게이지(결정론 3입력)는 다른 축이다.
티어2 오버레이는 validated 캘리브레이션 + 현재 방향 상위 기여만 자동 선별된다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.analyst.gauges import (
    build_direction_gauge,
    build_gauges,
    build_take_profit_gauge,
    select_tier2_overlays,
)

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


# ── 방향 게이지 ──────────────────────────────────────────────────────────────


def test_direction_gauge_translates_stance_without_new_logic() -> None:
    """바늘은 히스테리시스 EMA 점수의 정규화일 뿐 — 새 판정 없음."""
    gauge = build_direction_gauge(_confluence(stance="long_leaning", long_ema=30.0, short_ema=10.0))
    assert gauge["active"] is True
    assert gauge["needle"] == 0.5  # (30-10)/40
    assert gauge["stance"] == "long_leaning"
    assert gauge["reason"].startswith("유동성:")  # 상위 기여 증거 plain 문구
    assert "점수" not in gauge["reason"]  # 점수 남발 금지


def test_direction_gauge_transitioning_shows_observation_not_direction() -> None:
    """전환 관찰 중엔 억지 방향 문구 금지 — '전환 관찰 중'으로 표기 (WO-53 계승)."""
    confluence = _confluence(stance="long_leaning", long_ema=20.0, short_ema=18.0)
    confluence["stance_state"]["transitioning"] = True
    confluence["stance_state"]["target"] = "short_leaning"
    gauge = build_direction_gauge(confluence)
    assert gauge["transitioning"] is True
    assert "전환 관찰 중" in gauge["reason"]


def test_direction_gauge_insufficient_is_inactive_centered() -> None:
    gauge = build_direction_gauge({"stance": "insufficient", "stance_state": {}})
    assert gauge["active"] is False
    assert gauge["needle"] == 0.0
    assert "보류" in gauge["reason"]


def test_direction_gauge_conflicted_refuses_direction() -> None:
    gauge = build_direction_gauge(_confluence(stance="conflicted", long_ema=20.0, short_ema=19.0))
    assert "방향을 고르지 않음" in gauge["reason"]


# ── 익절 압력 게이지 ─────────────────────────────────────────────────────────


def test_tp_gauge_inactive_without_position() -> None:
    """포지션 보유 시에만 활성 — 진입 전엔 비활성."""
    gauge = build_take_profit_gauge(_analysis(), position=None)
    assert gauge["active"] is False
    assert gauge["level"] is None


def test_tp_gauge_high_pressure_when_targets_exhausted() -> None:
    """상단 풀 전부 스윕 + 저항 근접 + 거래량 고갈 → 높음."""
    analysis = _analysis(
        pools=[
            {"price": 103.0, "side": "buy_side", "swept": True},
            {"price": 105.0, "side": "buy_side", "swept": True},
        ],
        sweeps=[{"confirmed": True, "side": "buy_side", "label": "고점 청소"}],
        resistance=[{"price": 100.8, "score": 80}],
        relative_volume=0.5,
        volume_state="drying_up",
    )
    gauge = build_take_profit_gauge(analysis, position={"direction": "long", "entry_price": 95.0})
    assert gauge["active"] is True
    assert gauge["level"] == "높음"
    assert gauge["reason"]  # 이유 1줄 존재
    assert len(gauge["components"]) == 3


def test_tp_gauge_low_pressure_with_room_ahead() -> None:
    """미스윕 풀 다수 + 장벽 멀고 + 거래량 정상 → 낮음."""
    analysis = _analysis(
        pools=[
            {"price": 104.0, "side": "buy_side", "swept": False},
            {"price": 106.0, "side": "buy_side", "swept": False},
            {"price": 108.0, "side": "buy_side", "swept": False},
        ],
        resistance=[{"price": 110.0, "score": 80}],  # 10% 거리
        relative_volume=1.4,
    )
    gauge = build_take_profit_gauge(analysis, position={"direction": "long", "entry_price": 95.0})
    assert gauge["level"] == "낮음"


def test_tp_gauge_short_position_mirrors_downside() -> None:
    """숏 포지션은 하단(sell_side 풀·지지)이 목표 — 방향 대칭."""
    analysis = _analysis(
        pools=[{"price": 96.0, "side": "sell_side", "swept": True}],
        support=[{"price": 99.5, "score": 75}],
        relative_volume=0.6,
    )
    gauge = build_take_profit_gauge(analysis, position={"direction": "short", "entry_price": 105.0})
    assert gauge["active"] is True
    assert gauge["level"] in {"중간", "높음"}


def test_tp_gauge_independent_from_direction() -> None:
    """'롱 우세 + 익절 압력 높음'이 동시에 성립 — 두 축 합치기 금지 검증."""
    analysis = _analysis(
        pools=[{"price": 101.0, "side": "buy_side", "swept": True}],
        sweeps=[{"confirmed": True, "side": "buy_side", "label": "고점 청소"}],
        resistance=[{"price": 100.5, "score": 85}],
        relative_volume=0.5,
    )
    gauges = build_gauges(
        analysis=analysis,
        confluence=_confluence(stance="long_leaning", long_ema=30.0, short_ema=10.0),
        position={"direction": "long", "entry_price": 90.0},
        now=NOW,
    )
    assert gauges["direction"]["stance"] == "long_leaning"
    assert gauges["take_profit"]["level"] == "높음"  # 롱 우세인데 익절 압력 높음 = 정상


# ── 티어2 자동 선별 ──────────────────────────────────────────────────────────


def test_tier2_only_validated_engines_overlay() -> None:
    """validated인 wyckoff만 통과 — candidate 하모닉은 미표시 (성적이 자격을 정한다)."""
    confluence = _confluence(stance="long_leaning", long_ema=30.0, short_ema=10.0)
    confluence["long_evidence"] = [
        {"engine": "harmonic", "claim": "Crab PRZ", "direction": "long", "score": 14.0, "price": 103.0},
        {"engine": "wyckoff", "claim": "Spring 74", "direction": "long", "score": 12.0, "price": 96.0},
        {"engine": "liquidity", "claim": "저점 스윕", "direction": "long", "score": 11.0, "price": 96.5},  # 티어1 — 후보 아님
    ]
    hb = {
        "stats": [
            {"engine": "wyckoff", "direction": "long", "lifecycle_state": "validated"},
            {"engine": "harmonic", "direction": "long", "lifecycle_state": "candidate"},  # 표본 부족
        ]
    }
    overlays = select_tier2_overlays(confluence, hb)
    assert len(overlays) == 1
    assert overlays[0]["engine"] == "wyckoff"
    assert overlays[0]["qualification"] == "validated"


def test_tier2_empty_when_no_direction_or_no_validated() -> None:
    conflicted = _confluence(stance="conflicted", long_ema=20.0, short_ema=19.0)
    assert select_tier2_overlays(conflicted, {"stats": []}) == []
    directional = _confluence(stance="long_leaning", long_ema=30.0, short_ema=10.0)
    assert select_tier2_overlays(directional, None) == []  # 백테스트 부재 → 자격 증명 불가 → 0개


def test_tier2_caps_at_two_overlays() -> None:
    confluence = _confluence(stance="long_leaning", long_ema=30.0, short_ema=10.0)
    confluence["long_evidence"] = [
        {"engine": "wyckoff", "claim": "A", "direction": "long", "score": 12.0},
        {"engine": "harmonic", "claim": "B", "direction": "long", "score": 11.0},
        {"engine": "derivatives", "claim": "C", "direction": "long", "score": 10.0},
    ]
    hb = {"stats": [{"engine": e, "direction": "long", "lifecycle_state": "validated"} for e in ("wyckoff", "harmonic", "derivatives")]}
    assert len(select_tier2_overlays(confluence, hb)) == 2


# ── 잠정/확정 ────────────────────────────────────────────────────────────────


def test_bar_state_provisional_before_close_confirmed_after() -> None:
    analysis = _analysis()
    analysis["data_quality"] = {"last_candle_at": NOW.isoformat()}
    mid_bar = build_gauges(analysis=analysis, confluence=_confluence(stance="conflicted", long_ema=1, short_ema=1), now=NOW + timedelta(hours=1))
    assert mid_bar["bar_state"]["provisional"] is True
    assert 0 < mid_bar["bar_state"]["minutes_to_close"] <= 240
    closed = build_gauges(analysis=analysis, confluence=_confluence(stance="conflicted", long_ema=1, short_ema=1), now=NOW + timedelta(hours=5))
    assert closed["bar_state"]["provisional"] is False


# ── fixtures ─────────────────────────────────────────────────────────────────


def _confluence(*, stance: str, long_ema: float, short_ema: float) -> dict:
    return {
        "stance": stance,
        "stance_label": {"long_leaning": "롱 우위", "short_leaning": "숏 우위", "conflicted": "근거 충돌"}.get(stance, stance),
        "stance_state": {
            "long_score_ema": long_ema,
            "short_score_ema": short_ema,
            "transitioning": False,
            "target": None,
            "flip_threshold_progress": 0.0,
            "candles_in_state": 3,
        },
        "long_score": long_ema,
        "short_score": short_ema,
        "long_evidence": [{"engine": "liquidity", "claim": "저점 Strong 스윕 확인", "direction": "long", "score": 14.0}],
        "short_evidence": [{"engine": "level", "claim": "구조 저항 104", "direction": "short", "score": 5.0}],
    }


def _analysis(
    *,
    pools: list | None = None,
    sweeps: list | None = None,
    resistance: list | None = None,
    support: list | None = None,
    relative_volume: float = 1.0,
    volume_state: str = "balanced_flow",
) -> dict:
    return {
        "mark_price": 100.0,
        "timeframe": "4h",
        "data_quality": {"last_candle_at": NOW.isoformat()},
        "price_levels": {"support": support or [], "resistance": resistance or []},
        "liquidity": {"pools": pools or [], "sweeps": sweeps or [], "htf_range_sweeps": []},
        "harmonic_patterns": [],
        "volume_xray": {"relative_volume": relative_volume, "volume_state": volume_state},
    }
