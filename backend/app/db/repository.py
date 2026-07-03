from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.db.models import MonitoringLog, Position, PositionStatus, Report, Trade


class Repository(Protocol):
    def add_report(self, report: Report) -> Report: ...
    def get_report(self, report_id: UUID) -> Report | None: ...
    def latest_report(self, symbol: str) -> Report | None: ...
    def recent_reports(self, limit: int = 8) -> list[Report]: ...
    def add_position(self, position: Position) -> Position: ...
    def list_positions(self, status: PositionStatus | None = None) -> list[Position]: ...
    def get_position(self, position_id: UUID) -> Position | None: ...
    def update_position(self, position: Position) -> Position: ...
    def add_monitoring_log(self, log: MonitoringLog) -> MonitoringLog: ...
    def add_trade(self, trade: Trade) -> Trade: ...
    def list_trades(self) -> list[Trade]: ...


class MemoryRepository:
    def __init__(self) -> None:
        self.reports: dict[UUID, Report] = {}
        self.reports_by_symbol: dict[str, list[UUID]] = {}
        self.positions: dict[UUID, Position] = {}
        self.monitoring_logs: dict[UUID, list[MonitoringLog]] = {}
        self.trades: dict[UUID, Trade] = {}

    def add_report(self, report: Report) -> Report:
        self.reports[report.id] = report
        symbol = report.symbol.upper()
        self.reports_by_symbol.setdefault(symbol, []).insert(0, report.id)
        return report

    def get_report(self, report_id: UUID) -> Report | None:
        return self.reports.get(report_id)

    def latest_report(self, symbol: str) -> Report | None:
        report_ids = self.reports_by_symbol.get(symbol.upper(), [])
        return self.reports[report_ids[0]] if report_ids else None

    def recent_reports(self, limit: int = 8) -> list[Report]:
        return sorted(self.reports.values(), key=lambda item: item.created_at, reverse=True)[:limit]

    def add_position(self, position: Position) -> Position:
        self.positions[position.id] = position
        return position

    def list_positions(self, status: PositionStatus | None = None) -> list[Position]:
        positions = list(self.positions.values())
        if status:
            positions = [position for position in positions if position.status == status]
        return sorted(positions, key=lambda item: item.opened_at, reverse=True)

    def get_position(self, position_id: UUID) -> Position | None:
        return self.positions.get(position_id)

    def update_position(self, position: Position) -> Position:
        self.positions[position.id] = position
        return position

    def add_monitoring_log(self, log: MonitoringLog) -> MonitoringLog:
        self.monitoring_logs.setdefault(log.position_id, []).insert(0, log)
        return log

    def add_trade(self, trade: Trade) -> Trade:
        self.trades[trade.id] = trade
        return trade

    def list_trades(self) -> list[Trade]:
        return sorted(self.trades.values(), key=lambda item: item.created_at, reverse=True)


class SQLiteRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._lock = threading.RLock()
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    entry_score INTEGER NOT NULL,
                    fomo_index INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reports_symbol_created
                    ON reports(symbol, created_at DESC);

                CREATE TABLE IF NOT EXISTS positions (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_positions_status_opened
                    ON positions(status, opened_at DESC);

                CREATE TABLE IF NOT EXISTS monitoring_logs (
                    id TEXT PRIMARY KEY,
                    position_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_monitoring_position_created
                    ON monitoring_logs(position_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    position_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trades_created
                    ON trades(created_at DESC);
                """
            )

    def add_report(self, report: Report) -> Report:
        payload = _dump_model(report)
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO reports
                    (id, symbol, timeframe, entry_score, fomo_index, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(report.id),
                    report.symbol.upper(),
                    report.timeframe,
                    report.entry_score,
                    report.scores.fomo,
                    report.created_at.isoformat(),
                    payload,
                ),
            )
        return report

    def get_report(self, report_id: UUID) -> Report | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT payload FROM reports WHERE id = ?", (str(report_id),)).fetchone()
        return Report.model_validate_json(row["payload"]) if row else None

    def latest_report(self, symbol: str) -> Report | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM reports WHERE symbol = ? ORDER BY created_at DESC LIMIT 1",
                (symbol.upper(),),
            ).fetchone()
        return Report.model_validate_json(row["payload"]) if row else None

    def recent_reports(self, limit: int = 8) -> list[Report]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT payload FROM reports ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [Report.model_validate_json(row["payload"]) for row in rows]

    def add_position(self, position: Position) -> Position:
        return self._upsert_position(position)

    def list_positions(self, status: PositionStatus | None = None) -> list[Position]:
        query = "SELECT payload FROM positions"
        params: tuple[str, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status.value,)
        query += " ORDER BY opened_at DESC"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [Position.model_validate_json(row["payload"]) for row in rows]

    def get_position(self, position_id: UUID) -> Position | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT payload FROM positions WHERE id = ?", (str(position_id),)).fetchone()
        return Position.model_validate_json(row["payload"]) if row else None

    def update_position(self, position: Position) -> Position:
        return self._upsert_position(position)

    def add_monitoring_log(self, log: MonitoringLog) -> MonitoringLog:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO monitoring_logs
                    (id, position_id, created_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (str(log.id), str(log.position_id), log.created_at.isoformat(), _dump_model(log)),
            )
        return log

    def add_trade(self, trade: Trade) -> Trade:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO trades
                    (id, position_id, symbol, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(trade.id), str(trade.position_id), trade.symbol.upper(), trade.created_at.isoformat(), _dump_model(trade)),
            )
        return trade

    def list_trades(self) -> list[Trade]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT payload FROM trades ORDER BY created_at DESC").fetchall()
        return [Trade.model_validate_json(row["payload"]) for row in rows]

    def _upsert_position(self, position: Position) -> Position:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO positions
                    (id, symbol, status, opened_at, closed_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(position.id),
                    position.symbol.upper(),
                    position.status.value,
                    position.opened_at.isoformat(),
                    position.closed_at.isoformat() if position.closed_at else None,
                    _dump_model(position),
                ),
            )
        return position


def create_repository(database_url: str) -> Repository:
    if database_url == "memory://":
        return MemoryRepository()
    if database_url.startswith("sqlite:///"):
        return SQLiteRepository(database_url.removeprefix("sqlite:///"))
    raise ValueError(f"Unsupported database URL: {database_url}")


def _dump_model(model) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False)
