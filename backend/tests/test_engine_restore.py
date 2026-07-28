"""WO-FCE-ENGINE-RESTORE-01 — 무조건 돌게 만든다: 행 수리 회귀.

2026-07-28 사고: 하트비트 스냅샷 직렬화 실패(datetime) 하나로 심장박동이 11.7시간 멈췄다.
잡은 전부 "ok"였고(6ms 완주), 실패는 WARNING 으로 삼켜졌으며, supervisor 는 포트만 보고
개입하지 않았다. 외부 감시자는 그것을 프로세스 사망으로 오판했다.

고정하는 명제:
1. 심장박동은 어떤 값이 들어와도 멈추지 않는다 (직렬화 불가 타입 포함)
2. 심장박동은 알림·평가 경로와 분리되어 있다 — 그쪽이 매달려도 계속 뛴다 (C2)
3. 모든 잡에 실행 예산이 있고, 초과하면 취소 후 다음 틱이 정상 실행된다 (C3)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.worker import liveness
from app.worker.manager import WorkerManager


def _settings(tmp_path, **overrides) -> Settings:
    defaults = {
        "database_url": f"sqlite:///{tmp_path / 'restore.db'}",
        "background_worker_enabled": True,
        "telegram_bot_enabled": False,
        "telegram_alerts_enabled": False,
        "worker_startup_delay_seconds": 0,
        "worker_scout_scan_enabled": False,
        "worker_liveness_path": str(tmp_path / "liveness.json"),
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ── 1. 심장박동은 멈추지 않는다 ─────────────────────────────────────


def test_heartbeat_survives_non_serializable_values(tmp_path) -> None:
    """실제 사고 재현: status() 유래 datetime(muted_until)이 섞여도 기록되어야 한다.

    이 한 줄이 없어서 11.7시간 동안 심장박동이 멎었고 외부 감시자가 사망으로 오판했다.
    """
    path = tmp_path / "liveness.json"
    payload = {
        "written_at": datetime.now(timezone.utc).isoformat(),
        "muted_until": datetime.now(timezone.utc) + timedelta(hours=6),  # ← 직렬화 불가 타입
        "nested": {"when": datetime.now(timezone.utc)},
    }

    liveness.write_liveness_snapshot(path, payload)  # 예외 없이 성공해야 한다

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["written_at"] == payload["written_at"]
    assert isinstance(written["muted_until"], str)  # 문자열로 낮춰서라도 기록


def test_build_snapshot_with_real_status_is_writable(tmp_path) -> None:
    """status() 전체를 통과시켜도 기록 가능해야 한다(사고 경로 그대로)."""
    manager = WorkerManager(_settings(tmp_path))
    manager.state.muted_until = datetime.now(timezone.utc) + timedelta(hours=1)
    snapshot = liveness.build_liveness_snapshot(manager.status(), manager.settings, pid=1)

    liveness.write_liveness_snapshot(tmp_path / "detail.json", snapshot)

    assert (tmp_path / "detail.json").exists()


def test_heartbeat_job_writes_and_reports(tmp_path) -> None:
    manager = WorkerManager(_settings(tmp_path))

    result = manager._write_heartbeat()

    assert result["written"] is True
    written = json.loads((tmp_path / "liveness.json").read_text(encoding="utf-8"))
    assert written["source"] == "heartbeat_job"
    assert written["pid"] > 0


# ── 2. 심장박동은 알림 경로와 분리되어 있다 (C2) ────────────────────


@pytest.mark.asyncio
async def test_heartbeat_keeps_beating_while_telegram_hangs(tmp_path) -> None:
    """수용 기준: 텔레그램 전송 무한 대기를 주입해도 하트비트는 계속 갱신된다."""
    manager = WorkerManager(_settings(tmp_path, telegram_alerts_enabled=True))

    async def _never_returns(*_args, **_kwargs) -> int:
        await asyncio.Event().wait()  # 영원히 매달린다
        return 0

    setattr(manager.alerts, "evaluate_liveness_alerts", _never_returns)

    hung = asyncio.create_task(manager._evaluate_liveness())
    await asyncio.sleep(0.05)

    # 감시 평가가 매달려 있는 동안에도 심장박동은 독립적으로 뛴다.
    for _ in range(3):
        assert manager._write_heartbeat()["written"] is True
        await asyncio.sleep(0.01)

    assert not hung.done(), "테스트 전제: 평가 잡은 여전히 매달려 있어야 한다"
    hung.cancel()
    written = json.loads((tmp_path / "liveness.json").read_text(encoding="utf-8"))
    assert written["source"] == "heartbeat_job"


def test_heartbeat_job_is_separate_from_liveness_job(tmp_path) -> None:
    """심장박동이 감시 평가 잡 안에 들어 있으면 같은 사고가 재발한다."""
    manager = WorkerManager(_settings(tmp_path))
    assert "heartbeat" in manager.jobs
    assert manager.jobs["heartbeat"].runner is not manager.jobs["worker_liveness"].runner
    assert manager.jobs["heartbeat"].interval_seconds <= 60


# ── 3. 무한 대기 금지 (C3) ──────────────────────────────────────────


def test_every_job_has_a_timeout_budget(tmp_path) -> None:
    manager = WorkerManager(_settings(tmp_path))
    for name in manager.jobs:
        budget = manager._job_timeout_seconds(name)
        assert budget > 0, f"{name} 에 실행 예산이 없다 — 무한 대기 가능"
        assert budget <= manager.settings.worker_job_timeout_ceiling_seconds


@pytest.mark.asyncio
async def test_hung_job_times_out_releases_lock_and_next_tick_runs(tmp_path) -> None:
    """수용 기준: 무한 대기 잡 주입 → 타임아웃 → 락 해제 → 다음 틱 정상 실행."""
    manager = WorkerManager(_settings(tmp_path, worker_job_timeout_floor_seconds=10))
    setattr(manager.settings, "worker_job_timeout_floor_seconds", 10)

    async def _hang() -> None:
        await asyncio.Event().wait()

    manager.jobs["refresh_market_data"].runner = _hang
    # 예산을 짧게 강제(테스트 속도) — 실제 운영값은 주기 × 배수.
    setattr(manager, "_job_timeout_seconds", lambda name: 1)

    await manager._run_scheduled_job("refresh_market_data")

    heartbeat = manager.heartbeats["refresh_market_data"]
    assert heartbeat.status == "error"
    assert "timeout" in (heartbeat.last_error or "")
    assert not manager._locks["refresh_market_data"].locked(), "타임아웃 후 락이 남으면 잡이 영구 정지한다"

    # 다음 틱은 정상 실행되어야 한다.
    manager.jobs["refresh_market_data"].runner = lambda: {"ok": True}
    await manager._run_scheduled_job("refresh_market_data")
    assert manager.heartbeats["refresh_market_data"].status == "ok"
