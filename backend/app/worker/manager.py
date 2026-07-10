from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import Settings
from app.core.logging import configure_logging
from app.notify.alerts import AlertEngine
from app.notify.bot.bot import TelegramBotSupervisor
from app.notify.state import NotificationState
from app.notify.telegram import TelegramSender
from app.services import runtime as service
from app.worker.heartbeat import HeartbeatRecord, SQLiteHeartbeatStore

logger = logging.getLogger("worker.manager")


JobRunner = Callable[[], Any | Awaitable[Any]]


@dataclass
class WorkerJob:
    name: str
    interval_seconds: int
    runner: JobRunner | None
    scheduled: bool = True
    enabled: bool = True


class WorkerManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = NotificationState()
        # WO-44 Part C: 억제분·라이프사이클 트래커·아침 요약 상태의 재시작 유실 방지.
        state_path = str(getattr(settings, "notification_state_path", "") or "")
        if state_path:
            self.state.load(state_path)
        self.sender = TelegramSender(settings)
        self.alerts = AlertEngine(settings, self.sender, self.state)
        self.bot = TelegramBotSupervisor(settings, self.state)
        self.scheduler = AsyncIOScheduler(timezone=timezone.utc)
        self.heartbeat_store = SQLiteHeartbeatStore(settings.database_url)
        self.jobs = self._build_jobs()
        self.heartbeats = {
            name: HeartbeatRecord(
                job_name=name,
                base_interval_seconds=job.interval_seconds,
                current_interval_seconds=job.interval_seconds,
            )
            for name, job in self.jobs.items()
        }
        self._locks = {name: asyncio.Lock() for name in self.jobs}
        self._telegram_task: asyncio.Task | None = None
        self._started = False

    async def start(self) -> None:
        _configure_worker_logging(self.settings)
        if not self.settings.background_worker_enabled:
            for heartbeat in self.heartbeats.values():
                heartbeat.status = "disabled"
                self._persist(heartbeat)
            return

        self._started = True
        self.scheduler.start()
        startup_delay = max(0, self.settings.worker_startup_delay_seconds)
        next_run = datetime.now(timezone.utc) + timedelta(seconds=startup_delay)
        for job in self.jobs.values():
            if not job.scheduled:
                continue
            heartbeat = self.heartbeats[job.name]
            if not job.enabled:
                heartbeat.status = "disabled"
                self._persist(heartbeat)
                continue
            self._schedule_job(job.name, job.interval_seconds, self._first_run_at(job.name, next_run))

        self._telegram_task = asyncio.create_task(self._telegram_bot_loop(), name="fce-telegram-bot")
        logger.info("worker scheduler started jobs=%s", sorted(self.jobs))

    async def stop(self) -> None:
        self.bot.stop()
        if self._telegram_task is not None:
            self._telegram_task.cancel()
            await asyncio.gather(self._telegram_task, return_exceptions=True)
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        self._started = False

    def status(self) -> dict[str, Any]:
        persisted = self.heartbeat_store.list()
        jobs = {name: heartbeat.as_dict() for name, heartbeat in self.heartbeats.items()}
        for name in jobs:
            if name in persisted:
                jobs[name] = persisted[name]
        return {
            "status": "running" if self._started and self.scheduler.running else "disabled",
            "scheduler_running": self.scheduler.running,
            "heartbeat_persistence": "sqlite" if self.heartbeat_store.enabled else "memory",
            "jobs": jobs,
            "notifications": {
                "telegram_sender_enabled": self.sender.enabled,
                "telegram_bot_enabled": self.bot.enabled,
                "muted_until": self.state.muted_until,
                "is_muted": self.state.is_muted(),
            },
        }

    async def _run_scheduled_job(self, name: str) -> Any:
        job = self.jobs[name]
        if job.runner is None:
            return None
        return await self._run_job(name, job.runner, scheduled=True)

    async def _run_hook(self, name: str, runner: JobRunner) -> Any:
        return await self._run_job(name, runner, scheduled=False)

    async def _run_job(self, name: str, runner: JobRunner, *, scheduled: bool) -> Any:
        heartbeat = self.heartbeats[name]
        lock = self._locks[name]
        if lock.locked():
            heartbeat.skipped += 1
            heartbeat.status = "skipped"
            heartbeat.next_run_at = self._next_run_at(name)
            self._persist(heartbeat)
            logger.warning("worker.%s skipped previous tick still running", name)
            return None

        async with lock:
            heartbeat.status = "running"
            heartbeat.last_started_at = datetime.now(timezone.utc)
            heartbeat.next_run_at = self._next_run_at(name)
            self._persist(heartbeat)
            try:
                result = runner()
                if inspect.isawaitable(result):
                    result = await result
                heartbeat.runs += 1
                heartbeat.consecutive_failures = 0
                heartbeat.status = "ok"
                heartbeat.last_success_at = datetime.now(timezone.utc)
                heartbeat.last_error = None
                if scheduled:
                    self._restore_interval_if_needed(name)
                logger.info("worker.%s ok result=%s", name, _compact_result(result))
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                heartbeat.consecutive_failures += 1
                heartbeat.total_failures += 1
                heartbeat.status = "error"
                heartbeat.last_error_at = datetime.now(timezone.utc)
                heartbeat.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("worker.%s failed", name)
                if scheduled:
                    self._apply_backoff_if_needed(name)
                if name == "sync_positions":
                    try:
                        await self.alerts.evaluate_worker_status(self.status())
                    except Exception:
                        logger.exception("worker.%s data_stall alert evaluation failed", name)
                return None
            finally:
                heartbeat.next_run_at = self._next_run_at(name)
                self._persist(heartbeat)

    async def _sync_positions(self) -> dict[str, Any]:
        payload = await asyncio.to_thread(service.sync_and_analyze_positions)
        await self._run_hook("detect_closures", lambda: asyncio.to_thread(service.detect_closures))
        # WO-44: 진입/종료/판정 전이 — 라이프사이클이 1차 정보이므로 조건 알림보다 먼저.
        await self._run_hook("evaluate_lifecycle", lambda: self.alerts.evaluate_lifecycle(payload))
        await self._run_hook(
            "evaluate_alerts",
            lambda: self.alerts.evaluate_positions(payload.get("positions", [])),
        )
        await self._run_hook(
            "evaluate_performance_alerts",
            lambda: self._evaluate_performance_alerts(),
        )
        await self._run_hook("periodic_pulse", lambda: self.alerts.maybe_send_pulse(payload))
        await self._run_hook("daily_summary", lambda: self.alerts.maybe_send_daily_summary(payload))
        return {
            "open_count": payload.get("open_count"),
            "needs_exit_record_count": payload.get("needs_exit_record_count"),
            "positions": len(payload.get("positions", [])),
            "created": payload.get("created"),
            "auto_closed": payload.get("auto_closed"),
        }

    async def _evaluate_performance_alerts(self) -> int:
        performance = await asyncio.to_thread(service.performance_summary)
        return await self.alerts.evaluate_performance(performance)

    async def _daily_summary(self) -> dict[str, Any]:
        payload = await asyncio.to_thread(service.list_live_positions, store_snapshot=False)
        sent = await self.alerts.maybe_send_daily_summary(payload)
        return {"count": sent, "positions": len(payload.get("positions", []))}

    async def _weekly_calibration_report(self) -> dict[str, Any]:
        sent = await self.alerts.maybe_send_weekly_calibration_report()
        return {"count": sent}

    async def _collect_derivatives(self) -> dict[str, Any]:
        payload = await asyncio.to_thread(service.refresh_derivative_data)
        snapshots = payload.get("snapshots", [])
        if isinstance(snapshots, list):
            await self.alerts.evaluate_derivatives(snapshots)
        return payload

    async def _scout_scan(self) -> dict[str, Any]:
        payload = await asyncio.to_thread(service.refresh_scout_scan_cache)
        candidates = payload.get("_alert_candidate_objects", [])
        if candidates:
            await self.alerts.evaluate_scout_setups(candidates)
        payload.pop("_alert_candidate_objects", None)
        return payload

    async def _universe_scan(self) -> dict[str, Any]:
        payload = await asyncio.to_thread(service.refresh_universe_scan_cache)
        candidates = payload.get("_alert_candidate_objects", [])
        if candidates:
            await self.alerts.evaluate_scout_setups(candidates)
        payload.pop("_alert_candidate_objects", None)
        return payload

    async def _telegram_bot_loop(self) -> None:
        heartbeat = self.heartbeats["telegram_bot"]

        def mark(status: str, error: str | None) -> None:
            heartbeat.status = status
            if status == "running":
                heartbeat.last_started_at = datetime.now(timezone.utc)
            if status == "error":
                heartbeat.consecutive_failures += 1
                heartbeat.total_failures += 1
                heartbeat.last_error_at = datetime.now(timezone.utc)
                heartbeat.last_error = error
            if status == "disabled":
                heartbeat.last_error = error
            self._persist(heartbeat)

        try:
            await self.bot.run_forever(mark)
            if heartbeat.status == "running":
                heartbeat.status = "stopped"
                heartbeat.last_success_at = datetime.now(timezone.utc)
                self._persist(heartbeat)
        except asyncio.CancelledError:
            heartbeat.status = "stopped"
            self._persist(heartbeat)
            raise

    def _build_jobs(self) -> dict[str, WorkerJob]:
        return {
            "sync_positions": WorkerJob(
                "sync_positions",
                self.settings.worker_sync_positions_interval_seconds,
                self._sync_positions,
            ),
            "refresh_market_data": WorkerJob(
                "refresh_market_data",
                self.settings.worker_refresh_market_data_interval_seconds,
                lambda: asyncio.to_thread(service.refresh_market_data),
            ),
            "collect_derivatives": WorkerJob(
                "collect_derivatives",
                self.settings.derivative_tracking_interval_seconds,
                self._collect_derivatives,
                enabled=self.settings.derivative_tracking_enabled,
            ),
            "regen_stale_insights": WorkerJob(
                "regen_stale_insights",
                self.settings.worker_regen_stale_insights_interval_seconds,
                lambda: asyncio.to_thread(service.regenerate_stale_insights),
            ),
            "database_retention": WorkerJob(
                "database_retention",
                self.settings.db_backup_interval_seconds,
                lambda: asyncio.to_thread(service.database_retention),
            ),
            "database_backup": WorkerJob(
                "database_backup",
                self.settings.db_backup_interval_seconds,
                lambda: asyncio.to_thread(service.database_backup),
            ),
            "detect_closures": WorkerJob(
                "detect_closures",
                self.settings.worker_detect_closures_interval_seconds,
                lambda: service.detect_closures(),
                scheduled=False,
            ),
            "evaluate_lifecycle": WorkerJob(
                "evaluate_lifecycle",
                self.settings.worker_sync_positions_interval_seconds,
                None,
                scheduled=False,
            ),
            "evaluate_alerts": WorkerJob(
                "evaluate_alerts",
                self.settings.worker_sync_positions_interval_seconds,
                None,
                scheduled=False,
            ),
            "periodic_pulse": WorkerJob(
                "periodic_pulse",
                self.settings.worker_sync_positions_interval_seconds,
                None,
                scheduled=False,
            ),
            "evaluate_performance_alerts": WorkerJob(
                "evaluate_performance_alerts",
                self.settings.worker_sync_positions_interval_seconds,
                None,
                scheduled=False,
            ),
            "daily_summary": WorkerJob(
                "daily_summary",
                60,
                self._daily_summary,
            ),
            "weekly_calibration_report": WorkerJob(
                "weekly_calibration_report",
                60,
                self._weekly_calibration_report,
            ),
            "interim_scoring": WorkerJob(
                "interim_scoring",
                self.settings.worker_interim_scoring_interval_seconds,
                lambda: asyncio.to_thread(service.interim_score_open_positions),
            ),
            "alert_response_scoring": WorkerJob(
                "alert_response_scoring",
                self.settings.worker_alert_response_interval_seconds,
                lambda: asyncio.to_thread(service.score_alert_responses),
            ),
            "scout_scan": WorkerJob(
                "scout_scan",
                self.settings.worker_scout_scan_interval_seconds,
                self._scout_scan,
                enabled=self.settings.worker_scout_scan_enabled,
            ),
            "universe_scan": WorkerJob(
                "universe_scan",
                self.settings.worker_universe_scan_interval_seconds,
                self._universe_scan,
                enabled=self.settings.universe_scanner_enabled,
            ),
            "telegram_bot": WorkerJob("telegram_bot", 0, None, scheduled=False),
        }

    def _schedule_job(self, name: str, interval_seconds: int, next_run_time: datetime | None = None) -> None:
        interval = max(1, int(interval_seconds))
        self.scheduler.add_job(
            self._run_scheduled_job,
            trigger=IntervalTrigger(seconds=interval, timezone=timezone.utc),
            args=[name],
            id=name,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
            next_run_time=next_run_time,
        )
        heartbeat = self.heartbeats[name]
        heartbeat.current_interval_seconds = interval
        heartbeat.next_run_at = self._next_run_at(name)
        self._persist(heartbeat)

    def _apply_backoff_if_needed(self, name: str) -> None:
        heartbeat = self.heartbeats[name]
        threshold = max(1, self.settings.worker_backoff_failure_threshold)
        if heartbeat.consecutive_failures < threshold:
            return
        base_interval = max(1, heartbeat.base_interval_seconds)
        current_interval = max(1, heartbeat.current_interval_seconds or base_interval)
        max_interval = base_interval * max(1, self.settings.worker_backoff_max_multiplier)
        next_interval = min(max_interval, current_interval * 2)
        if next_interval != current_interval:
            self._schedule_job(name, next_interval)
            logger.warning(
                "worker.%s backoff interval=%ss failures=%s",
                name,
                next_interval,
                heartbeat.consecutive_failures,
            )

    def _restore_interval_if_needed(self, name: str) -> None:
        heartbeat = self.heartbeats[name]
        base_interval = max(1, heartbeat.base_interval_seconds)
        if heartbeat.current_interval_seconds and heartbeat.current_interval_seconds != base_interval:
            self._schedule_job(name, base_interval)
            logger.info("worker.%s interval restored=%ss", name, base_interval)

    def _next_run_at(self, name: str) -> datetime | None:
        scheduled = self.scheduler.get_job(name)
        return scheduled.next_run_time if scheduled else None

    def _first_run_at(self, name: str, fallback: datetime) -> datetime:
        if name == "database_retention":
            return _next_daily_time(4, 0, self.settings.db_maintenance_timezone)
        if name == "database_backup":
            return _next_daily_time(4, 30, self.settings.db_maintenance_timezone)
        # Starting every network-heavy job on the same second caused duplicate
        # Bitget requests and held SQLite writes long enough to starve API reads.
        # Keep position sync immediate, then spread independent collectors.
        startup_offsets = {
            "sync_positions": 0,
            "daily_summary": 12,
            "weekly_calibration_report": 18,
            "regen_stale_insights": 28,
            "collect_derivatives": 40,
            "refresh_market_data": 55,
            "alert_response_scoring": 70,
            "interim_scoring": 85,
            "scout_scan": 105,
            "universe_scan": 150,
        }
        return fallback + timedelta(seconds=startup_offsets.get(name, 0))

    def _persist(self, heartbeat: HeartbeatRecord) -> None:
        self.heartbeat_store.upsert(heartbeat)


def _configure_worker_logging(settings: Settings) -> None:
    configure_logging(settings)


def _compact_result(result: Any) -> Any:
    if isinstance(result, dict):
        return {key: value for key, value in result.items() if key in {"count", "open_count", "positions", "scores", "needs_exit_record_count"}}
    return result


def _next_daily_time(hour: int, minute: int, timezone_name: str) -> datetime:
    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning("unknown maintenance timezone=%s using UTC", timezone_name)
        local_timezone = timezone.utc
    now = datetime.now(local_timezone)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target.astimezone(timezone.utc)
