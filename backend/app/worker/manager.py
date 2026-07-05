from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings
from app.notify.alerts import AlertEngine
from app.notify.bot.bot import TelegramBotSupervisor
from app.notify.state import NotificationState
from app.notify.telegram import TelegramSender
from app.services import runtime as service

logger = logging.getLogger(__name__)


@dataclass
class JobHeartbeat:
    name: str
    status: str = "idle"
    runs: int = 0
    failures: int = 0
    last_started_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None
    next_run_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "runs": self.runs,
            "failures": self.failures,
            "last_started_at": self.last_started_at,
            "last_success_at": self.last_success_at,
            "last_error_at": self.last_error_at,
            "last_error": self.last_error,
            "next_run_at": self.next_run_at,
        }


class WorkerManager:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.state = NotificationState()
        self.sender = TelegramSender(settings)
        self.alerts = AlertEngine(settings, self.sender, self.state)
        self.bot = TelegramBotSupervisor(settings, self.state)
        self.jobs = {
            "position_sync": JobHeartbeat("position_sync"),
            "daily_summary": JobHeartbeat("daily_summary"),
            "telegram_bot": JobHeartbeat("telegram_bot"),
        }
        self._tasks: list[asyncio.Task] = []
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if not self.settings.background_worker_enabled:
            for job in self.jobs.values():
                job.status = "disabled"
            return
        self._tasks = [
            asyncio.create_task(self._position_loop(), name="fce-position-sync"),
            asyncio.create_task(self._telegram_bot_loop(), name="fce-telegram-bot"),
        ]

    async def stop(self) -> None:
        self._stop_event.set()
        self.bot.stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def status(self) -> dict[str, Any]:
        return {
            "status": "running" if self._tasks else "disabled",
            "jobs": {name: heartbeat.as_dict() for name, heartbeat in self.jobs.items()},
            "notifications": {
                "telegram_sender_enabled": self.sender.enabled,
                "telegram_bot_enabled": self.bot.enabled,
                "muted_until": self.state.muted_until,
                "is_muted": self.state.is_muted(),
            },
        }

    async def _position_loop(self) -> None:
        await asyncio.sleep(max(0, self.settings.worker_startup_delay_seconds))
        while not self._stop_event.is_set():
            heartbeat = self.jobs["position_sync"]
            heartbeat.status = "running"
            heartbeat.last_started_at = datetime.now(timezone.utc)
            try:
                payload = await asyncio.to_thread(service.sync_and_analyze_positions)
                heartbeat.runs += 1
                heartbeat.status = "ok"
                heartbeat.last_success_at = datetime.now(timezone.utc)
                await self.alerts.evaluate_positions(payload.get("positions", []))
                daily = self.jobs["daily_summary"]
                daily.status = "running"
                sent = await self.alerts.maybe_send_daily_summary(payload)
                daily.runs += 1
                daily.status = "ok" if sent >= 0 else "idle"
                daily.last_success_at = datetime.now(timezone.utc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("position worker failed")
                heartbeat.failures += 1
                heartbeat.status = "error"
                heartbeat.last_error_at = datetime.now(timezone.utc)
                heartbeat.last_error = f"{type(exc).__name__}: {exc}"
            heartbeat.next_run_at = datetime.now(timezone.utc)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(5, self.settings.live_position_sync_interval_seconds))
            except asyncio.TimeoutError:
                continue

    async def _telegram_bot_loop(self) -> None:
        heartbeat = self.jobs["telegram_bot"]

        def mark(status: str, error: str | None) -> None:
            heartbeat.status = status
            if status == "running":
                heartbeat.last_started_at = datetime.now(timezone.utc)
            if status == "error":
                heartbeat.failures += 1
                heartbeat.last_error_at = datetime.now(timezone.utc)
                heartbeat.last_error = error
            if status == "disabled":
                heartbeat.last_error = error

        try:
            await self.bot.run_forever(mark)
            if heartbeat.status == "running":
                heartbeat.status = "stopped"
                heartbeat.last_success_at = datetime.now(timezone.utc)
        except asyncio.CancelledError:
            heartbeat.status = "stopped"
            raise
