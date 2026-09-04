from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.events import EVENT_JOB_MISSED
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
from app.worker import hang_probe, liveness
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


# WO-FCE-WORKER-HANG-02 Phase 2-2 — misfire 정책.
#
# ## 왜 명시해야 하는가
#
# APScheduler 의 `misfire_grace_time` **기본값은 1초**다. 예정 시각이 1초를 넘겨 지나가면 그
# 발화는 지연되는 것이 아니라 **건너뛴다.** 그리고 이 워커의 이벤트 루프 지연은 실측
# 평균 11.66초 · 최대 49.73초다(Phase 1 `LoopLagMonitor`, 표본 860건 전부 5초 초과).
#
# 즉 **거의 모든 발화가 소실되고 있었다.** 그 결과가 `universe_scan` 24시간 19회 실행
# (예상 48회 · 40%)이고, 캐시가 얼어붙어 유니버스가 3종으로 말랐다.
#
# ## 관측 잡과 시각 민감 잡을 나눈다
#
# 주기 관측 잡은 **늦어도 다음 주기 전까지 실행되면 관측 가치가 있다** — grace 를 주기만큼 준다.
# 알림·펄스 잡은 늦은 발송이 무의미하거나 해롭다(지나간 상황을 지금 알리는 것) — 짧게 둔다.
#
# ⚠️ 간격은 건드리지 않는다(C1). 이 상수는 "얼마나 늦어도 실행할지"이며 "얼마나 자주"가 아니다.
_TIME_SENSITIVE_GRACE_SECONDS = 30

# 늦은 발송이 무의미한 잡. 이들만 짧은 grace 를 쓴다.
_TIME_SENSITIVE_JOBS = frozenset(
    {
        "evaluate_alerts",
        "evaluate_lifecycle",
        "evaluate_performance_alerts",
        "evaluate_structure_context",
        "periodic_pulse",
        "daily_summary",
        "weekly_calibration_report",
        "weekly_performance_report",
        "verdict_transition_watch",
    }
)

# 관측 잡 grace 상한. 하루 주기 잡에 하루치 grace 를 주면 "언제 돌아도 된다"가 되어
# 기아를 감지할 수 없다 — 상한을 둬서 이상 상태가 드러나게 한다.
_MAX_OBSERVATION_GRACE_SECONDS = 1800


# WO-FCE-WORKER-HANG-02 Phase 2-3 — 실행 격리.
#
# ## 왜 필요한가 (D3·D5)
#
# `asyncio.to_thread` 는 **루프의 기본 실행기를 공유**한다. 이 머신은 cpu_count 10 이라
# 기본 풀이 14 워커이고, 동시 실행 잡이 실측 최대 11개다. 여기에
# `leaderboard.py` 가 자체 12스레드 풀을 더 띄운다 → 풀 포화 + GIL 경합으로
# **이벤트 루프가 상시 굶는다**(실측 지연 평균 11.66초 · 최대 49.73초).
#
# 그리고 D5: `asyncio.wait_for` 는 코루틴만 취소하고 **스레드 안의 동기 코드는 취소되지
# 않는다.** 타임아웃이 나도 스레드는 계속 돌아 슬롯이 반환되지 않는다. `toss_stock_scout` 은
# 간격 10초인데 평균 46.8초라 주기 내 완료가 원리적으로 불가능하고, 실측 미완 111/576 건이
# 그 결과다. 공유 풀에서 이런 잡이 슬롯을 물면 **표본을 만드는 잡이 굶는다.**
#
# ## 처방: 격리한다. 끄거나 늘리지 않는다
#
# 무거운 잡을 전용 실행기로 옮겨 오염 범위를 그 풀 안으로 제한한다. 간격도(C1) 타임아웃도(C2)
# 건드리지 않고 잡을 끄지도(C3) 않는다 — **느린 잡은 격리하고, 빠른 잡의 슬롯을 지킨다.**
#
# 선정 근거는 Phase 1 `job-trace.jsonl` 24시간 실측이다(평균/최대 실행 초 · 미완 건수):
#   toss_stock_scout            46.8 / 102.1   미완 111/576   ← 확인된 슬롯 누수원
#   refresh_calibration_cache   99.6 / 1295.6
#   discover_whale_leaderboard 175.4 /  983.4  (+ 자체 12스레드 풀)
#   collect_derivatives         50.3 / 1015.1
#
# `sync_positions` 는 **제외한다.** 실측 평균 51.6초지만 그것은 훅들의 합계이고, 그 안에
# `paper_engine`(표본을 만드는 잡)이 들어 있다. 격리하면 표본 생산자를 좁은 풀에 넣는
# 셈이라 방향이 반대다. 자기 몫의 `sync_and_analyze` 는 평균 0.16초로 가볍다.
_HEAVY_JOBS = frozenset(
    {
        "toss_stock_scout",
        "refresh_calibration_cache",
        "discover_whale_leaderboard",
        "collect_derivatives",
        # WO-FCE-REPLAY-DEPTH-01 4-2: 심볼당 1.6초 × 최대 25심볼. 기본 풀에 두면
        # 표본 생산 잡의 슬롯을 먹는다 — 처음부터 격리한다(C8).
        "replay_history_backfill",
        # WO-FCE-WHALE-FOLLOW-01 6-2: 분석 조회 최대 3건 × 심볼당 ~30초. 기본 풀에 두면
        # 크립토 트랙 실행을 밀어낸다(C9). 조회 상한과 격리를 함께 건다 — 상한만으로는
        # 슬롯 점유를 막지 못한다.
        "whale_follow_engine",
    }
)

