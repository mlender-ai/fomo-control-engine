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
    # D3: 엔진이 조기 반환(비활성/미구성) 없이 실제로 평가를 수행한 마지막 시각.
    last_effective_run_at: datetime | None = None
    last_error_at: datetime | None = None
    last_error: str | None = None
    next_run_at: datetime | None = None
    updated_at: datetime | None = None
    # WO-FCE-WORKER-HANG-02 Phase 2-2. 스케줄러가 **건너뛴** 발화 수.
    #
    # `skipped` 는 "이전 틱이 아직 도는 중"이라 건너뛴 것이고, 이것은 "루프가 막혀 예정 시각을
    # 놓쳤다"라 건너뛴 것이다 — 원인이 다르므로 한 칸에 합치지 않는다. 합치면 misfire 가
    # 정상 스킵에 묻혀 보이지 않는다(이번 사고가 그렇게 두 달 숨었다).
    misfired: int = 0
    last_misfire_at: datetime | None = None
    misfire_grace_seconds: int = 0

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
            "last_effective_run_at": self.last_effective_run_at,
            "last_error_at": self.last_error_at,
            "last_error": self.last_error,
            "next_run_at": self.next_run_at,
            "updated_at": self.updated_at,
            "misfired": self.misfired,
            "last_misfire_at": self.last_misfire_at,
            "misfire_grace_seconds": self.misfire_grace_seconds,
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
                        last_effective_run_at,
                        last_error_at,
                        last_error,
                        next_run_at,
                        updated_at,
                        misfired,
                        last_misfire_at,
                        misfire_grace_seconds
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    _iso(record.last_effective_run_at),
                    _iso(record.last_error_at),
                    record.last_error,
                    _iso(record.next_run_at),
                    _iso(record.updated_at),
                    record.misfired,
                    _iso(record.last_misfire_at),
                    record.misfire_grace_seconds,
                ),
            )

    def list(self) -> dict[str, dict[str, Any]]:
        if self.database_path is None:
            return {}
        with self._connect() as connection:
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


def _row_get(row: sqlite3.Row, key: str) -> Any:
    # 마이그레이션 이전 스키마로 만든 행을 읽을 때 컬럼 부재를 관대하게 처리한다.
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


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
        last_effective_run_at=_parse_dt(_row_get(row, "last_effective_run_at")),
        last_error_at=_parse_dt(row["last_error_at"]),
        last_error=row["last_error"],
        next_run_at=_parse_dt(row["next_run_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        # Phase 2-2. 구 스키마 행을 읽을 때 컬럼 부재를 관대하게 처리한다.
        misfired=int(_row_get(row, "misfired") or 0),
        last_misfire_at=_parse_dt(_row_get(row, "last_misfire_at")),
        misfire_grace_seconds=int(_row_get(row, "misfire_grace_seconds") or 0),
    )
