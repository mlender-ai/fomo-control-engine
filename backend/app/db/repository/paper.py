from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID
from app.db.models import PaperTrade, Trade, UserTrade, utc_now
from .base import _aware_dt, _dump_model, _json_cache_default, _timestamp_or_min


class MemoryPaperRepositoryMixin:
    def add_trade(self, trade: Trade) -> Trade:
        self.trades[trade.id] = trade
        return trade

    def get_trade(self, trade_id: UUID) -> Trade | None:
        return self.trades.get(trade_id)

    def list_trades(self) -> list[Trade]:
        return sorted(self.trades.values(), key=lambda item: item.created_at, reverse=True)

    def upsert_paper_trade(self, trade: PaperTrade) -> PaperTrade:
        normalized = trade.model_copy(update={"symbol": trade.symbol.upper(), "updated_at": utc_now()})
        self.paper_trades[normalized.id] = normalized
        return normalized

    def get_paper_trade(self, trade_id: UUID) -> PaperTrade | None:
        return self.paper_trades.get(trade_id)

    def list_paper_trades(
        self,
        status: str | None = None,
        symbol: str | None = None,
        limit: int = 500,
    ) -> list[PaperTrade]:
        trades = list(self.paper_trades.values())
        if status:
            trades = [trade for trade in trades if trade.status == status]
        if symbol:
            trades = [trade for trade in trades if trade.symbol.upper() == symbol.upper()]
        return sorted(trades, key=lambda item: item.updated_at, reverse=True)[:limit]

    def upsert_user_trade(self, trade: UserTrade) -> UserTrade:
        normalized = trade.model_copy(update={"symbol": trade.symbol.upper(), "updated_at": utc_now()})
        self.user_trades[normalized.id] = normalized
        return normalized

    def list_user_trades(self, since: datetime | None = None, limit: int = 5000) -> list[UserTrade]:
        trades = list(self.user_trades.values())
        if since is not None:
            trades = [trade for trade in trades if trade.exit_at >= since]
        return sorted(trades, key=lambda item: item.exit_at, reverse=True)[:limit]

    def upsert_user_account_fill(self, fill: dict) -> bool:
        trade_id = str(fill.get("trade_id") or "")
        if not trade_id:
            raise ValueError("account fill trade_id is required")
        created = trade_id not in self.user_account_fills
        self.user_account_fills[trade_id] = dict(fill)
        return created

    def list_user_account_fills(self, since: datetime | None = None, limit: int = 10000) -> list[dict]:
        fills = list(self.user_account_fills.values())
        if since is not None:
            fills = [fill for fill in fills if _timestamp_or_min(fill.get("timestamp")) >= since]
        return sorted(fills, key=lambda fill: str(fill.get("timestamp") or ""))[-limit:]

    def get_paper_engine_state(self, symbol: str, timeframe: str) -> dict | None:
        state = self.paper_engine_states.get((symbol.upper(), timeframe))
        return dict(state) if isinstance(state, dict) else None

    def upsert_paper_engine_state(self, symbol: str, timeframe: str, state: dict) -> bool:
        key = (symbol.upper(), timeframe)
        current = self.paper_engine_states.get(key)
        if current == state:
            return False
        self.paper_engine_states[key] = dict(state)
        return True

    def upsert_paper_gate_funnel(self, record: dict) -> bool:
        key = (
            str(record.get("symbol") or "").upper(),
            str(record.get("timeframe") or "4h"),
            str(record.get("bar_at") or ""),
        )
        if key in self.paper_gate_funnel:
            return False
        self.paper_gate_funnel[key] = dict(record)
        return True

    def list_paper_gate_funnel(
        self,
        since: datetime | None = None,
        symbol: str | None = None,
        limit: int = 10000,
    ) -> list[dict]:
        rows = list(self.paper_gate_funnel.values())
        if since is not None:
            rows = [row for row in rows if (_timestamp_or_min(row.get("bar_at")) >= since)]
        if symbol is not None:
            rows = [row for row in rows if str(row.get("symbol") or "").upper() == symbol.upper()]
        return sorted(rows, key=lambda row: str(row.get("bar_at") or ""), reverse=True)[:limit]

    def upsert_entry_block_log(self, record: dict) -> bool:
        record_id = str(record.get("id") or "")
        if not record_id:
            raise ValueError("entry block log id is required")
        if record_id in self.entry_block_logs:
            return False
        self.entry_block_logs[record_id] = dict(record)
        return True

    def list_entry_block_logs(
        self,
        since: datetime | None = None,
        symbol: str | None = None,
        failed_gate: str | None = None,
        limit: int = 10000,
    ) -> list[dict]:
        rows = list(self.entry_block_logs.values())
        if since is not None:
            rows = [row for row in rows if _timestamp_or_min(row.get("bar_at")) >= since]
        if symbol is not None:
            rows = [row for row in rows if str(row.get("symbol") or "").upper() == symbol.upper()]
        if failed_gate is not None:
            rows = [row for row in rows if str(row.get("failed_gate") or "") == failed_gate]
        return sorted(rows, key=lambda row: str(row.get("bar_at") or ""), reverse=True)[:limit]


