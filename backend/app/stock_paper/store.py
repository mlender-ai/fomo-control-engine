from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from enum import Enum
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import UUID

from app.db.sqlite_utils import connect_sqlite

from .models import Currency, Market, OrderStatus, PaperFill, Side, StockOrder


def _liveness_clock(started_at: datetime, effective_days: set[str], now: datetime) -> dict[str, Any]:
    """검증 시계 보정을 `liveness.elapsed_excluding_gaps` 하나로 통일한다(중복 구현 금지).

    지연 import 인 이유: 트레이딩 스토어가 워커 모듈에 로드 시점 의존성을 갖지 않게 하기 위함.
    """
    from app.worker.liveness import elapsed_excluding_gaps

    return elapsed_excluding_gaps(started_at, effective_days, now)


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


# ── WO-FCE-STOCK-STATUS-01 3-2·3-3 ────────────────────────────────────

# 거부 카운터의 창. **전체 누적**이며 창이 없다는 사실 자체를 라벨로 낸다.
#
# 실측 2026-08-25: KR 1,762만 · US 137만. 이것은 서로 다른 거부가 아니라 큐에 남은
# 주문 15,755건(전부 `session_closed`)이 매 틱 다시 계수된 값이다. 창을 적지 않으면
# "1,762만 번 판단했다"로 읽히는데 그런 판단은 없었다 — `METRIC-TRUTH-01` 이 크립토에서
# 고친 것과 같은 결함이다.
REJECTION_COUNTER_WINDOW = "전체 누적 (창 없음)"
REJECTION_COUNTER_NOTE = "서로 다른 거부 건수가 아니라 이벤트 행 수다. 큐에 남은 주문이 매 실행 다시 계수되므로 같은 주문이 여러 번 들어간다."

# 정지를 푸는 절차. 화면이 이 문자열을 그대로 보여준다 — 지금은 푸는 법이 어디에도 없다.
RESUME_PROCEDURE = (
    "① 정지 사유와 유발 주문을 확인한다(아래 이력). "
    "② 원인이 시세 공급 결함이면 공급을 먼저 고친다. "
    "③ 원인 확인 후 수동 재개: UPDATE stock_paper_tracks SET status='running', stop_reason=NULL WHERE market='<MARKET>'. "
    "자동 재개는 금지다 — 원인 없이 재개하면 같은 체결이 다시 들어간다."
)


# 검증 대상 계정. 탐색(`coverage`)은 체결 파이프라인 표본을 빠르게 만들기 위한 별도
# 소액 계정이며 전략 성적에 합산하지 않는다(C4).
STRATEGY_ENTRY_MODE = "strict_signal"
EXPLORATION_ENTRY_MODE = "coverage"


def _sample_breakdown(market: str, fill_counts: dict[tuple[str, str], int]) -> dict[str, Any]:
    """전략 표본과 탐색 표본을 갈라서 낸다 (3-4).

    트랙 헤드라인(`engine_return_pct`)은 **트랙 전체 계정**의 값이고, 지금 그 값은 사실상
    탐색 계정이 움직인 결과다 — 실측 KR +0.0743% 가 `coverage` 계정 수익률과 같은 수였다.
    전략 표본이 0 이라는 사실이 그 옆에 없으면 성적으로 읽힌다(C5).
    """
    strategy = int(fill_counts.get((market, STRATEGY_ENTRY_MODE), 0))
    exploration = int(fill_counts.get((market, EXPLORATION_ENTRY_MODE), 0))
    return {
        "strategy_fills": strategy,
        "exploration_fills": exploration,
        "strategy_sample_zero": strategy == 0,
        "validation_eligible_mode": STRATEGY_ENTRY_MODE,
        "headline_note": (
            "트랙 수익률은 탐색·전략을 합친 계정 값이다. 전략 표본이 0 이면 그 수익률은 탐색 계정이 움직인 결과다."
            if strategy == 0
            else "트랙 수익률은 탐색·전략을 합친 계정 값이다. 전략 성적은 mode_performance 의 strict_signal 을 본다."
        ),
        "exclusion_note": "탐색 표본은 전략 성적에 합산하지 않는다(C4).",
    }


