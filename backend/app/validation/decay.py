"""부패 감지 + 비대칭 자율 전이 (WO-FCE-37 §2).

주 1회 워커. 입력: WO-36 워크포워드 곡선 + OOS + 라이브 괴리.
결정론 규칙 (docs/Validation.md):
  R1. 최근 창 승률 CI 상한 < 장기 승률 점추정 → degrade 신호
  R2. OOS unstable (학습/검증 괴리 > 15%p) → degrade 신호
  R3. 라이브 vs 백테스트 괴리 > 20%p (라이브 N ≥ 15) → degrade 신호
  R4. degraded 상태에서 부패 재관측 (2회 연속 창) → quarantine
  R5. degraded/quarantined에서 최근 2창 연속 게이트 재통과 → 복귀 제안 (자율 아님)
  레짐별 부분 부패: 특정 레짐 슬라이스만 부패 → 해당 레짐 한정 degrade

비대칭: degrade/quarantine 은 자율 즉시. 복귀는 제안-승인 필수.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.analyst.signature_registry import (
    base_state,
    record_transition,
    state_map,
)
from app.backtest.statistics import bootstrap_ci_from_counts


def evaluate_signature(
    stat: dict[str, Any],
    *,
    current: str,
    live: dict[str, Any] | None = None,
    settings: Any = None,
) -> dict[str, Any]:
    """부패/복귀 판정 (결정론). action ∈ {none, degrade, quarantine, recover_proposal}."""
    min_ci = float(getattr(settings, "universe_backtest_min_ci_low_pct", 50.0)) if settings else 50.0
    div_pct = float(getattr(settings, "decay_live_divergence_pct", 20.0)) if settings else 20.0
    div_min = int(getattr(settings, "decay_live_min_sample", 15)) if settings else 15

    point = _num(stat.get("win_1r_pct"))
    windows = _sufficient_windows(stat)
    signals: list[str] = []

    # R1: 최근 충분 표본 창의 CI 상한이 장기 점추정보다 낮으면 부패.
    recent_ci = _window_ci(windows[-1]) if windows else None
    if point is not None and recent_ci is not None and recent_ci[1] < point:
        signals.append("recent_window_ci_upper_below_longterm")

    # R2: OOS 불안정.
    if stat.get("unstable"):
        signals.append("oos_unstable")

    # R3: 라이브 괴리.
    if isinstance(live, dict):
        live_win = _num(live.get("win_pct"))
        live_n = int(live.get("sample") or 0)
        if point is not None and live_win is not None and live_n >= div_min and abs(live_win - point) > div_pct:
            signals.append("live_backtest_divergence")

    decayed = bool(signals)

    # R5: 복귀 — 최근 2창 연속 게이트 재통과 (CI 하한 ≥ 임계) 이고 현재 부패 신호 없음.
    recover = (
        current in {"degraded", "quarantined"} and not decayed and len(windows) >= 2 and all((_window_ci(w) or (0.0, 0.0))[0] >= min_ci for w in windows[-2:])
    )

    evidence = {
        "win_1r_pct": point,
        "win_1r_ci": stat.get("win_1r_ci"),
        "unstable": bool(stat.get("unstable")),
        "recent_window_ci": list(recent_ci) if recent_ci else None,
        "signals": signals,
        "live": live if isinstance(live, dict) else None,
        "sample_size": int(stat.get("sample_size") or 0),
        "symbol": stat.get("symbol"),  # 대표 통계의 심볼 (감사용)
        "stat_generated_at": stat.get("generated_at"),
    }

    if current == "validated" and decayed:
        # 전면 부패 신호에는 레짐 태그를 붙이지 않는다 — "레짐 한정" 오독 방지.
        return {"action": "degrade", "reason": signals[0], "regime": None, "evidence": evidence}
    if current == "validated" and not decayed:
        regime = _partial_decay_regime(stat, point)
        if regime:
            evidence["signals"] = ["regime_partial_decay"]
            return {"action": "degrade", "reason": "regime_partial_decay", "regime": regime, "evidence": evidence}
    if current == "degraded" and decayed:
        return {"action": "quarantine", "reason": signals[0], "regime": None, "evidence": evidence}
    if recover:
        return {"action": "recover_proposal", "reason": "two_windows_gate_repass", "regime": None, "evidence": evidence}
    return {"action": "none", "reason": "stable", "regime": None, "evidence": evidence}


def run_decay_sweep(
    repo: Any,
    settings: Any,
    *,
    now: datetime | None = None,
    live_stats: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """전 시그니처 부패 스윕. 자율 강등/격리 적용 + 복귀 제안 생성 + 안전장치."""
    now = now or datetime.now(timezone.utc)
    autonomy_enabled = bool(getattr(settings, "autonomy_enabled", True))
    cap = int(getattr(settings, "autonomy_weekly_transition_cap", 5))

    stats = _latest_stats_by_signature(repo)
    states = state_map(repo)
    # 이미 이번 주 자율 전이한 건수 (상한 계산).
    week_ago = now - timedelta(days=7)
    used = len([log for log in repo.list_autonomy_logs(since=week_ago, limit=500) if log.autonomous])

    def _effective_state(key: str, stat: dict[str, Any] | None) -> str:
        return states.get(key) or base_state(
            stat,
            min_sample=int(getattr(settings, "signature_validated_min_sample", 30)),
            min_ci_low=float(getattr(settings, "universe_backtest_min_ci_low_pct", 50.0)),
        )

    # 전면 격리(엔진 전면 불신) 판정 — 분모는 "평가 대상 전체"(통계 보유 ∪ 로그 보유).
    # 로그 있는 시그니처만 세면 첫 격리 1건만으로 전면 불신이 되는 오탐이 난다.
    universe_keys = set(stats) | set(states)
    effective_states = [_effective_state(key, stats.get(key)) for key in universe_keys]
    all_quarantined = bool(effective_states) and all(s == "quarantined" for s in effective_states)
    frozen = all_quarantined  # 신규 자율 강등 동결

    transitions: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    capped: list[dict[str, Any]] = []
    stale_skipped: list[dict[str, Any]] = []

    for key, stat in sorted(stats.items()):
        state = _effective_state(key, stat)
        decision = evaluate_signature(stat, current=state, live=(live_stats or {}).get(key), settings=settings)
        action = decision["action"]
        if action == "none":
            continue
        if action == "quarantine" and not _has_new_data_since_degrade(repo, key, stat):
            # R4: 격리는 "부패 재관측"이어야 한다 — 강등 이후 새 데이터 없이 같은 관측을
            # 두 번 세지 않는다 (주간 리포트를 연달아 요청해도 격리로 점프 금지).
            stale_skipped.append({"signature_key": key, "reason": "no_new_data_since_degrade"})
            continue
        if action == "recover_proposal":
            # 비대칭: 복귀는 자율 아님 — 제안으로만 기록 (상태 불변).
            # 온디맨드 스윕(봇 명령) 반복 호출 시 같은 제안을 중복 기록하지 않는다.
            if not _duplicate_pending_log(repo, key, "recover_proposed"):
                record_transition(
                    repo,
                    signature_key=key,
                    previous=state,
                    new=state,
                    transition="recover_proposed",
                    reason=decision["reason"],
                    evidence=decision["evidence"],
                    autonomous=False,
                    regime=decision["regime"],
                )
            proposals.append({"signature_key": key, "from": state, "reason": decision["reason"]})
            continue
        # degrade / quarantine — 자율 방향.
        target = "degraded" if action == "degrade" else "quarantined"
        blocked = (not autonomy_enabled) or frozen or (used >= cap)
        if blocked:
            # 킬스위치/동결/상한 초과 → 제안 모드 (상태 불변, 통보만).
            reason = "killswitch" if not autonomy_enabled else "frozen_all_quarantined" if frozen else "weekly_cap"
            if not _duplicate_pending_log(repo, key, f"{action}_proposed"):
                record_transition(
                    repo,
                    signature_key=key,
                    previous=state,
                    new=state,
                    transition=f"{action}_proposed",
                    reason=f"{decision['reason']}:{reason}",
                    evidence=decision["evidence"],
                    autonomous=False,
                    regime=decision["regime"],
                )
            capped.append({"signature_key": key, "action": action, "reason": reason})
            continue
        record_transition(
            repo,
            signature_key=key,
            previous=state,
            new=target,
            transition=action,
            reason=decision["reason"],
            evidence=decision["evidence"],
            autonomous=True,
            regime=decision["regime"],
        )
        used += 1
        transitions.append(
            {
                "signature_key": key,
                "from": state,
                "to": target,
                "reason": decision["reason"],
                "regime": decision["regime"],
            }
        )

    return {
        "generated_at": now.isoformat(),
        "autonomy_enabled": autonomy_enabled,
        "weekly_cap": cap,
        "autonomous_transitions": transitions,
        "recovery_proposals": proposals,
        "deferred_to_proposal": capped,
        "stale_skipped": stale_skipped,
        "all_quarantined": all_quarantined,
        "critical": all_quarantined,
        "frozen": frozen,
    }


def _duplicate_pending_log(repo: Any, key: str, transition: str) -> bool:
    """해당 시그니처의 최신 로그가 이미 같은 제안이면 재기록을 생략한다."""
    logs = repo.list_autonomy_logs(signature_key=key, limit=1)
    return bool(logs) and logs[0].transition == transition


def _has_new_data_since_degrade(repo: Any, key: str, stat: dict[str, Any]) -> bool:
    """강등 로그 이후 통계가 재생성됐는지 — 격리(R4)는 새 관측을 요구한다."""
    degrade_logs = [log for log in repo.list_autonomy_logs(signature_key=key, limit=50) if log.transition == "degrade"]
    if not degrade_logs:
        return True  # 강등 로그가 없으면(레거시/수동) 차단하지 않는다
    degraded_at = degrade_logs[0].created_at
    if degraded_at.tzinfo is None:
        degraded_at = degraded_at.replace(tzinfo=timezone.utc)
    generated = _parse_dt(stat.get("generated_at"))
    return generated is not None and generated > degraded_at


def apply_recovery(repo: Any, signature_key: str, *, approved_by: str = "manual") -> Any:
    """복귀 승인 적용 (제안-승인 경유). 자율 아님 — 사람 승인 후에만 호출."""
    logs = repo.list_autonomy_logs(signature_key=signature_key, limit=1)
    previous = logs[0].new_state if logs else "degraded"
    proposal = next(
        (log for log in repo.list_autonomy_logs(signature_key=signature_key, limit=50) if log.transition == "recover_proposed"),
        None,
    )
    evidence = proposal.evidence if proposal else {"approved_by": approved_by}
    return record_transition(
        repo,
        signature_key=signature_key,
        previous=previous,
        new="validated",
        transition="recover_applied",
        reason=f"approved_by:{approved_by}",
        evidence={**evidence, "approved_by": approved_by},
        autonomous=False,
    )


def build_self_audit(repo: Any, *, now: datetime | None = None, sweep: dict[str, Any] | None = None) -> dict[str, Any]:
    """주간 셀프 오딧 (WO-37 §3) — "엔진이 스스로 한 일 / 승인 대기 중인 일" 2단.

    자율과 승인의 경계를 매주 가시화한다.
    """
    now = now or datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    logs = repo.list_autonomy_logs(since=week_ago, limit=1000)
    states = state_map(repo)

    autonomous = [_transition_row(log) for log in logs if log.autonomous and log.transition in {"degrade", "quarantine"}]
    recover_applied = [_transition_row(log) for log in logs if log.transition == "recover_applied"]
    awaiting = [
        _transition_row(log)
        for log in logs
        if not log.autonomous and log.transition in {"recover_proposed", "promotion_proposed", "degrade_proposed", "quarantine_proposed"}
    ]

    degraded_now = sorted(key for key, state in states.items() if state == "degraded")
    quarantined_now = sorted(key for key, state in states.items() if state == "quarantined")
    # 복귀 제안 대기: "최신" recover_proposed가 "최신" recover_applied보다 나중인 시그니처.
    # 전 기간 키 집합 비교는 재강등 후 새 제안을 영원히 숨긴다.
    latest_proposed: dict[str, datetime] = {}
    latest_applied: dict[str, datetime] = {}
    for log in repo.list_autonomy_logs(limit=2000):
        stamp = log.created_at if log.created_at.tzinfo else log.created_at.replace(tzinfo=timezone.utc)
        if log.transition == "recover_proposed":
            latest_proposed[log.signature_key] = max(latest_proposed.get(log.signature_key, stamp), stamp)
        elif log.transition == "recover_applied":
            latest_applied[log.signature_key] = max(latest_applied.get(log.signature_key, stamp), stamp)
    recovery_pending = sorted(key for key, proposed_at in latest_proposed.items() if key not in latest_applied or proposed_at > latest_applied[key])
    divergence_top = _top_divergence(logs)

    return {
        "generated_at": now.isoformat(),
        "engine_did_autonomously": {
            "label": "엔진이 이번 주 스스로 한 일 (자율)",
            "transitions": autonomous,
            "count": len(autonomous),
        },
        "awaiting_approval": {
            "label": "사용자 승인 대기 중인 일 (제안)",
            "transitions": awaiting,
            "recovery_pending": recovery_pending,
            "count": len(awaiting),
        },
        "decaying_signatures": degraded_now,
        "quarantined_signatures": quarantined_now,
        "recovery_applied_this_week": recover_applied,
        "live_backtest_divergence_top": divergence_top[:3],
        "meta_integrity": autonomy_scorecard(repo, now=now),
        "critical": bool(sweep and sweep.get("critical")),
        "all_quarantined": bool(sweep and sweep.get("all_quarantined")),
    }


def _transition_row(log: Any) -> dict[str, Any]:
    signals = log.evidence.get("signals") if isinstance(log.evidence, dict) else None
    return {
        "signature_key": log.signature_key,
        "from": log.previous_state,
        "to": log.new_state,
        "transition": log.transition,
        "reason": log.reason,
        "regime": log.regime,
        "win_1r_pct": log.evidence.get("win_1r_pct") if isinstance(log.evidence, dict) else None,
        "signals": signals,
        "at": log.created_at.isoformat() if hasattr(log.created_at, "isoformat") else str(log.created_at),
    }


def _top_divergence(logs: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log in logs:
        evidence = log.evidence if isinstance(log.evidence, dict) else {}
        live = evidence.get("live") if isinstance(evidence.get("live"), dict) else None
        point = _num(evidence.get("win_1r_pct"))
        live_win = _num(live.get("win_pct")) if live else None
        if point is None or live_win is None:
            continue
        rows.append({"signature_key": log.signature_key, "gap_pct": round(abs(live_win - point), 1), "live_win_pct": live_win, "backtest_win_pct": point})
    return sorted(rows, key=lambda row: row["gap_pct"], reverse=True)


def autonomy_scorecard(repo: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """메타 무결성 — 자율 강등의 사후 성적 (오판율 집계).

    자율 강등/격리 후 복귀(recover_applied)된 비율 = 강등 오판율 프록시.
    자율 규칙 자체도 채점 대상 (WO-37 §4).
    """
    logs = repo.list_autonomy_logs(limit=2000)
    auto_downgrades = [log for log in logs if log.autonomous and log.transition in {"degrade", "quarantine"}]
    # 오판 = 강등 "이후에" 복귀 승인된 건. 전 기간 키 매칭은 한 번 복귀한 키의
    # 이후 정당한 강등까지 전부 오판으로 집계해 오판율을 과대평가한다.
    applied_at_by_key: dict[str, list[datetime]] = {}
    for log in logs:
        if log.transition == "recover_applied":
            stamp = log.created_at if log.created_at.tzinfo else log.created_at.replace(tzinfo=timezone.utc)
            applied_at_by_key.setdefault(log.signature_key, []).append(stamp)
    total = len(auto_downgrades)
    reversed_count = 0
    for log in auto_downgrades:
        downgraded_at = log.created_at if log.created_at.tzinfo else log.created_at.replace(tzinfo=timezone.utc)
        if any(applied > downgraded_at for applied in applied_at_by_key.get(log.signature_key, [])):
            reversed_count += 1
    return {
        "autonomous_downgrades": total,
        "reversed_after_recovery": reversed_count,
        "misjudgment_rate_pct": round(reversed_count / total * 100, 1) if total else None,
        "note": "자율 강등 후 복귀 승인된 비율 — 자율 규칙의 사후 오판율 (표본 부족 시 결론 유보)",
        "sample_state": "ok" if total >= 10 else "insufficient",
    }


# ── 내부 헬퍼 ─────────────────────────────────────────────────────


def _latest_stats_by_signature(repo: Any) -> dict[str, dict[str, Any]]:
    """시그니처별 대표 통계 — 표본이 가장 큰 심볼의 행 (동률이면 최신).

    signature_key는 심볼을 포함하지 않으므로 "최신 행"만 취하면 방금 분석된
    소표본 알트가 BTC의 대표본 통계를 밀어내고 전역 강등을 유발할 수 있다.
    """
    stats: dict[str, dict[str, Any]] = {}
    for stat in repo.list_backtest_stats(limit=1000):  # newest-first
        payload = stat.model_dump(mode="json")
        inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        merged = {**payload, **{k: v for k, v in inner.items() if k not in payload or payload[k] is None}}
        key = merged.get("signature_key")
        if not key:
            continue
        existing = stats.get(key)
        if existing is None or int(merged.get("sample_size") or 0) > int(existing.get("sample_size") or 0):
            stats[key] = merged
    return stats


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _sufficient_windows(stat: dict[str, Any]) -> list[dict[str, Any]]:
    curve = stat.get("walk_forward")
    if not isinstance(curve, list):
        return []
    return [w for w in curve if isinstance(w, dict) and w.get("sample_sufficient") and _num(w.get("win_1r_pct")) is not None]


def _window_ci(window: dict[str, Any]) -> tuple[float, float] | None:
    win = _num(window.get("win_1r_pct"))
    n = int(window.get("sample_size") or 0)
    if win is None or n <= 0:
        return None
    return bootstrap_ci_from_counts(round(win / 100 * n), n)


def _partial_decay_regime(stat: dict[str, Any], point: float | None) -> str | None:
    """특정 레짐 슬라이스만 CI 상한이 전체 점추정보다 낮은 경우 그 레짐 반환."""
    if point is None:
        return None
    regimes = stat.get("regimes") if isinstance(stat.get("regimes"), dict) else {}
    for name, slice_stat in sorted(regimes.items()):
        if not isinstance(slice_stat, dict):
            continue
        ci = slice_stat.get("win_1r_ci")
        n = int(slice_stat.get("sample_size") or 0)
        if isinstance(ci, (list, tuple)) and len(ci) == 2 and n >= 10 and float(ci[1]) < point:
            return name
    return None


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
