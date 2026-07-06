from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.db.migrations import run_migrations
from app.db.sqlite_utils import SQLITE_WRITE_LOCK, connect_sqlite
from app.exchange.bitget.trades import BitgetTradeFill


class BitgetTradeFillCache:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._lock = SQLITE_WRITE_LOCK
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @classmethod
    def from_database_url(cls, database_url: str) -> "BitgetTradeFillCache | None":
        if not database_url.startswith("sqlite:///"):
            return None
        return cls(database_url.removeprefix("sqlite:///"))

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.database_path)

    def _init_schema(self) -> None:
        with self._lock, self._connect() as connection:
            run_migrations(connection)

    def fresh_fills(
        self,
        symbol: str,
        timeframe: str,
        start_at: datetime,
        end_at: datetime,
        max_age_seconds: int,
    ) -> list[BitgetTradeFill] | None:
        symbol_key = symbol.upper()
        with self._lock, self._connect() as connection:
            state = connection.execute(
                """
                SELECT start_at, end_at, fetched_at
                FROM bitget_trade_fill_fetch_state
                WHERE symbol = ? AND timeframe = ?
                """,
                (symbol_key, timeframe),
            ).fetchone()
            if state is None:
                return None
            fetched_at = _parse_dt(state["fetched_at"])
            if (datetime.now(timezone.utc) - fetched_at).total_seconds() > max_age_seconds:
                return None
            if _parse_dt(state["start_at"]) > start_at:
                return None
            rows = connection.execute(
                """
                SELECT payload
                FROM bitget_trade_fills
                WHERE symbol = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
                """,
                (symbol_key, start_at.isoformat(), end_at.isoformat()),
            ).fetchall()
        return [BitgetTradeFill.model_validate_json(row["payload"]) for row in rows]

    def stale_fills(self, symbol: str, start_at: datetime, end_at: datetime) -> list[BitgetTradeFill]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload
                FROM bitget_trade_fills
                WHERE symbol = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC
                """,
                (symbol.upper(), start_at.isoformat(), end_at.isoformat()),
            ).fetchall()
        return [BitgetTradeFill.model_validate_json(row["payload"]) for row in rows]

    def store_fills(
        self,
        symbol: str,
        timeframe: str,
        start_at: datetime,
        end_at: datetime,
        fills: list[BitgetTradeFill],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        symbol_key = symbol.upper()
        with self._lock, self._connect() as connection:
            for fill in fills:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO bitget_trade_fills
                        (symbol, trade_id, timestamp, payload, fetched_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        symbol_key,
                        fill.trade_id,
                        fill.timestamp.isoformat(),
                        json.dumps(fill.model_dump(mode="json"), ensure_ascii=False),
                        now,
                    ),
                )
            connection.execute(
                """
                INSERT OR REPLACE INTO bitget_trade_fill_fetch_state
                    (symbol, timeframe, start_at, end_at, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (symbol_key, timeframe, start_at.isoformat(), end_at.isoformat(), now),
            )


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
