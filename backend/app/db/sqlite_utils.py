from __future__ import annotations

import logging
import sqlite3
import threading
import time
import traceback
from pathlib import Path
from typing import Any


SQLITE_WRITE_LOCK = threading.RLock()
logger = logging.getLogger(__name__)

# 전역 쓰기 락 대기 상한. **무한 대기를 금지한다.**
#
# ## 2026-09-10 침묵 (16시간)
#
# `SQLITE_WRITE_LOCK.acquire()` 에 타임아웃이 없었다. 그래서 대량 쓰기 하나가 락을 잡으면
# 나머지 스레드가 **영원히** 기다렸고, 매달린 채 아무 흔적도 남기지 않다가 워커 잡
# 타임아웃(450초)으로 죽었다. faulthandler 덤프가 그 모양을 그대로 보여줬다:
#
#   보유: executemany → trade_cache.store_fills → refresh_derivative_data
#   대기: 5개 스레드 (`_acquire_write_lock`) ← 알림 경로 포함
#
# 죽은 잡: sync_positions · deliver_alerts (둘 다 "timeout after 450s").
# 알림을 `sync_positions` 에서 분리했지만(ALERT-SILENCE-01 3-1) **분리된 잡도 같은 락에서
# 죽었다.** 분리는 부모 예산 문제를 풀었고 이것은 다른 문제다.
#
# 상한을 걸면 그 쓰기가 실패한다. 그러나 **캐시 쓰기 하나를 잃는 것이 알림 전체를 잃는 것보다
# 낫다** — 매달림은 조용하고, 실패는 로그를 남긴다.
WRITE_LOCK_TIMEOUT_SECONDS = 30.0

# 대기가 이 시간을 넘으면 경고한다. 치명적이 되기 **전에** 경합이 보여야 한다.
WRITE_LOCK_WARN_SECONDS = 3.0

# 진단 전용. 락을 보호하지 않으며 근사값이어도 된다 — 없는 것보다 낫다.
# 지난 침묵에서 "누가 잡고 있었나"에 답할 수 있는 것이 덤프뿐이었고, 덤프는 워커가
# 매달려 supervisor 가 kill 할 때만 남는다.
_WRITE_LOCK_HOLDER: dict[str, Any] = {}


class SQLiteWriteLockTimeout(sqlite3.OperationalError):
    """전역 쓰기 락을 제한 시간 안에 얻지 못했다.

    `sqlite3.OperationalError` 를 상속한다 — 기존 `except sqlite3.Error` 경로가 그대로
    받아서 DB 오류처럼 처리한다. 알 수 없는 예외로 터뜨려 상위를 놀래지 않는다.
    """


def write_lock_holder() -> dict[str, Any]:
    """현재 락 보유자 정보 (진단용 사본)."""
    return dict(_WRITE_LOCK_HOLDER)


class TimedSQLiteConnection(sqlite3.Connection):
    """SQLite connection that serializes writes without blocking concurrent reads.

    WAL permits readers while another connection writes.  The previous repository
    wrapped every SELECT in ``SQLITE_WRITE_LOCK`` as well, so a long worker write
    stopped every API read.  This connection acquires the process write lock only
    when the first mutating statement is executed and keeps it through commit.
    """

    _fce_write_lock_acquired = False

    def __enter__(self):
        self._fce_transaction_started_at = time.monotonic()
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        elapsed = time.monotonic() - getattr(self, "_fce_transaction_started_at", time.monotonic())
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self._release_write_lock()
            if elapsed > 5:
                logger.warning(
                    "sqlite transaction exceeded 5s",
                    extra={"elapsed_seconds": round(elapsed, 3)},
                )

    def execute(self, sql: str, parameters: Any = (), /):
        self._acquire_write_lock_if_needed(sql)
        return super().execute(sql, parameters)

    def executemany(self, sql: str, parameters: Any, /):
        self._acquire_write_lock_if_needed(sql)
        return super().executemany(sql, parameters)

    def executescript(self, sql_script: str, /):
        if any(_is_mutating_sql(statement) for statement in sql_script.split(";")):
            self._acquire_write_lock()
        return super().executescript(sql_script)

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._release_write_lock()

    def _acquire_write_lock_if_needed(self, sql: str) -> None:
        if _is_mutating_sql(sql):
            self._acquire_write_lock()

    def _acquire_write_lock(self) -> None:
        if self._fce_write_lock_acquired:
            return
        started = time.monotonic()
        if not SQLITE_WRITE_LOCK.acquire(timeout=WRITE_LOCK_TIMEOUT_SECONDS):
            waited = time.monotonic() - started
            holder = dict(_WRITE_LOCK_HOLDER)
            logger.warning(
                "sqlite write lock timeout after %.1fs — holder=%s",
                waited,
                holder or "미상",
            )
            raise SQLiteWriteLockTimeout(
                f"전역 쓰기 락을 {waited:.1f}초 안에 얻지 못했다 "
                f"(보유: {holder.get('thread', '미상')} / {holder.get('where', '미상')}). "
                "무한 대기하면 이 스레드가 조용히 매달린다 — 실패로 끝내고 기록한다."
            )
        waited = time.monotonic() - started
        self._fce_write_lock_acquired = True
        _WRITE_LOCK_HOLDER.update(
            {
                "thread": threading.current_thread().name,
                "where": _caller_outside_db_layer(),
                "acquired_at": time.time(),
            }
        )
        if waited > WRITE_LOCK_WARN_SECONDS:
            logger.warning("sqlite write lock waited %.1fs before acquiring", waited)

    def _release_write_lock(self) -> None:
        if not self._fce_write_lock_acquired:
            return
        self._fce_write_lock_acquired = False
        _WRITE_LOCK_HOLDER.clear()
        SQLITE_WRITE_LOCK.release()


