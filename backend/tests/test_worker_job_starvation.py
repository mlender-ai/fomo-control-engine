"""WO-FCE-WORKER-HANG-02 Phase 2 — 잡 기아 수리 회귀.

이 파일이 지키는 것:
1. **misfire 기본값 1초로 되돌아가지 않는다** — 그 값이 이번 사고의 증폭기였다.
2. **건너뛴 발화가 조용히 사라지지 않는다** (C8) — 잡별로 센다.
3. **간격·타임아웃은 변하지 않는다** (C1·C2) — 은폐 금지.
4. **잡 기아가 워커 생존과 분리되어 판정된다** (D1) — 감시 단위 == 실패 단위.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.worker.manager import (
    _MAX_OBSERVATION_GRACE_SECONDS,
    _TIME_SENSITIVE_GRACE_SECONDS,
    _TIME_SENSITIVE_JOBS,
    WorkerManager,
    _misfire_grace_seconds,
)


def _settings(tmp_path, **overrides) -> Settings:
    defaults = {
        "database_url": f"sqlite:///{tmp_path / 'worker.db'}",
        "background_worker_enabled": True,
        "telegram_bot_enabled": False,
        "telegram_alerts_enabled": False,
        "worker_startup_delay_seconds": 0,
        "worker_scout_scan_enabled": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# 2-2 misfire 정책
# ---------------------------------------------------------------------------


def test_observation_jobs_get_grace_equal_to_their_interval() -> None:
    """주기 관측 잡은 다음 주기 전까지 늦어도 실행돼야 한다."""
    assert _misfire_grace_seconds("universe_scan", 1800) == 1800
    assert _misfire_grace_seconds("scout_scan", 900) == 900
    assert _misfire_grace_seconds("paper_engine", 90) == 90


def test_grace_is_never_the_apscheduler_default_of_one_second() -> None:
    """1초는 루프 지연(실측 평균 11.66초) 앞에서 '항상 건너뜀'과 같다."""
    for name, interval in (("universe_scan", 1800), ("heartbeat", 1), ("evaluate_alerts", 1), ("collect_whale_positions", 30)):
        assert _misfire_grace_seconds(name, interval) >= _TIME_SENSITIVE_GRACE_SECONDS


def test_time_sensitive_jobs_keep_a_short_grace() -> None:
    """늦은 발송이 무의미한 잡은 짧게 둔다 — 지나간 상황을 지금 알리지 않는다."""
    for name in _TIME_SENSITIVE_JOBS:
        assert _misfire_grace_seconds(name, 3600) == _TIME_SENSITIVE_GRACE_SECONDS


def test_daily_jobs_are_capped_so_starvation_stays_visible() -> None:
    """하루 주기 잡에 하루치 grace 를 주면 '언제 돌아도 됨'이 되어 기아를 못 본다."""
    assert _misfire_grace_seconds("score_candidates", 86400) == _MAX_OBSERVATION_GRACE_SECONDS


@pytest.mark.asyncio
async def test_scheduled_job_registers_explicit_grace(tmp_path) -> None:
    """스케줄러에 실제로 전달되는지 — 상수만 맞고 배선이 빠지면 무의미하다."""
    manager = WorkerManager(_settings(tmp_path))
    manager.scheduler.start(paused=True)
    try:
        manager._schedule_job("universe_scan", 1800, datetime.now(timezone.utc) + timedelta(days=1))
        job = manager.scheduler.get_job("universe_scan")
        assert job is not None
        assert job.misfire_grace_time == 1800
        assert job.coalesce is True
        assert job.max_instances == 1
        assert manager.heartbeats["universe_scan"].misfire_grace_seconds == 1800
    finally:
        manager.scheduler.shutdown(wait=False)


def test_scheduler_defaults_do_not_fall_back_to_one_second(tmp_path) -> None:
    """등록 누락 시의 안전망. `job_defaults` 가 없으면 1초로 되돌아간다."""
    manager = WorkerManager(_settings(tmp_path))
    defaults = manager.scheduler._job_defaults
    assert defaults["misfire_grace_time"] == _TIME_SENSITIVE_GRACE_SECONDS
    assert defaults["coalesce"] is True
    assert defaults["max_instances"] == 1


# ---------------------------------------------------------------------------
# C8 침묵 금지 — 건너뛴 발화를 센다
# ---------------------------------------------------------------------------


def test_missed_event_is_counted_per_job(tmp_path) -> None:
    """APScheduler 로그에는 잡 이름이 없다. 리스너가 잡별로 적어야 조회 가능해진다."""
    manager = WorkerManager(_settings(tmp_path))
    heartbeat = manager.heartbeats["universe_scan"]
    assert heartbeat.misfired == 0

    scheduled_for = datetime.now(timezone.utc)
    manager._on_job_missed(SimpleNamespace(job_id="universe_scan", scheduled_run_time=scheduled_for))
    manager._on_job_missed(SimpleNamespace(job_id="universe_scan", scheduled_run_time=scheduled_for))

    assert heartbeat.misfired == 2
    assert heartbeat.last_misfire_at is not None
    assert heartbeat.as_dict()["misfired"] == 2


def test_misfire_is_counted_separately_from_skipped(tmp_path) -> None:
    """원인이 다르다 — `skipped` 는 잡이 느린 것, `misfired` 는 스케줄러가 안 돈 것."""
    manager = WorkerManager(_settings(tmp_path))
    heartbeat = manager.heartbeats["universe_scan"]
    heartbeat.skipped = 5
    manager._on_job_missed(SimpleNamespace(job_id="universe_scan", scheduled_run_time=datetime.now(timezone.utc)))
    assert heartbeat.skipped == 5
    assert heartbeat.misfired == 1


def test_unknown_job_id_is_ignored(tmp_path) -> None:
    """은퇴한 잡 id 가 들어와도 죽지 않는다 — 관측기가 장애 원인이 되면 안 된다."""
    manager = WorkerManager(_settings(tmp_path))
    manager._on_job_missed(SimpleNamespace(job_id="retired_job", scheduled_run_time=None))


# ---------------------------------------------------------------------------
# C1·C2 은폐 금지
# ---------------------------------------------------------------------------


def test_intervals_are_untouched_by_the_misfire_fix(tmp_path) -> None:
    """간격을 늘려 실행률을 올리는 것은 관측 축소다 (C1)."""
    settings = _settings(tmp_path)
    manager = WorkerManager(settings)
    assert manager.jobs["universe_scan"].interval_seconds == settings.worker_universe_scan_interval_seconds
    assert manager.jobs["scout_scan"].interval_seconds == settings.worker_scout_scan_interval_seconds
    assert manager.jobs["collect_whale_positions"].interval_seconds == settings.hyperliquid_whale_poll_interval_seconds


def test_grace_does_not_extend_job_timeout(tmp_path) -> None:
    """grace 는 '얼마나 늦게 시작해도 되는지'이고 타임아웃은 '얼마나 오래 돌아도 되는지'다 (C2)."""
    manager = WorkerManager(_settings(tmp_path))
    before = manager._job_timeout_seconds("universe_scan")
    manager._schedule_job("universe_scan", 1800, datetime.now(timezone.utc) + timedelta(days=1))
    assert manager._job_timeout_seconds("universe_scan") == before


def test_no_job_is_disabled_by_this_fix(tmp_path) -> None:
    """부하를 줄이려 잡을 끄는 것은 관측 축소다 (C3)."""
    manager = WorkerManager(_settings(tmp_path, worker_scout_scan_enabled=True))
    assert manager.jobs["universe_scan"].enabled is True
    assert manager.jobs["scout_scan"].enabled is True


# ---------------------------------------------------------------------------
# 2-4 잡 단위 기아 판정 (D1)
# ---------------------------------------------------------------------------


def test_starvation_flags_runs_zero_with_past_next_run(tmp_path) -> None:
    """이번 사고의 형태 — `runs=0` 인데 `next_run_at` 이 과거다."""
    from app.worker.liveness import job_starvation

    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    rows = {
        "universe_scan": {
            "status": "idle",
            "runs": 0,
            "base_interval_seconds": 1800,
            "next_run_at": now - timedelta(minutes=38),
            "last_effective_run_at": None,
            "misfired": 12,
        }
    }
    verdict = job_starvation(rows, now=now)
    assert verdict["starved"] == ["universe_scan"]
    detail = verdict["detail"]["universe_scan"]
    assert detail["reason"] == "never_ran_and_overdue"
    assert detail["overdue_seconds"] > 0


def test_starvation_flags_stale_relative_to_its_own_interval(tmp_path) -> None:
    """돌긴 했지만 예정 간격의 배수를 넘겨 안 돌았으면 기아다."""
    from app.worker.liveness import job_starvation

    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    rows = {
        "universe_scan": {
            "status": "ok",
            "runs": 19,
            "base_interval_seconds": 1800,
            "next_run_at": now + timedelta(minutes=5),
            "last_effective_run_at": now - timedelta(hours=4),
            "misfired": 30,
        }
    }
    verdict = job_starvation(rows, now=now)
    assert verdict["starved"] == ["universe_scan"]
    assert verdict["detail"]["universe_scan"]["reason"] == "interval_overrun"


def test_healthy_job_is_not_starved(tmp_path) -> None:
    from app.worker.liveness import job_starvation

    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    rows = {
        "paper_engine": {
            "status": "ok",
            "runs": 350,
            "base_interval_seconds": 90,
            "next_run_at": now + timedelta(seconds=45),
            "last_effective_run_at": now - timedelta(seconds=45),
            "misfired": 0,
        }
    }
    assert job_starvation(rows, now=now)["starved"] == []


def test_disabled_job_is_not_starved(tmp_path) -> None:
    """끈 잡은 굶은 것이 아니다 — 오탐이 쌓이면 신호가 무시된다."""
    from app.worker.liveness import job_starvation

    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    rows = {
        "stance_backtest": {
            "status": "disabled",
            "runs": 0,
            "base_interval_seconds": 86400,
            "next_run_at": now - timedelta(hours=2),
            "last_effective_run_at": None,
            "misfired": 0,
        }
    }
    assert job_starvation(rows, now=now)["starved"] == []


def test_starvation_is_reported_separately_from_worker_liveness(tmp_path) -> None:
    """D1 — 워커가 살아 있어도 잡이 굶으면 보여야 한다. 감시 단위 == 실패 단위."""
    from app.worker.liveness import job_starvation

    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
    rows = {
        "heartbeat": {
            "status": "ok",
            "runs": 460,
            "base_interval_seconds": 60,
            "next_run_at": now + timedelta(seconds=30),
            "last_effective_run_at": now - timedelta(seconds=30),
            "misfired": 0,
        },
        "universe_scan": {
            "status": "idle",
            "runs": 0,
            "base_interval_seconds": 1800,
            "next_run_at": now - timedelta(minutes=38),
            "last_effective_run_at": None,
            "misfired": 12,
        },
    }
    verdict = job_starvation(rows, now=now)
    # 하트비트는 건강한데 유니버스는 굶었다 — 이것이 구분돼야 한다.
    assert verdict["starved"] == ["universe_scan"]
    assert verdict["healthy_count"] == 1
    assert verdict["starved_count"] == 1


@pytest.mark.asyncio
async def test_worker_status_exposes_job_starvation(tmp_path) -> None:
    """조회 가능해야 한다 — 코드 안에만 있으면 아무도 안 본다."""
    manager = WorkerManager(_settings(tmp_path))
    status = manager.status()
    assert "job_starvation" in status
    assert set(status["job_starvation"]) >= {"starved", "starved_count", "detail"}


# ---------------------------------------------------------------------------
# 영속화 — 조회 경로가 값을 잃지 않는다
# ---------------------------------------------------------------------------


def test_misfire_counters_survive_the_sqlite_round_trip(tmp_path) -> None:
    """`status()` 는 영속 행을 우선한다 — 스키마에 칼럼이 없으면 값이 조용히 0이 된다.

    이 테스트가 없으면 misfire 카운터가 화면에서 사라진 것을 못 잡는다(실제로 한 번 그랬다).
    """
    from app.worker.heartbeat import HeartbeatRecord, SQLiteHeartbeatStore

    store = SQLiteHeartbeatStore(f"sqlite:///{tmp_path / 'hb.db'}")
    assert store.enabled
    record = HeartbeatRecord(
        job_name="universe_scan",
        base_interval_seconds=1800,
        current_interval_seconds=1800,
        misfired=7,
        last_misfire_at=datetime(2026, 8, 19, tzinfo=timezone.utc),
        misfire_grace_seconds=1800,
    )
    store.upsert(record)

    restored = store.list()["universe_scan"]
    assert restored["misfired"] == 7
    assert restored["misfire_grace_seconds"] == 1800
    assert restored["last_misfire_at"] == datetime(2026, 8, 19, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_status_reports_grace_after_scheduling(tmp_path) -> None:
    """스케줄 등록 → 영속 → 조회까지 값이 살아 있는지 (종단)."""
    manager = WorkerManager(_settings(tmp_path))
    manager.scheduler.start(paused=True)
    try:
        manager._schedule_job("universe_scan", 1800, datetime.now(timezone.utc) + timedelta(days=1))
        status = manager.status()
        assert status["jobs"]["universe_scan"]["misfire_grace_seconds"] == 1800
    finally:
        manager.scheduler.shutdown(wait=False)
