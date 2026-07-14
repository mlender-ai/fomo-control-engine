"""WO-FCE-43 Part A — TA별 1줄 판정 엔진 수용 기준 테스트.

7모듈 고정 어휘 출력 · 충돌 그대로 노출 + 종합 카운트 · 자유 문장 생성 경로 0.
"""

from __future__ import annotations

import pytest

from app.analyst.oneliner import (
    FALLBACK_PHRASE,
    MODULE_LABELS,
    PHRASES,
    STANCES,
    OneLinerVocabularyError,
    _line,
    build_one_liners,
)


def _full_analysis() -> dict:
    """7모듈 전부 판정 가능한 최소 분석 페이로드."""
    return {
        "mark_price": 102.0,
        "wyckoff": {
            "phase": "accumulation_phase_c",
            "side": "accumulation",
            "accumulation_score": 72,
            "distribution_score": 40,
            "events": [{"side": "accumulation", "confidence": 70, "type": "spring_candidate", "label": "Spring"}],
        },
        "liquidity": {
            "sweeps": [{"confirmed": True, "side": "sell_side", "grade": "Strong", "return_at": "2025-01-02T00:00:00", "id": "sweep-1"}],
            "htf_range_sweeps": [],
            "pools": [{"label": "저점 풀"}],
        },
        "volume_xray": {"data_available": True, "delta_ratio": 0.4, "volume_state": "delta_imbalanced"},
        "harmonic_patterns": [{"direction": "bullish", "confidence": 82, "label": "Gartley", "prz": {"mid": 101.0}}],
        "price_levels": {
            "support": [{"price": 100.0, "score": 82}],
            "resistance": [{"price": 110.0, "score": 75}],
        },
        "derivatives": {"signals": {"funding_state": {"state": "extreme", "funding": 0.08}, "crowding_score": {"score": 75}}},
        "indicators": {
            "rsi": [{"time": 1, "value": 62.0}],
            "macd": [{"time": 1, "macd": 1.2, "signal": 0.8, "histogram": 0.4}],
            "bollinger": {"middle": [{"time": 1, "value": 101.0}], "upper": [], "lower": []},
        },
    }


# ── 수용: 7모듈 전부 1줄 판정 + 고정 어휘 검증 ─────────────────────


def test_all_seven_modules_emit_fixed_vocabulary_lines() -> None:
    result = build_one_liners(_full_analysis())
    lines = result["lines"]
    assert [line["module"] for line in lines] == list(MODULE_LABELS.keys())
    for line in lines:
        vocabulary = set(PHRASES[line["module"]]) | {FALLBACK_PHRASE}
        assert line["phrase"] in vocabulary
        assert line["stance"] in STANCES
        assert line["confidence_class"] in {"강", "중", "약"}
        assert len(line["phrase"]) <= 25
        assert line["evidence_ref"]


def test_stances_match_states() -> None:
    lines = {line["module"]: line for line in build_one_liners(_full_analysis())["lines"]}
    assert lines["wyckoff"]["phrase"] == "매집 우세" and lines["wyckoff"]["stance"] == "상방"
    assert lines["liquidity"]["phrase"] == "저점 청소 후 반등 구조" and lines["liquidity"]["confidence_class"] == "강"
    assert lines["volume"]["phrase"] == "매수 체결 우위"
    assert lines["harmonic"]["phrase"] == "상방 반전 구간 접근" and lines["harmonic"]["confidence_class"] == "강"
    assert lines["levels"]["phrase"] == "지지 위 유지"  # mark 102는 100-110 밴드 하단 1/3
    assert lines["derivatives"]["phrase"] == "롱 쏠림 경계" and lines["derivatives"]["stance"] == "하방"
    assert lines["indicators"]["phrase"] == "상승 우세" and lines["indicators"]["confidence_class"] == "강"


# ── 수용: 충돌 그대로 노출 + 종합 카운트 ───────────────────────────


def test_conflicts_exposed_with_summary_counts() -> None:
    analysis = _full_analysis()
    result = build_one_liners(analysis)
    stances = {line["module"]: line["stance"] for line in result["lines"]}
    # 수급(하방)과 와이코프(상방)가 충돌 — 개별 줄은 왜곡 없이 유지된다.
    assert stances["derivatives"] == "하방"
    assert stances["wyckoff"] == "상방"
    counts = result["counts"]
    assert counts["상방"] >= 4 and counts["하방"] >= 1
    assert result["summary"].startswith("종합: ")
    assert f"상방 {counts['상방']}" in result["summary"]
    assert f"하방 {counts['하방']}" in result["summary"]


