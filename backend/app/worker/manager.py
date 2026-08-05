from __future__ import annotations

import asyncio
import inspect
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import Settings
from app.core.logging import configure_logging
from app.notify.alerts import AlertEngine
from app.notify.bot.bot import TelegramBotSupervisor
from app.notify.bot.formatters import format_paper_event
from app.notify.state import NotificationState
from app.notify.paper_events import SUPPRESSIBLE_KINDS, is_telegram_sendable, suppression_key
from app.notify.telegram import TelegramSender
from app.services import runtime as service
from app.worker import liveness
from app.worker.heartbeat import HeartbeatRecord, SQLiteHeartbeatStore
from app.toss.service import collect_market as collect_toss_market
from app.stock_paper.service import run_stock_paper_engine
from app.poly_paper.service import run_poly_paper_engine
from app.services import http_handlers as engine_runtime

logger = logging.getLogger("worker.manager")


JobRunner = Callable[[], Any | Awaitable[Any]]


def _track_key_for_event(event: dict[str, Any]) -> str:
    """페이퍼 이벤트를 성과 리포트의 트랙키로 매핑한다.

    주식은 KR·US 를 분리 집계하므로(작업 3) detail.market 으로 갈라야 한다 —
    market 이 없으면 어느 시장인지 단정하지 않고 빈 키를 돌려 접미를 생략한다.
    """
    track = str(event.get("track") or "")
    if not track:
        return "crypto"
    if track == "stock":
        market = str((event.get("detail") or {}).get("market") or "")
        return f"stock_{market.lower()}" if market else ""
    return track


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
        # D2: 스플릿 플래그 불일치(엔진 켜짐 · 구동 잡 꺼짐)를 조용히 두지 않는다.
        self.flag_warnings = self._flag_consistency_warnings()

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
        for warning in self.flag_warnings:
            logger.warning("worker flag inconsistency: %s", warning["message"])
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
            "flag_warnings": self.flag_warnings,
        }

    def _flag_consistency_warnings(self) -> list[dict[str, Any]]:
        """엔진은 켜졌는데 그것을 구동하는 잡이 꺼진 조용한 불일치를 노출한다 (D2).

        스플릿 플래그 함정: 상태 화면엔 페이퍼가 '활성'으로 보이지만 구동 잡이 꺼져 있어
        한 번도 실행되지 않는다. 이 경우를 기동 시 명시적 경고로 발행한다.
        """
        warnings: list[dict[str, Any]] = []
        if self.settings.stock_paper_engine_enabled and not self.settings.toss_stock_scout_enabled:
            warnings.append(
                {
                    "track": "stock",
                    "engine_flag": "stock_paper_engine_enabled=True",
                    "driver_flag": "toss_stock_scout_enabled=False",
                    "message": (
                        "주식 페이퍼 엔진은 활성이나 구동 잡(toss_stock_scout)이 꺼져 있어 한 번도 실행되지 않습니다. "
                        "FCE_TOSS_STOCK_SCOUT_ENABLED=true 로 구동 잡을 켜십시오."
                    ),
                }
            )
        return warnings

    def _job_timeout_seconds(self, name: str) -> int:
        """잡 실행 예산(초). 주기 × 배수를 하한/상한으로 클램프한다.

        하트비트처럼 주기가 짧은 잡도 최소 예산(기본 120초)을 받아 조기 취소되지 않는다.
        """
        job = self.jobs.get(name)
        interval = int(getattr(job, "interval_seconds", 0) or 0)
        multiplier = max(2, int(self.settings.worker_job_timeout_multiplier))
        floor = max(10, int(self.settings.worker_job_timeout_floor_seconds))
        ceiling = max(floor, int(self.settings.worker_job_timeout_ceiling_seconds))
        return max(floor, min(ceiling, interval * multiplier or floor))

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
                # C3: 무한 대기 금지. 잡마다 예산(주기 × 배수, 하한/상한 클램프)을 주고
                # 초과하면 취소한다. 취소해도 락은 `async with` 가 풀어주므로 다음 틱은 정상 실행된다.
                # 2026-07-28 사고 이전엔 manager 전체에 timeout 이 0건이었다.
                budget = self._job_timeout_seconds(name)
                result = runner()
                if inspect.isawaitable(result):
                    result = await asyncio.wait_for(result, timeout=budget)
                heartbeat.runs += 1
                heartbeat.consecutive_failures = 0
                heartbeat.status = "ok"
                now_ts = datetime.now(timezone.utc)
                heartbeat.last_success_at = now_ts
                # D3: 조기 반환(비활성/미구성)은 success이지만 effective run이 아니다. 엔진이 실제로
                # 평가를 수행한 잡만 last_effective_run_at을 갱신한다. 이 괴리가 "돌지만 안 돈다"를 드러낸다.
                if not (isinstance(result, dict) and result.get("effective_run") is False):
                    heartbeat.last_effective_run_at = now_ts
                heartbeat.last_error = None
                if scheduled:
                    self._restore_interval_if_needed(name)
                logger.info("worker.%s ok result=%s", name, _compact_result(result))
                return result
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                heartbeat.consecutive_failures += 1
                heartbeat.total_failures += 1
                heartbeat.status = "error"
                heartbeat.last_error_at = datetime.now(timezone.utc)
                heartbeat.last_error = f"timeout after {self._job_timeout_seconds(name)}s"
                logger.error("worker.%s TIMEOUT — 취소하고 다음 틱으로 넘어간다", name)
                if scheduled:
                    self._apply_backoff_if_needed(name)
                return None
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
        # WO-FCE-ENGINE-LIVENESS-01 작업 2(D1): 이 줄이 유일하게 _run_hook 보호 밖에 있어,
        # Bitget 오류·DB 잠금 하나로 아래 8단계(크립토 페이퍼·모든 알림·생존 펄스·일일 요약)가
        # 통째로 소멸했다. 훅으로 격리하고, 실패해도 빈 payload 로 나머지를 계속 실행한다.
        # 생존 신호(pulse·daily_summary)가 데이터 수집 성공에 의존하면 안 된다.
        sync_job = self.jobs["sync_and_analyze"]
        payload = await self._run_hook("sync_and_analyze", sync_job.runner) if sync_job.runner else None
        if not isinstance(payload, dict):
            payload = {"positions": [], "sync_failed": True}
        await self._run_hook("detect_closures", lambda: asyncio.to_thread(service.detect_closures))
        paper_result = await self._run_hook("paper_engine", lambda: asyncio.to_thread(service.run_paper_engine))
        if isinstance(paper_result, dict):
            await self._send_paper_events(paper_result)
        # WO-44: 진입/종료/판정 전이 — 라이프사이클이 1차 정보이므로 조건 알림보다 먼저.
        await self._run_hook("evaluate_lifecycle", lambda: self.alerts.evaluate_lifecycle(payload))
        await self._run_hook(
            "evaluate_alerts",
            lambda: self.alerts.evaluate_positions(payload.get("positions", [])),
        )
        # WO-FCE-STRUCTURE-CONTEXT-01: 보유 포지션의 구조 관계 전이(레인지 이탈·OB 진입·국면 전환).
        await self._run_hook(
            "evaluate_structure_context",
            lambda: self.alerts.evaluate_position_structure(payload.get("positions", [])),
        )
        await self._run_hook(
            "evaluate_performance_alerts",
            lambda: self._evaluate_performance_alerts(),
        )
        await self._run_hook("periodic_pulse", lambda: self.alerts.maybe_send_pulse(payload))
        # 작업 5: 트랙 생존 라인 동봉 — 진입이 0이어도 "살아있음"이 매일 도착해야 한다(뮤트 관통).
        await self._run_hook(
            "daily_summary",
            lambda: self.alerts.maybe_send_daily_summary(payload, self._liveness_lines(), self._performance_lines()),
        )
        return {
            "open_count": payload.get("open_count"),
            "needs_exit_record_count": payload.get("needs_exit_record_count"),
            "positions": len(payload.get("positions", [])),
            "created": payload.get("created"),
            "auto_closed": payload.get("auto_closed"),
        }

    async def _send_paper_events(self, result: dict[str, Any]) -> int:
        if not isinstance(result, dict):
            return 0
        events = [event for event in (result.get("events") or []) if isinstance(event, dict)]
        # 트랙이 실제로 평가했다면 그 트랙의 skip 억제 상태를 리셋한다 — 회복은 다음 스킵의
        # 새 상태 전이가 되어 다시 1회 발송되도록. 뮤트 여부와 무관하게 상태는 정확히 유지한다.
        if result.get("effective_run"):
            for track in {str(event.get("track") or "") for event in events if event.get("track")}:
                self.state.clear_paper_skips_for_track(track)
        if not getattr(self.settings, "paper_telegram_alerts_enabled", True) or not self.settings.telegram_alerts_enabled or self.state.is_muted():
            self._persist_state()
            return 0
        sent = 0
        # §2-3: 청산·정산이 하나라도 있으면 트랙 누적 승률·N 을 한 번만 집계해 하단에 붙인다.
        # "결과 뭐였다 → 승률 어떻다" 를 한 메시지에서 보게 한다.
        track_records = self._track_record_suffixes() if any(str(event.get("kind") or "") == "closed" for event in events) else {}
        for event in events:
            # 화이트리스트(작업 1): 거부·미발생·오류는 여기서 원천 차단된다. 억제가 아니라
            # 미도달이므로 유니버스가 오염돼 전이가 반복돼도 텔레그램에는 0건이다(C1).
            if not is_telegram_sendable(event):
                continue
            # 화이트리스트 통과분에 대한 추가 빈도 제한(현재 대상 없음 — 계약 유지).
            if str(event.get("kind") or "") in SUPPRESSIBLE_KINDS and event.get("track"):
                if not self.state.register_paper_skip(suppression_key(event)):
                    continue
            message = format_paper_event(event)
            suffix = track_records.get(_track_key_for_event(event)) if str(event.get("kind") or "") == "closed" else None
            if suffix:
                message = f"{message}\n{suffix}"
            sent += await self.sender.send_to_all(message)
        self._persist_state()
        return sent

    def _track_record_suffixes(self) -> dict[str, str]:
        """트랙키 → 누적 승률 문구. 집계 실패는 즉시 알림 자체를 막지 않는다."""
        try:
            from app.notify.performance_report import format_track_record_suffix

            payload = service.paper_performance()
            return {
                str(track.get("key")): format_track_record_suffix(track.get("closed") or {})
                for track in (payload.get("tracks") or [])
                if isinstance(track, dict) and track.get("key")
            }
        except Exception as exc:
            logger.warning("track record suffix failed: %s", exc, exc_info=True)
            return {}

    def _persist_state(self) -> None:
        path = str(getattr(self.settings, "notification_state_path", "") or "")
        if path:
            self.state.save(path)

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

    async def _weekly_performance_report(self) -> dict[str, Any]:
        sent = await self.alerts.maybe_send_weekly_performance_report()
        return {"count": sent}

    async def _collect_derivatives(self) -> dict[str, Any]:
        payload = await asyncio.to_thread(service.refresh_derivative_data)
        snapshots = payload.get("snapshots", [])
        if isinstance(snapshots, list):
            await self.alerts.evaluate_derivatives(snapshots)
        return payload

    async def _collect_whales(self) -> dict[str, Any]:
        payload = await asyncio.to_thread(service.collect_whales)
        dashboard = await asyncio.to_thread(service.whale_dashboard)
        await self.alerts.evaluate_whale_events(payload.get("events", []), dashboard)
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

    async def _collect_toss_stocks(self) -> dict[str, Any]:
        kr, us = await asyncio.gather(
            collect_toss_market(self.settings, "KR"),
            collect_toss_market(self.settings, "US"),
        )
        paper = await asyncio.to_thread(run_stock_paper_engine, self.settings, {"KR": kr, "US": us})
        # D1: 주식 페이퍼도 크립토와 같은 텔레그램 경로를 태운다(과거엔 미배선 = 구조적 침묵).
        await self._send_paper_events(paper)
        return {
            "KR": kr.get("status"),
            "US": us.get("status"),
            "stock_paper": paper,
            "effective_run": bool(paper.get("effective_run")),
        }

    async def _collect_polymarket(self) -> dict[str, Any]:
        result = await run_poly_paper_engine(
            self.settings,
            engine_runtime.market_provider,
            engine_runtime.repository,
        )
        # D1: 폴리 페이퍼도 텔레그램 경로 배선(과거엔 미호출).
        await self._send_paper_events(result)
        return result

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
            "score_candidates": WorkerJob(
                "score_candidates",
                self.settings.worker_score_candidates_interval_seconds,
                lambda: asyncio.to_thread(service.score_candidates),
            ),
            "stance_backtest": WorkerJob(
                "stance_backtest",
                self.settings.worker_stance_backtest_interval_seconds,
                lambda: asyncio.to_thread(service.refresh_stance_backtests),
                enabled=self.settings.worker_stance_backtest_enabled,
            ),
            "collect_derivatives": WorkerJob(
                "collect_derivatives",
                self.settings.derivative_tracking_interval_seconds,
                self._collect_derivatives,
                enabled=self.settings.derivative_tracking_enabled,
            ),
            "discover_whale_leaderboard": WorkerJob(
                "discover_whale_leaderboard",
                self.settings.hyperliquid_whale_discovery_interval_seconds,
                lambda: asyncio.to_thread(service.discover_whales),
                enabled=self.settings.hyperliquid_whale_discovery_enabled,
            ),
            "collect_whale_positions": WorkerJob(
                "collect_whale_positions",
                self.settings.hyperliquid_whale_poll_interval_seconds,
                self._collect_whales,
                enabled=self.settings.hyperliquid_whale_tracking_enabled,
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
            # WO-FCE-OBSERVATION-INTEGRITY-01 Phase 1: 하루가 검증일로 카운트되려면
            # 커버리지 게이트를 통과해야 한다. 매시간 최근 구간을 다시 재서 저장한다
            # (오늘은 진행 중이라 값이 계속 바뀌고, 어제는 지연 기록으로 늦게 채워질 수 있다).
            "observation_coverage": WorkerJob(
                "observation_coverage",
                3600,
                lambda: asyncio.to_thread(self._refresh_observation_coverage),
            ),
            # WO-FCE-ENGINE-LIVENESS-01(D1): sync_and_analyze 를 훅 잡으로 등록해 실패를 격리한다.
            # 이 잡이 실패해도 아래 단계(페이퍼·알림·펄스·일일요약)는 계속 실행된다.
            "sync_and_analyze": WorkerJob(
                "sync_and_analyze",
                self.settings.worker_sync_positions_interval_seconds,
                lambda: asyncio.to_thread(service.sync_and_analyze_positions),
                scheduled=False,
            ),
            "detect_closures": WorkerJob(
                "detect_closures",
                self.settings.worker_detect_closures_interval_seconds,
                lambda: service.detect_closures(),
                scheduled=False,
            ),
            "paper_engine": WorkerJob(
                "paper_engine",
                self.settings.worker_sync_positions_interval_seconds,
                None,
                scheduled=False,
                enabled=self.settings.paper_engine_enabled,
            ),
            "evaluate_lifecycle": WorkerJob(
                "evaluate_lifecycle",
                self.settings.worker_sync_positions_interval_seconds,
                None,
                scheduled=False,
            ),
            "evaluate_structure_context": WorkerJob(
                "evaluate_structure_context",
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
            # WO-FCE-PERFORMANCE-REPORT-01 §2-2: 주간 성과 리포트. 캘리브레이션과 별도 잡으로
            # 둔다 — 한쪽 실패가 다른 쪽 발송을 막지 않게.
            "weekly_performance_report": WorkerJob(
                "weekly_performance_report",
                60,
                self._weekly_performance_report,
            ),
            "refresh_calibration_cache": WorkerJob(
                "refresh_calibration_cache",
                self.settings.worker_calibration_cache_interval_seconds,
                lambda: asyncio.to_thread(service.refresh_calibration_report_cache),
            ),
            "sync_user_fills": WorkerJob(
                "sync_user_fills",
                self.settings.worker_user_fill_sync_interval_seconds,
                lambda: asyncio.to_thread(service.sync_user_fills),
                enabled=self.settings.worker_user_fill_sync_enabled,
            ),
            "refresh_symbol_catalog": WorkerJob(
                "refresh_symbol_catalog",
                self.settings.worker_symbol_catalog_interval_seconds,
                lambda: asyncio.to_thread(service.refresh_symbol_catalog),
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
            "toss_stock_scout": WorkerJob(
                "toss_stock_scout",
                self.settings.toss_poll_interval_seconds,
                self._collect_toss_stocks,
                enabled=self.settings.toss_stock_scout_enabled,
            ),
            "polymarket_paper": WorkerJob(
                "polymarket_paper",
                self.settings.polymarket_poll_interval_seconds,
                self._collect_polymarket,
                enabled=self.settings.polymarket_paper_enabled,
            ),
            # WO-FCE-ENGINE-LIVENESS-01(D2·D3): 사망 감지의 상시화.
            # 기존엔 sync_positions 예외 시점에만 평가돼, 잡이 "성공"하며 아무것도 안 하는
            # 4일 정지를 아무도 몰랐다. 독립 스케줄로 승격하고 3트랙을 감시 대상에 넣는다.
            # C2: 심장박동은 별도·초경량 잡. 감시 평가가 막혀도 이건 계속 뛴다.
            "heartbeat": WorkerJob(
                "heartbeat",
                self.settings.worker_heartbeat_interval_seconds,
                self._write_heartbeat,
            ),
            "worker_liveness": WorkerJob(
                "worker_liveness",
                self.settings.worker_liveness_interval_seconds,
                self._evaluate_liveness,
            ),
            "telegram_bot": WorkerJob("telegram_bot", 0, None, scheduled=False),
        }

    def _stock_market_data(self) -> dict[str, str | None]:
        """시장별 마지막 분석 시각 — KR·US 독립 생존 판정 재료(D3)."""
        try:
            from app.stock_paper.models import Market
            from app.stock_paper.store import StockPaperStore

            store = StockPaperStore(self.settings.database_url)
            if not store.enabled:
                return {}
            return {
                "stock_kr": store.latest_analysis_at(Market.KR),
                "stock_us": store.latest_analysis_at(Market.US),
            }
        except Exception as exc:
            logger.debug("stock market data probe failed: %s", exc)
            return {}

    def _refresh_observation_coverage(self, *, lookback_days: int = 30) -> dict[str, Any]:
        """관측 커버리지 재계산·저장. 저장된 관측 이력만 쓰므로 과거도 그대로 다시 잰다."""
        from app.db.maintenance import sqlite_path
        from app.db.sqlite_utils import connect_sqlite
        from app.worker import observation

        path = sqlite_path(self.settings.database_url)
        if path is None:
            return {"effective_run": False, "reason": "sqlite_only"}
        today = datetime.now(timezone.utc).date()
        start = today - timedelta(days=lookback_days)
        with connect_sqlite(str(path)) as connection:
            rows = observation.compute_range(connection, start, today)
            observation.save_coverage(connection, rows)
            connection.commit()
        clocks = {track: observation.verification_clock([row for row in rows if row["track"] == track]) for track in observation.TRACK_SPECS}
        return {
            "days": len(rows),
            "effective_days": {track: clock["effective_days"] for track, clock in clocks.items()},
            "action_items": len(observation.manual_action_items(rows)),
        }

    def _stock_market_reasons(self) -> dict[str, str]:
        """시장별 정지 사유(차단 래치·조기 반환) — 감시가 "왜"까지 말하게 한다(작업 3)."""
        try:
            from app.toss import blocks as toss_blocks

            return toss_blocks.stall_reasons()
        except Exception as exc:
            logger.debug("toss stall reason probe failed: %s", exc)
            return {}

    def _liveness_lines(self) -> list[str]:
        """일일 요약용 3트랙 생존 라인. 진단 실패는 라인 생략이 아니라 게이트 정보만 생략."""
        diagnosis: dict[str, Any] | None = None
        try:
            from app.api.paper_diagnosis import paper_diagnosis

            diagnosis = paper_diagnosis()
        except Exception as exc:
            logger.debug("paper diagnosis for liveness lines failed: %s", exc)
        return liveness.daily_liveness_lines(self.status(), self.settings, diagnosis, market_data=self._stock_market_data())

    def _performance_lines(self) -> list[str]:
        """일일 요약용 4트랙 성과 라인 (WO-FCE-PERFORMANCE-REPORT-01 §2-1).

        생존 라인과 역할이 다르다 — 생존은 "살아있음", 성과는 "무엇을 하고 있음"이다.
        집계 실패는 요약 전체를 막지 않는다(성과 블록만 생략).
        """
        try:
            from app.notify.performance_report import format_paper_performance

            payload = service.paper_performance()
            date_label = datetime.now(timezone.utc).strftime("%m-%d")
            return format_paper_performance(payload, date_label=date_label)
        except Exception as exc:
            logger.warning("paper performance report failed: %s", exc, exc_info=True)
            return []

    def _write_heartbeat(self) -> dict[str, Any]:
        """WO-FCE-ENGINE-RESTORE-01 (C2) — **심장박동 전용 잡. 가장 단순한 경로여야 한다.**

        네트워크·앱 서비스 호출 없음. 파일에 타임스탬프만 쓰고 즉시 반환한다.

        왜 분리했나: 하트비트가 평가·텔레그램 전송 뒤에 있으면 그 앞단이 매달리거나 실패할 때
        심장박동이 함께 멎는다. 2026-07-28 사고에서 스냅샷 직렬화 실패(datetime) 하나로
        하트비트가 11.7시간 멈췄고, 외부 감시자는 그것을 프로세스 사망으로 오판했다.
        이 잡은 실패할 이유가 최소여야 하며, 실패하면 **ERROR 로 크게 남긴다**(조용한 실패 금지).
        """
        payload = {
            "schema_version": 2,
            "written_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "scheduler_running": bool(self.scheduler.running),
            "source": "heartbeat_job",
        }
        try:
            liveness.write_liveness_snapshot(self.settings.worker_liveness_path, payload)
        except Exception as exc:
            # 심장박동 실패는 경고가 아니라 오류다 — 이걸 WARNING 으로 삼켜서 11.7시간을 잃었다.
            logger.error("HEARTBEAT WRITE FAILED (외부 감시자가 사망으로 오판한다): %s", exc, exc_info=True)
            return {"written": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"written": True, "at": payload["written_at"]}

    async def _evaluate_liveness(self) -> dict[str, Any]:
        """트랙 정지·백오프 고착·인프라·재시작 감시(진단 스냅샷 포함).

        ⚠️ 이것은 **내부** 감시다. 프로세스가 죽으면 이 잡도 죽는다 — 프로세스 사망 판정은
        `_write_heartbeat` 가 남긴 파일을 외부 감시자(scripts/local/deadman.sh)가 읽어서 한다.
        이 잡이 느려지거나 실패해도 심장박동은 별도 잡이라 계속 뛴다(C2).
        """
        status = self.status()
        market_data = self._stock_market_data()
        market_reasons = self._stock_market_reasons()
        candidates = list(liveness.evaluate_liveness(status, self.settings, market_data=market_data, market_reasons=market_reasons))
        candidates.extend(liveness.infra_alerts(*liveness.capacity_probe(self.settings), self.settings))
        restarts = liveness.recent_restarts(self.settings)
        restart_candidate = liveness.restart_alert(restarts)
        if restart_candidate is not None:
            candidates.append(restart_candidate)

        sent = await self.alerts.evaluate_liveness_alerts(candidates)

        snapshot = liveness.build_liveness_snapshot(
            status,
            self.settings,
            pid=os.getpid(),
            extra={"restarts_24h": len(restarts), "alerts_sent": sent},
            market_data=market_data,
        )
        # 상세 진단 스냅샷은 하트비트와 **다른 파일**에 쓴다 — 진단 실패가 심장박동을 멈추지 않게(C2).
        try:
            detail_path = str(Path(self.settings.worker_liveness_path).with_name("liveness-detail.json"))
            liveness.write_liveness_snapshot(detail_path, snapshot)
        except Exception as exc:
            logger.warning("liveness detail snapshot write failed: %s", exc)
        return {
            "stale_tracks": snapshot["stale_tracks"],
            "backoff_stuck": snapshot["backoff_stuck"],
            "alerts_sent": sent,
            "restarts_24h": len(restarts),
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
        if name == "score_candidates":
            return _next_daily_time(3, 30, self.settings.db_maintenance_timezone)
        if name == "stance_backtest":
            return _next_daily_time(5, 0, self.settings.db_maintenance_timezone)
        # Starting every network-heavy job on the same second caused duplicate
        # Bitget requests and held SQLite writes long enough to starve API reads.
        # Keep position sync immediate, then spread independent collectors.
        startup_offsets = {
            "sync_positions": 0,
            "refresh_calibration_cache": 4,
            "sync_user_fills": 6,
            "refresh_symbol_catalog": 8,
            "daily_summary": 12,
            "weekly_calibration_report": 18,
            "weekly_performance_report": 20,
            "discover_whale_leaderboard": 24,
            "regen_stale_insights": 28,
            "collect_derivatives": 40,
            "collect_whale_positions": 46,
            "refresh_market_data": 55,
            "alert_response_scoring": 70,
            "interim_scoring": 85,
            "scout_scan": 105,
            "universe_scan": 150,
            "score_candidates": 180,
            "stance_backtest": 210,
            "polymarket_paper": 240,
        }
        return fallback + timedelta(seconds=startup_offsets.get(name, 0))

    def _persist(self, heartbeat: HeartbeatRecord) -> None:
        self.heartbeat_store.upsert(heartbeat)


def _configure_worker_logging(settings: Settings) -> None:
    configure_logging(settings)


def _compact_result(result: Any) -> Any:
    if isinstance(result, dict):
        # KR·US 를 넣는 이유: 이게 빠져 있어서 "20.8시간째 authentication_failed" 가
        # 로그에 단 한 줄도 안 남았다(WO-FCE-TOSS-US-STALL-01 D4). 조기 반환 사유는 버리지 않는다.
        return {key: value for key, value in result.items() if key in {"count", "open_count", "positions", "scores", "needs_exit_record_count", "KR", "US"}}
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
