"""엔진 개선 증명 (WO-FCE-45) — "배우고 있는가"의 감사와 지표화.

정직성 우선: 효과 없음/악화 조치는 그대로 보고하고, 레짐 통제가 불가능하면
"판별 불가"를 명시한다. 개선 연출 금지 — 유의 기준을 통과한 사실만 개선으로
주장한다. 판정 규칙은 docs/ImprovementProof.md.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from app.backtest.statistics import bootstrap_ci_from_counts

MIN_WINDOW_SAMPLE = 15
WEAK_SIGNAL_DELTA_PCT = 5.0
EFFECT_WINDOW_DAYS = 14
SPARKLINE_WEEKS = 12

# 파라미터 → 영향 판단 유형 (조치별 효과표의 스코프).
PARAM_SCOPE: dict[str, tuple[str, str]] = {
    "min_invalidation_level_score": ("invalidation", "무효화 판단"),
    "wyckoff_event_min_confidence": ("wyckoff_event", "와이코프 이벤트"),
    "harmonic_min_confidence": ("harmonic_prz", "하모닉 PRZ"),
    "harmonic_ratio_tolerance_multiplier": ("harmonic_prz", "하모닉 PRZ"),
    "alert_trigger_near_pct": ("alert_fired", "감시 트리거"),
}

VERDICT_IMPROVED = "개선 (유의)"
VERDICT_IMPROVED_WEAK = "개선 신호 (유의 아님)"
VERDICT_NO_EFFECT = "효과 없음"
VERDICT_WORSENED_WEAK = "악화 신호 (유의 아님)"
VERDICT_WORSENED = "악화 (유의)"
VERDICT_INDETERMINATE = "판별 불가"


# ── 조치별 효과표 (Part A) ─────────────────────────────────────────


def action_effect_table(
    scores: list[Any],
    param_versions: list[Any],
    autonomy_logs: list[Any],
    *,
    now: datetime | None = None,
    window_days: int = EFFECT_WINDOW_DAYS,
    min_sample: int = MIN_WINDOW_SAMPLE,
    lookback_days: int = 90,
) -> list[dict[str, Any]]:
    """각 조치(파라미터 채택·자율 강등)의 전/후 창 적중률 대조.

    효과 없음/악화도 그대로 보고한다 — 개선 연출 금지.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    tested = [score for score in scores if score.outcome != "untested"]
    rows: list[dict[str, Any]] = []

    for version in param_versions:
        approved_at = _aware(version.approved_at)
        if approved_at < cutoff:
            continue
        scope_type, scope_label = PARAM_SCOPE.get(version.param, (None, version.param))
        pool = [score for score in tested if scope_type is None or score.judgment_type == scope_type]
        effect = _window_effect(pool, approved_at, now, window_days=window_days, min_sample=min_sample)
        rows.append(
            {
                "kind": "param_adoption",
                "action": f"{version.param} {version.old_value}→{version.new_value}",
                "scope": scope_label,
                "adopted_by": getattr(version, "adopted_by", "manual"),
                "at": approved_at.isoformat(),
                **effect,
            }
        )

    for log in autonomy_logs:
        if not getattr(log, "autonomous", False) or log.transition not in {"degrade", "quarantine"}:
            continue
        acted_at = _aware(log.created_at)
        if acted_at < cutoff:
            continue
        # 시그니처 조치의 스코프: 해당 시그니처가 남긴 판단만. 대개 표본이 작아
        # "판별 불가"가 정직한 답이다 — 구조 조치(노출 차단)라는 사실과 당시 근거를 병기.
        pool = [score for score in tested if isinstance(score.claim, dict) and score.claim.get("signature_key") == log.signature_key]
        effect = _window_effect(pool, acted_at, now, window_days=window_days, min_sample=min_sample)
        evidence = log.evidence if isinstance(log.evidence, dict) else {}
        rows.append(
            {
                "kind": "signature_downgrade",
                "action": f"{log.signature_key} → {log.new_state}",
                "scope": "해당 시그니처 판단",
                "adopted_by": "autonomy",
                "at": acted_at.isoformat(),
                "structural_note": f"발견/가중 노출 차단 (강등 시점 net 1R {evidence.get('win_1r_pct', '-')}%)",
                **effect,
            }
        )

    return sorted(rows, key=lambda row: row["at"], reverse=True)


