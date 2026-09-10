from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.db.migrations import run_migrations
from app.db.sqlite_utils import SQLITE_WRITE_LOCK, connect_sqlite
from app.exchange.bitget.trades import BitgetTradeFill


# 청크 크기. 작으면 커밋 오버헤드가, 크면 락 보유 시간이 늘어난다.
# 2000행이면 보유가 수십 ms 수준이라 대기자가 통과한다.
_STORE_CHUNK_ROWS = 2000


@dataclass(frozen=True)
class TradeFillCacheSlice:
    fills: list[BitgetTradeFill]
    truncated: bool


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
        with self._connect() as connection:
            run_migrations(connection)

    def fresh_fills(
        self,
        symbol: str,
        timeframe: str,
        start_at: datetime,
        end_at: datetime,
        max_age_seconds: int,
        max_rows: int,
    ) -> TradeFillCacheSlice | None:
        symbol_key = symbol.upper()
        try:
            with self._connect() as connection:
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
                rows = _latest_rows(connection, symbol_key, start_at, end_at, max_rows)
            return _deserialize_slice(rows, max_rows)
        except (sqlite3.Error, ValueError):
            return None

    def stale_fills(
        self,
        symbol: str,
        start_at: datetime,
        end_at: datetime,
        max_rows: int,
    ) -> TradeFillCacheSlice:
        try:
            with self._connect() as connection:
                rows = _latest_rows(connection, symbol.upper(), start_at, end_at, max_rows)
            return _deserialize_slice(rows, max_rows)
        except (sqlite3.Error, ValueError):
            return TradeFillCacheSlice(fills=[], truncated=False)

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
        rows = [
            (
                symbol_key,
                fill.trade_id,
                fill.timestamp.isoformat(),
                json.dumps(fill.model_dump(mode="json"), ensure_ascii=False),
                now,
            )
            for fill in fills
        ]
        with self._lock:
            # **한 트랜잭션으로 다 쓰지 않는다.** 전역 쓰기 락은 첫 변경문부터 커밋까지
            # 유지되므로, 수만 행을 한 번에 쓰면 그동안 다른 스레드가 전부 멈춘다.
            # 2026-09-10 침묵이 그 모양이었다 — 이 `executemany` 가 락을 잡은 채
            # 알림 경로를 포함한 5개 스레드가 대기하다 450초 타임아웃으로 죽었다.
            #
            # 청크마다 트랜잭션을 끊어 **락을 놓아준다.** 유입은 하루 최대 200만 행이고
            # 이 테이블은 캐시다 — 캐시 쓰기가 알림을 죽여선 안 된다.
            for index in range(0, len(rows), _STORE_CHUNK_ROWS):
                with self._connect() as connection:
                    connection.executemany(
                        """
                        INSERT OR REPLACE INTO bitget_trade_fills
                            (symbol, trade_id, timestamp, payload, fetched_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        rows[index : index + _STORE_CHUNK_ROWS],
                    )
            # 조회 상태는 **마지막에** 쓴다. 중간에 끊기면 이 행이 없으므로 그 창을 다시
            # 가져온다 — `INSERT OR REPLACE` 라 재수집은 멱등이다. 먼저 쓰면 부분 적재를
            # "수집 완료"로 표시해 **조용한 구멍**이 남는다.
            with self._connect() as connection:
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


def _latest_rows(
    connection: sqlite3.Connection,
    symbol: str,
    start_at: datetime,
    end_at: datetime,
    max_rows: int,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT payload
        FROM (
            SELECT payload, timestamp
            FROM bitget_trade_fills
            WHERE symbol = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp DESC
            LIMIT ?
        ) AS recent_fills
        ORDER BY timestamp ASC
        """,
        (symbol, start_at.isoformat(), end_at.isoformat(), max_rows + 1),
    ).fetchall()


def _deserialize_slice(rows: list[sqlite3.Row], max_rows: int) -> TradeFillCacheSlice:
    truncated = len(rows) > max_rows
    selected = rows[1:] if truncated else rows
    return TradeFillCacheSlice(
        fills=[BitgetTradeFill.model_validate_json(row["payload"]) for row in selected],
        truncated=truncated,
    )
