from __future__ import annotations

import sqlite3
import threading
import time
import logging
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


def configure_sqlite_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    connection.execute("PRAGMA busy_timeout=5000")
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
