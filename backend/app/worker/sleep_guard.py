"""WO-FCE-VALIDATION-VERDICT-01 Phase 2 — 절전 방지 자가 점검.

## 왜 필요한가

맥 절전이 US 정규장 후반(02:00~05:00 KST)을 반복 삭제했다. `pmset` 으로 절전을 끄고
`caffeinate` 를 띄우면 멈추지만, **그 설정이 풀리면 아무 소리 없이 손실이 다시 시작된다.**

조치를 적용했다는 사실보다 **조치가 지금도 살아 있다는 사실**이 중요하다. OS 업데이트,
재부팅, 배터리 전환 어느 것이든 설정을 되돌릴 수 있고, 그때 우리는 알 수 없다.

> 감지 없는 조치는 조치가 아니다.

## 어떻게 재나

`pmset -g`(현재 전원 설정)와 `pmset -g assertions`(살아 있는 wake lock)를 읽는다.
둘 다 필요하다 — 설정이 꺼져 있어도 `caffeinate` 가 붙잡고 있으면 안 자고, 반대로
`caffeinate` 가 죽어도 설정이 살아 있으면 안 잔다. **둘 다 없을 때만** 위험이다.

macOS 가 아니면 판정하지 않는다(`available=False`). 리눅스 컨테이너·CI 에서 이 점검이
실패로 잡히면 진짜 신호가 묻힌다.

## 타임아웃

외부 프로세스 호출에는 반드시 타임아웃을 건다 — 타임아웃 없는 대기는 장애다
(`docs/EngineLiveness.md`). 이 점검이 워커를 매달면 감시가 사고 원인이 된다.
"""

from __future__ import annotations

import platform
import re
import subprocess
from typing import Any, Callable

# 외부 명령 예산. pmset 은 즉시 반환하는 로컬 조회이므로 짧게 잡는다.
COMMAND_TIMEOUT_SECONDS = 5.0

# 이 값들이 0이어야 유휴 절전이 꺼진 것이다.
_SLEEP_KEYS = ("sleep", "disksleep", "standby", "powernap")
# `caffeinate -dimsu` 가 잡고 있으면 여기 1로 뜬다.
_ASSERTION_KEY = "PreventUserIdleSystemSleep"

Runner = Callable[[list[str]], str | None]


def _run(command: list[str]) -> str | None:
    """외부 명령 1회. 실패·부재·타임아웃은 전부 None — 점검이 워커를 죽이지 않는다."""
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _parse_power_settings(text: str) -> dict[str, int]:
    """`pmset -g` 출력에서 절전 관련 값만 뽑는다."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in _SLEEP_KEYS:
            match = re.match(r"^\d+", parts[1])
            if match:
                values[parts[0]] = int(match.group())
    return values


def _parse_assertions(text: str) -> bool:
    for line in text.splitlines():
        if _ASSERTION_KEY in line:
            match = re.search(rf"{_ASSERTION_KEY}\s+(\d+)", line)
            if match:
                return int(match.group(1)) > 0
    return False


def sleep_guard_status(*, runner: Runner | None = None, system: str | None = None) -> dict[str, Any]:
    """절전 방지가 지금 살아 있는가.

    `runner` 를 주입할 수 있게 둔 것은 테스트 때문이다 — 실제 `pmset` 없이 판정 로직을
    고정할 수 있어야 회귀가 성립한다.
    """
    system = system or platform.system()
    if system != "Darwin":
        return {
            "available": False,
            "reason": f"macOS 전용 점검 (현재 {system})",
            "guarded": None,
            "settings": {},
            "caffeinate_active": None,
        }
    run = runner or _run
    power_text = run(["pmset", "-g"])
    assertion_text = run(["pmset", "-g", "assertions"])
    if power_text is None and assertion_text is None:
        return {
            "available": False,
            "reason": "pmset 실행 실패 — 절전 상태를 확인할 수 없다",
            "guarded": None,
            "settings": {},
            "caffeinate_active": None,
        }
    settings = _parse_power_settings(power_text or "")
    caffeinate_active = _parse_assertions(assertion_text or "")
    # 관측된 값 중 0이 아닌 것이 하나라도 있으면 그 축은 잠들 수 있다.
    sleeping_axes = sorted(key for key, value in settings.items() if value != 0)
    settings_guarded = bool(settings) and not sleeping_axes
    guarded = settings_guarded or caffeinate_active
    return {
        "available": True,
        "guarded": guarded,
        "settings": settings,
        "sleeping_axes": sleeping_axes,
        "settings_guarded": settings_guarded,
        "caffeinate_active": caffeinate_active,
        "reason": None if guarded else "유휴 절전이 켜져 있고 caffeinate 도 없다 — 관측이 다시 끊긴다",
    }


def guard_action_item(status: dict[str, Any]) -> dict[str, Any] | None:
    """미적용이면 "수동 조치 필요" 항목. 적용 중이면 None (조용한 것이 정상이다).

    코드로 고칠 수 없는 손실이므로 조용히 유실로만 세면 영원히 안 고쳐진다 —
    직전 WO 의 `manual_action_items` 와 같은 이유로 상시 노출한다.
    """
    if not status.get("available") or status.get("guarded"):
        return None
    axes = status.get("sleeping_axes") or []
    return {
        "kind": "sleep_guard_off",
        "severity": "action_required",
        "title": "절전 방지가 적용돼 있지 않다",
        "detail": (
            f"{status.get('reason')}. 유휴 절전이 켜진 항목: {', '.join(axes) if axes else '확인 불가'} · "
            "이 상태면 02:00~05:00 KST 구간이 다시 비고 US 정규장 후반이 유실된다."
        ),
        "remedy": "sudo pmset -a sleep 0 disksleep 0 standby 0 powernap 0 · 전원 상시 연결 · `caffeinate -dimsu` 상시 실행",
        "verify": "scripts/local/check-sleep-guard.sh",
    }


__all__ = ["COMMAND_TIMEOUT_SECONDS", "guard_action_item", "sleep_guard_status"]
