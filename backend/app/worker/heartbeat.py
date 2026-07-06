from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.db.migrations import run_migrations
from app.db.sqlite_utils import SQLITE_WRITE_LOCK, connect_sqlite


@dataclass
class HeartbeatRecord:
    job_name: str
    status: str = "idle"
    runs: int = 0
    consecutive_failures: int = 0
    total_failures: int = 0
    skipped: int = 0
    base_interval_seconds: int = 0
    current_interval_seconds: int = 0
    last_started_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None
    next_run_at: datetime | None = None
    updated_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "runs": self.runs,
            "failures": self.total_failures,
            "consecutive_failures": self.consecutive_failures,
            "skipped": self.skipped,
            "base_interval_seconds": self.base_interval_seconds,
            "current_interval_seconds": self.current_interval_seconds,
            "last_started_at": self.last_started_at,
            "last_success_at": self.last_success_at,
            "last_error_at": self.last_error_at,
            "last_error": self.last_error,
            "next_run_at": self.next_run_at,
            "updated_at": self.updated_at,
        }


class SQLiteHeartbeatStore:
    def __init__(self, database_url: str) -> None:
        self.database_path = _sqlite_path(database_url)
        if self.database_path is None:
            return
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @property
    def enabled(self) -> bool:
        return self.database_path is not None

    def upsert(self, record: HeartbeatRecord) -> None:
        if self.database_path is None:
            return
        record.updated_at = datetime.now(timezone.utc)
        with SQLITE_WRITE_LOCK, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO worker_heartbeat
                    (
                        job_name,
                        status,
                        runs,
                        consecutive_failures,
                        total_failures,
                        skipped,
                        base_interval_seconds,
                        current_interval_seconds,
                        last_started_at,
                        last_success_at,
                        last_error_at,
                        last_error,
                        next_run_at,
                        updated_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.job_name,
                    record.status,
                    record.runs,
                    record.consecutive_failures,
                    record.total_failures,
                    record.skipped,
                    record.base_interval_seconds,
                    record.current_interval_seconds,
                    _iso(record.last_started_at),
                    _iso(record.last_success_at),
                    _iso(record.last_error_at),
                    record.last_error,
                    _iso(record.next_run_at),
                    _iso(record.updated_at),
                ),
            )

    def list(self) -> dict[str, dict[str, Any]]:
        if self.database_path is None:
            return {}
        with SQLITE_WRITE_LOCK, self._connect() as connection:
            rows = connection.execute("SELECT * FROM worker_heartbeat ORDER BY job_name ASC").fetchall()
        return {row["job_name"]: _row_to_record(row).as_dict() for row in rows}

    def _connect(self) -> sqlite3.Connection:
        if self.database_path is None:
            raise RuntimeError("heartbeat store is disabled")
        return connect_sqlite(self.database_path)

    def _init_schema(self) -> None:
        with SQLITE_WRITE_LOCK, self._connect() as connection:
            run_migrations(connection)


def _sqlite_path(database_url: str) -> str | None:
    if database_url == "memory://":
        return None
    if database_url.startswith("sqlite:///"):
        return database_url.removeprefix("sqlite:///")
    return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_record(row: sqlite3.Row) -> HeartbeatRecord:
    return HeartbeatRecord(
        job_name=row["job_name"],
        status=row["status"],
        runs=int(row["runs"] or 0),
        consecutive_failures=int(row["consecutive_failures"] or 0),
        total_failures=int(row["total_failures"] or 0),
        skipped=int(row["skipped"] or 0),
        base_interval_seconds=int(row["base_interval_seconds"] or 0),
        current_interval_seconds=int(row["current_interval_seconds"] or 0),
        last_started_at=_parse_dt(row["last_started_at"]),
        last_success_at=_parse_dt(row["last_success_at"]),
        last_error_at=_parse_dt(row["last_error_at"]),
        last_error=row["last_error"],
        next_run_at=_parse_dt(row["next_run_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )
