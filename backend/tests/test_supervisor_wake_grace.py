"""2026-09-15 — 절전을 "워커 매달림"으로 오진해 네 번 kill -9 했다.

## 무엇이 있었나

오전에 알림이 오지 않았다. 워커 잡은 정상이었고 정지된 잡도 없었다. 그런데 supervisor
로그에 재시작이 넷 있었다:

    05:07  heartbeat stale 5317s → 재시작
    06:41  heartbeat stale 3319s → 재시작
    07:03  heartbeat stale 1073s → 재시작
    08:42  heartbeat stale 3378s → 재시작

`pmset -g log` 의 절전 구간과 **정확히 일치한다**(09-15 절전 합계 7.1시간):

    03:39→05:07 88분 · 05:46→06:40 55분 · 06:46→07:03 17분 · 07:46→08:42 56분

맥이 자면 하트비트도 함께 멈춘다. 깨어나면 `age` 가 수천 초로 보이고 supervisor 는 그것을
매달림으로 읽었다. **워커는 멈춘 것이 아니라 얼어 있었고, 깨면 스스로 돌아온다.**
죽이면 진행 중이던 작업과 메모리 큐만 잃는다 — 복구가 손해를 만들었다.
"""

from __future__ import annotations

import pathlib
import re
import subprocess

SUPERVISOR = pathlib.Path(__file__).resolve().parents[2] / "scripts/local/supervisor.sh"
BODY = SUPERVISOR.read_text()

# `sysctl -n kern.waketime` 의 실제 출력 형식.
SAMPLE = "{ sec = 1789429328, usec = 486971 }"


def _extract(sample: str) -> str:
    """supervisor 가 쓰는 sed 를 **그대로 꺼내** 돌린다 — 베껴 쓰면 검증이 아니다."""
    match = re.search(r"sed -n '(s/\^\{[^']*)'", BODY)
    assert match, "waketime sed 를 찾지 못했다 — 스크립트 구조가 바뀌었다"
    script = match.group(1)
    done = subprocess.run(["sed", "-n", script], input=sample, capture_output=True, text=True, check=False)
    return done.stdout.strip()


def test_supervisor_reads_the_wake_time() -> None:
    assert "kern.waketime" in BODY
    assert "FCE_SUPERVISOR_WAKE_GRACE" in BODY


def test_the_sed_extracts_sec_not_usec() -> None:
    """`.*sec = ` 는 greedy 라 `usec` 을 잡는다 — 그러면 항상 "절전 아님"이 되어 수리가 죽는다."""
    assert _extract(SAMPLE) == "1789429328"


def test_the_sed_does_not_return_the_usec_value() -> None:
    assert _extract(SAMPLE) != "486971"


def test_missing_sysctl_output_yields_nothing() -> None:
    """`sysctl` 이 없거나 형식이 바뀌면 **빈 값**이어야 한다 — 빈 값이면 기존 경로로 간다."""
    assert _extract("") == ""
    assert _extract("not a waketime line") == ""


def test_the_wake_check_runs_before_the_restart_verdict() -> None:
    """판정 뒤에 두면 이미 죽인 다음이다."""
    wake_at = BODY.index("kern.waketime")
    verdict_at = BODY.index("포트는 열려 있으나 워커 매달림")
    assert wake_at < verdict_at


def test_a_stale_heartbeat_outside_the_grace_still_restarts() -> None:
    """절전을 핑계로 **진짜 매달림**을 놓치면 이 수리가 새 침묵을 만든다."""
    # `kern.waketime` 은 주석에도 나온다 — **코드 쪽**을 봐야 한다.
    block = BODY.split("kern.waketime")[-1].split("C5 쿨다운")[0]
    assert "-lt" in block, "유예 안일 때만 건너뛰어야 한다"
    assert "return 0" in block


def test_negative_elapsed_is_rejected() -> None:
    """시계가 뒤로 가면(시간대·NTP 보정) 음수가 나온다 — 그것을 "방금 깨어남"으로 읽으면 안 된다."""
    block = BODY.split("kern.waketime")[-1].split("C5 쿨다운")[0]
    assert "-ge 0" in block


def test_the_script_stays_syntactically_valid() -> None:
    done = subprocess.run(["bash", "-n", str(SUPERVISOR)], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
