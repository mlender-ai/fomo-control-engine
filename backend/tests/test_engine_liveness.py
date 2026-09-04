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
            "paper_engine": _job(effective_age_s=60),
            "polymarket_paper": _job(effective_age_s=60, interval=60),
            "sync_positions": _job(effective_age_s=60),
        }
    }
    # 주식은 KR·US 를 한 잡이 담당하므로 잡 하트비트가 아니라 시장별 평가 흔적으로 판정한다.
    market_data = {"stock_kr": (NOW - timedelta(days=4)).isoformat(), "stock_us": None}
    rows = {row["job"]: row for row in liveness.track_liveness(status, _settings(tmp_path), NOW, market_data)}

    assert rows["stock_kr"]["stale"] is True  # KR 장중인데 4일 정지
    assert rows["paper_engine"]["stale"] is False
    assert rows["polymarket_paper"]["stale"] is False

    candidates = liveness.evaluate_liveness(status, _settings(tmp_path), NOW, market_data)
    stale = [c for c in candidates if c.payload.get("kind") == "track_stale"]
    assert [c.identity for c in stale] == ["stock_kr"]
    assert stale[0].severity == "critical"


def test_three_paper_tracks_are_all_monitored() -> None:
    """D3: 감시 대상이 sync_positions 단독이었다 — 3트랙 전부 포함되어야 한다.

    주식은 KR·US 가 한 잡에 묶여 있어 시장 단위 가상 트랙으로 분리 감시한다
    (WO-FCE-PAPER-ENTRY-REALITY-01: 미장 침묵이 구조적으로 관측 불가였던 결함 수리).
    """
    assert {"paper_engine", "polymarket_paper"}.issubset(set(liveness.TRACKED_JOBS))
    assert {"stock_kr", "stock_us"}.issubset(set(liveness.MARKET_DATA_TRACKS))


def test_disabled_track_is_not_reported_as_dead(tmp_path) -> None:
    status = {"jobs": {"polymarket_paper": {"status": "disabled"}}}
    rows = {row["job"]: row for row in liveness.track_liveness(status, _settings(tmp_path), NOW)}
    assert rows["polymarket_paper"]["state"] == "disabled"
    assert rows["polymarket_paper"]["stale"] is False


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
    """생존·사망 신호는 **더 이상 푸시되지 않는다** (사용자 지시 2026-08-16).

    이 테스트는 원래 "뮤트를 관통해 사망 알림이 도착한다"를 고정했다. 그 설계의 근거는
    "침묵이 스스로를 은폐한다"였고 당시엔 옳았다. 그러나 실측 156회의 사망 푸시 중
    사용자 조치로 이어진 것이 없었고, 사용자가 명시적으로 중단을 지시했다:
    "죽은건 보내봤자 내가 그걸 보고 어떠한 인사이트도 얻을 수가 없잖아."

    은폐 방지는 발송이 아니라 **조회**로 담보한다 — 진단 API·로그·덤프.
    이 테스트는 이제 "생존 신호가 발송되지 않는다"를 고정한다.
    """
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
    candidates = liveness.evaluate_liveness({}, settings, NOW, {"stock_kr": (NOW - timedelta(days=4)).isoformat()})

    delivered = await engine.evaluate_liveness_alerts(candidates)

    assert candidates, "감시 자체는 계속 후보를 만들어야 한다 — 강등이지 삭제가 아니다"
    assert delivered == 0, "생존·사망 신호가 다시 발송된다"
    assert sent == []


@pytest.mark.asyncio
async def test_daily_summary_is_silent_when_muted(tmp_path) -> None:
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
    local = datetime(2026, 7, 27, hour, minute, tzinfo=ZoneInfo(settings.telegram_local_timezone))
    setattr(engine, "_now", lambda: local.astimezone(timezone.utc))

    count = await engine.maybe_send_daily_summary({"positions": []}, ["<b>트랙 생존</b>", "• 크립토 페이퍼: 🟢 정상"])

    # 뮤트 중에는 아무것도 보내지 않는다. 예전에는 생존 라인만 관통시켰는데,
    # 그것이 정확히 사용자가 없애달라고 한 신호다(2026-08-16).
    assert count == 0
    assert sent == []


