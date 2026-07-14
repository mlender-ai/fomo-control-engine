"""WO-FCE-45 — 엔진 개선 증명 수용 기준 테스트.

조치별 효과표(효과 없음 정직 표기) · 레짐 통제 + 판별 불가 · 다이제스트 스키마 ·
개선 연출 방지(무조치 주간 개선 문구 미생성).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.db.models import AutonomyLog, CalibrationSuggestion, EngineParamVersion, JudgmentScore
from app.review.improvement import (
    VERDICT_IMPROVED,
    VERDICT_INDETERMINATE,
    VERDICT_NO_EFFECT,
    VERDICT_WORSENED,
    accuracy_sparkline,
    action_effect_table,
    regime_controlled_week_delta,
    weekly_improvement_digest,
)

NOW = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
POSITION_ID = uuid4()


def _score(days_ago: float, *, correct: bool, judgment_type: str = "invalidation", regime: str | None = None) -> JudgmentScore:
    claim: dict = {"price": 100.0}
    if regime:
        claim["regime"] = regime
    return JudgmentScore(
        judgment_id=f"j-{uuid4()}",
        position_id=POSITION_ID,
        judgment_type=judgment_type,
        claim=claim,
        outcome="correct" if correct else "wrong",
        detail="fixture",
        created_at=NOW - timedelta(days=days_ago),
    )


def _scores_window(days_from: float, days_to: float, n: int, correct_rate: float, **kwargs) -> list[JudgmentScore]:
    scores = []
    span = days_from - days_to
    for i in range(n):
        days_ago = days_to + span * (i + 0.5) / n
        scores.append(_score(days_ago, correct=(i < round(n * correct_rate)), **kwargs))
    return scores


def _param(days_ago: float, param: str = "min_invalidation_level_score", old=40, new=55) -> EngineParamVersion:
    return EngineParamVersion(
        param=param,
        old_value=old,
        new_value=new,
        adopted_by="autonomy",
        approved_at=NOW - timedelta(days=days_ago),
    )


# ── 조치별 효과표 ──────────────────────────────────────────────────


def test_effect_table_detects_significant_improvement() -> None:
    # 조치 7일 전: 40% (N=20) → 조치 후: 85% (N=20) — CI 비겹침.
    scores = _scores_window(14, 7.01, 20, 0.40) + _scores_window(6.99, 0, 20, 0.85)
    table = action_effect_table(scores, [_param(7)], [], now=NOW)
    assert len(table) == 1
    assert table[0]["verdict"] == VERDICT_IMPROVED
    assert table[0]["delta_pct"] > 0
    assert table[0]["before"]["tested"] == 20 and table[0]["after"]["tested"] == 20


def test_effect_table_reports_no_effect_honestly() -> None:
    scores = _scores_window(14, 7.01, 20, 0.55) + _scores_window(6.99, 0, 20, 0.55)
    table = action_effect_table(scores, [_param(7)], [], now=NOW)
    assert table[0]["verdict"] == VERDICT_NO_EFFECT  # 개선 연출 금지


def test_effect_table_reports_worsening_honestly() -> None:
    scores = _scores_window(14, 7.01, 20, 0.85) + _scores_window(6.99, 0, 20, 0.35)
    table = action_effect_table(scores, [_param(7)], [], now=NOW)
    assert table[0]["verdict"] == VERDICT_WORSENED  # 악화도 그대로 보고


def test_effect_table_small_sample_is_indeterminate() -> None:
    scores = _scores_window(14, 7.01, 5, 0.4) + _scores_window(6.99, 0, 5, 1.0)
    table = action_effect_table(scores, [_param(7)], [], now=NOW)
    assert table[0]["verdict"] == VERDICT_INDETERMINATE
    assert "표본 부족" in table[0]["verdict_reason"]


def test_effect_table_signature_downgrade_scope() -> None:
    log = AutonomyLog(
        signature_key="liquidity:sweep_low:strong:long:crypto:4h",
        previous_state="validated",
        new_state="degraded",
        transition="degrade",
        reason="oos_unstable",
        autonomous=True,
        evidence={"win_1r_pct": 41.0},
        created_at=NOW - timedelta(days=3),
    )
    table = action_effect_table([], [], [log], now=NOW)
    assert table[0]["kind"] == "signature_downgrade"
    assert table[0]["verdict"] == VERDICT_INDETERMINATE  # 시그니처 스코프 표본 없음 — 정직
    assert "노출 차단" in table[0]["structural_note"]


# ── 레짐 통제 비교 ────────────────────────────────────────────────


def test_regime_controlled_delta_same_regime() -> None:
    # 전주: uptrend 50% (N=20) / 이번 주: uptrend 90% (N=20) → 통제 비교 + 유의 개선.
    scores = _scores_window(14, 7.01, 20, 0.50, regime="uptrend") + _scores_window(6.99, 0, 20, 0.90, regime="uptrend")
    delta = regime_controlled_week_delta(scores, now=NOW)
    assert delta["controlled"] is True
    assert delta["regime"] == "uptrend"
    assert "동일 레짐" in delta["basis"]
    assert delta["verdict"] == VERDICT_IMPROVED


def test_regime_control_impossible_without_tags() -> None:
    # 레짐 태그 없는 과거 표본 — 판별 불가를 명시하는 것이 감사의 성실성.
    scores = _scores_window(14, 7.01, 20, 0.50) + _scores_window(6.99, 0, 20, 0.90)
    delta = regime_controlled_week_delta(scores, now=NOW)
    assert delta["controlled"] is False
    assert "판별 불가" in delta["control_reason"]
    assert "레짐 통제 불가" in delta["basis"]


def test_regime_control_insufficient_same_regime_sample() -> None:
    scores = _scores_window(14, 7.01, 20, 0.50, regime="uptrend") + _scores_window(6.99, 0, 20, 0.90, regime="downtrend")
    delta = regime_controlled_week_delta(scores, now=NOW)
    assert delta["controlled"] is False
    assert "판별 불가" in delta["control_reason"]


# ── 주간 다이제스트 (WO-49 소비 스키마) ────────────────────────────


def _digest(scores, suggestions=None, params=None, logs=None, states=None):
    return weekly_improvement_digest(scores, suggestions or [], params or [], logs or [], states or {}, now=NOW)


def test_digest_schema_is_fixed_for_wo49() -> None:
    scores = _scores_window(14, 7.01, 20, 0.5, regime="uptrend") + _scores_window(6.99, 0, 20, 0.9, regime="uptrend")
    digest = _digest(scores)
    for key in (
        "generated_at",
        "period",
        "schema_version",
        "tested",
        "accuracy_pct",
        "accuracy_ci",
        "delta_pct",
        "delta_basis",
        "delta_verdict",
        "regime_control",
        "actions",
        "quarantined",
        "experiments",
        "weakest",
        "improvement_claim",
        "headline",
        "sparkline",
    ):
        assert key in digest, key
    assert digest["schema_version"] == 1
    assert len(digest["sparkline"]) == 12
    assert digest["improvement_claim"] is True
    assert "+" in digest["headline"] and "동일 레짐" in digest["headline"]


# ── 수용: 개선 연출 방지 — 무조치 주간에 개선 문구 미생성 ──────────


def test_no_improvement_claim_on_flat_week_without_actions() -> None:
    scores = _scores_window(14, 7.01, 30, 0.60, regime="uptrend") + _scores_window(6.99, 0, 30, 0.61, regime="uptrend")
    digest = _digest(scores)
    assert digest["improvement_claim"] is False
    assert digest["headline"] == "이번 주 유의미한 개선 없음"
    assert digest["actions"] == []  # 무조치


def test_weak_signal_never_claimed_as_improvement() -> None:
    # +8%p지만 CI 겹침 (N=20) → 유의 아님 → 개선 주장 금지.
    scores = _scores_window(14, 7.01, 20, 0.55, regime="uptrend") + _scores_window(6.99, 0, 20, 0.63, regime="uptrend")
    digest = _digest(scores)
    assert digest["improvement_claim"] is False
    assert digest["headline"] == "이번 주 유의미한 개선 없음"


def test_digest_includes_quarantine_and_experiments() -> None:
    suggestion = CalibrationSuggestion(
        suggestion_type="confidence_floor_review",
        title="하모닉 신뢰도 하한 검토",
        rationale="fixture",
        proposed_change={"parameter": "harmonic_min_confidence", "from": 70, "to": 80},
        sample_size=20,
        status="experiment",
        autonomy={"started_at": (NOW - timedelta(days=9)).isoformat()},
    )
    digest = _digest(
        _scores_window(6.99, 0, 20, 0.6, regime="uptrend"),
        suggestions=[suggestion],
        states={"harmonic:prz_touch:conf>=70:long:crypto:4h": "quarantined"},
    )
    assert digest["quarantined"] == ["harmonic:prz_touch:conf>=70:long:crypto:4h"]
    assert digest["experiments"][0]["days_running"] == 9


def test_weakest_type_surfaces_lowest_accuracy() -> None:
    scores = _scores_window(6.99, 0, 20, 0.8, judgment_type="invalidation", regime="uptrend") + _scores_window(
        6.99, 0, 20, 0.15, judgment_type="harmonic_prz", regime="uptrend"
    )
    digest = _digest(scores)
    assert digest["weakest"]["judgment_type"] == "harmonic_prz"
    assert digest["weakest"]["tested"] == 20


def test_sparkline_buckets_with_gaps() -> None:
    scores = _scores_window(7, 0, 20, 0.7) + _scores_window(35, 28, 15, 0.5)
    points = accuracy_sparkline(scores, now=NOW)
    assert len(points) == 12
    assert points[-1]["tested"] == 20 and points[-1]["accuracy_pct"] == 70.0
    empty = [point for point in points if point["tested"] == 0]
    assert empty and all(point["accuracy_pct"] is None for point in empty)  # 빈 주는 None — 조작 금지
