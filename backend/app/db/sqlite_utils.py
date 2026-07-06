from __future__ import annotations

import sqlite3
import threading
import time
import logging
from pathlib import Path


SQLITE_WRITE_LOCK = threading.RLock()
logger = logging.getLogger(__name__)


class TimedSQLiteConnection(sqlite3.Connection):
    def __enter__(self):
        self._fce_transaction_started_at = time.monotonic()
        return super().__enter__()

    def __exit__(self, exc_type, exc_value, traceback):
        elapsed = time.monotonic() - getattr(self, "_fce_transaction_started_at", time.monotonic())
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            if elapsed > 5:
                logger.warning(
                    "sqlite transaction exceeded 5s",
                    extra={"elapsed_seconds": round(elapsed, 3)},
                )


def connect_sqlite(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=5.0, check_same_thread=False, factory=TimedSQLiteConnection)
    connection.row_factory = sqlite3.Row
    return configure_sqlite_connection(connection)


def configure_sqlite_connection(connection: sqlite3.Connection) -> sqlite3.Connection:
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection
