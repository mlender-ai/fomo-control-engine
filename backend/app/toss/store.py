from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.db.sqlite_utils import connect_sqlite


class TossStockStore:
    def __init__(self, database_url: str) -> None:
        self.path = database_url.removeprefix("sqlite:///") if database_url.startswith("sqlite:///") else ""

    @property
    def enabled(self) -> bool:
        return bool(self.path)

    def _connect(self) -> sqlite3.Connection:
        if not self.enabled:
            raise RuntimeError("Toss stock store requires SQLite")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        return connect_sqlite(self.path)

    def append_raw(self, table: str, columns: dict[str, Any], payload: Any) -> None:
        allowed = {"toss_quotes", "toss_rankings_snapshot", "toss_investor_flow"}
        if table not in allowed:
            raise ValueError("unsupported Toss raw table")
        values = {**columns, "payload": json.dumps(payload, ensure_ascii=False)}
        names = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        with self._connect() as connection:
            connection.execute(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", tuple(values.values()))

    def upsert_warning(self, market: str, symbol: str, observed_at: str, payload: Any) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO toss_warnings (market, symbol, observed_at, payload) VALUES (?, ?, ?, ?)
                ON CONFLICT(market, symbol) DO UPDATE SET observed_at=excluded.observed_at, payload=excluded.payload""",
                (market, symbol, observed_at, json.dumps(payload, ensure_ascii=False)),
            )

    def upsert_candles(
        self,
        market: str,
        symbol: str,
        timeframe: str,
        source: str,
        observed_at: str,
        candles: list[dict[str, Any]],
    ) -> int:
        written = 0
        with self._connect() as connection:
            for candle in candles:
                connection.execute(
                    """INSERT INTO toss_candles
                    (market, symbol, timeframe, opened_at, open, high, low, close, volume, source, observed_at, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(market, symbol, timeframe, opened_at) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close,
                    volume=excluded.volume, source=excluded.source, observed_at=excluded.observed_at, payload=excluded.payload""",
                    (
                        market,
                        symbol,
                        timeframe,
                        candle["opened_at"],
                        candle["open"],
                        candle["high"],
                        candle["low"],
                        candle["close"],
                        candle["volume"],
                        source,
                        observed_at,
                        json.dumps(candle, ensure_ascii=False),
                    ),
                )
                written += 1
        return written

    def record_judgment(self, candidate: dict[str, Any], signal: dict[str, Any]) -> str | None:
        price = candidate.get("price")
        if price is None:
            return None
        with self._connect() as connection:
            recent = connection.execute(
                """SELECT observed_at FROM scout_judgment_snapshots
                WHERE entity_type=? AND symbol=? AND signal_type=? ORDER BY observed_at DESC LIMIT 1""",
                (candidate["entity_type"], candidate["symbol"], signal["type"]),
            ).fetchone()
            if recent is not None:
                last = datetime.fromisoformat(str(recent["observed_at"]).replace("Z", "+00:00"))
                observed = datetime.fromisoformat(str(candidate["observed_at"]).replace("Z", "+00:00"))
                if observed - last < timedelta(days=1):
                    return None
            judgment_id = str(uuid4())
            connection.execute(
                """INSERT INTO scout_judgment_snapshots
                (id, entity_type, symbol, signal_type, observed_at, price, evidence, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    judgment_id,
                    candidate["entity_type"],
                    candidate["symbol"],
                    signal["type"],
                    candidate["observed_at"],
                    float(price),
                    json.dumps(signal, ensure_ascii=False),
                    candidate["source"],
                ),
            )
        return judgment_id

    def performance(self, entity_type: str) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT s.signal_type, o.horizon_days, COUNT(*) AS n,
                    AVG(o.return_pct) AS avg_return_pct,
                    AVG(CASE WHEN o.return_pct > 0 THEN 1.0 ELSE 0.0 END) * 100 AS hit_rate_pct
                    FROM scout_judgment_snapshots s JOIN scout_judgment_outcomes o ON o.judgment_id=s.id
                    WHERE s.entity_type=? GROUP BY s.signal_type, o.horizon_days
                    ORDER BY s.signal_type, o.horizon_days""",
                    (entity_type,),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            return []
        return [dict(row) | {"sample_low": int(row["n"]) < 30} for row in rows]

    def record_due_outcomes(self, prices: dict[str, float], now: datetime | None = None) -> int:
        if not self.enabled:
            return 0
        now = now or datetime.now(timezone.utc)
        recorded = 0
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM scout_judgment_snapshots").fetchall()
            for row in rows:
                current = prices.get(str(row["symbol"]))
                if current is None:
                    continue
                age_days = (now - datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))).total_seconds() / 86400
                for horizon in (1, 5, 20):
                    if age_days < horizon:
                        continue
                    cursor = connection.execute(
                        """INSERT OR IGNORE INTO scout_judgment_outcomes
                        (judgment_id, horizon_days, observed_at, price, return_pct) VALUES (?, ?, ?, ?, ?)""",
                        (row["id"], horizon, now.isoformat(), current, (current / float(row["price"]) - 1) * 100),
                    )
                    recorded += cursor.rowcount
        return recorded
