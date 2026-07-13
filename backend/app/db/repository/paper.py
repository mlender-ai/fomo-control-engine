from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID
from app.db.models import PaperTrade, Trade, utc_now
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


class SQLitePaperRepositoryMixin:
    def add_trade(self, trade: Trade) -> Trade:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO trades
                    (id, position_id, symbol, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(trade.id),
                    str(trade.position_id),
                    trade.symbol.upper(),
                    trade.created_at.isoformat(),
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
