"""WO-FCE-WORKER-HANG-02 Phase 0·1 — 매달림 증거 포착·루프 지연 계측 회귀.

소급 실측(2026-08-14, `logs/restarts.jsonl` 122건):

    heartbeat_stale  101회   ← 매달림. 최근에는 17~30분마다 반복
    port_down         13회
    자동 복구 포기      6회
    HEARTBEAT WRITE FAILED  0건 → 쓰기 실패가 아니다. D1~D3(루프 블로킹) 가설 유지

수 주째 원인을 못 잡은 이유는 supervisor 가 감지 직후 곧바로 `kill -9` 해서
**증상이 잡힐 때마다 증거가 함께 사라졌기** 때문이다(D5).

고정하는 명제:
1. 포착이 파괴에 선행한다 — capture 가 `kill -9` 앞에 있다 (C2)
2. 포착 실패·타임아웃이 재시작을 막지 않는다 (C3)
3. 관측물에 보존 상한이 있다 (DB 12.8GB 비대 선례)
4. 새 푸시를 만들지 않는다 — 로그·파일로만 관측 (C8)
5. 문턱을 올리지 않는다 — 감지를 늦추는 건 은폐다 (C1)
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

import pytest

from app.worker import hang_probe

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPERVISOR = REPO_ROOT / "scripts" / "local" / "supervisor.sh"
CAPTURE = REPO_ROOT / "scripts" / "local" / "capture-hang.sh"


# ── 1. 포착이 파괴에 선행한다 (C2 · D5) ─────────────────────────────


def test_capture_runs_before_kill() -> None:
    """이 순서가 뒤집히면 101회 때처럼 증거가 계속 사라진다."""
    source = SUPERVISOR.read_text(encoding="utf-8")
    capture_at = source.find("capture-hang.sh")
    kill_at = source.find("xargs kill -9", capture_at if capture_at > 0 else 0)

    assert capture_at > 0, "supervisor 가 포착 스크립트를 호출하지 않는다"
    assert kill_at > capture_at, "kill -9 가 포착보다 먼저 실행되면 증거가 파괴된다"


def test_capture_also_runs_on_the_giveup_path() -> None:
    """ "자동 복구 포기"야말로 증거가 가장 필요한 순간이다.

    그 분기는 재시작 없이 `return` 하므로, 포착을 넣지 않으면 "재시작으로 해결되지 않는다"고
    판단한 바로 그 시점에 스택을 뜰 기회가 영영 없다. 실측에서 포기가 6회 있었다.
    """
    source = SUPERVISOR.read_text(encoding="utf-8")
    giveup_at = source.find("자동 복구 포기(수동 개입 필요)")
    assert giveup_at > 0
    block = source[max(0, giveup_at - 1200) : giveup_at]
    assert "capture-hang.sh" in block, "포기 경로에 증거 포착이 없다"


def test_capture_script_exists_and_is_executable() -> None:
    assert CAPTURE.exists()
    assert CAPTURE.stat().st_mode & 0o111, "실행 권한이 없으면 supervisor 가 호출해도 아무 일도 안 일어난다"


# ── 2. 포착이 사고를 만들지 않는다 (C3) ─────────────────────────────


def test_capture_call_is_time_boxed() -> None:
    """덤프가 느리면 재시작이 지연된다 — 감시자가 새로운 장애 원인이 되면 안 된다."""
    source = SUPERVISOR.read_text(encoding="utf-8")
    call = re.search(r"timeout \"\$\{FCE_HANG_CAPTURE_TIMEOUT:-\d+\}\" /bin/bash \"\$REPO_DIR/scripts/local/capture-hang\.sh\"", source)
    assert call, "capture 호출에 timeout 이 없다"


def test_capture_never_fails_the_caller() -> None:
    """실패해도 0으로 끝나야 supervisor 의 `set -e` 계열 동작이나 조건문을 흔들지 않는다."""
    source = CAPTURE.read_text(encoding="utf-8")
    assert source.rstrip().endswith("exit 0")
    assert "set -uo pipefail" in source and "set -e" not in source.split("\n")[0]


def test_capture_tolerates_missing_process(tmp_path) -> None:
    """죽은 PID 를 넘겨도 실패하지 않는다(경합으로 이미 종료된 경우)."""
    result = subprocess.run(
        ["/bin/bash", str(CAPTURE), "999999", str(tmp_path), "test"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"포착 실패가 호출자에게 전파됐다: {result.stderr[:300]}"
    dumps = list((tmp_path / "hang-dumps").glob("*.txt"))
    assert dumps, "프로세스가 없어도 사유가 담긴 파일은 남아야 한다(C7 침묵 금지)"
    # 사유는 상황에 따라 "생략"(pid 불일치) 또는 "전송 실패"(프로세스 없음)다. 둘 다 기록돼야 한다.
    body = dumps[0].read_text(encoding="utf-8")
    assert "SIGUSR1 생략" in body or "SIGUSR1 전송 실패" in body


def test_capture_completes_while_target_is_alive(tmp_path) -> None:
    """포착은 대상이 **살아 있는 동안** 끝나야 하고, 대상을 죽이면 안 된다(C3).

    회귀 근거: SIGUSR1 의 기본 동작은 **프로세스 종료**다. faulthandler 를 등록하지 않은
    프로세스에 보내면 그 자리에서 죽는다(초기 구현에서 returncode -30 으로 실측됐다).
    그래서 하트비트가 자기 pid 라고 밝힌 워커에게만 보낸다 — 여기서는 pid 가 다르므로
    SIGUSR1 이 생략되고 대상은 살아 있어야 한다.
    """
    target = subprocess.Popen(["python3", "-c", "import time; time.sleep(30)"])
    try:
        result = subprocess.run(
            ["/bin/bash", str(CAPTURE), str(target.pid), str(tmp_path), "alive_test"],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0
        assert target.poll() is None, "포착이 대상을 죽였다 — 증거 수집이 파괴가 되면 안 된다"
        dumps = list((tmp_path / "hang-dumps").glob("*.txt"))
        assert dumps, "살아 있는 대상인데 덤프가 안 남았다"
        body = dumps[0].read_text(encoding="utf-8")
        assert "pid: " in body and "--- process ---" in body
        assert "SIGUSR1 생략" in body, "핸들러 미등록 프로세스에 시그널을 보내면 죽는다"
    finally:
        target.kill()
        target.wait(timeout=10)


def test_capture_has_a_retention_policy() -> None:
    source = CAPTURE.read_text(encoding="utf-8")
    assert "FCE_HANG_DUMP_KEEP" in source, "덤프 보존 상한이 없으면 디스크를 먹는다"
    assert "faulthandler.log" in source and "20000000" in source, "누적 로그 상한이 없다"


# ── 3. 시그널 덤프 등록 (Phase 0) ───────────────────────────────────


def test_signal_dump_registers_and_reports(tmp_path) -> None:
    result = hang_probe.install_signal_dump(tmp_path)
    assert result["registered"] is True
    assert (tmp_path / "hang-dumps" / "faulthandler.log").exists()


def test_signal_dump_failure_is_reported_not_swallowed(tmp_path, monkeypatch) -> None:
    """등록 실패를 조용히 넘기면 '증거가 남고 있다'고 착각한다(C7)."""

    def _boom(*_args, **_kwargs):
        raise OSError("no space left")

    monkeypatch.setattr(hang_probe.faulthandler, "register", _boom)
    result = hang_probe.install_signal_dump(tmp_path)

    assert result["registered"] is False
    assert "no space left" in result["reason"]


def test_faulthandler_is_used_not_plain_signal_handler() -> None:
    """일반 signal 핸들러는 바이트코드 사이에서만 실행된다 — 블로킹 중엔 발화하지 않는다.

    faulthandler 는 C 레벨 핸들러로 프레임을 직접 순회하므로 루프가 막혀도 덤프가 나온다.
    이 사건에 정확히 필요한 성질이라 대체하면 안 된다.
    """
    source = (REPO_ROOT / "backend" / "app" / "worker" / "hang_probe.py").read_text(encoding="utf-8")
    assert "faulthandler.register(signal.SIGUSR1" in source
    assert "chain=False" in source


# ── 4. 루프 지연 계측 (Phase 1) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_lag_measures_blocking(tmp_path) -> None:
    """동기 블로킹을 주입하면 지연이 잡혀야 한다 — 이것이 D1~D3 검증의 도구다."""
    monitor = hang_probe.LoopLagMonitor(tmp_path, sample_seconds=0.2)
    monitor.start()
    await asyncio.sleep(0.05)

    import time as _time

    _time.sleep(0.9)  # 루프를 동기적으로 막는다(잡 안의 블로킹 호출과 같은 상황)
    await asyncio.sleep(0.5)
    await monitor.stop()

    assert monitor.max_lag_seconds >= 0.4, f"블로킹을 주입했는데 지연이 {monitor.max_lag_seconds}s 로 잡히지 않았다"


@pytest.mark.asyncio
async def test_loop_lag_is_quiet_when_healthy(tmp_path) -> None:
    """정상 루프에서 기록이 쌓이면 노이즈가 되고 임계가 무의미해진다."""
    monitor = hang_probe.LoopLagMonitor(tmp_path, sample_seconds=0.2)
    monitor.start()
    await asyncio.sleep(0.7)
    await monitor.stop()

    assert monitor.samples >= 2
    assert monitor.max_lag_seconds < hang_probe.LAG_ERROR_THRESHOLD_SECONDS
    assert not (tmp_path / "loop-lag.jsonl").exists(), "임계 미만인데 파일이 생기면 스팸이다"


@pytest.mark.asyncio
async def test_loop_lag_monitor_is_an_independent_task(tmp_path) -> None:
    """스케줄러 잡으로 만들면 측정 대상(루프)에 측정기가 함께 갇힌다."""
    monitor = hang_probe.LoopLagMonitor(tmp_path)
    monitor.start()
    assert monitor._task is not None and not monitor._task.done()
    await monitor.stop()


def test_lag_snapshot_exposes_overhead(tmp_path) -> None:
    """계측 자체의 오버헤드를 모르면 '계측이 원인'을 배제할 수 없다(Phase 1-4)."""
    snapshot = hang_probe.LoopLagMonitor(tmp_path).snapshot()
    for key in ("samples", "sample_seconds", "max_lag_seconds", "write_overhead_seconds", "error_threshold_seconds"):
        assert key in snapshot


# ── 5. 절대 제약 ────────────────────────────────────────────────────


def test_thresholds_are_not_relaxed() -> None:
    """C1 — 감지를 늦추는 것은 수리가 아니라 은폐다.

    문턱은 선행 WO 에서 공용 변수(`scripts/local/lib/liveness.sh`)로 모였다. 정의부 하나만
    지키면 supervisor·deadman 양쪽이 함께 고정된다.
    """
    shared = (REPO_ROOT / "scripts" / "local" / "lib" / "liveness.sh").read_text(encoding="utf-8")
    assert 'FCE_STALE_LIMIT="${FCE_DEADMAN_STALE_SECONDS:-900}"' in shared, "사망 문턱 900 이 변경됐다"
    assert 'HB_STALE_LIMIT="${FCE_SUPERVISOR_HB_STALE_SECONDS:-$FCE_STALE_LIMIT}"' in SUPERVISOR.read_text(encoding="utf-8")


def test_no_new_push_alert_in_capture_path() -> None:
    """C8 — Phase 0·1 의 관측은 로그·파일로 한다. 새 푸시를 만들지 않는다."""
    source = CAPTURE.read_text(encoding="utf-8")
    assert "api.telegram.org" not in source
    assert "sendMessage" not in source
    probe = (REPO_ROOT / "backend" / "app" / "worker" / "hang_probe.py").read_text(encoding="utf-8")
    assert "telegram" not in probe.lower()
