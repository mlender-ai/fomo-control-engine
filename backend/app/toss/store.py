from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.db.sqlite_utils import connect_sqlite


POSITION_SIGNAL_LABELS = {
    "basis_behavior": "베이시스 행동",
    "funding_momentum_divergence": "펀딩 × 기초 모멘텀",
    "underlying_flow_alignment": "기초 수급 × 내 방향",
}


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

    def candles_around(
        self,
        market: str,
        symbol: str,
        timeframe: str,
        anchor: datetime,
        *,
        before: int = 90,
        after: int = 90,
    ) -> list[dict[str, Any]]:
        """Return only persisted candles surrounding an observed event time."""
        if not self.enabled:
            return []
        anchor_value = anchor.astimezone(timezone.utc).isoformat()
        columns = "opened_at, open, high, low, close, volume, source, observed_at"
        with self._connect() as connection:
            earlier = connection.execute(
                f"""SELECT {columns} FROM toss_candles
                WHERE market=? AND symbol=? AND timeframe=?
                AND julianday(opened_at)<=julianday(?)
                ORDER BY julianday(opened_at) DESC LIMIT ?""",
                (market, symbol.upper(), timeframe, anchor_value, before),
            ).fetchall()
            later = connection.execute(
                f"""SELECT {columns} FROM toss_candles
                WHERE market=? AND symbol=? AND timeframe=?
                AND julianday(opened_at)>julianday(?)
                ORDER BY julianday(opened_at) ASC LIMIT ?""",
                (market, symbol.upper(), timeframe, anchor_value, after),
            ).fetchall()
        rows = [dict(row) for row in reversed(earlier)] + [dict(row) for row in later]
        return rows

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

    def record_position_judgment(
        self,
        *,
        judgment_id: str,
        symbol: str,
        observed_at: str,
        price: float,
        evidence: dict[str, Any],
    ) -> bool:
        if not self.enabled or price <= 0:
            return False
        with self._connect() as connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO scout_judgment_snapshots
                (id, entity_type, symbol, signal_type, observed_at, price, evidence, source)
                VALUES (?, 'stock_us', ?, 'position_deepdive', ?, ?, ?, 'bitget+toss+position')""",
                (
                    judgment_id,
                    symbol.upper(),
                    observed_at,
                    float(price),
                    json.dumps(evidence, ensure_ascii=False),
                ),
            )
        return cursor.rowcount > 0

    def position_performance(self, position_id: str) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT s.evidence, o.horizon_days, o.return_pct
                    FROM scout_judgment_snapshots s
                    JOIN scout_judgment_outcomes o ON o.judgment_id=s.id
                    WHERE s.signal_type='position_deepdive'
                    ORDER BY o.observed_at DESC"""
                ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            return []
        grouped: dict[int, list[bool]] = {1: [], 5: [], 20: []}
        for row in rows:
            try:
                evidence = json.loads(row["evidence"])
            except (TypeError, json.JSONDecodeError):
                continue
            if str(evidence.get("position_id") or "") != position_id:
                continue
            expected = evidence.get("expected_move")
            if expected not in {"up", "down"}:
                continue
            correct = float(row["return_pct"]) > 0 if expected == "up" else float(row["return_pct"]) < 0
            grouped[int(row["horizon_days"])].append(correct)
        return [
            {
                "horizon_days": horizon,
                "n": len(results),
                "hit_rate_pct": round(sum(results) / len(results) * 100, 1) if results else None,
                "sample_low": len(results) < 30,
            }
            for horizon, results in grouped.items()
        ]

    def outcomes_for_judgment(self, judgment_id: str) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT horizon_days, observed_at, price, return_pct
                    FROM scout_judgment_outcomes WHERE judgment_id=? ORDER BY horizon_days""",
                    (judgment_id,),
                ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            return []
        return [dict(row) for row in rows]

    def position_signal_performance(self, position_id: str) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT s.evidence, o.horizon_days, o.return_pct
                    FROM scout_judgment_snapshots s
                    LEFT JOIN scout_judgment_outcomes o ON o.judgment_id=s.id
                    WHERE s.signal_type='position_deepdive'
                    ORDER BY s.observed_at DESC, o.horizon_days"""
                ).fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            return []
        labels: dict[str, str] = {}
        grouped: dict[tuple[str, int], list[bool]] = {}
        for row in rows:
            try:
                evidence = json.loads(row["evidence"])
            except (TypeError, json.JSONDecodeError):
                continue
            if str(evidence.get("position_id") or "") != position_id:
                continue
            for signal in evidence.get("cross_signals") or []:
                if not isinstance(signal, dict) or signal.get("status") != "active":
                    continue
                expected = _signal_expected_move(signal)
                signal_id = str(signal.get("id") or "")
                if not signal_id or expected is None:
                    continue
                labels.setdefault(signal_id, str(signal.get("label") or POSITION_SIGNAL_LABELS.get(signal_id) or signal_id))
                for horizon in (1, 5, 20):
                    grouped.setdefault((signal_id, horizon), [])
                if row["horizon_days"] is None or row["return_pct"] is None:
                    continue
                horizon = int(row["horizon_days"])
                result = float(row["return_pct"])
                grouped[(signal_id, horizon)].append(result > 0 if expected == "up" else result < 0)
        return [
            {
                "signal_id": signal_id,
                "signal_label": labels[signal_id],
                "horizon_days": horizon,
                "n": len(results),
                "hit_rate_pct": round(sum(results) / len(results) * 100, 1) if results else None,
                "sample_low": len(results) < 30,
            }
            for (signal_id, horizon), results in grouped.items()
        ]

    def due_position_symbols(self, now: datetime | None = None) -> list[str]:
        if not self.enabled:
            return []
        now = now or datetime.now(timezone.utc)
        try:
            with self._connect() as connection:
                snapshots = connection.execute(
                    """SELECT id, symbol, observed_at FROM scout_judgment_snapshots
                    WHERE signal_type='position_deepdive'"""
                ).fetchall()
                recorded = {
                    (str(row["judgment_id"]), int(row["horizon_days"]))
                    for row in connection.execute(
                        """SELECT judgment_id, horizon_days FROM scout_judgment_outcomes
                        WHERE judgment_id IN (
                            SELECT id FROM scout_judgment_snapshots WHERE signal_type='position_deepdive'
                        )"""
                    ).fetchall()
                }
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            return []
        due: set[str] = set()
        for row in snapshots:
            observed = datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
            age_days = (now - observed).total_seconds() / 86_400
            if any(age_days >= horizon and (str(row["id"]), horizon) not in recorded for horizon in (1, 5, 20)):
                due.add(str(row["symbol"]).upper())
        return sorted(due)

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

    def latest_execution_observation(self, market: str, symbol: str, *, session_open: bool) -> dict[str, Any] | None:
        """Return only directly observed fields needed by the stock PaperBroker.

        Missing bid/ask, limits, candle or opening price stays ``None`` so the
        execution invariant can reject the fill instead of inventing liquidity.
        """
        if not self.enabled:
            return None
        symbol = symbol.upper()
        with self._connect() as connection:
            candle = connection.execute(
                """SELECT * FROM toss_candles WHERE market=? AND symbol=? AND timeframe='1m'
                ORDER BY opened_at DESC LIMIT 1""",
                (market, symbol),
            ).fetchone()
            if candle is None:
                return None
            opened = _session_open_utc(market, datetime.now(timezone.utc))
            session_first = connection.execute(
                """SELECT open FROM toss_candles WHERE market=? AND symbol=? AND timeframe='1m'
                AND opened_at>=? ORDER BY opened_at ASC LIMIT 1""",
                (market, symbol, opened.isoformat()),
            ).fetchone()
            raw_rows = connection.execute(
                """SELECT payload FROM toss_quotes WHERE market=? AND symbol=?
                ORDER BY observed_at DESC LIMIT 8""",
                (market, symbol),
            ).fetchall()
            warning_row = connection.execute(
                "SELECT payload FROM toss_warnings WHERE market=? AND symbol=?",
                (market, symbol),
            ).fetchone()
            fx_row = connection.execute(
                """SELECT rate, observed_at FROM stock_paper_fx_snapshots
                WHERE base_currency='USD' AND quote_currency='KRW' ORDER BY observed_at DESC LIMIT 1"""
            ).fetchone()
        orderbook: dict[str, Any] = {}
        limits: dict[str, Any] = {}
        for row in raw_rows:
            try:
                envelope = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError):
                continue
            kind = envelope.get("kind") if isinstance(envelope, dict) else None
            response = envelope.get("response") if isinstance(envelope, dict) else None
            result = (response or {}).get("result") if isinstance(response, dict) else None
            if kind == "orderbook" and not orderbook and isinstance(result, dict):
                orderbook = result
            if kind == "price_limits" and not limits and isinstance(result, dict):
                limits = result
        warnings: list[str] = []
        if warning_row:
            try:
                warning_payload = json.loads(warning_row["payload"])
                result = warning_payload.get("result") or warning_payload.get("data") or []
                if isinstance(result, list):
                    warnings = [str(item.get("warningType") or item.get("type") or "") for item in result if isinstance(item, dict)]
            except (TypeError, json.JSONDecodeError):
                warnings = []
        bid = _book_price(orderbook.get("bids"))
        ask = _book_price(orderbook.get("asks"))
        upper = _optional_float(limits.get("upperLimitPrice"))
        lower = _optional_float(limits.get("lowerLimitPrice"))
        close = float(candle["close"])
        return {
            "symbol": symbol,
            "market": market,
            "observed_at": str(candle["observed_at"]),
            "session_open": session_open,
            "session_open_price": float(session_first["open"]) if session_first else None,
            "minute_open": float(candle["open"]),
            "minute_high": float(candle["high"]),
            "minute_low": float(candle["low"]),
            "minute_close": close,
            "minute_volume": float(candle["volume"]),
            "bid": bid,
            "ask": ask,
            "upper_limit": upper,
            "lower_limit": lower,
            "upper_locked": bool(upper is not None and close >= upper and ask is None),
            "lower_locked": bool(lower is not None and close <= lower and bid is None),
            "vi_active": any(value.strip().lower().startswith("vi") or value == "변동성완화장치" for value in warnings),
            "halted": any(value.strip().lower() in {"halted", "trading_halt", "거래정지"} for value in warnings),
            "warnings": warnings,
            "fx_rate_to_krw": float(fx_row["rate"]) if fx_row else None,
            "fx_observed_at": str(fx_row["observed_at"]) if fx_row else None,
        }

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


