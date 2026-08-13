"""WO-FCE-WORKER-HANG-02 Phase 0·1 — 매달림 증거 자동 포착 + 이벤트 루프 지연 계측.

## 설계 원칙

> **사람이 그 순간에 붙어 있어야 하는 진단은 진단이 아니다.**

매달림은 간헐적이고 발생 시각을 예측할 수 없으며, supervisor 가 900초 뒤 `kill -9` 로
**증거를 파괴한다.** 수 주째 원인을 못 잡은 직접적 이유가 이것이다(D5).
그래서 1단계는 원인 수리가 아니라 **증거가 자동으로 남게 만드는 것**이다.

## 왜 faulthandler 인가 (도구 선택의 실측 근거)

| 도구 | sudo | 파이썬 프레임 | 루프가 막혀도 동작 |
| --- | --- | --- | --- |
| `py-spy dump` | **필요**(macOS) | 예 | 예 |
| `sample <pid>` | 불필요 | 아니오(C 프레임만) | 예 |
| **`faulthandler` + SIGUSR1** | **불필요** | **예(파일:라인)** | **예** |

실측 2026-08-14: `py-spy dump --pid` → `This program requires root on OSX`.
`sample` 은 동작하지만 `_PyEval_EvalFrameDefault` 까지만 보여 어느 파이썬 함수인지 모른다.

`faulthandler.register()` 는 **C 레벨 시그널 핸들러**로 설치되어 프레임 객체를 직접 순회한다.
인터프리터 루프가 파이썬 코드를 실행하지 않는 상태(동기 C 호출에 갇힘)에서도 덤프가 나온다 —
이것이 이 사건에 정확히 필요한 성질이다. 일반 `signal.signal` 핸들러는 바이트코드 사이에서만
실행되므로 **블로킹 중에는 발화하지 않는다**(그래서 쓰지 않는다).

## 이벤트 루프 지연 계측 (Phase 1)

D1~D3 의 가설은 "잡이 매달린 게 아니라 이벤트 루프가 막혔다"이다. 하트비트 잡은 파일에
타임스탬프만 쓰고 반환하므로 900초를 소모할 수 없고(D1), `AsyncIOScheduler` 는 단일 루프를
공유하므로 잡 분리는 실행 격리가 아니며(D2), `asyncio.wait_for` 의 만료도 루프 위에서
평가되므로 루프가 막히면 타임아웃조차 발화하지 못한다(D3).

이 모듈은 그 가설을 **추정이 아니라 측정**으로 만든다: 짧은 주기로 자고 일어나 예정 대비
초과분(lag)을 기록한다. 하트비트 정체 구간에서 lag 이 동반 상승하면 D1~D3 확증,
상승하지 않으면 반증이다.

C8: 알림을 만들지 않는다. 관측은 로그와 파일로만 한다.
C3: 계측·포착이 새로운 장애 원인이 되면 안 된다 — 전부 실패 허용이다.
"""

from __future__ import annotations

import asyncio
import faulthandler
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("worker.hang_probe")

# 루프 지연 샘플 주기(초). 계측 자체가 루프를 점유하면 본말전도이므로 짧게 자고 즉시 반환한다.
LAG_SAMPLE_SECONDS = 1.0
# 이 초를 넘는 지연은 ERROR 로 남긴다. 정상 루프의 지연은 밀리초 단위다.
LAG_ERROR_THRESHOLD_SECONDS = 5.0
# 지연 기록 파일에 남길 최대 줄 수(회전). 관측이 디스크를 먹는 사고를 만들지 않는다.
LAG_LOG_MAX_LINES = 20_000

_dump_file: Any = None


def dump_path(log_dir: Path) -> Path:
    return log_dir / "hang-dumps" / "faulthandler.log"


