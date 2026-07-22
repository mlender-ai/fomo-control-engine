from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.db.models import JudgmentLedgerEntry, JudgmentScore
from app.db.sqlite_utils import connect_sqlite

from .models import PaperFill, PaperOrder, PolyMarket, ProbabilityEstimate


POLY_LEDGER_POSITION_ID = uuid5(NAMESPACE_URL, "fce:polymarket:paper:ledger")


class PolyPaperStore:
    def __init__(self, database_url: str) -> None:
        self.path = database_url.removeprefix("sqlite:///") if database_url.startswith("sqlite:///") else ""

    @property
    def enabled(self) -> bool:
        return bool(self.path)

    def _connect(self) -> sqlite3.Connection:
        if not self.enabled:
            raise RuntimeError("Polymarket paper store requires SQLite")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        return connect_sqlite(self.path)

    def ensure_track(self, *, initial_cash: float, parameter_version: str, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO poly_paper_track
                (id, currency, parameter_version, initial_cash, cash, status, created_at, updated_at)
                VALUES (1, 'USDC', ?, ?, ?, 'waiting', ?, ?)""",
                (parameter_version, initial_cash, initial_cash, now.isoformat(), now.isoformat()),
            )

    def activate_clock(self, observed_at: datetime) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT clock_valid FROM poly_paper_track WHERE id=1").fetchone()
            if row is None or bool(row["clock_valid"]):
                return False
            connection.execute(
                """UPDATE poly_paper_track SET clock_valid=1, status='running', started_at=?, ends_at=?,
                updated_at=? WHERE id=1""",
                (observed_at.isoformat(), (observed_at + timedelta(weeks=4)).isoformat(), observed_at.isoformat()),
            )
        return True

    def record_collection(self, *, status: str, observed_at: datetime, error: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE poly_paper_track SET last_collection_at=?, last_collection_status=?,
                last_collection_error=?, updated_at=? WHERE id=1""",
                (observed_at.isoformat(), status, error, observed_at.isoformat()),
            )

    def stop_track(self, reason: str, observed_at: datetime) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE poly_paper_track SET status='stopped', stop_reason=?, updated_at=? WHERE id=1",
                (reason, observed_at.isoformat()),
            )

    def save_market(self, market: PolyMarket) -> None:
        payload = _market_payload(market)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO poly_markets
                (market_id, slug, question, category, observed_at, end_at, active, closed,
                 market_probability, liquidity, trade_eligible, exclusion_reason, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_id) DO UPDATE SET slug=excluded.slug, question=excluded.question,
                category=excluded.category, observed_at=excluded.observed_at, end_at=excluded.end_at,
                active=excluded.active, closed=excluded.closed, market_probability=excluded.market_probability,
                liquidity=excluded.liquidity, trade_eligible=excluded.trade_eligible,
                exclusion_reason=excluded.exclusion_reason, payload=excluded.payload""",
                (
                    market.id,
                    market.slug,
                    market.question,
                    market.category.value,
                    market.observed_at.isoformat(),
                    market.end_at.isoformat() if market.end_at else None,
                    int(market.active),
                    int(market.closed),
                    market.market_probability,
                    market.liquidity,
                    int(market.trade_eligible),
                    market.exclusion_reason,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    def latest_estimate_at(self, market_id: str) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT observed_at FROM poly_estimates WHERE market_id=? ORDER BY observed_at DESC LIMIT 1",
                (market_id,),
            ).fetchone()
        return _datetime(row["observed_at"]) if row else None

    def latest_estimate_needs_execution_retry(self, market_id: str) -> bool:
        """Return true when a qualified estimate never reached the order ledger.

        This closes the crash window between appending an estimate and writing
        its PaperBroker order. A later collection may re-price that candidate
        instead of suppressing it for the normal estimate interval.
        """
        with self._connect() as connection:
            row = connection.execute(
                """SELECT e.trade_eligible, o.id AS order_id
                FROM poly_estimates e
                LEFT JOIN poly_orders o ON o.estimate_id=e.id
                WHERE e.market_id=?
                ORDER BY e.observed_at DESC LIMIT 1""",
                (market_id,),
            ).fetchone()
        return bool(row and row["trade_eligible"] and row["order_id"] is None)

    def save_estimate(self, estimate: ProbabilityEstimate, repository: Any) -> str:
        judgment_id = f"poly:{estimate.market_id}:{estimate.id}"
        payload = estimate.payload()
        with self._connect() as connection:
            market_row = connection.execute(
                "SELECT category FROM poly_markets WHERE market_id=?",
                (estimate.market_id,),
            ).fetchone()
            if market_row is None:
                raise RuntimeError("Polymarket estimate requires a persisted market")
            connection.execute(
                """INSERT INTO poly_estimates
                (id, judgment_id, market_id, observed_at, category, market_probability,
                 estimated_probability, confidence_low, confidence_high, estimate_quality,
                 direction, gross_edge, effective_price, after_cost_edge, trade_eligible, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    estimate.id,
                    judgment_id,
                    estimate.market_id,
                    estimate.observed_at.isoformat(),
                    str(market_row["category"]),
                    estimate.market_probability,
                    estimate.estimated_probability,
                    estimate.confidence_low,
                    estimate.confidence_high,
                    estimate.quality.value,
                    estimate.direction.value,
                    estimate.gross_edge,
                    estimate.effective_price,
                    estimate.after_cost_edge,
                    int(estimate.trade_eligible),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
        repository.add_judgment(
            JudgmentLedgerEntry(
                judgment_id=judgment_id,
                position_id=POLY_LEDGER_POSITION_ID,
                source_type="polymarket",
                source_id=estimate.market_id,
                as_of=estimate.observed_at,
                type="probability_estimate",
                claim=payload,
                confidence=round((estimate.confidence_high - estimate.confidence_low) * -100 + 100),
                param_version={"poly": "poly-v1", "entity_type": "polymarket"},
            )
        )
        return judgment_id

    def cash(self) -> float:
        with self._connect() as connection:
            row = connection.execute("SELECT cash FROM poly_paper_track WHERE id=1").fetchone()
        return float(row["cash"]) if row else 0.0

    def open_position_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM poly_positions WHERE status='open'").fetchone()
        return int(row["count"]) if row else 0

    def has_open_position(self, market_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM poly_positions WHERE market_id=? AND status='open'",
                (market_id,),
            ).fetchone()
        return row is not None

    def save_execution(self, order: PaperOrder, *, status: str, reason: str | None, fill: PaperFill | None) -> None:
        order_payload = {
            "id": order.id,
            "market_id": order.market_id,
            "estimate_id": order.estimate_id,
            "token_id": order.token_id,
            "direction": order.direction.value,
            "requested_notional": order.requested_notional,
            "created_at": order.created_at.isoformat(),
        }
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO poly_orders
                (id, market_id, estimate_id, token_id, direction, requested_notional,
                 status, reason, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    order.id,
                    order.market_id,
                    order.estimate_id,
                    order.token_id,
                    order.direction.value,
                    order.requested_notional,
                    status,
                    reason,
                    order.created_at.isoformat(),
                    json.dumps(order_payload, ensure_ascii=False),
                ),
            )
            if fill is None:
                return
            cash_row = connection.execute("SELECT cash FROM poly_paper_track WHERE id=1").fetchone()
            available_cash = float(cash_row["cash"]) if cash_row else 0.0
            if fill.notional > available_cash + 1e-9:
                raise RuntimeError("Polymarket paper fill exceeds isolated USDC cash")
            fill_payload = {
                "id": fill.id,
                "order_id": fill.order_id,
                "market_id": fill.market_id,
                "direction": fill.direction.value,
                "shares": fill.shares,
                "price": fill.price,
                "fee": fill.fee,
                "notional": fill.notional,
                "filled_at": fill.filled_at.isoformat(),
            }
            connection.execute(
                """INSERT INTO poly_fills
                (id, order_id, market_id, direction, shares, price, fee, notional, filled_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    fill.id,
                    fill.order_id,
                    fill.market_id,
                    fill.direction.value,
                    fill.shares,
                    fill.price,
                    fill.fee,
                    fill.notional,
                    fill.filled_at.isoformat(),
                    json.dumps(fill_payload, ensure_ascii=False),
                ),
            )
            connection.execute(
                "UPDATE poly_paper_track SET cash=cash-?, updated_at=? WHERE id=1",
                (fill.notional, fill.filled_at.isoformat()),
            )
            connection.execute(
                """INSERT INTO poly_positions
                (market_id, estimate_id, direction, shares, average_price, cost,
                 opened_at, status, payload) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
                (
                    fill.market_id,
                    order.estimate_id,
                    fill.direction.value,
                    fill.shares,
                    fill.price,
                    fill.notional,
                    fill.filled_at.isoformat(),
                    json.dumps(fill_payload, ensure_ascii=False),
                ),
            )

    def unresolved_market_ids(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT market_id FROM poly_estimates
                WHERE judgment_id NOT IN (SELECT judgment_id FROM poly_resolutions)"""
            ).fetchall()
        return [str(row["market_id"]) for row in rows]

    def settle_market(
        self,
        market: PolyMarket,
        *,
        outcome: int,
        source: str,
        repository: Any,
        resolved_at: datetime,
    ) -> int:
        with self._connect() as connection:
            estimates = connection.execute(
                """SELECT * FROM poly_estimates WHERE market_id=? AND
                judgment_id NOT IN (SELECT judgment_id FROM poly_resolutions)""",
                (market.id,),
            ).fetchall()
        scored = 0
        for row in estimates:
            probability = float(row["estimated_probability"])
            brier = (probability - outcome) ** 2
            payload = {
                "entity_type": "polymarket",
                "market_id": market.id,
                "question": market.question,
                "estimated_probability": probability,
                "outcome": outcome,
                "brier_score": brier,
                "resolution_source": source,
                "resolved_at": resolved_at.isoformat(),
            }
            with self._connect() as connection:
                connection.execute(
                    """INSERT OR IGNORE INTO poly_resolutions
                    (judgment_id, estimate_id, market_id, outcome, estimated_probability,
                     brier_score, resolved_at, source, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["judgment_id"],
                        row["id"],
                        market.id,
                        outcome,
                        probability,
                        brier,
                        resolved_at.isoformat(),
                        source,
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
            direction_correct = (row["direction"] == "YES" and outcome == 1) or (row["direction"] == "NO" and outcome == 0)
            repository.add_judgment_score(
                JudgmentScore(
                    judgment_id=str(row["judgment_id"]),
                    position_id=POLY_LEDGER_POSITION_ID,
                    judgment_type="probability_estimate",
                    claim=json.loads(row["payload"]),
                    confidence=None,
                    outcome="correct" if direction_correct else "wrong",
                    detail=f"Polymarket 공식 정산 outcome={outcome}; Brier={brier:.6f}",
                    metrics=payload,
                    param_version={"poly": "poly-v1", "entity_type": "polymarket"},
                )
            )
            scored += 1
        self._settle_position(market.id, outcome=outcome, resolved_at=resolved_at)
        return scored

    def _settle_position(self, market_id: str, *, outcome: int, resolved_at: datetime) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM poly_positions WHERE market_id=? AND status='open'",
                (market_id,),
            ).fetchone()
            if row is None:
                return
            wins = (row["direction"] == "YES" and outcome == 1) or (row["direction"] == "NO" and outcome == 0)
            payout = float(row["shares"]) if wins else 0.0
            pnl = payout - float(row["cost"])
            connection.execute(
                """UPDATE poly_positions SET status='resolved', resolved_at=?, outcome=?,
                payout=?, pnl=? WHERE market_id=?""",
                (resolved_at.isoformat(), outcome, payout, pnl, market_id),
            )
            connection.execute(
                "UPDATE poly_paper_track SET cash=cash+?, updated_at=? WHERE id=1",
                (payout, resolved_at.isoformat()),
            )

    def dashboard(self) -> dict[str, Any]:
        with self._connect() as connection:
            track = connection.execute("SELECT * FROM poly_paper_track WHERE id=1").fetchone()
            markets = connection.execute(
                """WITH ranked AS (
                    SELECT m.*, e.payload AS estimate_payload,
                    ROW_NUMBER() OVER (
                        PARTITION BY m.category
                        ORDER BY m.trade_eligible DESC, m.liquidity DESC
                    ) AS category_rank
                    FROM poly_markets m
                    LEFT JOIN poly_estimates e ON e.id=(
                        SELECT id FROM poly_estimates
                        WHERE market_id=m.market_id ORDER BY observed_at DESC LIMIT 1
                    )
                )
                SELECT * FROM ranked
                WHERE (category='crypto' AND category_rank<=30)
                   OR (category='macro' AND category_rank<=10)
                ORDER BY trade_eligible DESC, liquidity DESC"""
            ).fetchall()
            positions = connection.execute(
                """SELECT p.*, m.question, m.slug FROM poly_positions p
                JOIN poly_markets m ON m.market_id=p.market_id ORDER BY p.opened_at DESC"""
            ).fetchall()
            fills = connection.execute("SELECT payload FROM poly_fills ORDER BY filled_at DESC LIMIT 20").fetchall()
            resolutions = connection.execute("SELECT * FROM poly_resolutions ORDER BY resolved_at DESC").fetchall()
        track_payload = dict(track) if track else {}
        elapsed_days = 0
        if track and bool(track["clock_valid"]) and track["started_at"]:
            elapsed_days = max(0, min(28, (datetime.now(timezone.utc) - _datetime(track["started_at"])).days))
        track_payload["elapsed_days"] = elapsed_days
        market_payload = []
        for row in markets:
            item = {key: row[key] for key in row.keys() if key not in {"payload", "estimate_payload", "category_rank"}}
            item["metadata"] = json.loads(row["payload"])
            item["estimate"] = json.loads(row["estimate_payload"]) if row["estimate_payload"] else None
            market_payload.append(item)
        return {
            "track": track_payload,
            "markets": market_payload,
            "positions": [dict(row) for row in positions],
            "recent_fills": [json.loads(row["payload"]) for row in fills],
            "calibration": _calibration([dict(row) for row in resolutions]),
            "resolution_count": len(resolutions),
        }


def _calibration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        index = min(9, int(float(row["estimated_probability"]) * 10))
        buckets.setdefault(index, []).append(row)
    curve = []
    for index in range(10):
        samples = buckets.get(index, [])
        curve.append(
            {
                "bucket": f"{index * 10}–{(index + 1) * 10}%",
                "n": len(samples),
                "mean_forecast": sum(float(item["estimated_probability"]) for item in samples) / len(samples) if samples else None,
                "actual_yes_rate": sum(int(item["outcome"]) for item in samples) / len(samples) if samples else None,
            }
        )
    count = len(rows)
    return {
        "n": count,
        "mean_brier_score": sum(float(row["brier_score"]) for row in rows) / count if count else None,
        "sample_sufficient": count >= 30,
        "sample_warning": None if count >= 30 else f"표본 부족 · N={count}/30",
        "curve": curve,
    }


def _market_payload(market: PolyMarket) -> dict[str, Any]:
    return {
        "market_id": market.id,
        "slug": market.slug,
        "question": market.question,
        "category": market.category.value,
        "observed_at": market.observed_at.isoformat(),
        "end_at": market.end_at.isoformat() if market.end_at else None,
        "resolution_source": market.resolution_source,
        "description": market.description,
        "yes_token_id": market.yes_token_id,
        "no_token_id": market.no_token_id,
        "yes_price": market.yes_price,
        "no_price": market.no_price,
        "taker_fee_rate": market.taker_fee_rate,
        "trade_eligible": market.trade_eligible,
        "exclusion_reason": market.exclusion_reason,
        "source": "polymarket_gamma_public",
    }


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
