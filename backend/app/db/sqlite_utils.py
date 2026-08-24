from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


SQLITE_WRITE_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


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
        SQLITE_WRITE_LOCK.acquire()
        self._fce_write_lock_acquired = True

    def _release_write_lock(self) -> None:
        if not self._fce_write_lock_acquired:
            return
        self._fce_write_lock_acquired = False
        SQLITE_WRITE_LOCK.release()


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
