"""2026-09-01 알림 침묵 회귀 — 굶은 잡이 화면에 도달하는지 고정한다.

## 무엇이 있었나

`sync_positions` 가 450초 타임아웃 3연속으로 죽었다. 알림 훅은 전부 `scheduled=False` 이고
그 잡 **안에서만** 호출되므로 진입 알림·펄스·구조 알림이 함께 죽었다.

시스템은 알고 있었다 — `job_starvation` 이 굶음을 잡았다. 그런데 사용자에게 도달하는 경로가
둘 다 막혀 있었다:

- **푸시**: `job_backoff_stuck`·`data_stall`·`process_restarted` 는 `LIVENESS_DEMOTED_RULES`
  로 강등됐다 (사용자 지시 2026-08-16). 이것은 결함이 아니라 설계다.
- **화면**: 대시보드가 `job_starvation` 을 **읽지 않았다.**

그래서 15시간 동안 상단 바는 "워커 점검 · 마지막 sync -" 한 줄이었고, 그 사이 열린 포지션
2건(BTCUSDT·ETHUSDT)의 진입 알림이 통째로 누락됐다.

**푸시를 막았으면 화면이 유일한 통로다.** 그 통로가 없으면 침묵이 정상으로 위장된다.
"""

from __future__ import annotations

import pathlib

from app.notify import delivery_gate
from app.worker import liveness

_TOP_BAR = pathlib.Path(__file__).resolve().parents[2] / "dashboard/components/terminal/TerminalTopBar.tsx"
_SHELL = pathlib.Path(__file__).resolve().parents[2] / "dashboard/components/terminal/TerminalShell.tsx"


def test_error_status_jobs_are_not_exempt_from_starvation() -> None:
    """죽은 잡이 기아 판정에서 빠지면 감시망에 구멍이 난다."""
    assert "error" not in liveness._NON_STARVING_STATUSES


def test_a_dead_job_is_reported_as_starved() -> None:
    """`sync_positions` 가 죽었던 그 모양 — runs=0, 성공 이력 없음, 재예약 없음."""
    jobs = {
        "sync_positions": {
            "status": "error",
            "runs": 0,
            "base_interval_seconds": 90,
            "last_success_at": None,
            "last_effective_run_at": None,
            "next_run_at": None,
            "consecutive_failures": 3,
        }
    }
    result = liveness.job_starvation(jobs)
    assert "sync_positions" in result["starved"]


def test_liveness_push_demotion_is_intact() -> None:
    """푸시 강등은 사용자 지시다(2026-08-16). 화면을 고쳐도 이걸 되돌리지 않는다."""
    for rule in ("job_backoff_stuck", "data_stall", "process_restarted", "infra_capacity"):
        assert not delivery_gate.evaluate_rule(rule).allowed


def test_position_opened_still_pushes() -> None:
    """진입 알림은 강등 대상이 아니다 — 이것까지 막히면 트랙이 무의미하다."""
    assert delivery_gate.evaluate_rule("position_opened").allowed


def test_dashboard_reads_job_starvation() -> None:
    """푸시가 막힌 신호는 **화면이 유일한 통로다.** 화면이 안 읽으면 침묵이다."""
    assert "job_starvation" in _SHELL.read_text()
    assert "job_starvation" in _TOP_BAR.read_text()


def test_starved_jobs_break_the_worker_ok_label() -> None:
    """`status: running` 은 스케줄러 생존일 뿐이다. 굶은 잡이 있으면 정상이라고 쓰지 않는다."""
    body = _TOP_BAR.read_text()
    assert "starved === 0" in body
    assert "잡 굶음" in body


def test_null_last_success_is_not_flattened_to_a_dash() -> None:
    """`-` 는 "값 없음"으로 읽힌다. "한 번도 성공한 적 없음"은 다른 사실이다."""
    assert "성공 이력 없음" in _TOP_BAR.read_text()


def test_alert_hooks_no_longer_live_inside_sync_positions() -> None:
    """**그 결합이 단일 실패점이었고, 이제 없다** (WO-FCE-ALERT-SILENCE-01 3-1).

    이 회귀는 방향이 뒤집혔다. 예전에는 "알림이 `sync_positions` 안에 있다"를 고정했다 —
    구조가 바뀌면 알리기 위한 감시였다. 구조가 바뀌었으므로 이제 **되돌아가지 않는 것**을
    고정한다. 알림이 다시 그 잡으로 들어오면 같은 침묵이 반복된다.
    """
    manager_src = (pathlib.Path(__file__).resolve().parents[1] / "app/worker/manager.py").read_text()
    body = manager_src.split("async def _sync_positions")[1].split("\n    def _alert_payload")[0]
    for hook in ("evaluate_lifecycle", "evaluate_alerts", "periodic_pulse"):
        assert f'"{hook}"' not in body, f"{hook} 가 다시 _sync_positions 안으로 들어왔다 — 단일 실패점이 되살아난다"
    assert '"deliver_alerts": WorkerJob(' in manager_src, "알림 전용 잡이 사라졌다"


def test_worker_is_not_called_normal_without_a_single_sync_success() -> None:
    """ "워커 정상 · 마지막 sync 성공 이력 없음" 은 모순이다.

    포지션 동기화가 한 번도 성공하지 않았으면 원장이 비어 있고 알림도 나갈 수 없다.
    그것을 "정상" 으로 쓰면 화면이 다시 거짓말한다.
    """
    body = _TOP_BAR.read_text()
    assert "sync 첫 실행 대기" in body
    assert "!syncJob.last_success_at" in body