# 라이프사이클 대기 큐 상한. 알림이 며칠 죽어 있어도 메모리가 무한히 늘면 안 된다.
# 넘치면 **버린 수를 센다** — 조용히 버리는 것이 이 결함의 본체다.
_LIFECYCLE_QUEUE_MAX = 500

# 부모 잡 하나에 매달린 훅 수. 예산을 이 수로 나눈다(3-3) — **올리는 값이 아니다.**
# 훅을 늘리거나 줄이면 여기도 함께 고친다. 어긋나면 예산이 다시 한 훅에 쏠린다.
_HOOK_SHARES = {
    # sync_and_analyze · detect_closures · paper_engine
    "sync_positions": 3,
    # evaluate_lifecycle · evaluate_alerts · evaluate_structure_context ·
    # evaluate_performance_alerts · periodic_pulse · daily_summary
    "deliver_alerts": 6,
}

# 전용 풀 크기. 작게 둔다 — 크게 잡으면 GIL 경합이 되살아나 격리의 의미가 없다.
# 누수가 나도 이 수만큼만 묶이고 기본 풀은 온전하다.
_HEAVY_EXECUTOR_WORKERS = 4


def _misfire_grace_seconds(name: str, interval_seconds: int) -> int:
    """이 잡이 예정 시각을 얼마나 넘겨도 실행할지 (Phase 2-2).

    관측 잡: 주기만큼(상한 30분). 다음 주기가 오기 전이면 실행하는 것이 관측에 이롭다.
    시각 민감 잡: 30초. 1초(기본값)는 루프 지연 앞에서 사실상 "항상 건너뜀"이다.
    """
    interval = max(1, int(interval_seconds))
    if name in _TIME_SENSITIVE_JOBS:
        return _TIME_SENSITIVE_GRACE_SECONDS
    return max(_TIME_SENSITIVE_GRACE_SECONDS, min(interval, _MAX_OBSERVATION_GRACE_SECONDS))


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
        # Phase 2-2: `misfire_grace_time` 기본값 1초가 루프 지연(평균 11.66초)과 만나면
        # 거의 모든 발화가 소실된다. 잡별 값은 `_schedule_job` 에서 명시하고, 여기서는
        # 등록 누락 시의 안전망만 둔다 — 1초로 되돌아가지 않게.
        self.scheduler = AsyncIOScheduler(
            timezone=timezone.utc,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": _TIME_SENSITIVE_GRACE_SECONDS},
        )
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
        # WO-FCE-WHALE-FOLLOW-02 7-2: 체결 구동 추종 실행. 수집 잡(30초)이 깨우지만
        # **기다리지 않는다** — 기다리면 추종 엔진의 분석 조회(최대 3건 × ~30초)가 수집 잡의
        # 예산(150초)을 먹고 그것이 곧 잡 타임아웃이다. 대신 별도 태스크로 띄우고
        # 중복 실행만 막는다.
        self._whale_follow_task: asyncio.Task | None = None
        # ALERT-SILENCE-01 3-1: 알림 잡이 읽는 마지막 동기화 결과. 동기화가 죽어도 알림은
        # 이것을 들고 돌며, 낡았으면 낡았다고 알린다.
        self._last_sync_payload: dict[str, Any] | None = None
        self._last_sync_at: datetime | None = None
        # 진입·청산은 **누적한다.** 페이로드에 얹어 두면 다음 동기화가 통째로 덮어써
        # 알림 잡이 한 틱만 늦어도 사라진다 — `sync_positions` 는 90초마다 돌면서 대부분
        # 빈 목록을 반환하므로 그 손실은 예외가 아니라 흔한 경우다.
        self._pending_created: list[str] = []
        self._pending_closed: list[dict[str, Any]] = []
        self._lifecycle_dropped = 0
        self._started = False
        # WO-FCE-WORKER-HANG-02: 매달림 증거 경로. 하트비트 파일과 같은 logs 디렉터리를 쓴다.
        log_dir = Path(str(settings.worker_liveness_path)).expanduser().parent
        self._log_dir = log_dir
        self._job_trace_path: Path | None = log_dir / "job-trace.jsonl"
        self.loop_lag = hang_probe.LoopLagMonitor(log_dir)
        # Phase 2-3: 무거운 잡 전용 실행기. 기본 풀을 비워 둬 표본 생산 잡(universe_scan ·
        # scout_scan · paper_engine · heartbeat)의 슬롯을 지킨다.
        self._heavy_executor = ThreadPoolExecutor(
            max_workers=_HEAVY_EXECUTOR_WORKERS,
            thread_name_prefix="fce-heavy",
        )
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
        # Phase 2-2 · C8: 건너뛴 발화는 지금까지 **조용히 사라졌다.** APScheduler 는 WARNING 을
        # 찍지만 잡 이름이 없어(`WorkerManager._run_scheduled_job`) 어느 잡이 굶는지 알 수 없었다.
        # 리스너로 잡별 카운터에 적어 조회 가능하게 만든다.
        self.scheduler.add_listener(self._on_job_missed, EVENT_JOB_MISSED)
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

        # 매달림 증거는 사고 순간에 자동으로 남아야 한다 — 사람이 붙어 있을 수 없다(Phase 0).
        self.hang_dump = hang_probe.install_signal_dump(self._log_dir)
        # 루프 지연 계측은 **잡이 아니라 독립 태스크**다. 잡으로 만들면 측정 대상(루프)에
        # 측정기가 함께 갇혀 정작 정체 구간에서 기록이 끊긴다(Phase 1).
        self.loop_lag.start()
        self._telegram_task = asyncio.create_task(self._telegram_bot_loop(), name="fce-telegram-bot")
        for warning in self.flag_warnings:
            logger.warning("worker flag inconsistency: %s", warning["message"])
        logger.info("worker scheduler started jobs=%s", sorted(self.jobs))

    async def stop(self) -> None:
        await self.loop_lag.stop()
        # Phase 2-3: 전용 실행기 정리. 누수된 스레드가 있으면 대기하지 않는다 —
        # 종료가 매달리면 그것이 새로운 침묵이다.
        self._heavy_executor.shutdown(wait=False, cancel_futures=True)
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
            # Phase 2-4 · D1: 워커 생존과 **별도로** 잡 기아를 노출한다. 워커가 살아 있어도
            # 잡이 굶으면 보여야 한다 — 감시 단위를 실패 단위에 맞춘다. 조회 전용이며
            # 신규 푸시를 만들지 않는다(2-4 작업 3).
            "job_starvation": liveness.job_starvation(jobs),
            # Phase 1: 루프 지연은 조회로만 노출한다(C8 — 새 푸시를 만들지 않는다).
            "loop_lag": self.loop_lag.snapshot(),
            "hang_dump": getattr(self, "hang_dump", {"registered": False, "reason": "not_started"}),
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

    async def _run_hook(self, name: str, runner: JobRunner, *, parent: str | None = None) -> Any:
        """부모 잡 안에서 도는 훅. `parent` 를 주면 **부모 예산을 나눠 쓴다**(3-3).

        훅마다 자기 예산이 있어도 **부모 잡의 타임아웃이 전체를 덮는다.** 훅 5개가 각자
        450초를 받아도 부모가 450초면 첫 훅 하나가 그것을 다 먹고 나머지는 시작조차 못 한다 —
        `sync_positions` 가 3회 이상 그렇게 죽었다.

        그래서 훅 예산을 **부모 예산 ÷ 훅 수**로 낮춘다. **타임아웃을 올리지 않는다**(C4) —
        나누는 것이므로 모든 값이 작아진다. 느린 훅 하나가 잘리고 뒤쪽 훅은 살아남는다.
        """
        return await self._run_job(name, runner, scheduled=False, max_seconds=self._hook_budget(parent) if parent else None)

    def _hook_budget(self, parent: str) -> int:
        """훅 하나가 부모 예산을 통째로 먹지 못하게 하는 상한.

        ## 하한은 시스템이 이미 안전하다고 정한 값이다

        처음에는 `부모 예산 ÷ 훅 수` 를 그대로 썼고 그것이 **너무 조였다** —
        `deliver_alerts` 훅이 450초에서 **90초로** 줄었다. `evaluate_lifecycle` 은 신규
        포지션마다 분석을 조회하므로(심볼당 ~30초) 진입이 3건이면 그 자리에서 잘린다.
        **알림을 살리려던 변경이 알림을 자르는 것이 된다.**

        그래서 하한을 `worker_job_timeout_floor_seconds` 로 둔다. 그 설정은 "주기가 짧은
        잡도 이만큼은 받아야 조기 취소되지 않는다"는 뜻이며, 새 문턱을 만들지 않고 그것을
        재사용한다.

        ## 이것이 보장하지 않는 것 (정직하게)

        훅 여럿이 동시에 느리면 합이 부모 예산을 넘어 뒷훅이 시작조차 못 하는 것은 여전하다.
        이 상한은 **하나가 전부를 먹는 것**만 막는다. 어느 훅이 실제로 느린지는 호스트
        트레이스(`job-trace.jsonl`)로 특정해야 하고, 그것이 3-3 의 남은 절반이다.

        **어느 경우에도 부모 예산을 넘기지 않는다** — 올리는 경로가 아니다(C4).
        """
        parent_budget = self._job_timeout_seconds(parent)
        share = max(1, int(_HOOK_SHARES.get(parent, 1)))
        floor = max(1, int(self.settings.worker_job_timeout_floor_seconds))
        return min(parent_budget, max(floor, parent_budget // share))

    async def _run_in_thread(self, name: str, func: Any, *args: Any, **kwargs: Any) -> Any:
        """잡을 알맞은 실행기의 스레드에서 돌린다 (Phase 2-3).

        무거운 잡은 전용 풀로 보내 기본 풀을 비워 둔다 — 표본을 만드는 잡(`universe_scan` ·
        `scout_scan` · `paper_engine` · `heartbeat`)의 슬롯을 지키는 것이 목적이다.

        ⚠️ **타임아웃은 스레드를 회수하지 못한다**(D5). `asyncio.wait_for` 는 이 코루틴을
        취소하지만 스레드 안의 동기 코드는 계속 돈다. 그래서 이 함수는 누수를 막지 못하고
        **누수의 범위를 전용 풀 안으로 제한**한다. 근본 수리는 동기 코드에 취소 지점을
        두는 것이며 별건이다.
        """
        loop = asyncio.get_running_loop()
        executor = self._heavy_executor if name in _HEAVY_JOBS else None
        return await loop.run_in_executor(executor, functools.partial(func, *args, **kwargs))

    def _trace_job(self, name: str, phase: str) -> None:
        """잡 시작·종료를 append-only 파일에 남긴다 (WO-FCE-WORKER-HANG-02 Phase 1-2).

        정체 구간에서 **마지막으로 시작됐으나 끝나지 않은 잡**이 곧 용의자다.
        `kill -9` 로 프로세스가 죽으면 메모리 상태는 사라지므로 즉시 디스크로 내린다.

        C3: 기록 실패가 잡 실행을 막으면 관측기가 장애 원인이 된다 — 조용히 넘기되 로그는 남긴다.
        """
        path = self._job_trace_path
        if path is None:
            return
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f'{{"at":"{datetime.now(timezone.utc).isoformat()}","job":"{name}","phase":"{phase}"}}\n')
        except OSError as exc:
            logger.debug("job trace write failed: %s", exc)

    async def _run_job(self, name: str, runner: JobRunner, *, scheduled: bool, max_seconds: int | None = None) -> Any:
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
            # WO-FCE-WORKER-HANG-02 Phase 1-2: 어느 잡이 도는 동안 하트비트가 멎었는지
            # 대조하려면 시작·종료 시각이 파일에 남아 있어야 한다. 프로세스가 kill -9 로
            # 죽으면 메모리 상태는 사라지므로 append-only 파일에 즉시 쓴다.
            self._trace_job(name, "start")
            heartbeat.status = "running"
            heartbeat.last_started_at = datetime.now(timezone.utc)
            heartbeat.next_run_at = self._next_run_at(name)
            self._persist(heartbeat)
            try:
                # C3: 무한 대기 금지. 잡마다 예산(주기 × 배수, 하한/상한 클램프)을 주고
                # 초과하면 취소한다. 취소해도 락은 `async with` 가 풀어주므로 다음 틱은 정상 실행된다.
                # 2026-07-28 사고 이전엔 manager 전체에 timeout 이 0건이었다.
                budget = self._job_timeout_seconds(name)
                if max_seconds is not None:
                    # **낮추기만 한다**(C4). 부모 예산을 나눈 값이며 올리는 경로가 아니다.
                    budget = min(budget, max(1, int(max_seconds)))
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
                self._trace_job(name, "ok")
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
        """포지션 동기화와 페이퍼 실행. **알림은 여기서 돌지 않는다**(ALERT-SILENCE-01 3-1).

        ## 왜 뗐나

        `27b6e11` 이 기록한 사고가 이 구조 때문이다:

        ```
        sync_positions ── 진입 알림 · 정기 펄스 · 구조 알림 · 무효화 경보
        ```

        **단일 실패점이었다.** 이 잡이 450초 타임아웃으로 죽으면 알림 전체가 같이 죽었고,
        그 전력이 3회 이상이다. 9/1 에는 15시간, 이번에는 8시간 43분 침묵했다.

        `ENGINE-LIVENESS-01` D1 이 훅 격리로 **한 훅의 예외**는 막았지만 **부모 잡의
        타임아웃**은 못 막는다 — 훅이 아무리 격리돼 있어도 부모가 취소되면 뒤쪽 훅은
        시작조차 못 한다. 그래서 알림을 별도 스케줄 잡으로 뺀다.

        동기화 결과는 여기서 **저장만** 하고, `_deliver_alerts` 가 그것을 읽는다.
        동기화가 죽어도 알림 잡은 돌고, 데이터가 낡았으면 **낡았다고 알린다.**
        """
        sync_job = self.jobs["sync_and_analyze"]
        payload = await self._run_hook("sync_and_analyze", sync_job.runner, parent="sync_positions") if sync_job.runner else None
        if not isinstance(payload, dict):
            payload = {"positions": [], "sync_failed": True}
        else:
            self._last_sync_at = datetime.now(timezone.utc)
        self._last_sync_payload = payload
        # **덮어쓰지 않고 쌓는다.** 알림 잡이 가져갈 때까지 남아 있어야 한다.
        self._queue_lifecycle(payload)
        await self._run_hook("detect_closures", lambda: asyncio.to_thread(service.detect_closures), parent="sync_positions")
        paper_result = await self._run_hook("paper_engine", lambda: asyncio.to_thread(service.run_paper_engine), parent="sync_positions")
        if isinstance(paper_result, dict):
            await self._send_paper_events(paper_result)
        return {
            "open_count": payload.get("open_count"),
            "needs_exit_record_count": payload.get("needs_exit_record_count"),
            "positions": len(payload.get("positions", [])),
            "created": payload.get("created"),
            "auto_closed": payload.get("auto_closed"),
        }

    def _alert_payload(self) -> dict[str, Any]:
        """알림 잡이 읽을 동기화 결과. **없거나 낡았으면 그 사실을 실어 보낸다.**

        침묵하지 않는 것이 요점이다 — 동기화가 죽었을 때 알림까지 멈추면 그 죽음을 알릴
        경로가 사라진다(3-1 항목 2).
        """
        payload = dict(self._last_sync_payload or {"positions": [], "sync_failed": True})
        if self._last_sync_at is None:
            payload["sync_age_seconds"] = None
            payload["sync_stale"] = True
            payload["sync_stale_note"] = "동기화 결과가 아직 없다"
            return payload
        age = (datetime.now(timezone.utc) - self._last_sync_at).total_seconds()
        # 동기화 주기의 3배를 넘기면 낡은 것으로 본다. `worker_liveness_stale_multiplier`
        # 와 같은 배수다 — 문턱을 새로 만들지 않는다.
        limit = float(self.settings.worker_sync_positions_interval_seconds) * max(2, int(self.settings.worker_liveness_stale_multiplier))
        payload["sync_age_seconds"] = round(age, 1)
        payload["sync_stale"] = age > limit
        if payload["sync_stale"]:
            payload["sync_stale_note"] = f"포지션 동기화가 {age / 60:.0f}분째 갱신되지 않았다 — 아래 값은 그 시점 기준이다"
        return payload

    async def _deliver_alerts(self) -> dict[str, Any]:
        """알림 전용 잡 (ALERT-SILENCE-01 3-1). **동기화 실패와 독립이다.**

        `sync_positions` 가 타임아웃으로 죽어도 이 잡은 돈다. 읽는 데이터가 낡았으면
        낡았다고 알리고, 조용해지지 않는다.

        훅 목록과 순서는 그대로다 — 옮긴 것이지 바꾼 것이 아니다(C3).
        """
        payload = self._alert_payload()
        # 진입·청산은 **큐에서 꺼낸다.** 페이로드에서 읽으면 다음 동기화가 덮어써 사라진다.
        created, closed = self._drain_lifecycle()
        lifecycle = {**payload, "created_position_ids": created, "closed_positions": closed}
        # WO-44: 진입/종료/판정 전이 — 라이프사이클이 1차 정보이므로 조건 알림보다 먼저.
        delivered = await self._run_hook("evaluate_lifecycle", lambda: self.alerts.evaluate_lifecycle(lifecycle), parent="deliver_alerts")
        if delivered is None and (created or closed):
            # 훅이 죽었다. 큐를 이미 비웠으므로 되돌리지 않으면 그 진입은 영영 사라진다.
            self._requeue_lifecycle(created, closed)
        await self._run_hook(
            "evaluate_alerts",
            lambda: self.alerts.evaluate_positions(payload.get("positions", [])),
            parent="deliver_alerts",
        )
        # WO-FCE-STRUCTURE-CONTEXT-01: 보유 포지션의 구조 관계 전이(레인지 이탈·OB 진입·국면 전환).
        await self._run_hook(
            "evaluate_structure_context",
            lambda: self.alerts.evaluate_position_structure(payload.get("positions", [])),
            parent="deliver_alerts",
        )
        await self._run_hook(
            "evaluate_performance_alerts",
            lambda: self._evaluate_performance_alerts(),
            parent="deliver_alerts",
        )
        await self._run_hook("periodic_pulse", lambda: self.alerts.maybe_send_pulse(payload), parent="deliver_alerts")
        # 작업 5: 트랙 생존 라인 동봉 — 진입이 0이어도 "살아있음"이 매일 도착해야 한다(뮤트 관통).
        await self._run_hook(
            "daily_summary",
            lambda: self.alerts.maybe_send_daily_summary(payload, self._liveness_lines(), self._performance_lines()),
            parent="deliver_alerts",
        )
        return {
            "sync_stale": bool(payload.get("sync_stale")),
            "sync_age_seconds": payload.get("sync_age_seconds"),
            "lifecycle_delivered": len(created) + len(closed),
            "lifecycle_pending": len(self._pending_created) + len(self._pending_closed),
            "lifecycle_dropped": self._lifecycle_dropped,
        }

    def _queue_lifecycle(self, payload: dict[str, Any]) -> None:
        """진입·청산을 대기 큐에 **쌓는다** (덮어쓰지 않는다).

        ## 왜 큐인가

        `_last_sync_payload` 에 얹어 두면 다음 동기화가 통째로 교체한다. 두 잡이 같은 주기로
        돌고 순서 보장이 없으므로, 알림 잡이 한 틱만 늦으면(락 점유·타임아웃) 그 사이의
        진입이 **영영 사라진다.** 실측: 동기화 2회 사이에 알림 1회가 빠지면 첫 진입이 소멸.

        게다가 동기화는 대부분 **빈 목록**을 반환한다 — 진입이 없는 주기가 정상이기 때문이다.
        그래서 빈 목록이 직전 진입을 지우는 것이 예외가 아니라 흔한 경로였다.

        중복은 id 로 거른다. 같은 진입이 두 번 큐에 들어가면 알림이 두 번 나간다.
        """
        created = [str(item) for item in (payload.get("created_position_ids") or [])]
        known = set(self._pending_created)
        self._pending_created.extend(item for item in created if item not in known and not known.add(item))
        for item in payload.get("closed_positions") or []:
            if isinstance(item, dict):
                self._pending_closed.append(item)
        # 알림이 며칠 죽어 있어도 메모리가 무한히 늘면 안 된다. **버린 수를 센다** —
        # 조용히 버리는 것이 이 WO 가 고치는 결함 그 자체다.
        for queue in (self._pending_created, self._pending_closed):
            overflow = len(queue) - _LIFECYCLE_QUEUE_MAX
            if overflow > 0:
                del queue[:overflow]
                self._lifecycle_dropped += overflow
                logger.warning("lifecycle queue overflow: dropped %s (total %s)", overflow, self._lifecycle_dropped)

    def _drain_lifecycle(self) -> tuple[list[str], list[dict[str, Any]]]:
        """큐를 비우고 내용을 돌려준다. **가져간 것만** 비운다 — 그 사이 들어온 것은 남는다."""
        created, closed = self._pending_created, self._pending_closed
        self._pending_created, self._pending_closed = [], []
        return created, closed

    def _requeue_lifecycle(self, created: list[str], closed: list[dict[str, Any]]) -> None:
        """전달에 실패한 사건을 큐 **앞으로** 되돌린다.

        훅이 예외로 죽으면 `_run_hook` 이 `None` 을 준다. 그때 큐를 이미 비웠다면 그 진입은
        사라진다 — 되돌리지 않으면 이 수리가 새 손실을 만든다.
        """
        self._pending_created = created + [item for item in self._pending_created if item not in set(created)]
        self._pending_closed = closed + self._pending_closed

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

    async def _verdict_transition_watch(self) -> dict[str, Any]:
        """완주 판정이 바뀌면 1건 알린다 (WO-FCE-VALIDATION-VERDICT-01 Phase 1-3).

        상시 보고는 주간 리포트가 담당한다. 이 잡은 **변화**만 담당하므로 판정이 유지되는
        동안에는 0건이다.
        """
        sent = await self.alerts.maybe_send_verdict_transition()
        return {"count": sent}

    async def _collect_derivatives(self) -> dict[str, Any]:
        payload = await self._run_in_thread("collect_derivatives", service.refresh_derivative_data)
        snapshots = payload.get("snapshots", [])
        if isinstance(snapshots, list):
            await self.alerts.evaluate_derivatives(snapshots)
        return payload

    async def _collect_whales(self) -> dict[str, Any]:
        payload = await asyncio.to_thread(service.collect_whales)
        dashboard = await asyncio.to_thread(service.whale_dashboard)
        await self.alerts.evaluate_whale_events(payload.get("events", []), dashboard)
        # WO-FCE-WHALE-FOLLOW-02 7-2 — **체결 감지 즉시 추종 판정.**
        #
        # 이 잡은 30초마다 돈다. 추종 잡은 15분 주기이므로 주기 잡만 두면 그 15분이 곧
        # 지연의 바닥이 되고, "고래가 들어간 가격 근처"라는 규칙이 성립하지 않는다.
        # 자격 지갑의 진입 체결이 실제로 들어왔을 때만 깨운다 — 조건 없이 깨우면 30초마다
        # 분석 조회가 도는 것이고 그것이 예산 사고다(C9).
        try:
            fresh = await asyncio.to_thread(service.whale_follow_has_fresh_signal, payload.get("events", []))
        except Exception as exc:  # 추종 트리거 실패가 수집 잡을 죽이면 안 된다
            payload["whale_follow_trigger_error"] = f"{type(exc).__name__}: {exc}"
            return payload
        payload["whale_follow_triggered"] = self._dispatch_whale_follow() if fresh else False
        return payload

    def _dispatch_whale_follow(self) -> bool:
        """추종 실행을 **기다리지 않고** 띄운다. 이미 돌고 있으면 띄우지 않는다.

        수집 잡을 블로킹하면 추종 엔진의 조회 시간이 수집 잡 예산을 먹는다 — 30초 주기 잡의
        예산은 150초이고 분석 조회 3건이 그것을 넘길 수 있다. 그 형태가 정확히
        `DISCOVERY-UNBLOCK-01` 의 라이브 장애 기전이었다.

        중복 실행을 막는 것도 같은 이유다. 30초마다 체결이 오면 추종 실행이 겹쳐 쌓이고,
        겹친 만큼 분석 조회가 곱해진다.
        """
        if self._whale_follow_task is not None and not self._whale_follow_task.done():
            return False
        self._whale_follow_task = asyncio.create_task(self._run_whale_follow_on_fill())
        # 예외를 삼키지 않는다 — 조용히 죽으면 "체결은 오는데 진입이 없다"가 원인 미상이 된다.
        self._whale_follow_task.add_done_callback(self._log_whale_follow_result)
        return True

    def _log_whale_follow_result(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning("whale_follow event-driven run failed: %s: %s", type(error).__name__, error)

    async def _run_whale_follow(self, trigger: str = "scheduled") -> dict[str, Any]:
        """추종 트랙 1회. 알림 후보는 **기존 통합 관문**으로 넘긴다 — 우회 경로를 만들지 않는다.

        주기 실행 경로다. 잡 잠금은 `_run_job` 이 이미 잡고 있으므로 여기서 다시 잡지 않는다.
        """
        payload = await self._run_in_thread("whale_follow_engine", service.run_whale_follow_engine, trigger)
        candidates = payload.get("_alert_candidate_objects", [])
        if candidates:
            await self.alerts.evaluate_scout_setups(candidates)
        payload.pop("_alert_candidate_objects", None)
        return payload

    async def _run_whale_follow_on_fill(self) -> dict[str, Any]:
        """체결 구동 실행. 주기 실행과 **같은 잡 잠금**을 잡는다.

        겹치면 분석 조회가 곱해져 예산이 두 배가 된다(C9). 주기 틱이 돌고 있으면 이번
        체결은 그 실행이 함께 처리하므로 건너뛴다 — 신호는 원장에 남아 있고 사라지지 않는다.
        """
        lock = self._locks["whale_follow_engine"]
        if lock.locked():
            return {"skipped": True, "trigger": "whale_fill", "reason": "추종 실행이 이미 돌고 있다 — 겹치면 분석 조회가 곱해진다"}
        async with lock:
            return await self._run_whale_follow(trigger="whale_fill")

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
        paper = await self._run_in_thread("toss_stock_scout", run_stock_paper_engine, self.settings, {"KR": kr, "US": us})
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
            # ALERT-SILENCE-01 3-1 — **알림은 독립 잡이다.**
            #
            # `sync_positions` 안에서 돌던 구조가 단일 실패점이었다. 그 잡이 450초
            # 타임아웃으로 죽으면 진입 알림·펄스·구조 알림이 통째로 죽었고, 그 전력이
            # 3회 이상이다(9/1 15시간 · 이번 8시간 43분).
            #
            # 같은 주기로 돌되 **부모가 없다.** 동기화가 죽어도 이 잡은 산다.
            "deliver_alerts": WorkerJob(
                "deliver_alerts",
                self.settings.worker_sync_positions_interval_seconds,
                self._deliver_alerts,
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
                lambda: self._run_in_thread("discover_whale_leaderboard", service.discover_whales),
                enabled=self.settings.hyperliquid_whale_discovery_enabled,
            ),
            "collect_whale_positions": WorkerJob(
                "collect_whale_positions",
                self.settings.hyperliquid_whale_poll_interval_seconds,
                self._collect_whales,
                enabled=self.settings.hyperliquid_whale_tracking_enabled,
            ),
            # WHALE-FOLLOW-01 6-2: 추종 트랙. 진입과 출구를 같은 잡에서 돌린다 —
            # 한쪽만 돌면 진입만 쌓이고 표본이 0 이 된다.
            "whale_follow_engine": WorkerJob(
                "whale_follow_engine",
                self.settings.whale_follow_interval_seconds,
                self._run_whale_follow,
                enabled=self.settings.whale_follow_track_enabled,
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
            # WO-FCE-VALIDATION-VERDICT-01 Phase 1-3: 판정 전이 감시. 관측 커버리지가
            # 1시간 주기로 갱신되므로 같은 주기로 본다 — 더 자주 봐도 판정이 안 바뀐다.
            "verdict_transition_watch": WorkerJob(
                "verdict_transition_watch",
                3600,
                self._verdict_transition_watch,
            ),
            "refresh_calibration_cache": WorkerJob(
                "refresh_calibration_cache",
                self.settings.worker_calibration_cache_interval_seconds,
                lambda: self._run_in_thread("refresh_calibration_cache", service.refresh_calibration_report_cache),
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
            # WO-FCE-REPLAY-DEPTH-01 4-2. 기본 꺼짐이며 저장만 한다.
            "replay_history_backfill": WorkerJob(
                "replay_history_backfill",
                self.settings.replay_history_backfill_interval_seconds,
                lambda: self._run_in_thread("replay_history_backfill", service.replay_history_backfill),
                enabled=self.settings.replay_history_backfill_enabled,
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
        from app.validation import window_anchor
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
            # 검증 창 앵커를 같은 연결에서 읽는다 — 표면마다 다른 창을 보면 D3 이 재발한다.
            anchors = {track: window_anchor.current_anchor(connection, track) for track in observation.TRACK_SPECS}
        clocks = {
            track: observation.verification_clock(
                [row for row in rows if row["track"] == track],
                anchor_day=anchors[track].anchor_day if anchors.get(track) else None,
            )
            for track in observation.TRACK_SPECS
        }
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

    def _on_job_missed(self, event: Any) -> None:
        """스케줄러가 발화를 건너뛴 사실을 잡별로 기록한다 (Phase 2-2 · C8).

        `skipped`(이전 틱이 아직 도는 중)와 **분리해서** 센다. 원인이 다르고 처방이 다르다:
        `skipped` 는 잡이 느린 것이고, `misfired` 는 스케줄러가 아예 실행하지 않은 것이다.
        """
        name = str(getattr(event, "job_id", "") or "")
        heartbeat = self.heartbeats.get(name)
        if heartbeat is None:
            return
        heartbeat.misfired += 1
        heartbeat.last_misfire_at = datetime.now(timezone.utc)
        self._persist(heartbeat)
        logger.warning(
            "worker.%s misfired total=%s grace=%ss scheduled_for=%s",
            name,
            heartbeat.misfired,
            heartbeat.misfire_grace_seconds,
            getattr(event, "scheduled_run_time", None),
        )

    def _schedule_job(self, name: str, interval_seconds: int, next_run_time: datetime | None = None) -> None:
        interval = max(1, int(interval_seconds))
        grace = _misfire_grace_seconds(name, interval)
        self.scheduler.add_job(
            self._run_scheduled_job,
            trigger=IntervalTrigger(seconds=interval, timezone=timezone.utc),
            args=[name],
            id=name,
            coalesce=True,
            max_instances=1,
            replace_existing=True,
            next_run_time=next_run_time,
            # Phase 2-2: 기본 1초로 두면 루프 지연 앞에서 발화가 소실된다.
            misfire_grace_time=grace,
        )
        heartbeat = self.heartbeats[name]
        heartbeat.misfire_grace_seconds = grace
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
            "verdict_transition_watch": 22,
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