def _window_effect(
    pool: list[Any],
    acted_at: datetime,
    now: datetime,
    *,
    window_days: int,
    min_sample: int,
) -> dict[str, Any]:
    window = timedelta(days=window_days)
    before = [score for score in pool if acted_at - window <= _aware(score.created_at) < acted_at]
    after = [score for score in pool if acted_at < _aware(score.created_at) <= min(acted_at + window, now)]
    before_stat = _accuracy(before)
    after_stat = _accuracy(after)
    verdict, verdict_reason = _improvement_verdict(before_stat, after_stat, min_sample=min_sample)
    return {
        "window_days": window_days,
        "before": before_stat,
        "after": after_stat,
        "delta_pct": (
            round(after_stat["accuracy_pct"] - before_stat["accuracy_pct"], 1)
            if before_stat["accuracy_pct"] is not None and after_stat["accuracy_pct"] is not None
            else None
        ),
        "verdict": verdict,
        "verdict_reason": verdict_reason,
    }


def _improvement_verdict(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    min_sample: int = MIN_WINDOW_SAMPLE,
) -> tuple[str, str]:
    """소표본 개선 판정 최소 기준 (docs/ImprovementProof.md).

    - 두 창 모두 N ≥ min_sample 이어야 판정. 아니면 판별 불가.
    - CI 비겹침 + 방향 일치 → 유의. |Δ| ≥ 5%p (CI 겹침) → 신호 (유의 아님). 그 외 효과 없음.
    """
    if before["tested"] < min_sample or after["tested"] < min_sample:
        return VERDICT_INDETERMINATE, f"표본 부족 (전 N={before['tested']}, 후 N={after['tested']} — 최소 {min_sample})"
    delta = after["accuracy_pct"] - before["accuracy_pct"]
    before_ci = before["accuracy_ci"]
    after_ci = after["accuracy_ci"]
    non_overlap = bool(before_ci and after_ci and (after_ci[0] > before_ci[1] or after_ci[1] < before_ci[0]))
    if delta > 0:
        if non_overlap:
            return VERDICT_IMPROVED, f"+{delta:.1f}%p · CI 비겹침"
        if delta >= WEAK_SIGNAL_DELTA_PCT:
            return VERDICT_IMPROVED_WEAK, f"+{delta:.1f}%p · CI 겹침 — 유의 기준 미달"
        return VERDICT_NO_EFFECT, f"{delta:+.1f}%p — 잡음 범위"
    if delta < 0:
        if non_overlap:
            return VERDICT_WORSENED, f"{delta:.1f}%p · CI 비겹침"
        if abs(delta) >= WEAK_SIGNAL_DELTA_PCT:
            return VERDICT_WORSENED_WEAK, f"{delta:.1f}%p · CI 겹침 — 유의 기준 미달"
        return VERDICT_NO_EFFECT, f"{delta:+.1f}%p — 잡음 범위"
    return VERDICT_NO_EFFECT, "±0.0%p"


# ── 레짐 통제 주간 비교 (Part A) ───────────────────────────────────


