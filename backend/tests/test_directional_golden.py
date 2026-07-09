"""WO-FCE-54 — 방향 판정 골든 케이스 + 실증 회귀.

재설계(WO-51 시간가중 · 52 HTF 앵커 · 53 히스테리시스)가 사용자 실증 실패를 실제로
고쳤는지 증명하고 회귀를 막는다. `directional_v2=False`는 재설계 전(v1) 엔진을 재현해
전/후를 대조한다 (recency·HTF 배수/가드·히스테리시스 off, mtf는 옛 단일 투표).

Part A 실증 재현 · Part B 골든 세트 · Part C 합성 리플레이 적중률 · Part D 룩어헤드 감사.
케이스는 검증용이며 엔진 파라미터를 케이스에 맞춰 튜닝하지 않는다(오버핏 금지).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from app.analyst.confluence import build_confluence

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def _dir(stance: str) -> int:
    return {"long_leaning": 1, "short_leaning": -1}.get(stance, 0)


# ── Part A · 실증 재현 (재설계 전 실패 / 후 통과 대조) ──────────────────────


def test_A1_soxl_rebound_no_longer_reads_short() -> None:
    """SOXL 반등: 오래된 숏 잔재(저항·고점청소·하모닉) + 최근 롱(지지·POC) + 1D 상승.
    재설계 전(v1)은 '숏 우세'로 오판, 후(v2)는 숏 아님(상방 또는 conflicted)."""
    analysis = _soxl_rebound()
    v1 = build_confluence(symbol="SOXL", timeframe="4h", analysis=analysis, generated_at=NOW, directional_v2=False)
    v2 = build_confluence(symbol="SOXL", timeframe="4h", analysis=analysis, generated_at=NOW, directional_v2=True)

    assert v1["stance"] == "short_leaning"  # 재설계 전: 실증 실패 재현
    assert v2["stance"] != "short_leaning"  # 재설계 후: 종합 하방 불가
    assert v2["stance"] == "long_leaning"
    assert v2["htf_context"]["bias"] == "long"


def test_A2_minute_flip_oscillation_holds_stance() -> None:
    """분단위 반전: 균형에서 4분 간격으로 raw가 롱↔숏 깜빡이는 시퀀스.
    재설계 후(v2)는 held 무반전. 재설계 전(v1)은 순간 스냅샷이라 매 바 반전."""
    seq = [(85, 55), (55, 85), (85, 55), (55, 85), (85, 55), (55, 85)]

    def run(v2: bool) -> int:
        prior = None
        flips = 0
        last = 0
        for i, (ls, ss) in enumerate(seq):
            c = build_confluence(
                symbol="X", timeframe="4h", analysis=_bal(ls, ss, i), generated_at=NOW + timedelta(minutes=4 * i), prior_state=prior, directional_v2=v2
            )
            prior = c["stance_state"]
            d = _dir(c["stance"])
            if d != 0 and last != 0 and d != last:
                flips += 1
            if d != 0:
                last = d
        return flips

    assert run(True) == 0  # 재설계 후: 무반전
    assert run(False) >= 3  # 재설계 전: 분단위 반전 다발


def test_A3_htf_pullback_stays_long() -> None:
    """HTF 되돌림: 1D 명확한 상승 + 4h 저항 터치. 종합 상방 유지(되돌림이지 반전 아님)."""
    analysis = _htf_pullback()
    v2 = build_confluence(symbol="X", timeframe="4h", analysis=analysis, generated_at=NOW, directional_v2=True)
    v1 = build_confluence(symbol="X", timeframe="4h", analysis=analysis, generated_at=NOW, directional_v2=False)

    assert v2["htf_context"]["bias"] == "long"
    assert v2["stance"] == "long_leaning"  # 되돌림 중에도 상방 유지
    # 재설계 전엔 저항이 상방을 끌어내려 최소한 clean 상방은 아니었음 (대조).
    assert v1["stance"] != "long_leaning"


# ── Part B · 방향 정확성 골든 세트 (교과서 케이스) ──────────────────────────


def test_B1_sustained_downtrend_holds_short_through_counter() -> None:
    """명확한 하락 지속 → 하방. 중간 역행(롱) 1바가 있어도 유지."""
    prior = None
    for i, (ls, ss) in enumerate([(50, 88), (50, 88), (85, 52), (48, 90), (46, 92)]):  # 3번째 바가 역행 스파이크
        c = build_confluence(
            symbol="X", timeframe="4h", analysis=_bal(ls, ss, i), generated_at=NOW + timedelta(minutes=4 * i), prior_state=prior, directional_v2=True
        )
        prior = c["stance_state"]
    assert c["stance"] == "short_leaning"
    assert c["stance_state"]["transitioning"] is False


def test_B2_sustained_uptrend_is_long() -> None:
    prior = None
    for i, (ls, ss) in enumerate([(88, 50), (88, 50), (90, 48)]):
        c = build_confluence(
            symbol="X", timeframe="4h", analysis=_bal(ls, ss, i), generated_at=NOW + timedelta(minutes=4 * i), prior_state=prior, directional_v2=True
        )
        prior = c["stance_state"]
    assert c["stance"] == "long_leaning"
    assert c["htf_context"]["bias"] == "long"


def test_B3_real_transition_flips_when_htf_turns() -> None:
    """진짜 추세 전환(HTF까지 하락으로): 히스테리시스가 전환을 막지 않고 flip 발생."""
    prior = None
    stances = []
    for i, (ls, ss) in enumerate([(88, 50), (88, 50), (50, 88), (48, 90), (46, 92), (46, 92)]):
        c = build_confluence(
            symbol="X", timeframe="4h", analysis=_bal(ls, ss, i), generated_at=NOW + timedelta(minutes=4 * i), prior_state=prior, directional_v2=True
        )
        prior = c["stance_state"]
        stances.append(c["stance"])
    assert stances[0] == "long_leaning"
    assert stances[-1] == "short_leaning"  # 전환은 결국 통과 (둔감 방지)


def test_B4_genuine_range_is_conflicted() -> None:
    """진짜 레인지(독립적 롱/숏 근거 공존, HTF 중립) → conflicted."""
    analysis = _range_balance()
    c = build_confluence(symbol="X", timeframe="4h", analysis=analysis, generated_at=NOW, directional_v2=True)
    assert c["stance"] == "conflicted"
    assert c["htf_context"]["bias"] == "neutral"


def test_B5_insufficient_data_is_insufficient() -> None:
    analysis = {"mark_price": 100.0, "price_levels": {"support": [], "resistance": []}, "wyckoff_mtf": {}}
    c = build_confluence(symbol="X", timeframe="4h", analysis=analysis, generated_at=NOW, directional_v2=True)
    assert c["stance"] == "insufficient"
    assert c["stance_state"]["transitioning"] is False


def test_B6_htf_neutral_caps_confidence() -> None:
    """HTF 기준선 부재/횡보 → 방향을 골라도 종합 신뢰도 상한(강한 확정 금지)."""
    prior = None
    for i in range(3):  # 롱 우세를 확립
        c = build_confluence(
            symbol="X", timeframe="4h", analysis=_bal(85, 55, i, htf=None), generated_at=NOW + timedelta(minutes=4 * i), prior_state=prior, directional_v2=True
        )
        prior = c["stance_state"]
    assert c["htf_context"]["bias"] == "neutral"
    if c["stance"] == "long_leaning":
        assert c["composite_score"] <= 75.0


# ── Part C · 합성 라벨 리플레이 방향 적중률 (재설계 전/후) ────────────────────


def test_C_replay_improves_stability_and_accuracy() -> None:
    """합성 라벨 경로(추세·전환·노이즈)에서 재설계 후가 (1) 스퓨리어스 반전을 줄이고
    (2) 추세 적중을 악화시키지 않는다. 실히스토리 백테스트는 OHLCV 수집기 부재로 후속."""
    flips_v1, acc_v1 = _replay(directional_v2=False)
    flips_v2, acc_v2 = _replay(directional_v2=True)

    assert flips_v2 < flips_v1  # 진동 반전 감소 (핵심 개선)
    assert acc_v2 >= acc_v1  # 추세 적중 악화 없음
    # 오버핏 방지: 특정 수치가 아니라 "개선 방향"만 고정. 실측 표는 docs/DirectionalEngine.md.


# ── Part D · 룩어헤드 감사 (미래 데이터 미참조) ──────────────────────────────


def test_D1_recency_never_rewards_future_evidence() -> None:
    """WO-51: as_of가 now 이후(미래)면 age가 0으로 클램프 → recency는 1.0을 넘지 못한다
    (미래 데이터가 가중을 부풀리지 못함)."""
    future = _bal(85, 55, 0)
    future["price_levels"]["support"][0]["last_touch_at"] = (NOW + timedelta(hours=48)).isoformat()
    c = build_confluence(symbol="X", timeframe="4h", analysis=future, generated_at=NOW, directional_v2=True)
    support = next(item for item in c["long_evidence"] if item["engine"] == "level")
    assert support["recency_factor"] <= 1.0  # 미래라고 >1 보상 없음
    # now 시점 증거와 동일(둘 다 1.0) — 미래가 과거보다 유리하지 않음.
    present = _bal(85, 55, 0)
    present["price_levels"]["support"][0]["last_touch_at"] = NOW.isoformat()
    c2 = build_confluence(symbol="X", timeframe="4h", analysis=present, generated_at=NOW, directional_v2=True)
    support2 = next(item for item in c2["long_evidence"] if item["engine"] == "level")
    assert support["recency_factor"] == support2["recency_factor"] == 1.0


def test_D2_state_at_bar_independent_of_future_bars() -> None:
    """WO-53: 바 i의 stance_state는 직전 상태 + 현재만 참조 — 이후 바가 과거 계산을 바꾸지 않는다."""
    seq = [(88, 50), (60, 80), (85, 52), (50, 88)]

    def state_at(cut: int) -> dict:
        prior = None
        c = None
        for i in range(cut + 1):
            ls, ss = seq[i]
            c = build_confluence(
                symbol="X", timeframe="4h", analysis=_bal(ls, ss, i), generated_at=NOW + timedelta(minutes=4 * i), prior_state=prior, directional_v2=True
            )
            prior = c["stance_state"]
        return c["stance_state"]

    # 바 2까지만 존재하는 경우와, 바 3까지 존재하지만 바 2 시점을 본 경우가 동일해야 한다.
    truncated = state_at(2)
    prior = None
    full_at_2 = None
    for i in range(4):
        ls, ss = seq[i]
        c = build_confluence(
            symbol="X", timeframe="4h", analysis=_bal(ls, ss, i), generated_at=NOW + timedelta(minutes=4 * i), prior_state=prior, directional_v2=True
        )
        prior = c["stance_state"]
        if i == 2:
            full_at_2 = c["stance_state"]
    assert truncated["stance"] == full_at_2["stance"]
    assert truncated["long_score_ema"] == full_at_2["long_score_ema"]
    assert truncated["pending_count"] == full_at_2["pending_count"]


# ── fixtures / helpers ──────────────────────────────────────────────────────


def _bal(long_s: float, short_s: float, i: int, htf: bool | None = True) -> dict:
    """방향 점수를 직접 제어하는 최소 분석. htf: True=우세측 정렬 1D, None=중립."""
    up = long_s >= short_s
    iso = (NOW + timedelta(minutes=4 * i)).isoformat()
    if htf is None:
        mtf = {"htf_phase": "undetermined", "htf_trend": "neutral", "alignment": "neutral"}
    else:
        mtf = {
            "htf_phase": "accumulation" if up else "distribution",
            "htf_trend": "bullish" if up else "bearish",
            "alignment": "aligned",
        }
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
        "wyckoff_mtf": mtf,
    }


def _soxl_rebound() -> dict:
    """오래된 숏 잔재(저항 40h·SOW 36h·고점스윕 30h·HTF청소 44h·하모닉 34h) + 최근 롱(지지 2h·POC) + 1D 상승."""
    return {
        "mark_price": 100.0,
        "as_of": _iso(1),
        "data_quality": {"last_candle_at": _iso(1)},
        "price_levels": {
            "support": [{"price": 97.0, "score": 62, "touches": 3, "last_touch_at": _iso(2)}],
            "resistance": [{"price": 103.0, "score": 90, "touches": 5, "last_touch_at": _iso(40)}],
        },
        "wyckoff": {"side": "", "distribution_score": 85},
        "wyckoff_phase": {},
        "wyckoff_markers": [{"label": "SOW", "confidence": 85, "side": "distribution", "time": _iso(36), "price": 103}],
        "liquidity": {
            "sweeps": [{"confirmed": True, "side": "buy_side", "grade": "Strong", "confidence": 82, "label": "고점 스윕", "time": _iso(30)}],
            "htf_range_sweeps": [{"confirmed": True, "side": "buy_side", "grade": "Major", "confidence": 80, "label": "HTF 고점청소", "time": _iso(44)}],
        },
        "harmonic_patterns": [{"direction": "bearish", "label": "AB=CD", "confidence": 72, "prz": {"mid": 103.0}, "detected_at": _iso(34)}],
        "volume_profile": {"poc_price": 98.0},
        "derivatives": {"signals": {}},
        "wyckoff_mtf": {"htf_phase": "accumulation", "htf_trend": "bullish", "alignment": "aligned"},
    }


def _htf_pullback() -> dict:
    """1D 명확한 상승 + 4h 신선한 저항 거부(저항 터치·고점스윕·하모닉). 되돌림 구간 —
    하위만 보면 하방 압력이지만 상위 추세는 상승. 재설계 전엔 저항이 상방을 끌어내려 conflicted."""
    return {
        "mark_price": 100.0,
        "as_of": _iso(1),
        "data_quality": {"last_candle_at": _iso(1)},
        "price_levels": {
            "support": [{"price": 96.0, "score": 66, "touches": 4, "last_touch_at": _iso(6)}],
            "resistance": [{"price": 101.0, "score": 88, "touches": 3, "last_touch_at": _iso(3)}],
        },
        "wyckoff": {"side": "accumulation", "accumulation_score": 64},
        "wyckoff_phase": {"side": "accumulation", "phase": "Phase E"},
        "wyckoff_markers": [],
        "liquidity": {
            "sweeps": [{"confirmed": True, "side": "buy_side", "grade": "Strong", "confidence": 78, "label": "고점 거부", "time": _iso(2)}],
            "htf_range_sweeps": [],
        },
        "harmonic_patterns": [{"direction": "bearish", "label": "Gartley", "confidence": 76, "prz": {"mid": 101.0}, "detected_at": _iso(4)}],
        "volume_profile": {"poc_price": 98.0},
        "derivatives": {"signals": {}},
        "wyckoff_mtf": {"htf_phase": "accumulation", "htf_trend": "bullish", "alignment": "aligned"},
    }


def _range_balance() -> dict:
    """독립적 롱(지지·매집마커) vs 숏(저항·분산마커) 공존, HTF 중립 → 진짜 균형."""
    iso = _iso(2)
    return {
        "mark_price": 100.0,
        "as_of": iso,
        "data_quality": {"last_candle_at": iso},
        "price_levels": {
            "support": [{"price": 97.0, "score": 70, "touches": 4, "last_touch_at": iso}],
            "resistance": [{"price": 103.0, "score": 70, "touches": 4, "last_touch_at": iso}],
        },
        "wyckoff": {"side": ""},
        "wyckoff_phase": {},
        "wyckoff_markers": [
            {"label": "Spring", "confidence": 70, "side": "accumulation", "time": iso, "price": 97},
            {"label": "UTAD", "confidence": 70, "side": "distribution", "time": iso, "price": 103},
        ],
        "liquidity": {"sweeps": [], "htf_range_sweeps": []},
        "volume_profile": {},
        "derivatives": {"signals": {}},
        "wyckoff_mtf": {"htf_phase": "undetermined", "htf_trend": "neutral", "alignment": "neutral"},
    }


def _replay(*, directional_v2: bool) -> tuple[int, float]:
    """라벨된 합성 경로 리플레이 → (방향 반전 횟수, 추세 구간 적중률%). 고정 시드."""
    rnd = random.Random(42)
    regimes = [("up", 1, 40), ("down", -1, 40), ("range", 0, 30), ("rev_up", 1, 20), ("rev_down", -1, 20)]
    prior = None
    flips = 0
    last = 0
    correct = 0
    total = 0
    bar = 0
    for _name, td, n in regimes:
        for _ in range(n):
            noise = rnd.gauss(0, 10)
            spike = rnd.random() < 0.15
            if td == 1:
                ls, ss = 80 + noise, 58 - noise
            elif td == -1:
                ls, ss = 58 + noise, 80 - noise
            else:
                ls, ss = 68 + rnd.gauss(0, 8), 68 + rnd.gauss(0, 8)
            if spike:
                ls, ss = ss, ls
            c = build_confluence(
                symbol="S",
                timeframe="4h",
                analysis=_bal(ls, ss, bar),
                generated_at=NOW + timedelta(minutes=4 * bar),
                prior_state=prior,
                directional_v2=directional_v2,
            )
            prior = c["stance_state"]
            bar += 1
            d = _dir(c["stance"])
            if d != 0 and last != 0 and d != last:
                flips += 1
            if d != 0:
                last = d
            if td != 0:
                total += 1
                correct += 1 if d == td else 0
    return flips, round(100 * correct / total, 1)
