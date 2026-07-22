from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import UUID

from app.db.sqlite_utils import connect_sqlite

from .models import Currency, Market, OrderStatus, PaperFill, Side, StockOrder


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


class StockPaperStore:
    def __init__(self, database_url: str) -> None:
        self.path = database_url.removeprefix("sqlite:///") if database_url.startswith("sqlite:///") else ""

    @property
    def enabled(self) -> bool:
        return bool(self.path)

    def _connect(self) -> sqlite3.Connection:
        if not self.enabled:
            raise RuntimeError("stock paper store requires SQLite")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        return connect_sqlite(self.path)

    def ensure_tracks(self, *, universe_version: str, initial_krw: float, initial_usd: float, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        started = now.isoformat()
        ends = (now + timedelta(weeks=4)).isoformat()
        with self._connect() as connection:
            for market, currency, benchmark, proxy, capital in (
                ("KR", "KRW", "KOSPI100", "237350", initial_krw),
                ("US", "USD", "NASDAQ100", "QQQ", initial_usd),
            ):
                connection.execute(
                    """INSERT OR IGNORE INTO stock_paper_tracks
                    (market, currency, benchmark_index, benchmark_proxy_symbol, benchmark_method,
                     universe_version, started_at, ends_at, initial_cash, cash,
                     status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'unlevered_etf_proxy_close', ?, ?, ?, ?, ?, 'running', ?, ?)""",
                    (market, currency, benchmark, proxy, universe_version, started, ends, capital, capital, started, started),
                )
                for entry_mode in ("strict_signal", "coverage"):
                    connection.execute(
                        """INSERT OR IGNORE INTO stock_paper_mode_accounts
                        (market, entry_mode, currency, initial_cash, cash, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (market, entry_mode, currency, capital, capital, started, started),
                    )

    def update_market_state(self, market: Market, state: str, observed_at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE stock_paper_tracks SET last_market_state=?, last_market_observed_at=?, updated_at=?
                WHERE market=?""",
                (state, observed_at.isoformat(), observed_at.isoformat(), market.value),
            )

    def activate_clock(self, market: Market, *, parameter_version: str, observed_at: datetime) -> bool:
        now = observed_at.isoformat()
        ends = (observed_at + timedelta(weeks=4)).isoformat()
        event = "validation_clock_started"
        reason = "first_authenticated_observation"
        with self._connect() as connection:
            row = connection.execute(
                "SELECT clock_valid, parameter_version FROM stock_paper_tracks WHERE market=?",
                (market.value,),
            ).fetchone()
            if row is None:
                return False
            current_version = str(row["parameter_version"])
            if bool(row["clock_valid"]) and current_version == parameter_version:
                return False
            if bool(row["clock_valid"]):
                event = "validation_clock_restarted"
                reason = f"parameter_version_changed:{current_version}->{parameter_version}"
            connection.execute(
                """UPDATE stock_paper_tracks SET started_at=?, ends_at=?, clock_valid=1,
                clock_invalidation_reason=NULL, parameter_version=?, status='running', updated_at=? WHERE market=?""",
                (now, ends, parameter_version, now, market.value),
            )
        self.record_event(market, event, reason=reason, observed_at=observed_at)
        return True

    def save_analysis_snapshot(self, market: Market, symbol: str, *, observed_at: datetime, parameter_version: str, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO stock_paper_analysis_snapshots
                (market, symbol, observed_at, parameter_version, payload) VALUES (?, ?, ?, ?, ?)""",
                (market.value, symbol.upper(), observed_at.isoformat(), parameter_version, _json_dumps(payload)),
            )

    def latest_analysis_snapshot(self, market: Market, symbol: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT payload FROM stock_paper_analysis_snapshots WHERE market=? AND symbol=?
                ORDER BY observed_at DESC LIMIT 1""",
                (market.value, symbol.upper()),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def record_entry_rejection(
        self,
        market: Market,
        symbol: str,
        *,
        gate: str,
        measured_value: Any,
        threshold: Any,
        payload: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
        stale_seconds: int = 300,
    ) -> bool:
        now = observed_at or datetime.now(timezone.utc)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT ts FROM stock_paper_entry_rejections WHERE market=? AND symbol=? AND gate=?
                ORDER BY ts DESC LIMIT 1""",
                (market.value, symbol.upper(), gate),
            ).fetchone()
            if row:
                previous = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
                if (now - previous).total_seconds() < stale_seconds:
                    return False
            connection.execute(
                """INSERT INTO stock_paper_entry_rejections
                (market, symbol, ts, gate, measured_value, threshold, payload) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    market.value,
                    symbol.upper(),
                    now.isoformat(),
                    gate,
                    json.dumps(measured_value, ensure_ascii=False),
                    json.dumps(threshold, ensure_ascii=False),
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
        return True

    def rejection_distribution(self, days: int = 7) -> dict[str, Any]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT market, gate, COUNT(*) AS count, MAX(ts) AS latest_at
                FROM stock_paper_entry_rejections WHERE ts>=?
                GROUP BY market, gate ORDER BY count DESC, gate""",
                (since,),
            ).fetchall()
        gates = [dict(row) for row in rows]
        return {"period_days": days, "total": sum(int(row["count"]) for row in rows), "gates": gates}

    def update_benchmark(self, market: Market, price: float, observed_at: datetime) -> None:
        if price <= 0:
            return
        with self._connect() as connection:
            connection.execute(
                """UPDATE stock_paper_tracks SET
                benchmark_start=COALESCE(benchmark_start, ?), benchmark_current=?,
                benchmark_observed_at=?, updated_at=? WHERE market=?""",
                (price, price, observed_at.isoformat(), observed_at.isoformat(), market.value),
            )

    def record_fx(self, payload: dict[str, Any], observed_at: datetime) -> None:
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            return
        try:
            rate = float(result["rate"])
        except (KeyError, TypeError, ValueError):
            return
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO stock_paper_fx_snapshots
                (base_currency, quote_currency, rate, observed_at, valid_from, valid_until, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(result.get("baseCurrency") or "USD"),
                    str(result.get("quoteCurrency") or "KRW"),
                    rate,
                    observed_at.isoformat(),
                    result.get("validFrom"),
                    result.get("validUntil"),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    def update_marks(self, market: Market, prices: dict[str, float], observed_at: datetime) -> None:
        with self._connect() as connection:
            connection.executemany(
                """INSERT INTO stock_paper_marks (market, symbol, price, observed_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(market, symbol) DO UPDATE SET price=excluded.price, observed_at=excluded.observed_at""",
                [(market.value, symbol.upper(), price, observed_at.isoformat()) for symbol, price in prices.items() if price > 0],
            )

    def save_order(self, order: StockOrder, observed_at: datetime | None = None) -> None:
        updated = (observed_at or datetime.now(timezone.utc)).isoformat()
        payload = order.payload()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO stock_paper_orders
                (id, market, symbol, side, status, signal_at, updated_at, reason, entry_mode, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at,
                reason=excluded.reason, entry_mode=excluded.entry_mode, payload=excluded.payload""",
                (
                    order.id,
                    order.market.value,
                    order.symbol,
                    order.side.value,
                    order.status.value,
                    payload["signal_at"],
                    updated,
                    order.reason,
                    order.entry_mode,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    def save_fill(self, fill: PaperFill) -> None:
        payload = fill.payload()
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO stock_paper_fills
                (id, order_id, market, symbol, side, filled_at, entry_mode, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fill.id,
                    fill.order_id,
                    fill.market.value,
                    fill.symbol,
                    fill.side.value,
                    payload["filled_at"],
                    fill.entry_mode,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            signed_cash = -1 if fill.side == Side.BUY else 1
            net_cash = signed_cash * fill.gross_amount - fill.commission - fill.transaction_tax
            connection.execute(
                "UPDATE stock_paper_tracks SET cash=cash+?, updated_at=? WHERE market=?",
                (net_cash, payload["filled_at"], fill.market.value),
            )
            connection.execute(
                """UPDATE stock_paper_mode_accounts SET cash=cash+?, updated_at=?
                WHERE market=? AND entry_mode=?""",
                (net_cash, payload["filled_at"], fill.market.value, fill.entry_mode),
            )
            row = connection.execute(
                "SELECT quantity, average_price FROM stock_paper_positions WHERE market=? AND symbol=?",
                (fill.market.value, fill.symbol),
            ).fetchone()
            old_qty = int(row["quantity"]) if row else 0
            old_average = float(row["average_price"]) if row else 0.0
            quantity = old_qty + fill.quantity if fill.side == Side.BUY else max(0, old_qty - fill.quantity)
            average = ((old_average * old_qty) + (fill.price * fill.quantity)) / quantity if fill.side == Side.BUY and quantity else old_average
            connection.execute(
                """INSERT INTO stock_paper_positions (market, symbol, quantity, average_price, currency, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(market, symbol) DO UPDATE SET quantity=excluded.quantity,
                average_price=excluded.average_price, updated_at=excluded.updated_at""",
                (fill.market.value, fill.symbol, quantity, average, fill.currency.value, payload["filled_at"]),
            )
            mode_row = connection.execute(
                """SELECT quantity, average_price FROM stock_paper_mode_positions
                WHERE market=? AND symbol=? AND entry_mode=?""",
                (fill.market.value, fill.symbol, fill.entry_mode),
            ).fetchone()
            mode_old_qty = int(mode_row["quantity"]) if mode_row else 0
            mode_old_average = float(mode_row["average_price"]) if mode_row else 0.0
            mode_quantity = mode_old_qty + fill.quantity if fill.side == Side.BUY else max(0, mode_old_qty - fill.quantity)
            mode_average = (
                ((mode_old_average * mode_old_qty) + (fill.price * fill.quantity)) / mode_quantity
                if fill.side == Side.BUY and mode_quantity
                else mode_old_average
            )
            connection.execute(
                """INSERT INTO stock_paper_mode_positions
                (market, symbol, entry_mode, quantity, average_price, currency, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market, symbol,entry_mode) DO UPDATE SET quantity=excluded.quantity,
                average_price=excluded.average_price, updated_at=excluded.updated_at""",
                (
                    fill.market.value,
                    fill.symbol,
                    fill.entry_mode,
                    mode_quantity,
                    mode_average,
                    fill.currency.value,
                    payload["filled_at"],
                ),
            )

    def record_event(
        self,
        market: Market,
        event_type: str,
        *,
        symbol: str | None = None,
        order_id: str | None = None,
        reason: str | None = None,
        payload: dict[str, Any] | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        now = (observed_at or datetime.now(timezone.utc)).isoformat()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO stock_paper_events
                (market, symbol, order_id, event_type, reason, observed_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (market.value, symbol, order_id, event_type, reason, now, json.dumps(payload or {}, ensure_ascii=False)),
            )

    def record_event_if_stale(
        self,
        market: Market,
        event_type: str,
        *,
        symbol: str,
        reason: str,
        payload: dict[str, Any] | None = None,
        stale_seconds: int = 300,
        observed_at: datetime | None = None,
    ) -> bool:
        now = observed_at or datetime.now(timezone.utc)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT observed_at FROM stock_paper_events
                WHERE market=? AND symbol=? AND event_type=? AND reason=?
                ORDER BY observed_at DESC LIMIT 1""",
                (market.value, symbol, event_type, reason),
            ).fetchone()
        if row:
            previous = datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
            if (now - previous).total_seconds() < stale_seconds:
                return False
        self.record_event(market, event_type, symbol=symbol, reason=reason, payload=payload, observed_at=now)
        return True

    def stop_track(self, market: Market, reason: str, now: datetime | None = None) -> None:
        updated = (now or datetime.now(timezone.utc)).isoformat()
        with self._connect() as connection:
            connection.execute(
                "UPDATE stock_paper_tracks SET status='stopped', stop_reason=?, updated_at=? WHERE market=?",
                (reason, updated, market.value),
            )
        self.record_event(market, "track_stopped", reason=reason, observed_at=now)

    def list_orders(self, statuses: tuple[OrderStatus, ...] | None = None) -> list[StockOrder]:
        query = "SELECT payload FROM stock_paper_orders"
        parameters: list[Any] = []
        if statuses:
            query += f" WHERE status IN ({','.join('?' for _ in statuses)})"
            parameters.extend(item.value for item in statuses)
        query += " ORDER BY updated_at"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_order_from_payload(json.loads(row["payload"])) for row in rows]

    def position_quantity(self, market: Market, symbol: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT quantity FROM stock_paper_positions WHERE market=? AND symbol=?",
                (market.value, symbol.upper()),
            ).fetchone()
        return int(row["quantity"]) if row else 0

    def position_symbols(self, market: Market) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT symbol FROM stock_paper_positions WHERE market=? AND quantity>0 ORDER BY symbol",
                (market.value,),
            ).fetchall()
        return [str(row["symbol"]) for row in rows]

    def has_active_order(self, market: Market, symbol: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM stock_paper_orders WHERE market=? AND symbol=?
                AND status IN ('queued', 'partial') LIMIT 1""",
                (market.value, symbol.upper()),
            ).fetchone()
        return row is not None

    def mode_position_count(self, market: Market, entry_mode: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS count FROM stock_paper_mode_positions
                WHERE market=? AND entry_mode=? AND quantity>0""",
                (market.value, entry_mode),
            ).fetchone()
        return int(row["count"]) if row else 0

    def mode_active_order_count(self, market: Market, entry_mode: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS count FROM stock_paper_orders
                WHERE market=? AND entry_mode=? AND status IN ('queued', 'partial')""",
                (market.value, entry_mode),
            ).fetchone()
        return int(row["count"]) if row else 0

    def list_fills(self, limit: int = 100) -> list[PaperFill]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM stock_paper_fills ORDER BY filled_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_fill_from_payload(json.loads(row["payload"])) for row in rows]

    def list_instrument_fills(self, market: Market, symbol: str, limit: int = 100) -> list[PaperFill]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT payload FROM stock_paper_fills
                WHERE market=? AND symbol=? ORDER BY filled_at DESC LIMIT ?""",
                (market.value, symbol.upper(), limit),
            ).fetchall()
        return [_fill_from_payload(json.loads(row["payload"])) for row in rows]

    def dashboard(self) -> dict[str, Any]:
        with self._connect() as connection:
            tracks = [dict(row) for row in connection.execute("SELECT * FROM stock_paper_tracks ORDER BY market").fetchall()]
            events = connection.execute(
                """SELECT market, reason, COUNT(*) AS count FROM stock_paper_events
                WHERE reason IS NOT NULL AND event_type NOT IN ('validation_clock_started', 'validation_clock_invalidated')
                GROUP BY market, reason"""
            ).fetchall()
            fills = [
                json.loads(row["payload"]) for row in connection.execute("SELECT payload FROM stock_paper_fills ORDER BY filled_at DESC LIMIT 100").fetchall()
            ]
            fill_count = int(connection.execute("SELECT COUNT(*) FROM stock_paper_fills").fetchone()[0])
            positions = [
                dict(row)
                for row in connection.execute(
                    """SELECT p.*, m.price AS current_price, m.observed_at AS mark_observed_at
                    FROM stock_paper_positions p LEFT JOIN stock_paper_marks m
                    ON m.market=p.market AND m.symbol=p.symbol
                    WHERE p.quantity > 0 ORDER BY p.market, p.symbol"""
                ).fetchall()
            ]
            mode_accounts = [dict(row) for row in connection.execute("SELECT * FROM stock_paper_mode_accounts ORDER BY market, entry_mode").fetchall()]
            mode_positions = [
                dict(row)
                for row in connection.execute(
                    """SELECT p.*, m.price AS current_price, m.observed_at AS mark_observed_at
                    FROM stock_paper_mode_positions p LEFT JOIN stock_paper_marks m
                    ON m.market=p.market AND m.symbol=p.symbol
                    WHERE p.quantity > 0 ORDER BY p.market, p.entry_mode, p.symbol"""
                ).fetchall()
            ]
        reason_by_market: dict[str, Counter[str]] = {"KR": Counter(), "US": Counter()}
        for row in events:
            reason_by_market[str(row["market"])][str(row["reason"])] = int(row["count"])
        now = datetime.now(timezone.utc)
        result_tracks = []
        for track in tracks:
            start = datetime.fromisoformat(str(track["started_at"]).replace("Z", "+00:00"))
            benchmark_return = None
            if track["benchmark_start"] and track["benchmark_current"]:
                benchmark_return = (float(track["benchmark_current"]) / float(track["benchmark_start"]) - 1) * 100
            track_positions = [item for item in positions if item["market"] == track["market"]]
            marks_complete = all(item["current_price"] is not None for item in track_positions)
            nav = float(track["cash"]) + sum(
                int(item["quantity"]) * float(item["current_price"]) for item in track_positions if item["current_price"] is not None
            )
            engine_return = (nav / float(track["initial_cash"]) - 1) * 100 if marks_complete else None
            result_tracks.append(
                {
                    **track,
                    "elapsed_days": max(0, min(28, (now - start).days)) if bool(track.get("clock_valid")) else 0,
                    "benchmark_return_pct": round(benchmark_return, 4) if benchmark_return is not None else None,
                    "nav": round(nav, 4) if marks_complete else None,
                    "nav_complete": marks_complete,
                    "engine_return_pct": round(engine_return, 4) if engine_return is not None else None,
                    "rejection_reasons": dict(reason_by_market[str(track["market"])]),
                }
            )
        mode_performance = []
        for account in mode_accounts:
            account_positions = [item for item in mode_positions if item["market"] == account["market"] and item["entry_mode"] == account["entry_mode"]]
            marks_complete = all(item["current_price"] is not None for item in account_positions)
            nav = float(account["cash"]) + sum(
                int(item["quantity"]) * float(item["current_price"]) for item in account_positions if item["current_price"] is not None
            )
            return_pct = (nav / float(account["initial_cash"]) - 1) * 100 if marks_complete else None
            mode_performance.append(
                {
                    **account,
                    "position_count": len(account_positions),
                    "nav": round(nav, 4) if marks_complete else None,
                    "nav_complete": marks_complete,
                    "return_pct": round(return_pct, 4) if return_pct is not None else None,
                    "validation_eligible": account["entry_mode"] == "strict_signal",
                }
            )
        return {
            "as_of": now.isoformat(),
            "tracks": result_tracks,
            "positions": positions,
            "recent_fills": fills,
            "fill_count": fill_count,
            "mode_performance": mode_performance,
            "mode_positions": mode_positions,
            "live_orders_enabled": False,
            "performance_gate": "Toss 실주문은 주식 페이퍼가 4주간 벤치마크를 초과할 경우에만 재논의",
            "sample_note": "KR/US 원통화 성적이며 크립토 검증과 합산하지 않습니다.",
            "entry_rejection_distribution": self.rejection_distribution(7),
        }


def _order_from_payload(payload: dict[str, Any]) -> StockOrder:
    return StockOrder(
        id=str(payload["id"]),
        symbol=str(payload["symbol"]),
        market=Market(payload["market"]),
        currency=Currency(payload["currency"]),
        side=Side(payload["side"]),
        quantity=int(payload["quantity"]),
        signal_at=datetime.fromisoformat(str(payload["signal_at"]).replace("Z", "+00:00")),
        status=OrderStatus(payload["status"]),
        remaining_quantity=int(payload["remaining_quantity"]),
        signal_price=float(payload["signal_price"]) if payload.get("signal_price") is not None else None,
        reason=payload.get("reason"),
        evidence=dict(payload.get("evidence") or {}),
        entry_mode=str(payload.get("entry_mode") or "strict_signal"),
    )


def _fill_from_payload(payload: dict[str, Any]) -> PaperFill:
    return PaperFill(
        id=str(payload["id"]),
        order_id=str(payload["order_id"]),
        symbol=str(payload["symbol"]),
        market=Market(payload["market"]),
        currency=Currency(payload["currency"]),
        side=Side(payload["side"]),
        quantity=int(payload["quantity"]),
        price=float(payload["price"]),
        filled_at=datetime.fromisoformat(str(payload["filled_at"]).replace("Z", "+00:00")),
        gross_amount=float(payload["gross_amount"]),
        commission=float(payload["commission"]),
        transaction_tax=float(payload["transaction_tax"]),
        fx_rate_to_krw=float(payload["fx_rate_to_krw"]) if payload.get("fx_rate_to_krw") is not None else None,
        fx_observed_at=(datetime.fromisoformat(str(payload["fx_observed_at"]).replace("Z", "+00:00")) if payload.get("fx_observed_at") else None),
        entry_mode=str(payload.get("entry_mode") or "strict_signal"),
    )
