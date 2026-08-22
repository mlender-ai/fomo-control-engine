from __future__ import annotations

from datetime import datetime
from typing import Any

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

    def prune_stance_history_candles(self, symbol: str, timeframe: str, keep_bars: int) -> int:
        keep = max(1, int(keep_bars))
        normalized_symbol = symbol.upper()
        keys = sorted(
            (key for key in self.stance_history_candles if key[0] == normalized_symbol and key[1] == timeframe),
            key=lambda key: key[2],
        )
        stale = keys[:-keep] if len(keys) > keep else []
        for key in stale:
            self.stance_history_candles.pop(key, None)
        return len(stale)

    def stance_history_candle_inventory(self) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[MarketCandle]] = {}
        for (row_symbol, row_timeframe, _opened_at), candle in self.stance_history_candles.items():
            grouped.setdefault((row_symbol, row_timeframe), []).append(candle)
        rows: list[dict[str, Any]] = []
        for (symbol, timeframe), candles in grouped.items():
            ordered = sorted(candles, key=lambda item: item.timestamp)
            rows.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bars": len(ordered),
                    "first_at": ordered[0].timestamp,
                    "last_at": ordered[-1].timestamp,
                }
            )
        return sorted(rows, key=lambda item: (str(item["symbol"]), str(item["timeframe"])))

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

    def prune_stance_history_candles(self, symbol: str, timeframe: str, keep_bars: int) -> int:
        """심볼·타임프레임당 최신 `keep_bars` 봉만 남기고 **실제로 지운다** (C7).

        `history_backfill.apply_retention` 은 upsert 대상 목록을 잘라낼 뿐이고 upsert 는
        INSERT/UPDATE 만 한다 — 이미 저장된 오래된 행은 그대로 남는다. 실측에서 리텐션 10 을
        건 2회차 실행이 `pruned=45` 를 보고하면서 표는 50행에서 **55행으로 늘었다.**
        보고와 실제가 반대 방향이면 리텐션은 없는 것보다 나쁘다 — 있다고 믿게 만든다.
        """
        keep = max(1, int(keep_bars))
        with self._connect() as connection:
            cursor = connection.execute(
                """DELETE FROM stance_history_candles
                   WHERE symbol=? AND timeframe=? AND opened_at NOT IN (
                       SELECT opened_at FROM stance_history_candles
                       WHERE symbol=? AND timeframe=?
                       ORDER BY opened_at DESC LIMIT ?
                   )""",
                (symbol.upper(), timeframe, symbol.upper(), timeframe, keep),
            )
            return int(cursor.rowcount or 0)

    def stance_history_candle_inventory(self) -> list[dict[str, Any]]:
        """저장 실태 — 심볼·타임프레임별 봉 수와 구간. 읽기 전용이다."""
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT symbol, timeframe, COUNT(*) AS bars,
                          MIN(opened_at) AS first_at, MAX(opened_at) AS last_at
                   FROM stance_history_candles
                   GROUP BY symbol, timeframe
                   ORDER BY symbol, timeframe"""
            ).fetchall()
        return [
            {
                "symbol": str(row["symbol"]),
                "timeframe": str(row["timeframe"]),
                "bars": int(row["bars"]),
                "first_at": datetime.fromisoformat(str(row["first_at"])),
                "last_at": datetime.fromisoformat(str(row["last_at"])),
            }
            for row in rows
        ]

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