def _halt_block(track: dict[str, Any], halts: list[dict[str, Any]]) -> dict[str, Any]:
    """트랙 정지 상태. 정지했으면 **언제·왜·어떻게 푸는지**가 함께 나와야 한다(3-2).

    `updated_at` 은 정지 시각이 아니다 — 트랙 행이 갱신될 때마다 바뀐다. 정지 시각은
    `track_stopped` 이벤트에만 있고, 그 이벤트가 없으면 **없다고 적는다**(C5).
    """
    stopped = str(track.get("status") or "") == "stopped"
    latest = halts[0] if halts else None
    return {
        "stopped": stopped,
        "reason": track.get("stop_reason"),
        # 이력이 있으면 그 시각, 없으면 None. `updated_at` 으로 대신하지 않는다.
        "stopped_at": latest.get("observed_at") if latest else None,
        "stopped_at_known": bool(latest),
        # 정지했는데 이력이 없을 때만 적는다. 돌고 있는 트랙에 띄우면 소음이다.
        "evidence_note": (
            "정지 시각·유발 주문을 조회할 수 없다 — 리텐션이 사건 이벤트를 함께 지웠다(2026-08-25 수리). 이후 정지부터 남는다."
            if stopped and not latest
            else None
        ),
        "history": halts,
        "resume_procedure": RESUME_PROCEDURE if stopped else None,
        "auto_resume": False,
        "auto_resume_note": "자동 재개는 금지다(C2). 원인 확인 후 수동으로만 푼다.",
    }


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

    def effective_days(self, market: Market) -> set[str]:
        """검증일로 카운트되는 날짜(UTC, ISO date) 집합.

        WO-FCE-OBSERVATION-INTEGRITY-01: **커버리지 게이트를 통과한 날만** 센다.
        이전엔 "분석 스냅샷이 하루 한 건이라도 있으면 그 날은 검증일"이었는데, 그 기준으로는
        정규장 6.5시간 중 1.5시간만 수집한 날(KST 롤오버 결함)도 온전한 검증일로 세어졌다.
        실측 2026-08-04 US 커버리지 23% — 그런 날 3주치가 "검증했다"로 집계되고 있었다.

        커버리지 테이블이 아직 없으면(첫 기동·마이그레이션 직후) **빈 집합**을 돌려준다.
        모르면서 아는 척하느니 "아직 0일"이 정직하다.
        """
        if not self.enabled:
            return set()
        track = "stock_kr" if market == Market.KR else "stock_us"
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    "SELECT day FROM observation_coverage WHERE track=? AND valid=1",
                    (track,),
                ).fetchall()
            except sqlite3.OperationalError:
                return set()
        return {str(row["day"]) for row in rows if row["day"]}

    def latest_analysis_at(self, market: Market) -> str | None:
        """시장별 마지막 분석 시각 — WO-FCE-PAPER-ENTRY-REALITY-01(D3) 미장 독립 생존 판정용.

        KR·US 를 한 잡(`toss_stock_scout`)이 함께 수집하므로 잡 하트비트로는 시장별 정지를
        구분할 수 없다. 실제 평가 흔적(분석 스냅샷)이 시장별 생존의 유일한 증거다.
        """
        if not self.enabled:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(observed_at) AS latest FROM stock_paper_analysis_snapshots WHERE market=?",
                (market.value,),
            ).fetchone()
        return str(row["latest"]) if row and row["latest"] else None

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

    # ── 청산 경로 (WO-FCE-STOCK-EXIT-01) ──────────────────────────

    def set_exit_plan(self, market: Market, symbol: str, plan: dict[str, Any], opened_at: datetime) -> None:
        """진입 시점의 무효화선·목표를 고정 저장한다.

        청산 판단이 '진입 근거로 삼은 선'을 그대로 쓰게 하기 위함이다. 최신 분석으로
        매번 다시 계산하면 기준선이 움직여 손절이 사후적으로 정당화된다.
        """
        with self._connect() as connection:
            connection.execute(
                """UPDATE stock_paper_positions
                SET exit_plan=?, opened_at=COALESCE(opened_at, ?)
                WHERE market=? AND symbol=?""",
                (_json_dumps(plan), opened_at.isoformat(), market.value, symbol.upper()),
            )
            connection.execute(
                """UPDATE stock_paper_mode_positions
                SET opened_at=COALESCE(opened_at, ?)
                WHERE market=? AND symbol=?""",
                (opened_at.isoformat(), market.value, symbol.upper()),
            )

    def open_positions(self, market: Market | None = None) -> list[dict[str, Any]]:
        """청산 판단 대상인 보유 포지션. 통계 제외분도 포함한다(청산은 해야 하므로)."""
        query = """SELECT p.*, m.price AS current_price, m.observed_at AS mark_observed_at
            FROM stock_paper_positions p LEFT JOIN stock_paper_marks m
            ON m.market=p.market AND m.symbol=p.symbol
            WHERE p.quantity > 0"""
        params: tuple[Any, ...] = ()
        if market is not None:
            query += " AND p.market=?"
            params = (market.value,)
        with self._connect() as connection:
            rows = [dict(row) for row in connection.execute(query + " ORDER BY p.market, p.symbol", params).fetchall()]
        for row in rows:
            raw = row.get("exit_plan")
            try:
                row["exit_plan"] = json.loads(raw) if raw else {}
            except (TypeError, ValueError):
                row["exit_plan"] = {}
        return rows

    def record_realized_level(self, market: Market, symbol: str, level: int) -> None:
        """TP 사다리에서 실현한 단계를 기록해 같은 단계가 반복 체결되지 않게 한다."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT exit_plan FROM stock_paper_positions WHERE market=? AND symbol=?",
                (market.value, symbol.upper()),
            ).fetchone()
            if row is None:
                return
            try:
                plan = json.loads(row["exit_plan"]) if row["exit_plan"] else {}
            except (TypeError, ValueError):
                plan = {}
            levels = sorted({int(item) for item in plan.get("realized_levels") or []} | {int(level)})
            plan["realized_levels"] = levels
            connection.execute(
                "UPDATE stock_paper_positions SET exit_plan=? WHERE market=? AND symbol=?",
                (_json_dumps(plan), market.value, symbol.upper()),
            )

    def mark_excluded_from_stats(self, market: Market, symbol: str, reason: str) -> None:
        """고아 포지션을 성과 통계에서 제외한다(C4). 원장 행은 그대로 남는다."""
        with self._connect() as connection:
            for table in ("stock_paper_positions", "stock_paper_mode_positions"):
                connection.execute(
                    f"UPDATE {table} SET excluded_from_stats=1, exclusion_reason=? WHERE market=? AND symbol=?",
                    (reason, market.value, symbol.upper()),
                )

    def excluded_symbols(self, market: Market | None = None) -> list[dict[str, Any]]:
        query = """SELECT market, symbol, quantity, average_price, exclusion_reason
            FROM stock_paper_positions WHERE excluded_from_stats=1"""
        params: tuple[Any, ...] = ()
        if market is not None:
            query += " AND market=?"
            params = (market.value,)
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

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
        """진입 게이팅용 모드별 보유 수 — **청산 엔진이 관리할 수 없는 포지션은 세지 않는다.**

        WO-FCE-BREACH-ALERT-FIX-01 실측: 청산 경로 부재 시절 진입한 고아 포지션을
        `excluded_from_stats` 로 통계에서 제외하기로 했으나 슬롯 카운트에서는 제외하지
        않아 coverage_slots_used 가 KR 3/US 3 으로 고정됐다. 목표가 3이므로 3 >= 3 →
        coverage 레인 영구 차단이고, 청산 가능 포지션이 0건이라 슬롯이 절대 비지 않았다.
        **진입 0과 청산 0이 서로를 지탱하는 데드락**이었다.

        관리 대상이 아닌 포지션이 관리 예산을 잡아먹으면 게이트 자체가 죽는다.
        고아는 자본과 max_open_positions 는 계속 점유하며, 그 한도는 별도로 검사된다.
        """
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS count FROM stock_paper_mode_positions
                WHERE market=? AND entry_mode=? AND quantity>0 AND excluded_from_stats=0""",
                (market.value, entry_mode),
            ).fetchone()
        return int(row["count"]) if row else 0

    def exit_exempt_count(self, market: Market, entry_mode: str) -> int:
        """청산 엔진 관리 밖에 있는 보유 수 — 침묵 금지: 대시보드·리포트에 노출한다."""
        with self._connect() as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS count FROM stock_paper_mode_positions
                WHERE market=? AND entry_mode=? AND quantity>0 AND excluded_from_stats=1""",
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
            # 3-2: 정지 이력. 리텐션이 지우지 않는 종류이므로 이제 남는다.
            halt_events = connection.execute(
                """SELECT market, event_type, symbol, reason, observed_at, payload FROM stock_paper_events
                WHERE event_type IN ('track_stopped', 'invariant_failure')
                ORDER BY id DESC LIMIT 20"""
            ).fetchall()
            fills = [
                json.loads(row["payload"]) for row in connection.execute("SELECT payload FROM stock_paper_fills ORDER BY filled_at DESC LIMIT 100").fetchall()
            ]
            fill_count = int(connection.execute("SELECT COUNT(*) FROM stock_paper_fills").fetchone()[0])
            # 3-4: 전략(엄격) 표본을 시장별로 센다. 헤드라인 수익률이 어느 계정 값인지
            # 보이지 않으면 탐색 표본 5건의 +0.07% 가 전략 성적으로 읽힌다.
            mode_fill_counts = {
                (str(row["market"]), str(row["entry_mode"])): int(row["count"])
                for row in connection.execute("SELECT market, entry_mode, COUNT(*) AS count FROM stock_paper_fills GROUP BY market, entry_mode").fetchall()
            }
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
        halts_by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in halt_events:
            payload: dict[str, Any] = {}
            try:
                parsed = json.loads(str(row["payload"] or "{}"))
                payload = parsed if isinstance(parsed, dict) else {}
            except (TypeError, ValueError):
                payload = {}
            halts_by_market[str(row["market"])].append(
                {
                    "event_type": str(row["event_type"]),
                    "symbol": row["symbol"],
                    "reason": row["reason"],
                    "observed_at": row["observed_at"],
                    "detail": payload.get("error") or payload.get("reason"),
                }
            )
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
            # C5: 유실 구간을 정상 검증 기간으로 계산하지 않는다. 달력일과 실측일을 **둘 다** 싣고,
            # 화면은 "경과 N일 (유실 M일 제외)" 로 정직하게 표기한다.
            clock = _liveness_clock(start, self.effective_days(Market(track["market"])), now)
            result_tracks.append(
                {
                    **track,
                    "elapsed_days": min(28, clock["effective_days"]) if bool(track.get("clock_valid")) else 0,
                    "calendar_days": clock["calendar_days"],
                    "lost_days": clock["lost_days"],
                    "elapsed_label": clock["label"] if bool(track.get("clock_valid")) else "첫 수집 대기",
                    "benchmark_return_pct": round(benchmark_return, 4) if benchmark_return is not None else None,
                    "nav": round(nav, 4) if marks_complete else None,
                    "nav_complete": marks_complete,
                    "engine_return_pct": round(engine_return, 4) if engine_return is not None else None,
                    "rejection_reasons": dict(reason_by_market[str(track["market"])]),
                    # 3-3: 창을 명시한다. 이 수는 **전체 누적**이고 서로 다른 후보 수가 아니다.
                    "rejection_window": REJECTION_COUNTER_WINDOW,
                    "rejection_counter_note": REJECTION_COUNTER_NOTE,
                    # 3-2: 정지 상태를 시각·사유·재개 절차와 함께 낸다.
                    "halt": _halt_block(track, halts_by_market.get(str(track["market"]), [])),
                    # 3-4: 표본이 어느 계정 것인지 명시한다.
                    "sample_breakdown": _sample_breakdown(str(track["market"]), mode_fill_counts),
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