def install_signal_dump(log_dir: str | Path) -> dict[str, Any]:
    """SIGUSR1 을 받으면 전 스레드 파이썬 스택을 덤프하도록 등록한다.

    외부(supervisor)가 `kill -USR1 <pid>` 만 보내면 증거가 남는다. 사람이 붙어 있을 필요가 없다.
    등록 실패는 **삼키지 않고** 로그로 남기되 기동을 막지 않는다(C3·C7).
    """
    global _dump_file
    directory = Path(log_dir).expanduser() / "hang-dumps"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # 파일 객체를 살려둬야 시그널 시점에 쓸 수 있다(모듈 전역으로 참조 유지).
        _dump_file = open(directory / "faulthandler.log", "a", buffering=1, encoding="utf-8")
        faulthandler.enable(file=_dump_file, all_threads=True)
        faulthandler.register(signal.SIGUSR1, file=_dump_file, all_threads=True, chain=False)
    except Exception as exc:  # 포착 장치가 기동을 막으면 감시자가 장애 원인이 된다
        logger.error("HANG DUMP 등록 실패 — 매달림 증거를 못 남긴다: %s", exc, exc_info=True)
        return {"registered": False, "reason": str(exc)[:200]}
    logger.info("hang dump 등록 완료 pid=%s signal=SIGUSR1 file=%s", os.getpid(), _dump_file.name)
    return {"registered": True, "pid": os.getpid(), "path": _dump_file.name}


class LoopLagMonitor:
    """이벤트 루프 지연 계측기 (Phase 1).

    `asyncio.sleep(N)` 후 실제 경과에서 N 을 뺀 값이 지연이다. 루프가 다른 작업에 점유되면
    깨어나는 시각이 밀리므로, 이 값이 곧 "루프가 얼마나 막혀 있었나"다.

    **잡 러너가 아니라 독립 태스크**로 돈다. 스케줄러 잡으로 만들면 측정 대상(루프)에
    측정기가 함께 갇혀 정체 구간에서 오히려 기록이 끊긴다.
    """

    def __init__(self, log_dir: str | Path, *, sample_seconds: float = LAG_SAMPLE_SECONDS) -> None:
        self.path = Path(log_dir).expanduser() / "loop-lag.jsonl"
        self.sample_seconds = max(0.2, float(sample_seconds))
        self.max_lag_seconds = 0.0
        self.samples = 0
        self.overhead_seconds = 0.0
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="fce-loop-lag")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _run(self) -> None:
        while True:
            started = time.monotonic()
            try:
                await asyncio.sleep(self.sample_seconds)
            except asyncio.CancelledError:
                raise
            lag = max(0.0, time.monotonic() - started - self.sample_seconds)
            self.samples += 1
            if lag > self.max_lag_seconds:
                self.max_lag_seconds = lag
            if lag >= LAG_ERROR_THRESHOLD_SECONDS:
                # C8: 푸시가 아니라 로그다. 스팸을 만들지 않는다.
                logger.error("EVENT LOOP LAG %.1fs — 루프가 막혔다(하트비트 정체와 대조하라)", lag)
                self._append({"at": datetime.now(timezone.utc).isoformat(), "lag_seconds": round(lag, 3)})

    def _append(self, row: dict[str, Any]) -> None:
        """지연 기록. 실패해도 조용히 넘기지 않되 워커를 죽이지 않는다(C3·C7)."""
        overhead_started = time.monotonic()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._rotate()
        except OSError as exc:
            logger.error("loop lag 기록 실패: %s", exc)
        self.overhead_seconds += time.monotonic() - overhead_started

    def _rotate(self) -> None:
        try:
            if not self.path.exists() or self.path.stat().st_size < 4_000_000:
                return
            lines = self.path.read_text(encoding="utf-8").splitlines()[-LAG_LOG_MAX_LINES // 2 :]
            self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    def snapshot(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "sample_seconds": self.sample_seconds,
            "max_lag_seconds": round(self.max_lag_seconds, 3),
            "error_threshold_seconds": LAG_ERROR_THRESHOLD_SECONDS,
            "write_overhead_seconds": round(self.overhead_seconds, 4),
            "path": str(self.path),
        }