def _signal_expected_move(signal: dict[str, Any]) -> str | None:
    raw_data = signal.get("data")
    data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
    signal_id = signal.get("id")
    if signal_id == "basis_behavior":
        width_change = data.get("width_change_pct_points")
        raw_sparkline = data.get("sparkline")
        sparkline: list[Any] = raw_sparkline if isinstance(raw_sparkline, list) else []
        if len(sparkline) >= 2 and isinstance(sparkline[0], dict) and isinstance(sparkline[-1], dict):
            first = sparkline[0].get("value")
            last = sparkline[-1].get("value")
            if isinstance(first, (int, float)) and isinstance(last, (int, float)):
                width_change = abs(last) - abs(first)
        current = data.get("current_pct")
        if isinstance(width_change, (int, float)) and width_change > 0.25 and isinstance(current, (int, float)) and current != 0:
            return "down" if current < 0 else "up"
    if signal_id == "funding_momentum_divergence":
        funding = data.get("funding_rate")
        momentum = data.get("underlying_momentum_5d_pct")
        if isinstance(funding, (int, float)) and funding != 0 and isinstance(momentum, (int, float)) and momentum != 0:
            return "up" if momentum > 0 else "down"
    if signal_id == "underlying_flow_alignment":
        net_amount = data.get("net_amount")
        if isinstance(net_amount, (int, float)) and net_amount != 0:
            return "up" if net_amount > 0 else "down"
    return None


def _book_price(rows: Any) -> float | None:
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        return None
    return _optional_float(rows[0].get("price") or rows[0].get("priceValue"))


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _session_open_utc(market: str, now: datetime) -> datetime:
    zone = ZoneInfo("Asia/Seoul" if market == "KR" else "America/New_York")
    local = now.astimezone(zone)
    hour, minute = (9, 0) if market == "KR" else (9, 30)
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0).astimezone(timezone.utc)