def regime_controlled_week_delta(
    scores: list[Any],
    *,
    now: datetime | None = None,
    min_sample: int = MIN_WINDOW_SAMPLE,
) -> dict[str, Any]:
    """이번 주 vs 전주 적중률 — 가능하면 동일 레짐 내 비교, 불가능하면 판별 불가 명시.

    레짐 태그는 판단 생성 시점에 claim.regime으로 기록된다 (WO-45부터).
    과거 미태깅 표본은 통제 불가 → 정직하게 '판별 불가'로 보고한다.
    """
    now = now or datetime.now(timezone.utc)
    week = timedelta(days=7)
    tested = [score for score in scores if score.outcome != "untested"]
    this_week = [score for score in tested if now - week <= _aware(score.created_at) < now]
    prev_week = [score for score in tested if now - 2 * week <= _aware(score.created_at) < now - week]

    raw = _delta_block(prev_week, this_week, min_sample=min_sample)

    def _regime(score: Any) -> str | None:
        claim = score.claim if isinstance(score.claim, dict) else {}
        value = claim.get("regime")
        return str(value) if value else None

    tagged_this = [score for score in this_week if _regime(score)]
    if not tagged_this:
        return {
            **raw,
            "controlled": False,
            "regime": None,
            "control_reason": "판별 불가 — 판단에 레짐 태그 없음 (이번 주부터 기록 시작)",
            "basis": "전체 (레짐 통제 불가)",
        }
    dominant = Counter(_regime(score) for score in tagged_this).most_common(1)[0][0]
    this_regime = [score for score in tagged_this if _regime(score) == dominant]
    prev_regime = [score for score in prev_week if _regime(score) == dominant]
    if len(this_regime) < min_sample or len(prev_regime) < min_sample:
        return {
            **raw,
            "controlled": False,
            "regime": dominant,
            "control_reason": (f"판별 불가 — 동일 레짐({dominant}) 표본 부족 (전주 N={len(prev_regime)}, 이번 주 N={len(this_regime)})"),
            "basis": "전체 (레짐 통제 불가)",
        }
    controlled = _delta_block(prev_regime, this_regime, min_sample=min_sample)
    return {
        **controlled,
        "controlled": True,
        "regime": dominant,
        "control_reason": None,
        "basis": f"동일 레짐({dominant}) 기준",
        "raw": raw,
    }


def _delta_block(prev: list[Any], current: list[Any], *, min_sample: int) -> dict[str, Any]:
    prev_stat = _accuracy(prev)
    current_stat = _accuracy(current)
    verdict, reason = _improvement_verdict(prev_stat, current_stat, min_sample=min_sample)
    return {
        "prev": prev_stat,
        "current": current_stat,
        "delta_pct": (
            round(current_stat["accuracy_pct"] - prev_stat["accuracy_pct"], 1)
            if prev_stat["accuracy_pct"] is not None and current_stat["accuracy_pct"] is not None
            else None
        ),
        "verdict": verdict,
        "verdict_reason": reason,
    }


# ── 주간 다이제스트 (Part B — WO-49 소비 스키마 고정) ──────────────


