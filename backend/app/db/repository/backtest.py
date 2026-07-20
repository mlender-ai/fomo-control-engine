from __future__ import annotations

from datetime import datetime

from app.db.models import BacktestStat, MarketCandle
from .base import _dump_model


class MemoryBacktestRepositoryMixin:
    def upsert_stance_history_candles(
        self,
        symbol: str,
        timeframe: str,
        candles: list[MarketCandle],
        source: str,
        observed_at: datetime,
    ) -> int:
        del source, observed_at
        normalized_symbol = symbol.upper()
        for candle in candles:
            self.stance_history_candles[(normalized_symbol, timeframe, candle.timestamp)] = candle
        return len(candles)

    def list_stance_history_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 5000,
    ) -> list[MarketCandle]:
        normalized_symbol = symbol.upper()
        rows = [
            candle
            for (row_symbol, row_timeframe, _opened_at), candle in self.stance_history_candles.items()
            if row_symbol == normalized_symbol and row_timeframe == timeframe
        ]
        return sorted(rows, key=lambda item: item.timestamp)[-limit:]

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
    def upsert_stance_history_candles(
        self,
        symbol: str,
        timeframe: str,
        candles: list[MarketCandle],
        source: str,
        observed_at: datetime,
    ) -> int:
        normalized_symbol = symbol.upper()
        with self._connect() as connection:
            for candle in candles:
                connection.execute(
                    """
                    INSERT INTO stance_history_candles
                        (symbol, timeframe, opened_at, open, high, low, close, volume,
                         quote_volume, source, observed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, timeframe, opened_at) DO UPDATE SET
                        open=excluded.open, high=excluded.high, low=excluded.low,
                        close=excluded.close, volume=excluded.volume,
                        quote_volume=excluded.quote_volume, source=excluded.source,
                        observed_at=excluded.observed_at
                    """,
                    (
                        normalized_symbol,
                        timeframe,
                        candle.timestamp.isoformat(),
                        candle.open,
                        candle.high,
                        candle.low,
                        candle.close,
                        candle.volume,
                        candle.quote_volume,
                        source,
                        observed_at.isoformat(),
                    ),
                )
        return len(candles)

    def list_stance_history_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 5000,
    ) -> list[MarketCandle]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT opened_at, open, high, low, close, volume, quote_volume
                FROM stance_history_candles
                WHERE symbol=? AND timeframe=? ORDER BY opened_at DESC LIMIT ?""",
                (symbol.upper(), timeframe, limit),
            ).fetchall()
        return sorted(
            [
                MarketCandle(
                    timestamp=datetime.fromisoformat(str(row["opened_at"])),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    quote_volume=float(row["quote_volume"]) if row["quote_volume"] is not None else None,
                )
                for row in rows
            ],
            key=lambda item: item.timestamp,
        )

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
