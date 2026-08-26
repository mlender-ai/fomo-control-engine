"""3-track paper diagnosis surface (WO-FCE-PAPER-OBSERVABILITY-01, 작업 7).

"왜 조용한가"의 답을 한 화면에서 낸다. 레포 정밀 실사를 반복하지 않기 위한 산출물이다.
각 트랙(crypto/stock/poly)에 대해 구동 잡·엔진 플래그, 마지막 effective run, 텔레그램 배선,
뮤트 상태, ready_to_start 사유, 최다 거부 게이트를 반환한다.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.notify.paper_events import TELEGRAM_SENDABLE_KINDS
from app.stock_paper.service import _ready_to_start as stock_ready_to_start
from app.stock_paper.store import StockPaperStore
from app.worker.runtime import get_worker_status


def _top_reject_gate(distribution: dict[str, Any]) -> str | None:
    gates = distribution.get("gates") if isinstance(distribution, dict) else None
    if not gates:
        return None
    first = gates[0]
    return str(first.get("gate")) if isinstance(first, dict) else None


def _observation_integrity(settings: Any) -> dict[str, Any]:
    """저장된 커버리지로 트랙별 검증 시계를 낸다(WO-FCE-OBSERVATION-INTEGRITY-01 Phase 1).

    계산은 워커 잡(`observation_coverage`)이 하고 여기선 읽기만 한다 — 진단 호출이 무거워지면
    관측 표면 자체가 장애 원인이 된다.
    """
    from app.db.maintenance import sqlite_path
    from app.db.sqlite_utils import connect_sqlite
    from app.validation import window_anchor
    from app.worker import observation

    path = sqlite_path(settings.database_url)
    if path is None or not path.exists():
        return {"available": False, "reason": "sqlite_only"}
    try:
        with connect_sqlite(str(path)) as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM observation_coverage ORDER BY day")]
            # 진단 표면도 검증 판정과 **같은 창**을 본다. 다른 창을 보면 화면끼리 어긋난다.
            anchors = {track: window_anchor.current_anchor(connection, track) for track in observation.TRACK_SPECS}
    except Exception as exc:  # 진단 실패가 나머지 진단을 못 죽이게 한다
        return {"available": False, "reason": str(exc)[:120]}
    from app.worker import sleep_guard

    actions = observation.manual_action_items(rows)
    # WO-FCE-VALIDATION-VERDICT-01 Phase 2: 조치를 적용했다는 사실보다 **지금도 살아 있다는
    # 사실**이 중요하다. 설정이 풀리면 아무 소리 없이 손실이 다시 시작된다.
    guard = sleep_guard.sleep_guard_status()
    guard_action = sleep_guard.guard_action_item(guard)
    if guard_action:
        actions.append(guard_action)
    return {
        "available": True,
        "principle": "검증일은 커버리지 게이트를 통과한 날만 센다 — 통과 못 한 날은 유실일이다.",
        "min_coverage_pct": observation.MIN_COVERAGE_PCT,
        "bin_seconds": observation.BIN_SECONDS,
        "tracks": {
            track: observation.verification_clock(
                [row for row in rows if row["track"] == track],
                anchor_day=anchors[track].anchor_day if anchors.get(track) else None,
            )
            for track in observation.TRACK_SPECS
        },
        "windows": {track: (anchor.as_dict() if anchor else None) for track, anchor in anchors.items()},
        "manual_actions": actions,
        "sleep_guard": guard,
    }


def _sample_viability(settings: Any) -> dict[str, Any]:
    """검증 완료 판정 (WO-FCE-SAMPLE-VIABILITY-01 PHASE 1·6).

    유효 관측일만으로는 검증이 되지 않는다 — 채점 가능한 표본이 나와야 검증이다.
    폴리처럼 선정 기준상 표본이 생성될 수 없는 트랙은 여기서 `STRUCTURALLY_BLOCKED` 로 뜬다.
    """
    from app.db.maintenance import sqlite_path
    from app.db.sqlite_utils import connect_sqlite
    from app.validation import sample_viability

    path = sqlite_path(settings.database_url)
    if path is None or not path.exists():
        return {"available": False, "reason": "sqlite_only"}
    try:
        with connect_sqlite(str(path)) as connection:
            report = sample_viability.sample_viability_report(connection)
    except Exception as exc:  # 진단 실패가 나머지 진단을 못 죽이게 한다
        return {"available": False, "reason": str(exc)[:120]}
    return {"available": True, **report}


def _live_trading_gate(settings: Any) -> dict[str, Any]:
    """자동매매 전환 게이트 진행도 (WO-FCE-VALIDATION-VERDICT-01 Phase 4).

    **판정만 노출한다.** 이 블록에는 봉인을 푸는 경로가 없다 — `LiveBroker` 는 미구현이고
    `FCE_STOCK_LIVE_TRADING_ENABLED=true` 는 기동 자체가 거부된다.
    """
    from app.db.maintenance import sqlite_path
    from app.db.sqlite_utils import connect_sqlite
    from app.validation import live_trading_gate

    path = sqlite_path(settings.database_url)
    if path is None or not path.exists():
        return {"available": False, "reason": "sqlite_only"}
    try:
        with connect_sqlite(str(path)) as connection:
            report = live_trading_gate.live_trading_gate_report(connection)
    except Exception as exc:  # 진단 실패가 나머지 진단을 못 죽이게 한다
        return {"available": False, "reason": str(exc)[:120]}
    return {"available": True, **report}


def _rate_gap(settings: Any) -> dict[str, Any]:
    """표본 생성 속도 격차 (WO-FCE-SAMPLE-RATE-01 Phase 1)."""
    from app.db.maintenance import sqlite_path
    from app.db.sqlite_utils import connect_sqlite
    from app.validation import sample_rate

    path = sqlite_path(settings.database_url)
    if path is None or not path.exists():
        return {"available": False, "reason": "sqlite_only"}
    try:
        with connect_sqlite(str(path)) as connection:
            return {"available": True, **sample_rate.rate_gap_report(connection)}
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:120]}


def _pending_decisions(settings: Any) -> dict[str, Any]:
    """사람을 기다리는 결정들 (Phase 5). 잊힌 결정은 없는 결정과 같다."""
    from app.validation import live_trading_gate, pending_decisions
    from app.worker import sleep_guard

    items = pending_decisions.pending_decisions(
        gate_approved=live_trading_gate.GATE_APPROVED,
        sleep_guard=sleep_guard.sleep_guard_status(),
        # 3-5: 유실일이 정한 유효일 상한. 창을 못 채우면 절전 결정이 차단 등급이 된다.
        lost_day_ceilings=_stock_lost_day_ceilings(settings),
        # POLY-STATUS-01 2-3: 폴리가 구조적으로 막혀 있으면 처리 방침 결정을 올린다.
        poly_blocked=_poly_blocked_state(settings),
    )
    return pending_decisions.pending_summary(items)


def _poly_blocked_state(settings: Any) -> dict[str, Any] | None:
    """폴리 차단 상태. 막혀 있지 않으면 `None` 이라 결정 항목이 뜨지 않는다.

    판정을 만들지 않는다 — `poly_paper_dashboard` 가 이미 낸 상태를 읽는다(C3).
    """
    try:
        from app.poly_paper.service import poly_paper_dashboard

        status = poly_paper_dashboard(settings).get("status") or {}
    except Exception:
        return None
    blocked = bool(status.get("structurally_blocked")) or (status.get("collection") or {}).get("status") == "geo_blocked"
    if not blocked:
        return None
    return {
        "structurally_blocked": bool(status.get("structurally_blocked")),
        "collection_status": (status.get("collection") or {}).get("status"),
        "headline": status.get("headline"),
        "verdict_reason": status.get("verdict_reason"),
    }


def _stock_lost_day_ceilings(settings: Any) -> dict[str, Any]:
    """주식 트랙별 유효일 상한 (WO-FCE-STOCK-STATUS-01 3-5).

    유실일은 이미 대시보드가 세고 있다 — 다시 세지 않고 그 값을 읽는다. 조회 실패가
    진단 표면을 죽이면 안 되므로 실패 시 빈 dict 다(등급이 오르지 않을 뿐 숨기지 않는다).
    """
    from app.stock_paper.store import StockPaperStore
    from app.validation import pending_decisions

    try:
        store = StockPaperStore(str(getattr(settings, "database_url", "")))
        if not store.enabled:
            return {}
        tracks = store.dashboard().get("tracks") or []
    except Exception:
        return {}
    return {
        str(track.get("market")): pending_decisions.effective_day_ceiling(
            calendar_days=int(track.get("calendar_days") or 0),
            lost_days=int(track.get("lost_days") or 0),
        )
        for track in tracks
        if track.get("market")
    }


def _whale_observations(settings: Any) -> dict[str, Any]:
    """강등된 고래 다중체결 관측 조회 (WO-FCE-WHALE-ALERT-DEMOTE-01 Phase 2).

    **발송하지 않은 것과 발생하지 않은 것은 다르다**(C3). 푸시에서 뺐으므로 여기서 반드시
    조회 가능해야 하고, 각 건에 미발송 사유가 붙어야 한다. 수집·저장은 그대로다(C2).
    """
    from app.notify import delivery_gate
    from app.notify.state import NotificationState

    state = NotificationState()
    try:
        state.load(settings.notification_state_path)
    except Exception as exc:  # 조회 실패가 나머지 진단을 못 죽이게 한다
        return {"available": False, "reason": str(exc)[:120]}
    items = []
    for entry in reversed(state.blocked_alerts):
        if str(entry.get("rule_id") or "") != "whale_entry":
            continue
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        items.append(
            {
                "blocked_at": entry.get("blocked_at"),
                "wallet_address": payload.get("wallet_address"),
                "wallet_label": payload.get("wallet_label"),
                "window_seconds": payload.get("window_seconds"),
                "fill_count": payload.get("fill_count"),
                "coins": payload.get("coins") or [],
                "total_notional": payload.get("total_notional"),
                "validated": payload.get("validated"),
                "validation_state": payload.get("validation_state"),
                "win_1r_pct": payload.get("win_1r_pct"),
                "sample_size": payload.get("sample_size"),
                "sample_sufficient": int(payload.get("sample_size") or 0) >= 30,
                "cumulative_return_r": payload.get("cumulative_return_r"),
                "not_delivered_reason": entry.get("reason"),
            }
        )
    return {
        "available": True,
        "principle": "푸시에서 뺐으므로 여기서 조회된다 — 조용히 사라지지 않는다(C3).",
        "count": len(items),
        "sample_rule": "사후 채점 승률은 N<30 이면 표본 부족입니다. 선정은 quality_score 기준이며 승률로 뽑지 않습니다.",
        "items": items[:100],
        "gate": delivery_gate.registry_snapshot(),
    }


def paper_diagnosis() -> dict[str, Any]:
    settings = get_settings()
    worker = get_worker_status()
    jobs = worker.get("jobs", {}) if isinstance(worker, dict) else {}
    notifications = worker.get("notifications", {}) if isinstance(worker, dict) else {}
    telegram_base = bool(getattr(settings, "paper_telegram_alerts_enabled", True) and settings.telegram_alerts_enabled)
    mute_state = {
        "is_muted": bool(notifications.get("is_muted")),
        "muted_until": notifications.get("muted_until"),
    }

    def _job(name: str) -> dict[str, Any]:
        job = jobs.get(name)
        return job if isinstance(job, dict) else {}

    # ── stock ────────────────────────────────────────────────
    stock_top_gate: str | None = None
    stock_store = StockPaperStore(settings.database_url)
    if stock_store.enabled:
        try:
            stock_top_gate = _top_reject_gate(stock_store.rejection_distribution(days=1))
        except Exception:
            stock_top_gate = None
    stock_ready = stock_ready_to_start(settings)
    stock_job = _job("toss_stock_scout")
    stock = {
        "enabled_flags": {
            "engine": bool(settings.stock_paper_engine_enabled),
            "driver_job": bool(settings.toss_stock_scout_enabled),
        },
        "last_effective_run_at": stock_job.get("last_effective_run_at"),
        "last_success_at": stock_job.get("last_success_at"),
        "top_reject_gate": stock_top_gate,
        "telegram_wired": telegram_base,
        "mute_state": mute_state,
        "ready_to_start": stock_ready,
        "ready_to_start_reason": None if stock_ready else "toss_observation_not_configured (엔진·구동잡 플래그와 Toss 자격증명 필요)",
    }

    # ── poly ─────────────────────────────────────────────────
    poly_job = _job("polymarket_paper")
    poly = {
        "enabled_flags": {
            "engine": bool(settings.polymarket_paper_enabled),
            "driver_job": bool(settings.polymarket_paper_enabled),
        },
        "last_effective_run_at": poly_job.get("last_effective_run_at"),
        "last_success_at": poly_job.get("last_success_at"),
        "top_reject_gate": None,
        "telegram_wired": telegram_base,
        "mute_state": mute_state,
        "ready_to_start": bool(settings.polymarket_paper_enabled),
        "ready_to_start_reason": None if settings.polymarket_paper_enabled else "disabled",
    }

    # ── crypto (참조: 정상 동작 트랙) ─────────────────────────
    crypto_job = _job("paper_engine")
    crypto = {
        "enabled_flags": {
            "engine": bool(settings.paper_engine_enabled),
            "driver_job": True,
        },
        "last_effective_run_at": crypto_job.get("last_effective_run_at"),
        "last_success_at": crypto_job.get("last_success_at"),
        "top_reject_gate": None,
        "telegram_wired": telegram_base,
        "mute_state": mute_state,
        "ready_to_start": bool(settings.paper_engine_enabled),
        "ready_to_start_reason": None if settings.paper_engine_enabled else "disabled",
    }

    # WO-FCE-ENGINE-LIVENESS-01: 생존 감시 상태를 같은 화면에 노출한다 —
    # 트랙 정지·백오프 고착·재시작 이력이 진단 응답 하나로 보여야 한다(작업 4·6).
    from app.worker import liveness as _liveness

    # 시장 단위 가상 트랙(stock_kr/us)은 잡 하트비트가 아니라 실제 평가 흔적으로 판정한다(D3).
    market_data: dict[str, str | None] = {}
    try:
        from app.stock_paper.models import Market

        if stock_store.enabled:
            market_data = {
                "stock_kr": stock_store.latest_analysis_at(Market.KR),
                "stock_us": stock_store.latest_analysis_at(Market.US),
            }
    except Exception:
        market_data = {}
    # 조기 반환 사유·차단 래치는 예외를 던지지 않아 잡 지표에 안 남는다 — 여기서 노출한다(D4).
    from app.toss import blocks as _toss_blocks

    toss_blocks_snapshot = _toss_blocks.blocks_snapshot()
    toss_outcomes = _toss_blocks.outcomes_snapshot()
    tracks_liveness = _liveness.track_liveness(worker, settings, market_data=market_data, market_reasons=_toss_blocks.stall_reasons())
    restarts = _liveness.recent_restarts(settings)
    return {
        "principle": "침묵 금지 — 모든 미발생은 사유와 함께 관측 가능해야 한다.",
        # WO-FCE-ALERT-WHITELIST-02: 거부는 **알림이 아니라 조회 대상**이다. 텔레그램에
        # 도달하는 kind 를 여기 명시해 "왜 거부 알림이 안 오는가"가 설계임을 드러낸다.
        "telegram_sendable_kinds": sorted(TELEGRAM_SENDABLE_KINDS),
        "rejection_policy": "거부·미발생·오류는 텔레그램 미발송(화이트리스트). 이 진단 표면과 일 1회 요약으로 조회한다.",
        "flag_warnings": worker.get("flag_warnings", []) if isinstance(worker, dict) else [],
        "tracks": {"crypto": crypto, "stock": stock, "poly": poly},
        "liveness": {
            "watchdog_topology": "internal(worker_liveness job) + external(scripts/local/deadman.sh)",
            "tracks": tracks_liveness,
            "stale_tracks": [row["job"] for row in tracks_liveness if row["stale"]],
            "backoff_stuck": _liveness.backoff_stuck_jobs(worker),
            "restarts_24h": {"count": len(restarts), "events": restarts[-10:]},
        },
        # WO-FCE-OBSERVATION-INTEGRITY-01: 검증일은 커버리지 게이트를 통과한 날만 센다.
        "observation_integrity": _observation_integrity(settings),
        # WO-FCE-SAMPLE-VIABILITY-01: 관측일은 필요조건이다. 채점 가능 표본이 나와야 검증이다.
        "sample_viability": _sample_viability(settings),
        # WO-FCE-VALIDATION-VERDICT-01: 자동매매 전환 조건은 결과를 보기 전에 확정됐다.
        "live_trading_gate": _live_trading_gate(settings),
        # WO-FCE-SAMPLE-RATE-01: 부족분은 기준을 낮춰서가 아니라 속도·기간·회전으로 메운다.
        "sample_rate_gap": _rate_gap(settings),
        # 잊힌 결정은 없는 결정과 같다 — 사람을 기다리는 항목을 상시 노출한다.
        "pending_decisions": _pending_decisions(settings),
        # WO-FCE-WHALE-ALERT-DEMOTE-01: 강등된 고래 관측은 조회로 갚는다.
        "whale_observations": _whale_observations(settings),
        # 자기잠금 래치 재발 감시용. blocked 가 있는데 retry_in_seconds 가 줄지 않으면 이상이다.
        "toss_collection": {
            "principle": "모든 차단에는 자동 재시도 경로가 있다 — 재시작 없이 스스로 풀려야 한다.",
            "blocks": toss_blocks_snapshot,
            "last_outcomes": toss_outcomes,
        },
    }