class SQLitePaperRepositoryMixin:
    def add_trade(self, trade: Trade) -> Trade:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO trades
                    (id, position_id, symbol, created_at, plan_price, chase_pct,
                     report_to_entry_minutes, scout_originated, stance_alignment,
                     entry_state_label, fomo_index, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(trade.id),
                    str(trade.position_id),
                    trade.symbol.upper(),
                    trade.created_at.isoformat(),
                    trade.plan_price,
                    trade.chase_pct,
                    trade.report_to_entry_minutes,
                    1 if trade.scout_originated else 0 if trade.scout_originated is not None else None,
                    trade.stance_alignment,
                    trade.entry_state_label,
                    trade.fomo_index,
                    _dump_model(trade),
                ),
            )
        return trade

    def get_trade(self, trade_id: UUID) -> Trade | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM trades WHERE id = ?", (str(trade_id),)).fetchone()
        return Trade.model_validate_json(row["payload"]) if row else None

    def list_trades(self) -> list[Trade]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM trades ORDER BY created_at DESC").fetchall()
        return [Trade.model_validate_json(row["payload"]) for row in rows]

    def upsert_paper_trade(self, trade: PaperTrade) -> PaperTrade:
        normalized = trade.model_copy(update={"symbol": trade.symbol.upper(), "updated_at": utc_now()})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO paper_trades
                    (id, symbol, timeframe, status, entry_bar_at, exit_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(normalized.id),
                    normalized.symbol,
                    normalized.timeframe,
                    normalized.status,
                    normalized.entry_bar_at.isoformat(),
                    normalized.exit_at.isoformat() if normalized.exit_at else None,
                    normalized.updated_at.isoformat(),
                    _dump_model(normalized),
                ),
            )
        return normalized

    def get_paper_trade(self, trade_id: UUID) -> PaperTrade | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM paper_trades WHERE id = ?",
                (str(trade_id),),
            ).fetchone()
        return PaperTrade.model_validate_json(row["payload"]) if row else None

    def list_paper_trades(
        self,
        status: str | None = None,
        symbol: str | None = None,
        limit: int = 500,
    ) -> list[PaperTrade]:
        query = "SELECT payload FROM paper_trades"
        clauses: list[str] = []
        params: list[str | int] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [PaperTrade.model_validate_json(row["payload"]) for row in rows]

    def upsert_user_trade(self, trade: UserTrade) -> UserTrade:
        normalized = trade.model_copy(update={"symbol": trade.symbol.upper(), "updated_at": utc_now()})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO user_trades
                    (id, symbol, direction, entry_at, exit_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(normalized.id),
                    normalized.symbol,
                    normalized.direction.value,
                    normalized.entry_at.isoformat(),
                    normalized.exit_at.isoformat(),
                    normalized.updated_at.isoformat(),
                    _dump_model(normalized),
                ),
            )
        return normalized

    def list_user_trades(self, since: datetime | None = None, limit: int = 5000) -> list[UserTrade]:
        query = "SELECT payload FROM user_trades"
        params: list[str | int] = []
        if since is not None:
            query += " WHERE exit_at >= ?"
            params.append(since.isoformat())
        query += " ORDER BY exit_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [UserTrade.model_validate_json(row["payload"]) for row in rows]

    def upsert_user_account_fill(self, fill: dict) -> bool:
        trade_id = str(fill.get("trade_id") or "")
        timestamp = str(fill.get("timestamp") or "")
        if not trade_id or not timestamp:
            raise ValueError("account fill trade_id and timestamp are required")
        encoded = json.dumps(fill, ensure_ascii=False, sort_keys=True, default=_json_cache_default)
        with self._connect() as connection:
            existed = connection.execute("SELECT 1 FROM user_account_fills WHERE trade_id = ?", (trade_id,)).fetchone()
            connection.execute(
                """
                INSERT OR REPLACE INTO user_account_fills (trade_id, symbol, timestamp, payload, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (trade_id, str(fill.get("symbol") or "").upper(), timestamp, encoded, utc_now().isoformat()),
            )
        return existed is None

    def list_user_account_fills(self, since: datetime | None = None, limit: int = 10000) -> list[dict]:
        query = "SELECT payload FROM user_account_fills"
        params: list[str | int] = []
        if since is not None:
            query += " WHERE timestamp >= ?"
            params.append(since.isoformat())
        query += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def get_paper_engine_state(self, symbol: str, timeframe: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM paper_engine_states WHERE symbol = ? AND timeframe = ?",
                (symbol.upper(), timeframe),
            ).fetchone()
        return json.loads(row["state"]) if row else None

    def upsert_paper_engine_state(self, symbol: str, timeframe: str, state: dict) -> bool:
        encoded = json.dumps(state, ensure_ascii=True, sort_keys=True)
        with self._connect() as connection:
            current = connection.execute(
                "SELECT state FROM paper_engine_states WHERE symbol = ? AND timeframe = ?",
                (symbol.upper(), timeframe),
            ).fetchone()
            if current and current["state"] == encoded:
                return False
            connection.execute(
                """
                INSERT INTO paper_engine_states (symbol, timeframe, state, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe) DO UPDATE SET
                    state = excluded.state,
                    updated_at = excluded.updated_at
                """,
                (symbol.upper(), timeframe, encoded, utc_now().isoformat()),
            )
        return True

    def upsert_paper_gate_funnel(self, record: dict) -> bool:
        symbol = str(record.get("symbol") or "").upper()
        timeframe = str(record.get("timeframe") or "4h")
        bar_at = str(record.get("bar_at") or "")
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_cache_default)
        with self._connect() as connection:
            current = connection.execute(
                "SELECT 1 FROM paper_gate_funnel WHERE symbol = ? AND timeframe = ? AND bar_at = ?",
                (symbol, timeframe, bar_at),
            ).fetchone()
            if current:
                return False
            connection.execute(
                """
                INSERT INTO paper_gate_funnel (symbol, timeframe, bar_at, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (symbol, timeframe, bar_at, encoded, utc_now().isoformat()),
            )
        return True

    def list_paper_gate_funnel(
        self,
        since: datetime | None = None,
        symbol: str | None = None,
        limit: int = 10000,
    ) -> list[dict]:
        query = "SELECT payload FROM paper_gate_funnel"
        clauses: list[str] = []
        params: list[str | int] = []
        if since is not None:
            clauses.append("bar_at >= ?")
            params.append(_aware_dt(since).isoformat())
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY bar_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def upsert_entry_block_log(self, record: dict) -> bool:
        record_id = str(record.get("id") or "")
        if not record_id:
            raise ValueError("entry block log id is required")
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_cache_default)
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO entry_block_log
                    (id, symbol, timeframe, bar_at, direction, failed_gate, detail, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    str(record.get("symbol") or "").upper(),
                    str(record.get("timeframe") or "4h"),
                    str(record.get("bar_at") or ""),
                    str(record.get("direction") or "unknown"),
                    str(record.get("failed_gate") or "unknown"),
                    str(record.get("detail") or ""),
                    encoded,
                    str(record.get("created_at") or utc_now().isoformat()),
                ),
            )
        return cursor.rowcount > 0

    def list_entry_block_logs(
        self,
        since: datetime | None = None,
        symbol: str | None = None,
        failed_gate: str | None = None,
        limit: int = 10000,
    ) -> list[dict]:
        query = "SELECT payload FROM entry_block_log"
        clauses: list[str] = []
        params: list[str | int] = []
        if since is not None:
            clauses.append("bar_at >= ?")
            params.append(_aware_dt(since).isoformat())
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if failed_gate is not None:
            clauses.append("failed_gate = ?")
            params.append(failed_gate)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY bar_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [json.loads(row["payload"]) for row in rows]
