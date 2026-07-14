from __future__ import annotations

from app.db.models import BacktestStat
from .base import _dump_model


class MemoryBacktestRepositoryMixin:
    def upsert_backtest_stat(self, stat: BacktestStat) -> BacktestStat:
        normalized = stat.model_copy(update={"symbol": stat.symbol.upper()})
        existing = next(
            (
                item_id
                for item_id, item in self.backtest_stats.items()
                if item.symbol == normalized.symbol
                and item.timeframe == normalized.timeframe
                and item.signature_key == normalized.signature_key
                and item.scope == normalized.scope
            ),
            None,
        )
        if existing is not None and existing != normalized.id:
            self.backtest_stats.pop(existing, None)
        self.backtest_stats[normalized.id] = normalized
        return normalized

    def list_backtest_stats(
        self,
        symbol: str | None = None,
        signature_key: str | None = None,
        limit: int = 100,
    ) -> list[BacktestStat]:
        stats = list(self.backtest_stats.values())
        if symbol:
            stats = [stat for stat in stats if stat.symbol.upper() == symbol.upper()]
        if signature_key:
            stats = [stat for stat in stats if stat.signature_key == signature_key]
        return sorted(stats, key=lambda item: item.generated_at, reverse=True)[:limit]


class SQLiteBacktestRepositoryMixin:
    def upsert_backtest_stat(self, stat: BacktestStat) -> BacktestStat:
        normalized = stat.model_copy(update={"symbol": stat.symbol.upper()})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO backtest_stats
                    (id, signature_key, symbol, timeframe, asset_class, scope, generated_at, sample_size, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(normalized.id),
                    normalized.signature_key,
                    normalized.symbol,
                    normalized.timeframe,
                    normalized.asset_class,
                    normalized.scope,
                    normalized.generated_at.isoformat(),
                    normalized.sample_size,
                    _dump_model(normalized),
                ),
            )
        return normalized

    def list_backtest_stats(
        self,
        symbol: str | None = None,
        signature_key: str | None = None,
        limit: int = 100,
    ) -> list[BacktestStat]:
        query = "SELECT payload FROM backtest_stats"
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if signature_key:
            clauses.append("signature_key = ?")
            params.append(signature_key)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY generated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [BacktestStat.model_validate_json(row["payload"]) for row in rows]
