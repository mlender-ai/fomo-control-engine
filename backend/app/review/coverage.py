"""Judgment-ledger coverage audit (WO-FCE-89).

Coverage is measured from authoritative emitted records. Unknown types fail the
inventory audit instead of being silently counted as covered.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any


SCOREABLE_TYPES = {
    "analyst_briefing",
    "candidate_signature",
    "entry_checklist",
    "entry_intent",
    "fomo_entry",
    "harmonic_prz",
    "invalidation",
    "liquidity_sweep",
    "paper_trade_entry",
    "planned_invalidation",
    "planned_take_profit",
    "position_deepdive",
    "position_status_transition",
    "scout_setup",
    "stance_flipped",
    "take_profit",
    "universe_discovery",
    "wyckoff_event",
}

UNSCORABLE_TYPES = {
    "alert_fired": "알림 전달 기록이며 판정 결과는 의미별 원장 행이 담당",
    "entry_intent_registered": "등록 수명주기 기록; 발화한 entry_intent를 별도 채점",
    "position_entry_snapshot": "진입 당시 사실 스냅샷; 방향 주장이 아님",
    "position_health_change": "점수 변화 사실; 독립 방향 결과 정의 없음",
}


def judgment_coverage(repo: Any, *, now: datetime | None = None, days: int = 7) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    judgments = repo.list_judgments_all(since=since, limit=10000)
    scores = repo.list_judgment_scores(limit=20000)
    scored_ids = {score.judgment_id for score in scores if score.outcome != "untested"}
    autonomy = repo.list_autonomy_logs(since=since, limit=10000)
    type_counts = Counter(entry.type for entry in judgments)
    unknown = sorted(set(type_counts) - SCOREABLE_TYPES - set(UNSCORABLE_TYPES))
    scoreable = [entry for entry in judgments if entry.type in SCOREABLE_TYPES]
    pending = [entry for entry in scoreable if entry.judgment_id not in scored_ids]
    unscorable_count = sum(type_counts.get(kind, 0) for kind in UNSCORABLE_TYPES) + len(autonomy)
    total = len(scoreable) + unscorable_count + sum(type_counts.get(kind, 0) for kind in unknown)
    recorded = len(scoreable)
    coverage_pct = round(recorded / max(1, total - unscorable_count) * 100, 1) if total > unscorable_count else 100.0
    unscorable_types = {
        **{kind: count for kind, count in type_counts.items() if kind in UNSCORABLE_TYPES and count},
        **({"autonomy_state_transition": len(autonomy)} if autonomy else {}),
    }
    return {
        "status": "ok" if not unknown else "attention",
        "period_days": days,
        "as_of": now.isoformat(),
        "total": total,
        "recorded": recorded,
        "pending": len(pending),
        "scored": recorded - len(pending),
        "unscorable": unscorable_count,
        "coverage_pct": coverage_pct,
        "unscorable_types": unscorable_types,
        "unclassified_types": unknown,
        "recorded_type_counts": dict(sorted(type_counts.items())),
        "definition": "coverage = 원장에 기록된 채점 가능 판단 / (전체 판단 - 채점 불가)",
    }