def weekly_improvement_digest(
    scores: list[Any],
    suggestions: list[Any],
    param_versions: list[Any],
    autonomy_logs: list[Any],
    signature_states: dict[str, str],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    delta = regime_controlled_week_delta(scores, now=now)
    effects = action_effect_table(scores, param_versions, autonomy_logs, now=now)
    actions_this_week = [row for row in effects if _aware_iso(row["at"]) >= week_ago]

    experiments = []
    for suggestion in suggestions:
        if getattr(suggestion, "status", "") != "experiment":
            continue
        started = _parse_dt((suggestion.autonomy or {}).get("started_at")) or _aware(suggestion.created_at)
        experiments.append(
            {
                "id": str(suggestion.id),
                "title": suggestion.title,
                "days_running": max(0, (now - started).days),
            }
        )

    quarantined = sorted(key for key, state in signature_states.items() if state == "quarantined")
    weakest = _weakest_type(scores, now=now)

    significant_action = any(row["verdict"] == VERDICT_IMPROVED for row in actions_this_week)
    significant_delta = delta["verdict"] == VERDICT_IMPROVED
    improvement_claim = bool(significant_action or significant_delta)

    if significant_delta:
        headline = f"적중률 {delta['current']['accuracy_pct']}% · 전주 대비 {delta['delta_pct']:+.1f}%p ({delta['basis']})"
    elif significant_action:
        best = next(row for row in actions_this_week if row["verdict"] == VERDICT_IMPROVED)
        headline = f"조치 효과 확인: {best['action']} ({best['verdict_reason']})"
    else:
        headline = "이번 주 유의미한 개선 없음"

    return {
        "generated_at": now.isoformat(),
        "period": {"start_at": week_ago.isoformat(), "end_at": now.isoformat(), "label": "최근 7일"},
        "schema_version": 1,
        "tested": delta["current"]["tested"],
        "accuracy_pct": delta["current"]["accuracy_pct"],
        "accuracy_ci": delta["current"]["accuracy_ci"],
        "delta_pct": delta["delta_pct"],
        "delta_basis": delta["basis"],
        "delta_verdict": delta["verdict"],
        "regime_control": {
            "controlled": delta["controlled"],
            "regime": delta.get("regime"),
            "reason": delta.get("control_reason"),
        },
        "actions": actions_this_week,
        "actions_total_90d": len(effects),
        "quarantined": quarantined,
        "experiments": experiments,
        "weakest": weakest,
        "improvement_claim": improvement_claim,
        "headline": headline,
        "sparkline": accuracy_sparkline(scores, now=now),
        "honesty_policy": "유의 기준(CI 비겹침) 미달은 개선으로 주장하지 않습니다. 효과 없음·악화·판별 불가는 그대로 표기합니다.",
    }


def accuracy_sparkline(scores: list[Any], *, now: datetime | None = None, weeks: int = SPARKLINE_WEEKS) -> list[dict[str, Any]]:
    """주간 적중률 12주 시계열 — 판단 성적표 상단 스파크라인 (WO-49)."""
    now = now or datetime.now(timezone.utc)
    tested = [score for score in scores if score.outcome != "untested"]
    points: list[dict[str, Any]] = []
    for index in range(weeks, 0, -1):
        start = now - timedelta(days=7 * index)
        end = start + timedelta(days=7)
        bucket = [score for score in tested if start <= _aware(score.created_at) < end]
        stat = _accuracy(bucket)
        points.append(
            {
                "week_start": start.date().isoformat(),
                "tested": stat["tested"],
                "accuracy_pct": stat["accuracy_pct"],
                "accuracy_ci": stat["accuracy_ci"],
            }
        )
    return points


def _weakest_type(scores: list[Any], *, now: datetime, min_sample: int = MIN_WINDOW_SAMPLE, window_days: int = 28) -> dict[str, Any] | None:
    """최근 창에서 표본 충분한 판단 유형 중 최저 적중률 — 최약점."""
    cutoff = now - timedelta(days=window_days)
    recent = [score for score in scores if score.outcome != "untested" and _aware(score.created_at) >= cutoff]
    by_type: dict[str, list[Any]] = {}
    for score in recent:
        by_type.setdefault(score.judgment_type, []).append(score)
    candidates = []
    for judgment_type, bucket in by_type.items():
        stat = _accuracy(bucket)
        if stat["tested"] >= min_sample and stat["accuracy_pct"] is not None:
            candidates.append({"judgment_type": judgment_type, **stat})
    if not candidates:
        return None
    weakest = min(candidates, key=lambda item: item["accuracy_pct"])
    return {**weakest, "window_days": window_days}


# ── 공용 ──────────────────────────────────────────────────────────


def _accuracy(bucket: list[Any]) -> dict[str, Any]:
    tested = len(bucket)
    correct = len([score for score in bucket if score.outcome == "correct"])
    accuracy = round(correct / tested * 100, 1) if tested else None
    ci = bootstrap_ci_from_counts(correct, tested) if tested else None
    return {"tested": tested, "correct": correct, "accuracy_pct": accuracy, "accuracy_ci": list(ci) if ci else None}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _aware_iso(value: str) -> datetime:
    return _aware(datetime.fromisoformat(value))


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(str(value)))
    except ValueError:
        return None