def test_overall_stance_uses_confluence_when_present() -> None:
    analysis = _full_analysis()
    # 개별 카운트는 상방 다수지만, CI 가중 컨플루언스가 하방이면 종합만 하방.
    result = build_one_liners(analysis, confluence={"stance": "short_leaning"})
    assert result["overall_stance"] == "하방"
    stances = [line["stance"] for line in result["lines"]]
    assert stances.count("상방") >= 4  # 개별 줄 왜곡 금지


def test_overall_stance_majority_without_confluence() -> None:
    assert build_one_liners(_full_analysis())["overall_stance"] == "상방"


# ── 수용: 자유 문장 생성 경로 0 ────────────────────────────────────


def test_free_text_phrase_build_fails() -> None:
    with pytest.raises(OneLinerVocabularyError):
        _line("wyckoff", "상승 각도가 매우 가파릅니다", "강", "x")
    with pytest.raises(OneLinerVocabularyError):
        _line("volume", "매집 우세", "강", "x")  # 타 모듈 어휘 차용도 금지
    with pytest.raises(OneLinerVocabularyError):
        _line("unknown_module", "중립", "강", "x")
    with pytest.raises(OneLinerVocabularyError):
        _line("wyckoff", "매집 우세", "매우강", "x")  # 신뢰 등급도 고정


# ── 판정 보류·데이터 부족 경로 ─────────────────────────────────────


def test_undetermined_wyckoff_reports_hold() -> None:
    analysis = _full_analysis()
    analysis["wyckoff"] = {"phase": "undetermined", "side": "neutral", "events": []}
    line = next(item for item in build_one_liners(analysis)["lines"] if item["module"] == "wyckoff")
    assert line["phrase"] == "레인지 미형성"
    assert line["stance"] == "판단불가"


def test_trending_wyckoff_uses_trend_direction() -> None:
    analysis = _full_analysis()
    analysis["wyckoff"] = {"phase": "trending", "side": "neutral", "events": [], "trend": {"direction": "bearish"}}
    line = next(item for item in build_one_liners(analysis)["lines"] if item["module"] == "wyckoff")
    assert line["phrase"] == "추세 진행 중"
    assert line["stance"] == "하방"


def test_mixed_wyckoff_signals_downgrade_confidence() -> None:
    analysis = _full_analysis()
    analysis["wyckoff"]["events"].append({"side": "distribution", "confidence": 70, "type": "utad_candidate", "label": "UT"})
    line = next(item for item in build_one_liners(analysis)["lines"] if item["module"] == "wyckoff")
    assert line["phrase"] == "매집 우세"  # 판정 유지
    assert line["confidence_class"] == "약"  # 반대측 고신뢰 공존 → 강도 강등


def test_empty_analysis_falls_back_to_no_data() -> None:
    result = build_one_liners({})
    assert all(line["stance"] == "판단불가" for line in result["lines"])
    assert result["overall_stance"] == "판단불가"
    for line in result["lines"]:
        assert line["phrase"] in (set(PHRASES[line["module"]]) | {FALLBACK_PHRASE})


def test_volume_without_fills_reports_no_data() -> None:
    analysis = _full_analysis()
    analysis["volume_xray"] = {"data_available": False}
    line = next(item for item in build_one_liners(analysis)["lines"] if item["module"] == "volume")
    assert line["phrase"] == "데이터 부족"
    assert line["stance"] == "판단불가"


def test_vocabulary_phrases_within_length_budget() -> None:
    for module, vocabulary in PHRASES.items():
        for phrase in vocabulary:
            assert len(phrase) <= 25, f"{module}/{phrase}"


# ── E2E: 차트 분석 파이프라인이 one_liners를 발행 ──────────────────


def test_chart_analysis_pipeline_emits_one_liners() -> None:
    from datetime import datetime, timedelta, timezone

    from app.db.models import MarketCandle, MarketSnapshot
    from app.positions.chart_analysis import build_chart_analysis

    base = datetime(2025, 1, 1, tzinfo=timezone.utc)
    candles = []
    for i in range(120):
        mid = 104.0 + (i % 5) * 0.6
        candles.append(
            MarketCandle(
                timestamp=base + timedelta(hours=4 * i),
                open=mid - 0.3,
                high=mid + 1.0,
                low=mid - 1.0,
                close=mid + 0.3,
                volume=100.0,
            )
        )
    snapshot = MarketSnapshot(
        symbol="TESTUSDT",
        timeframe="4h",
        price=candles[-1].close,
        change_24h=0.5,
        funding_rate=0.0001,
        open_interest_change=0.1,
        candles=candles,
        provider="mock",
    )
    payload = build_chart_analysis(snapshot, None, None)
    one_liners = payload["one_liners"]
    assert len(one_liners["lines"]) == 7
    assert one_liners["summary"].startswith("종합: ")
    for line in one_liners["lines"]:
        assert line["phrase"] in (set(PHRASES[line["module"]]) | {FALLBACK_PHRASE})
