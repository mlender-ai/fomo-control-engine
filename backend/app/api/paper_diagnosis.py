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
        # 자기잠금 래치 재발 감시용. blocked 가 있는데 retry_in_seconds 가 줄지 않으면 이상이다.
        "toss_collection": {
            "principle": "모든 차단에는 자동 재시도 경로가 있다 — 재시작 없이 스스로 풀려야 한다.",
            "blocks": toss_blocks_snapshot,
            "last_outcomes": toss_outcomes,
        },
    }