def _caller_outside_db_layer() -> str:
    """락을 요구한 **호출부**를 찾는다. DB 계층 프레임은 건너뛴다 — 그건 항상 같다."""
    try:
        for frame in traceback.extract_stack()[::-1]:
            name = frame.filename.replace("\\", "/")
            if "/app/db/" in name:
                continue
            if "/app/" not in name:
                continue
            return f"{name.split('/app/', 1)[1]}:{frame.lineno} {frame.name}"
    except Exception:  # noqa: BLE001 — 진단이 실패해도 쓰기를 막지 않는다
        pass
    return "미상"


def connect_sqlite(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5.0, check_same_thread=False, factory=TimedSQLiteConnection)
    connection.row_factory = sqlite3.Row
    return configure_sqlite_connection(connection)


# `PRAGMA auto_vacuum` 의 INCREMENTAL 값. 숫자로 비교해야 한다 — 읽기는 정수를 돌려준다.
_AUTO_VACUUM_INCREMENTAL = 2


def configure_sqlite_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    """연결 하나를 설정한다. **읽기 전용 경로가 쓰기 락을 요구하면 안 된다.**

    ## 왜 이렇게 조심하는가 (2026-08-22 장애)

    이전 구현은 연결할 때마다 `PRAGMA auto_vacuum=INCREMENTAL` 을 **무조건 실행**했다.
    주석은 "기존 DB 에는 무해 no-op" 이라고 적혀 있었지만 무해하지 않다 —
    이 pragma 는 DB 헤더에 쓰므로 **쓰기 락을 요구하고**, 긴 쓰기 트랜잭션(페이퍼 엔진)이
    도는 동안에는 `busy_timeout` 5초를 넘겨 `database is locked` 를 던진다.

    그 예외가 **쿼리 실행 전에 연결 자체를 죽여서** 읽기 엔드포인트까지 전부 500 이 됐다:

        GET /api/stock-paper/dashboard  →  행 → 프론트 500
        GET /api/live/positions         →  500 database is locked
        GET /api/system/worker          →  500 database is locked

    게다가 이 머신 DB 는 이미 `auto_vacuum=2` 였다 — **아무것도 바꾸지 않으면서 락만
    요구하고 있었다.**

    ## 처방

    1. **먼저 읽는다.** 이미 INCREMENTAL 이면 쓰기를 시도하지 않는다(대부분의 경우)
    2. 바꿔야 할 때도 **실패를 삼킨다.** 이 설정은 파일 크기 최적화용이고,
       그것 때문에 연결이 죽으면 안 된다. 다음 연결이 다시 시도한다
    """
    connection.execute("PRAGMA busy_timeout=5000")
    # 빈 DB(신규 설치·CI)는 INCREMENTAL 로 만들어 리텐션 후 incremental_vacuum 이 파일을 줄인다.
    # 이미 그 모드면 건드리지 않는다 — 재설정은 no-op 이면서 쓰기 락만 요구한다.
    try:
        current = connection.execute("PRAGMA auto_vacuum").fetchone()
    except sqlite3.Error:
        current = None
    if current is not None and int(current[0]) == _AUTO_VACUUM_INCREMENTAL:
        return connection
    try:
        connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
    except sqlite3.OperationalError:
        # 락 경합. 파일 크기 최적화 설정 하나 때문에 연결을 죽이지 않는다(장애 원인).
        logger.debug("auto_vacuum pragma skipped: database busy")
    return connection


_MUTATING_SQL_PREFIXES = {
    "ALTER",
    "BEGIN",
    "CREATE",
    "DELETE",
    "DROP",
    "INSERT",
    "REINDEX",
    "REPLACE",
    "UPDATE",
    "VACUUM",
}


def _is_mutating_sql(sql: str) -> bool:
    normalized = sql.lstrip()
    if not normalized:
        return False
    return normalized.split(None, 1)[0].upper() in _MUTATING_SQL_PREFIXES
