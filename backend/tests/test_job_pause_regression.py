"""2026-09-15 — 백오프가 잡을 영구히 정지시켰다. 반복된 알림 침묵의 근본.

## 무엇이 있었나

알림이 또 멈췄다. 이번에도 워커는 "running" 이었고 `heartbeat` 는 runs=1529 로 정상이었다.
그런데 `sync_positions` 와 `deliver_alerts` 는 `last_started_at` 09-12T03:49 이후 **한 번도
시작하지 않았고**, `skipped=0 · misfired=0 · next_run_at=None` 이었다.

스케줄러가 도는데 그 두 잡만 스케줄에서 빠진 것이다.

## 원인 — `None` 은 "계산해줘"가 아니라 "정지"다

APScheduler 에서 `add_job(next_run_time=None)` 은 **일시정지된 잡**을 만든다.
`pause_job()` 의 구현이 정확히 `modify_job(next_run_time=None)` 이고, "지정 안 함"의
센티널은 `undefined` 다.

`_schedule_job` 의 기본값이 `None` 이었고 호출부 셋 중 **둘이 기본값을 쓴다**:

    _apply_backoff_if_needed    → 3연속 실패 시 간격을 늘리며 재등록
    _restore_interval_if_needed → 회복 시 간격을 되돌리며 재등록

절전·네트워크 단절·락 대기 무엇이든 3연속 실패를 만들면 **백오프가 그 잡을 죽였다.**
그리고 프로세스 재시작만이 유일한 복구 경로였다 — 회복하려는 코드가 잡을 죽이고 있었다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.util import undefined


def _noop() -> None:
    return None


@pytest.fixture
def scheduler():
    sched = BackgroundScheduler(timezone=timezone.utc)
    sched.start(paused=True)
    try:
        yield sched
    finally:
        # 테스트가 이미 내렸을 수 있다 — 정리가 실패로 잡히면 진짜 신호가 묻힌다.
        if sched.running:
            sched.shutdown(wait=False)


def test_none_next_run_time_pauses_the_job(scheduler) -> None:
    """**이 동작이 결함의 전제다.** 바뀌면 이 회귀의 근거가 사라지므로 알려야 한다."""
    scheduler.add_job(_noop, trigger=IntervalTrigger(seconds=60), id="j", next_run_time=None)
    assert scheduler.get_job("j").next_run_time is None


def test_undefined_next_run_time_lets_the_trigger_compute(scheduler) -> None:
    """수리가 의존하는 반대편 사실."""
    scheduler.add_job(_noop, trigger=IntervalTrigger(seconds=60), id="j", next_run_time=undefined)
    assert scheduler.get_job("j").next_run_time is not None


def test_schedule_job_without_a_time_does_not_pause() -> None:
    """`_schedule_job(name, interval)` 이 잡을 재우면 백오프가 곧 침묵이 된다."""
    import inspect

    from app.worker import manager

    src = inspect.getsource(manager.WorkerManager._schedule_job)
    assert "next_run_time if next_run_time is not None else undefined" in src, "`None` 을 그대로 넘기면 재등록이 정지가 된다"


def test_backoff_and_restore_paths_use_the_default(monkeypatch) -> None:
    """이 두 경로가 기본값을 쓰기 때문에 결함이 터졌다 — 관계를 고정한다."""
    import inspect

    from app.worker import manager

    for fn in (manager.WorkerManager._apply_backoff_if_needed, manager.WorkerManager._restore_interval_if_needed):
        src = inspect.getsource(fn)
        assert "_schedule_job(" in src
        assert "next_run_time" not in src, "시각을 직접 넘기기 시작하면 이 회귀의 전제가 바뀐다"


# ── 자가 복구 ──────────────────────────────────────────────────────────


class _Job:
    def __init__(self, interval: int, *, scheduled: bool = True, enabled: bool = True) -> None:
        self.interval_seconds = interval
        self.scheduled = scheduled
        self.enabled = enabled


class _Beat:
    def __init__(self, interval: int) -> None:
        self.base_interval_seconds = interval


class _Manager:
    """`_revive_paused_jobs` 만 떼어 검사한다 — 워커 전체를 띄우면 느리고 불안정하다."""

    from app.worker.manager import WorkerManager

    _revive_paused_jobs = WorkerManager._revive_paused_jobs

    def __init__(self, scheduler, jobs, heartbeats) -> None:
        self.scheduler = scheduler
        self.jobs = jobs
        self.heartbeats = heartbeats
        self.rescheduled: list[tuple[str, int]] = []

    def _schedule_job(self, name: str, interval: int, next_run_time=None) -> None:
        self.rescheduled.append((name, interval))
        self.scheduler.add_job(_noop, trigger=IntervalTrigger(seconds=interval), id=name, replace_existing=True, next_run_time=undefined)


def test_a_paused_job_is_revived(scheduler) -> None:
    """**사용자가 매번 프로세스를 재시작해야 했던 그 상태다.**"""
    scheduler.add_job(_noop, trigger=IntervalTrigger(seconds=90), id="sync_positions", next_run_time=None)
    manager = _Manager(scheduler, {"sync_positions": _Job(90)}, {"sync_positions": _Beat(90)})
    assert manager._revive_paused_jobs() == 1
    assert scheduler.get_job("sync_positions").next_run_time is not None


def test_a_missing_job_is_re_registered(scheduler) -> None:
    manager = _Manager(scheduler, {"deliver_alerts": _Job(90)}, {"deliver_alerts": _Beat(90)})
    assert manager._revive_paused_jobs() == 1
    assert scheduler.get_job("deliver_alerts") is not None


def test_a_healthy_job_is_left_alone(scheduler) -> None:
    """건강한 잡을 건드리면 매 주기 실행 시각이 밀린다."""
    scheduler.add_job(_noop, trigger=IntervalTrigger(seconds=90), id="heartbeat", next_run_time=undefined)
    manager = _Manager(scheduler, {"heartbeat": _Job(90)}, {"heartbeat": _Beat(90)})
    assert manager._revive_paused_jobs() == 0
    assert manager.rescheduled == []


def test_unscheduled_hooks_are_not_revived(scheduler) -> None:
    """`scheduled=False` 훅은 부모 안에서 돈다 — 되살리면 중복 실행이 된다."""
    manager = _Manager(scheduler, {"evaluate_lifecycle": _Job(90, scheduled=False)}, {"evaluate_lifecycle": _Beat(90)})
    assert manager._revive_paused_jobs() == 0


def test_disabled_jobs_are_not_revived(scheduler) -> None:
    """꺼둔 잡을 되살리면 사용자의 결정을 뒤집는다."""
    manager = _Manager(scheduler, {"replay_history_backfill": _Job(90, enabled=False)}, {"replay_history_backfill": _Beat(90)})
    assert manager._revive_paused_jobs() == 0


def test_nothing_is_revived_while_the_scheduler_is_down(scheduler) -> None:
    """스케줄러가 멈춰 있으면 문제는 잡이 아니다."""
    scheduler.shutdown(wait=False)
    manager = _Manager(scheduler, {"sync_positions": _Job(90)}, {"sync_positions": _Beat(90)})
    assert manager._revive_paused_jobs() == 0


def test_revive_survives_one_bad_job(scheduler) -> None:
    """한 잡의 복구 실패가 나머지 복구를 막으면 안 된다."""
    manager = _Manager(scheduler, {"bad": _Job(90), "good": _Job(90)}, {"bad": _Beat(90), "good": _Beat(90)})
    original = manager._schedule_job

    def flaky(name: str, interval: int, next_run_time=None) -> None:
        if name == "bad":
            raise RuntimeError("등록 실패")
        original(name, interval, next_run_time)

    manager._schedule_job = flaky
    assert manager._revive_paused_jobs() == 1
    assert scheduler.get_job("good") is not None


def test_liveness_revives_before_judging() -> None:
    """판정만 하고 두면 사용자가 매번 재시작해야 한다 — 복구가 먼저다."""
    import inspect

    from app.worker import manager

    src = inspect.getsource(manager.WorkerManager._evaluate_liveness)
    assert "_revive_paused_jobs" in src
    assert src.index("_revive_paused_jobs") < src.index("self.status()")


def test_revive_uses_the_base_interval_not_the_backed_off_one(scheduler) -> None:
    """백오프된 간격으로 되살리면 느려진 채 굳는다."""
    scheduler.add_job(_noop, trigger=IntervalTrigger(seconds=720), id="sync_positions", next_run_time=None)
    manager = _Manager(scheduler, {"sync_positions": _Job(720)}, {"sync_positions": _Beat(90)})
    manager._revive_paused_jobs()
    assert manager.rescheduled == [("sync_positions", 90)]


def test_the_observed_outage_shape_is_detected(scheduler) -> None:
    """실측 그대로: 스케줄러 정상 · heartbeat 정상 · 두 잡만 정지."""
    scheduler.add_job(_noop, trigger=IntervalTrigger(seconds=60), id="heartbeat", next_run_time=undefined)
    for name in ("sync_positions", "deliver_alerts"):
        scheduler.add_job(_noop, trigger=IntervalTrigger(seconds=90), id=name, next_run_time=None)
    jobs = {n: _Job(90) for n in ("sync_positions", "deliver_alerts")} | {"heartbeat": _Job(60)}
    beats = {n: _Beat(90) for n in ("sync_positions", "deliver_alerts")} | {"heartbeat": _Beat(60)}
    manager = _Manager(scheduler, jobs, beats)
    assert manager._revive_paused_jobs() == 2
    assert sorted(n for n, _ in manager.rescheduled) == ["deliver_alerts", "sync_positions"]


def test_revived_job_actually_has_a_future_run_time(scheduler) -> None:
    scheduler.add_job(_noop, trigger=IntervalTrigger(seconds=90), id="sync_positions", next_run_time=None)
    manager = _Manager(scheduler, {"sync_positions": _Job(90)}, {"sync_positions": _Beat(90)})
    manager._revive_paused_jobs()
    nxt = scheduler.get_job("sync_positions").next_run_time
    assert nxt is not None
    assert nxt <= datetime.now(timezone.utc) + timedelta(seconds=120)
