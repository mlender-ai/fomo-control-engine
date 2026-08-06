"""WO-FCE-VALIDATION-VERDICT-01 Phase 2 — 절전 방지 자가 점검 회귀.

배경: 맥 절전이 US 정규장 후반을 반복 삭제했다. 설정을 껐어도 OS 업데이트·재부팅·배터리
전환이 되돌릴 수 있고, 그러면 **아무 소리 없이 손실이 다시 시작된다.**

> 감지 없는 조치는 조치가 아니다.

고정하는 명제:
1. 설정과 caffeinate 를 **둘 다** 본다 — 하나만 살아 있어도 안 잔다
2. 둘 다 없을 때만 "수동 조치 필요"를 올린다
3. macOS 가 아니면 판정하지 않는다 (CI·컨테이너에서 가짜 경고가 진짜 신호를 묻는다)
4. 외부 명령 실패가 워커를 죽이지 않는다
"""

from __future__ import annotations

from app.worker import sleep_guard

PMSET_GUARDED = """System-wide power settings:
Currently in use:
 standby              0
 powernap             0
 sleep                0
 disksleep            0
"""

PMSET_SLEEPING = """System-wide power settings:
Currently in use:
 standby              1
 powernap             1
 sleep                10 (sleep prevented by coreaudiod)
 disksleep            10
"""

ASSERTIONS_ACTIVE = """Assertion status system-wide:
   PreventUserIdleSystemSleep     1
   PreventUserIdleDisplaySleep    0
"""

ASSERTIONS_IDLE = """Assertion status system-wide:
   PreventUserIdleSystemSleep     0
   PreventUserIdleDisplaySleep    0
"""


def _runner(power: str | None, assertions: str | None):
    def run(command: list[str]) -> str | None:
        return assertions if "assertions" in command else power

    return run


# ── 1. 두 근거를 함께 본다 ──────────────────────────────────────────────


def test_settings_alone_are_enough() -> None:
    """caffeinate 가 죽어도 유휴 절전이 꺼져 있으면 안 잔다."""
    status = sleep_guard.sleep_guard_status(runner=_runner(PMSET_GUARDED, ASSERTIONS_IDLE), system="Darwin")

    assert status["guarded"] is True
    assert status["settings_guarded"] is True
    assert status["caffeinate_active"] is False


def test_caffeinate_alone_is_enough() -> None:
    """설정이 켜져 있어도 caffeinate 가 잡고 있으면 안 잔다."""
    status = sleep_guard.sleep_guard_status(runner=_runner(PMSET_SLEEPING, ASSERTIONS_ACTIVE), system="Darwin")

    assert status["guarded"] is True
    assert status["settings_guarded"] is False
    assert status["caffeinate_active"] is True


def test_neither_guard_present_is_the_only_danger() -> None:
    status = sleep_guard.sleep_guard_status(runner=_runner(PMSET_SLEEPING, ASSERTIONS_IDLE), system="Darwin")

    assert status["guarded"] is False
    assert set(status["sleeping_axes"]) == {"sleep", "disksleep", "standby", "powernap"}
    assert "다시 끊긴다" in status["reason"]


# ── 2. 조치 항목 ────────────────────────────────────────────────────────


def test_action_item_is_raised_when_unguarded() -> None:
    """조용히 유실로만 세면 영원히 안 고쳐진다 — 상시 노출한다."""
    status = sleep_guard.sleep_guard_status(runner=_runner(PMSET_SLEEPING, ASSERTIONS_IDLE), system="Darwin")

    item = sleep_guard.guard_action_item(status)

    assert item is not None
    assert item["severity"] == "action_required"
    assert "pmset" in item["remedy"] and "caffeinate" in item["remedy"]


def test_no_action_item_while_guarded() -> None:
    """적용 중이면 조용한 것이 정상이다."""
    status = sleep_guard.sleep_guard_status(runner=_runner(PMSET_GUARDED, ASSERTIONS_ACTIVE), system="Darwin")

    assert sleep_guard.guard_action_item(status) is None


# ── 3. 판정하지 않아야 할 때 ────────────────────────────────────────────


def test_non_macos_is_not_judged() -> None:
    """리눅스에서 가짜 경고를 올리면 진짜 신호가 묻힌다."""
    status = sleep_guard.sleep_guard_status(system="Linux")

    assert status["available"] is False
    assert status["guarded"] is None
    assert sleep_guard.guard_action_item(status) is None


def test_command_failure_is_reported_not_raised() -> None:
    """점검이 워커를 죽이면 감시가 사고 원인이 된다."""
    status = sleep_guard.sleep_guard_status(runner=_runner(None, None), system="Darwin")

    assert status["available"] is False
    assert "확인할 수 없다" in status["reason"]
    assert sleep_guard.guard_action_item(status) is None


def test_external_calls_have_a_timeout_budget() -> None:
    """타임아웃 없는 대기는 장애다 (docs/EngineLiveness.md)."""
    assert 0 < sleep_guard.COMMAND_TIMEOUT_SECONDS <= 10
