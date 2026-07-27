"""WO-FCE-ENGINE-LIVENESS-01 — 자기은폐 침묵의 근본 수리 회귀 테스트.

핵심 명제 3가지를 고정한다:
1. 데이터 수집이 죽어도 생존 신호는 계속 나간다 (D1 단일 실패점 제거)
2. 잡이 "성공"하며 아무것도 안 하면 사망으로 판정한다 (D2·D3, effective run 기준)
3. 뮤트는 생존/사망 신호를 끄지 못한다 (C2)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.api.deps import configure_runtime
from app.core.config import Settings
from app.db.repository import MemoryRepository
from app.exchange.mock import MockMarketDataProvider
from app.notify.state import NotificationState
from app.notify.alerts import AlertEngine
from app.worker import liveness
from app.worker.manager import WorkerManager

NOW = datetime(2026, 7, 27, 2, 0, tzinfo=timezone.utc)  # 월 11:00 KST — KR 장중


def _settings(tmp_path, **overrides) -> Settings:
    defaults = {
        "database_url": f"sqlite:///{tmp_path / 'liveness.db'}",
        "background_worker_enabled": True,
        "telegram_bot_enabled": False,
        "telegram_alerts_enabled": True,
        "worker_startup_delay_seconds": 0,
        "worker_sync_positions_interval_seconds": 1,
        "worker_scout_scan_enabled": False,
        "worker_liveness_path": str(tmp_path / "liveness.json"),
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _job(*, effective_age_s: float | None, interval: int = 90, status: str = "ok", base: int | None = None) -> dict:
    effective = None if effective_age_s is None else (NOW - timedelta(seconds=effective_age_s)).isoformat()
    return {
        "status": status,
        "current_interval_seconds": interval,
        "base_interval_seconds": base if base is not None else interval,
        "last_effective_run_at": effective,
        "last_success_at": NOW.isoformat(),
        "consecutive_failures": 0,
    }


# ── D2·D3: "성공하지만 아무것도 안 함"을 사망으로 잡는다 ────────────────


def test_track_stale_detected_even_when_job_reports_success(tmp_path) -> None:
    """2026-07-23 실제 사고: toss_stock_scout 35,014회 '성공' + 4일간 실제 평가 0.

    last_success_at 은 현재(정상)인데 last_effective_run_at 만 낡은 상태 —
    이걸 못 잡아서 4일간 아무도 몰랐다.
    """
    status = {
        "jobs": {
            "toss_stock_scout": _job(effective_age_s=4 * 86400, interval=10),  # 4일 정지
            "paper_engine": _job(effective_age_s=60),
            "polymarket_paper": _job(effective_age_s=60, interval=60),
            "sync_positions": _job(effective_age_s=60),
        }
    }
    rows = {row["job"]: row for row in liveness.track_liveness(status, _settings(tmp_path), NOW)}

    assert rows["toss_stock_scout"]["stale"] is True
    assert rows["paper_engine"]["stale"] is False
    assert rows["polymarket_paper"]["stale"] is False

    candidates = liveness.evaluate_liveness(status, _settings(tmp_path), NOW)
    stale = [c for c in candidates if c.payload.get("kind") == "track_stale"]
    assert len(stale) == 1
    assert stale[0].identity == "toss_stock_scout"
    assert stale[0].severity == "critical"


def test_three_paper_tracks_are_all_monitored() -> None:
    """D3: 감시 대상이 sync_positions 단독이었다 — 3트랙 전부 포함되어야 한다."""
    assert {"paper_engine", "toss_stock_scout", "polymarket_paper"}.issubset(set(liveness.TRACKED_JOBS))


def test_disabled_track_is_not_reported_as_dead(tmp_path) -> None:
    status = {"jobs": {"toss_stock_scout": {"status": "disabled"}}}
    rows = {row["job"]: row for row in liveness.track_liveness(status, _settings(tmp_path), NOW)}
    assert rows["toss_stock_scout"]["state"] == "disabled"
    assert rows["toss_stock_scout"]["stale"] is False


# ── D4: 백오프 고착 ────────────────────────────────────────────────


def test_backoff_stuck_job_is_reported(tmp_path) -> None:
    status = {"jobs": {"paper_engine": _job(effective_age_s=30, interval=720, base=90)}}
    stuck = liveness.backoff_stuck_jobs(status)
    assert stuck and stuck[0]["job"] == "paper_engine" and stuck[0]["multiple"] == 8.0

    candidates = liveness.evaluate_liveness(status, _settings(tmp_path), NOW)
    assert any(c.rule_id == "job_backoff_stuck" for c in candidates)


# ── 작업 6: 인프라 감시 · 재시작 가시화 · 시계 보정 ─────────────────


def test_infra_alerts_fire_on_db_and_disk_thresholds(tmp_path) -> None:
    settings = _settings(tmp_path, db_size_alert_gb=10.0, disk_free_alert_gb=20.0)
    assert liveness.infra_alerts(int(6.8e9), int(500e9), settings) == []  # 정상 구간
    fired = liveness.infra_alerts(int(12.8e9), int(5e9), settings)
    kinds = {c.payload["kind"] for c in fired}
    assert kinds == {"db_size", "disk_free"}
    assert any(c.severity == "critical" for c in fired)  # 디스크 고갈은 치명


def test_restart_alert_surfaces_silent_recovery() -> None:
    assert liveness.restart_alert([]) is None
    candidate = liveness.restart_alert([{"at": NOW.isoformat(), "target": "backend:8875"}])
    assert candidate is not None and candidate.rule_id == "process_restarted"
    assert "재시작" in candidate.message


def test_elapsed_excludes_lost_days() -> None:
    """C4: 유실일을 정상 경과로 계산하지 않는다."""
    start = datetime(2026, 7, 20, tzinfo=timezone.utc)
    effective = {"2026-07-20", "2026-07-21", "2026-07-22"}  # 23~27 유실
    result = liveness.elapsed_excluding_gaps(start, effective, now=datetime(2026, 7, 27, tzinfo=timezone.utc))
    assert result["calendar_days"] == 7
    assert result["effective_days"] == 3
    assert result["lost_days"] == 4
    assert "유실 4일 제외" in result["label"]


# ── 작업 3: 외부 데드맨용 하트비트 ──────────────────────────────────


def test_liveness_snapshot_is_written_atomically_for_external_watchdog(tmp_path) -> None:
    settings = _settings(tmp_path)
    status = {"scheduler_running": True, "jobs": {"paper_engine": _job(effective_age_s=30)}}
    snapshot = liveness.build_liveness_snapshot(status, settings, now=NOW, pid=4242)
    path = tmp_path / "sub" / "liveness.json"
    liveness.write_liveness_snapshot(path, snapshot)

    import json

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["written_at"] == NOW.isoformat()
    assert written["pid"] == 4242
    assert not list(path.parent.glob("*.tmp"))  # 임시 파일 잔존 금지(반쯤 쓰인 파일 오탐 방지)


# ── C2: 뮤트 관통 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_liveness_alerts_pierce_mute(tmp_path) -> None:
    """뮤트는 조건 알림만 끈다. 사망 신호까지 끄면 침묵이 스스로를 은폐한다."""
    settings = _settings(tmp_path)
    state = NotificationState()
    state.muted_until = datetime.now(timezone.utc) + timedelta(hours=6)
    assert state.is_muted() is True

    sent: list[str] = []

    class _Sender:
        enabled = True

        async def send_to_all(self, text: str, *, reply_markup=None) -> int:
            sent.append(text)
            return 1

    engine = AlertEngine(settings, _Sender(), state)
    status = {"jobs": {"toss_stock_scout": _job(effective_age_s=4 * 86400, interval=10)}}
    candidates = liveness.evaluate_liveness(status, settings, NOW)

    delivered = await engine.evaluate_liveness_alerts(candidates)

    assert delivered == 1, "뮤트 상태에서도 사망 알림은 도착해야 한다"
    assert "트랙 정지" in sent[0]


@pytest.mark.asyncio
async def test_daily_summary_sends_liveness_lines_even_when_muted(tmp_path) -> None:
    settings = _settings(tmp_path)
    state = NotificationState()
    state.muted_until = datetime.now(timezone.utc) + timedelta(hours=6)
    sent: list[str] = []

    class _Sender:
        enabled = True

        async def send_to_all(self, text: str, *, reply_markup=None) -> int:
            sent.append(text)
            return 1

    engine = AlertEngine(settings, _Sender(), state)
    # 요약 발송 조건(설정된 일일 요약 시각)을 우회하지 않고, due=True 가 되는 시각으로 고정.
    from zoneinfo import ZoneInfo

    hour, minute = (int(part) for part in settings.telegram_daily_summary_time.strip().split(":"))
    local = datetime(2026, 7, 27, hour, minute, tzinfo=ZoneInfo(settings.telegram_quiet_hours_timezone))
    setattr(engine, "_now", lambda: local.astimezone(timezone.utc))

    count = await engine.maybe_send_daily_summary({"positions": []}, ["<b>트랙 생존</b>", "• 크립토 페이퍼: 🟢 정상"])

    assert count == 1
    assert "트랙 생존" in sent[0]
    assert "뮤트 중" in sent[0]


# ── D1: 단일 실패점 제거 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_failure_does_not_kill_pulse_and_paper(tmp_path) -> None:
    """sync_and_analyze_positions 가 던져도 페이퍼·펄스·일일요약은 계속 실행되어야 한다.

    기존 구조에선 이 한 줄이 아래 8단계를 통째로 삼켰다.
    """
    repo = MemoryRepository()
    configure_runtime(repo=repo, provider=MockMarketDataProvider())
    manager = WorkerManager(_settings(tmp_path, telegram_alerts_enabled=False))

    def _boom() -> dict:
        raise RuntimeError("bitget down")

    manager.jobs["sync_and_analyze"].runner = _boom

    result = await manager._sync_positions()

    # 수집은 실패로 기록되고…
    assert manager.heartbeats["sync_and_analyze"].status == "error"
    # …나머지 8단계는 전부 실행됐어야 한다(하트비트가 증거). 특히 생존 신호 2종.
    for name in ("detect_closures", "paper_engine", "evaluate_lifecycle", "evaluate_alerts", "evaluate_performance_alerts", "periodic_pulse", "daily_summary"):
        assert manager.heartbeats[name].status == "ok", f"{name} 이 수집 실패에 함께 죽었다"
    assert manager.heartbeats["periodic_pulse"].runs >= 1, "생존 펄스가 데이터 수집 실패에 종속되면 안 된다"
    assert isinstance(result, dict)


# ── 오탐 방지: 장 마감 중 "정지" 알림 금지 ──────────────────────────


def test_market_session_gate_prevents_false_alarm_at_night(tmp_path) -> None:
    """장 마감 시간에 주식 트랙 정지 알림을 쏘면 매일 밤 오탐 → 사용자 뮤트 → 침묵 재발.

    WO 금지 항목(알림 스팸의 악순환)을 구조적으로 차단한다.
    """
    night = datetime(2026, 7, 27, 14, 30, tzinfo=timezone.utc)  # 23:30 KST
    weekend = datetime(2026, 7, 25, 2, 0, tzinfo=timezone.utc)  # 토 11:00 KST
    assert liveness.market_session_active("KR", NOW) is True
    assert liveness.market_session_active("KR", night) is False
    assert liveness.market_session_active("KR", weekend) is False
    assert liveness.market_session_active(None, night) is True  # 코인은 24/7

    status = {"jobs": {"toss_stock_scout": _job(effective_age_s=6 * 3600, interval=10)}}
    rows = {row["job"]: row for row in liveness.track_liveness(status, _settings(tmp_path), night)}
    assert rows["toss_stock_scout"]["state"] == "market_closed"
    assert rows["toss_stock_scout"]["stale"] is False
    assert liveness.evaluate_liveness(status, _settings(tmp_path), night) == []

    # 같은 정체 상태라도 장중이면 반드시 잡는다.
    assert any(c.rule_id == "engine_liveness" for c in liveness.evaluate_liveness(status, _settings(tmp_path), NOW))
