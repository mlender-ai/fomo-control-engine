from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services import http_handlers as routes
from app.services import runtime as service
from app.api.deps import configure_runtime
from app.core.config import Settings
from app.db.models import Direction, Position, WatchlistItem
from app.db.repository import MemoryRepository
from app.exchange.mock import MockMarketDataProvider
from app.worker.manager import WorkerManager, _next_daily_time


def _settings(tmp_path, **overrides) -> Settings:
    defaults = {
        "database_url": f"sqlite:///{tmp_path / 'worker.db'}",
        "background_worker_enabled": True,
        "telegram_bot_enabled": False,
        "telegram_alerts_enabled": False,
        "worker_startup_delay_seconds": 0,
        "worker_sync_positions_interval_seconds": 1,
        "worker_refresh_market_data_interval_seconds": 60,
        "worker_regen_stale_insights_interval_seconds": 60,
        "worker_detect_closures_interval_seconds": 1,
        "worker_scout_scan_enabled": False,
        "worker_backoff_failure_threshold": 3,
        "worker_backoff_max_multiplier": 4,
    }
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.mark.asyncio
async def test_worker_backoff_isolated_and_restores_on_success(tmp_path) -> None:
    manager = WorkerManager(_settings(tmp_path))
    manager.scheduler.start()
    manager._schedule_job("sync_positions", 1, datetime.now(timezone.utc) + timedelta(days=1))

    def fail() -> None:
        raise RuntimeError("forced failure")

    manager.jobs["sync_positions"].runner = fail
    await manager._run_scheduled_job("sync_positions")
    await manager._run_scheduled_job("sync_positions")
    await manager._run_scheduled_job("sync_positions")

    heartbeat = manager.heartbeats["sync_positions"]
    assert heartbeat.status == "error"
    assert heartbeat.consecutive_failures == 3
    assert heartbeat.total_failures == 3
    assert heartbeat.current_interval_seconds == 2

    manager.jobs["sync_positions"].runner = lambda: {"count": 1}
    await manager._run_scheduled_job("sync_positions")

    assert heartbeat.status == "ok"
    assert heartbeat.consecutive_failures == 0
    assert heartbeat.current_interval_seconds == 1
    assert manager.heartbeats["refresh_market_data"].status == "idle"

    manager.scheduler.shutdown(wait=False)


@pytest.mark.asyncio
async def test_worker_three_ticks_create_snapshots_and_heartbeat(tmp_path) -> None:
    repo = MemoryRepository()
    configure_runtime(repo=repo, provider=MockMarketDataProvider())
    report = routes._generate_and_store_report("BTCUSDT", "4h")
    position = repo.add_position(
        Position(
            symbol="BTCUSDT",
            direction=Direction.long,
            entry_price=report.price,
            quantity=0.1,
            leverage=5,
            entry_report_id=report.id,
            planned_stop_price=report.price * 0.95,
            planned_take_profit_price=report.price * 1.1,
        )
    )

    manager = WorkerManager(_settings(tmp_path))
    await manager.start()
    try:
        # 고정 sleep 은 커버리지 계측·CI 부하에서 틱 스킵(1초 간격 < 실행시간)으로 깨진다 —
        # 데드라인 폴링으로 "2회 실행 + 스냅샷 2개" 도달을 기다린다(도달 즉시 종료).
        deadline = asyncio.get_running_loop().time() + 20
        while asyncio.get_running_loop().time() < deadline:
            runs = manager.status()["jobs"]["sync_positions"]["runs"]
            if runs >= 2 and len(repo.list_position_snapshots(position.id, limit=10)) >= 2:
                break
            await asyncio.sleep(0.2)
    finally:
        await manager.stop()

    snapshots = repo.list_position_snapshots(position.id, limit=10)
    status = manager.status()

    assert len(snapshots) >= 2
    assert status["jobs"]["sync_positions"]["runs"] >= 2
    assert status["jobs"]["sync_positions"]["last_success_at"] is not None
    assert "sync_positions" in manager.heartbeat_store.list()


def test_worker_status_ignores_retired_persisted_jobs(tmp_path) -> None:
    manager = WorkerManager(_settings(tmp_path))
    manager.heartbeat_store.upsert(
        manager.heartbeats["sync_positions"].__class__(
            job_name="database_maintenance",
            status="ok",
            runs=99,
        )
    )

    status = manager.status()

    assert "database_retention" in status["jobs"]
    assert "database_backup" in status["jobs"]
    assert "database_maintenance" not in status["jobs"]
    assert manager.jobs["score_candidates"].interval_seconds == 86400


def test_refresh_market_data_covers_held_and_tracked_symbols(tmp_path) -> None:
    repo = MemoryRepository()
    configure_runtime(repo=repo, provider=MockMarketDataProvider())
    report = routes._generate_and_store_report("BTCUSDT", "4h")
    repo.add_position(
        Position(
            symbol="BTCUSDT",
            direction=Direction.long,
            entry_price=report.price,
            quantity=0.1,
            leverage=5,
        )
    )
    repo.upsert_watchlist_item(WatchlistItem(symbol="ETHUSDT"))

    payload = service.refresh_market_data()

    assert {"BTCUSDT", "ETHUSDT"}.issubset(set(payload["symbols"]))
    assert {("BTCUSDT", "4h"), ("ETHUSDT", "4h")}.issubset({(row["symbol"], row["timeframe"]) for row in payload["pairs"]})


def test_daily_maintenance_schedule_uses_configured_timezone() -> None:
    next_run = _next_daily_time(4, 30, "Asia/Seoul")

    assert next_run.tzinfo is timezone.utc
    assert next_run.astimezone(timezone(timedelta(hours=9))).hour == 4
    assert next_run.astimezone(timezone(timedelta(hours=9))).minute == 30


def test_candidate_scoring_job_is_registered_as_daily_low_priority(tmp_path) -> None:
    manager = WorkerManager(_settings(tmp_path))

    job = manager.jobs["score_candidates"]
    assert job.enabled is True
    assert job.interval_seconds == 86_400


def test_user_fill_sync_job_runs_independently_every_two_minutes(tmp_path) -> None:
    manager = WorkerManager(_settings(tmp_path))

    job = manager.jobs["sync_user_fills"]
    assert job.enabled is True
    assert job.interval_seconds == 120