# ── D1: 단일 실패점 제거 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_failure_does_not_kill_paper_or_alerts(tmp_path) -> None:
    """수집이 던져도 페이퍼·알림은 계속 돈다.

    ## 구조가 바뀌었다 (WO-FCE-ALERT-SILENCE-01 3-1)

    `ENGINE-LIVENESS-01` D1 은 훅 격리로 **한 훅의 예외**를 막았다. 그러나 알림이 여전히
    `sync_positions` **안에서** 돌았기 때문에 **부모 잡의 타임아웃**은 못 막았다 — 훅이
    아무리 격리돼 있어도 부모가 취소되면 뒤쪽 훅은 시작조차 못 한다. 실제로 그렇게
    3회 이상 죽었다(9/1 15시간 · 8시간 43분 침묵).

    이제 알림은 **독립 잡**이다. 그래서 이 회귀도 두 잡을 각각 돌려 확인한다 — 예외뿐
    아니라 **부모가 통째로 사라져도** 알림이 사는 것이 요점이다.
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
    # …같은 잡의 나머지 단계는 계속 실행됐다.
    for name in ("detect_closures", "paper_engine"):
        assert manager.heartbeats[name].status == "ok", f"{name} 이 수집 실패에 함께 죽었다"
    assert isinstance(result, dict)

    # **알림 잡은 별도로 돈다.** 수집이 죽어도, 아예 호출되지 않아도 산다.
    await manager._deliver_alerts()
    for name in ("evaluate_lifecycle", "evaluate_alerts", "evaluate_performance_alerts", "periodic_pulse", "daily_summary"):
        assert manager.heartbeats[name].status == "ok", f"{name} 이 수집 실패에 함께 죽었다"
    assert manager.heartbeats["periodic_pulse"].runs >= 1, "생존 펄스가 데이터 수집 실패에 종속되면 안 된다"


@pytest.mark.asyncio
async def test_alerts_run_even_if_sync_never_ran_at_all(tmp_path) -> None:
    """**부모 잡이 통째로 사라진 경우.** 타임아웃으로 취소되면 이 상태가 된다.

    `_sync_positions` 를 한 번도 부르지 않고 알림 잡만 돌린다 — 예전 구조에서는
    불가능했고, 그 불가능이 8시간 43분 침묵의 형태였다.
    """
    repo = MemoryRepository()
    configure_runtime(repo=repo, provider=MockMarketDataProvider())
    manager = WorkerManager(_settings(tmp_path, telegram_alerts_enabled=False))

    await manager._deliver_alerts()

    assert manager.heartbeats["periodic_pulse"].status == "ok"
    assert manager.heartbeats["evaluate_lifecycle"].status == "ok"


# ── 오탐 방지: 장 마감 중 "정지" 알림 금지 ──────────────────────────


def test_market_session_gate_prevents_false_alarm_at_night(tmp_path) -> None:
    """장 마감 시간에 주식 트랙 정지 알림을 쏘면 매일 밤 오탐 → 사용자 뮤트 → 침묵 재발.

    WO 금지 항목(알림 스팸의 악순환)을 구조적으로 차단한다.
    """
    # 20:00 KST = 07:00 ET — KR·US 둘 다 닫힌 시각(23:30 KST 는 미국 장중이라 US 경보가 정상 발화한다).
    night = datetime(2026, 7, 27, 11, 0, tzinfo=timezone.utc)
    weekend = datetime(2026, 7, 25, 2, 0, tzinfo=timezone.utc)  # 토 11:00 KST
    assert liveness.market_session_active("KR", NOW) is True
    assert liveness.market_session_active("KR", night) is False
    assert liveness.market_session_active("KR", weekend) is False
    assert liveness.market_session_active(None, night) is True  # 코인은 24/7

    market_data = {"stock_kr": (night - timedelta(hours=6)).isoformat(), "stock_us": (night - timedelta(hours=6)).isoformat()}
    rows = {row["job"]: row for row in liveness.track_liveness({}, _settings(tmp_path), night, market_data)}
    assert rows["stock_kr"]["state"] == "market_closed"
    assert rows["stock_kr"]["stale"] is False
    assert liveness.evaluate_liveness({}, _settings(tmp_path), night, market_data) == []

    # 같은 정체 상태라도 장중이면 반드시 잡는다.
    stale_kr = {"stock_kr": (NOW - timedelta(hours=6)).isoformat()}
    assert any(c.rule_id == "engine_liveness" for c in liveness.evaluate_liveness({}, _settings(tmp_path), NOW, stale_kr))
