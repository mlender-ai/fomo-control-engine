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


def _liveness_clock(started_at: datetime, effective_days: set[str], now: datetime) -> dict[str, Any]:
    """검증 시계 보정 공용 계산(지연 import — 스토어가 워커에 로드 의존성을 갖지 않게)."""
    from app.worker.liveness import elapsed_excluding_gaps

    return elapsed_excluding_gaps(started_at, effective_days, now)


POLY_LEDGER_POSITION_ID = uuid5(NAMESPACE_URL, "fce:polymarket:paper:ledger")


def _position_view(
    rows: list[sqlite3.Row],
    *,
    validation_ends_at: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """보유 포지션에 미실현 손익과 만기 정보를 붙인다 (WO-FCE-OBSERVATION-INTEGRITY-01 Phase 4).

    실측 2026-08-05: 보유 9건 중 **8건이 2027-01-01 만기** — 검증 종료(08-19)보다 5개월 뒤다.
    검증 기간 내 만기 도래 보유 시장은 0건(1건은 08-01 에 이미 정산됨).
    즉 **폴리는 이번 검증에서 정산 표본을 만들 수 없다.**

    그래서 미실현을 낸다. 다만 **정산 손익과 절대 섞지 않는다** — 미실현은 확정이 아니고,
    합쳐 표기하면 없는 성적을 있는 것처럼 보이게 한다(C3).
    """
    deadline = _datetime(validation_ends_at) if validation_ends_at else None
    payload: list[dict[str, Any]] = []
    unrealized_cost = unrealized_value = 0.0
    open_count = 0
    within_deadline = 0
    expiries: list[str] = []
    for row in rows:
        item = dict(row)
        probability = item.get("market_probability")
        shares = float(item.get("shares") or 0)
        cost = float(item.get("cost") or 0)
        is_open = str(item.get("status") or "") == "open"
        if is_open and probability is not None:
            # NO 포지션의 현재가는 1 - YES 확률이다.
            unit = float(probability) if str(item.get("direction") or "").upper() == "YES" else 1.0 - float(probability)
            value = shares * unit
            item["unrealized_value"] = round(value, 4)
            item["unrealized_pnl"] = round(value - cost, 4)
            item["unrealized_return_pct"] = round((value / cost - 1) * 100, 2) if cost else None
            unrealized_cost += cost
            unrealized_value += value
        else:
            item["unrealized_value"] = None
            item["unrealized_pnl"] = None
            item["unrealized_return_pct"] = None
        if is_open:
            open_count += 1
            end_at = _datetime(item.get("end_at")) if item.get("end_at") else None
            if end_at is not None:
                expiries.append(end_at.isoformat())
                if deadline is not None and end_at <= deadline:
                    within_deadline += 1
        item["settles_within_validation"] = bool(is_open and item.get("end_at") and deadline is not None and (_datetime(item["end_at"]) <= deadline))
        payload.append(item)

    unrealized = {
        "basis": "current_market_probability",
        "is_settled": False,
        "note": "미실현은 확정 손익이 아닙니다. 정산 손익과 합산하지 않습니다.",
        "open_positions": open_count,
        "cost": round(unrealized_cost, 4),
        "value": round(unrealized_value, 4),
        "pnl": round(unrealized_value - unrealized_cost, 4),
        "return_pct": round((unrealized_value / unrealized_cost - 1) * 100, 2) if unrealized_cost else None,
    }
    expiry = {
        "open_positions": open_count,
        "nearest_end_at": min(expiries) if expiries else None,
        "settling_within_validation": within_deadline,
        "validation_ends_at": deadline.isoformat() if deadline else None,
        "label": (f"정산 대기 {open_count}건 · 최근접 만기 {min(expiries)[:10] if expiries else '—'} · 검증 기간 내 정산 예정 {within_deadline}건"),
        # 0이면 "폴리는 이번 검증에서 표본을 만들 수 없다"가 사실이다 — 숨기지 않는다.
        "sample_possible": within_deadline > 0,
    }
    return payload, unrealized, expiry


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
            row = connection.execute("SELECT parameter_version FROM poly_paper_track WHERE id=1").fetchone()
            if row and str(row["parameter_version"]) != parameter_version:
                connection.execute(
                    """UPDATE poly_paper_track SET parameter_version=?, clock_valid=0, started_at=NULL,
                    ends_at=NULL, status='waiting', stop_reason=NULL, updated_at=? WHERE id=1""",
                    (parameter_version, now.isoformat()),
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

    def validation_ends_at(self) -> datetime | None:
        """검증 종료일. 만기 필터의 채점 마감을 여기서 끌어온다 (PHASE 2-3)."""
        with self._connect() as connection:
            row = connection.execute("SELECT ends_at FROM poly_paper_track WHERE id=1").fetchone()
        return _datetime(row["ends_at"]) if row and row["ends_at"] else None

    def settlement_latency(self) -> dict[str, Any]:
        """만기(end_at) → 정산 확정(resolved_at) 지연 실측 (PHASE 2-3 '안전 여유').

        안전 여유를 추측으로 정하지 않기 위한 근거다. 만기 당일에 정산이 확정되지 않는다 —
        `resolved_outcome` 는 가격이 확정 극단값에 도달한 뒤에야 참이 된다. 표본이 적으면
        적다고 쓴다(N 을 항상 같이 낸다).
        """
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    """SELECT r.resolved_at, m.end_at FROM poly_resolutions r
                    JOIN poly_markets m ON m.market_id=r.market_id WHERE m.end_at IS NOT NULL"""
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        deltas: list[float] = []
        for row in rows:
            resolved = _datetime(row["resolved_at"])
            end = _datetime(row["end_at"])
            if resolved and end:
                deltas.append((resolved - end).total_seconds() / 86_400)
        ordered = sorted(deltas)
        return {
            "n": len(ordered),
            "median_days": round(ordered[len(ordered) // 2], 3) if ordered else None,
            "max_days": round(ordered[-1], 3) if ordered else None,
            "sample_sufficient": len(ordered) >= 30,
            "note": "N<30 이면 중앙값을 안전 여유의 근거로 쓰되 보수적으로 올려 잡는다." if len(ordered) < 30 else "",
        }

    def expiry_distribution(self, *, now: datetime | None = None) -> dict[str, Any]:
        """저장된 유니버스의 만기 분포 — 수정 전후 대조의 '전' 쪽 근거 (PHASE 2-4)."""
        now = now or datetime.now(timezone.utc)
        deadline = self.validation_ends_at()
        with self._connect() as connection:
            rows = connection.execute("SELECT end_at, trade_eligible, exclusion_reason FROM poly_markets WHERE closed=0").fetchall()
        buckets: dict[str, int] = {"<=7d": 0, "8-28d": 0, "29-90d": 0, ">90d": 0, "unknown": 0}
        within = 0
        for row in rows:
            end = _datetime(row["end_at"]) if row["end_at"] else None
            if end is None:
                buckets["unknown"] += 1
                continue
            days = (end - now).total_seconds() / 86_400
            key = "<=7d" if days <= 7 else "8-28d" if days <= 28 else "29-90d" if days <= 90 else ">90d"
            buckets[key] += 1
            if deadline is not None and end <= deadline:
                within += 1
        return {
            "as_of": now.isoformat(),
            "validation_ends_at": deadline.isoformat() if deadline else None,
            "open_markets": len(rows),
            "buckets": buckets,
            "within_validation_window": within,
        }

    def expiry_bias_diagnosis(self, *, now: datetime | None = None) -> dict[str, Any]:
        """**왜** 장기 만기가 선호되는지 실측한다 (PHASE 2-2).

        원인을 모른 채 만기 필터만 씌우면 다른 항이 다시 왜곡을 만든다. 그래서 수정 전에
        만기 구간별로 선정 점수의 재료(유동성·총엣지·비용후엣지·통과율)를 비교한다.

        선정 점수 함수에 만기 항이 직접 들어 있지는 않다. 따라서 편향이 있다면 **간접 경로**,
        즉 장기 시장의 유동성이 더 크거나 가격 괴리(엣지)가 더 크게 산출되는 구조여야 한다.
        이 표가 그 가설을 지지하는지 반증하는지 보여준다.
        """
        now = now or datetime.now(timezone.utc)
        with self._connect() as connection:
            try:
                rows = connection.execute(
                    """SELECT m.end_at, m.liquidity, m.trade_eligible, e.gross_edge, e.after_cost_edge, e.estimate_quality
                    FROM poly_markets m
                    LEFT JOIN poly_estimates e ON e.id=(
                        SELECT id FROM poly_estimates WHERE market_id=m.market_id ORDER BY observed_at DESC LIMIT 1
                    )
                    WHERE m.closed=0"""
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        buckets: dict[str, list[sqlite3.Row]] = {"<=7d": [], "8-28d": [], "29-90d": [], ">90d": [], "unknown": []}
        for row in rows:
            end = _datetime(row["end_at"]) if row["end_at"] else None
            if end is None:
                buckets["unknown"].append(row)
                continue
            days = (end - now).total_seconds() / 86_400
            buckets["<=7d" if days <= 7 else "8-28d" if days <= 28 else "29-90d" if days <= 90 else ">90d"].append(row)

        def _median(values: list[float]) -> float | None:
            ordered = sorted(values)
            return round(ordered[len(ordered) // 2], 5) if ordered else None

        table = []
        for key, group in buckets.items():
            table.append(
                {
                    "bucket": key,
                    "n": len(group),
                    "median_liquidity": _median([float(row["liquidity"] or 0) for row in group]),
                    "median_gross_edge": _median([float(row["gross_edge"]) for row in group if row["gross_edge"] is not None]),
                    "median_after_cost_edge": _median([float(row["after_cost_edge"]) for row in group if row["after_cost_edge"] is not None]),
                    "trade_eligible_pct": round(sum(int(row["trade_eligible"] or 0) for row in group) / len(group) * 100, 1) if group else None,
                }
            )
        return {
            "as_of": now.isoformat(),
            "hypothesis": "선정 점수에 만기 항은 없다. 편향이 있다면 유동성·엣지를 통한 간접 경로여야 한다.",
            "buckets": table,
            "note": "구간별 N 을 함께 본다. N 이 한 자리인 구간의 중앙값 차이는 근거가 되지 않는다.",
        }

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
                """SELECT e.trade_eligible, e.payload, o.id AS order_id
                FROM poly_estimates e
                LEFT JOIN poly_orders o ON o.estimate_id=e.id
                WHERE e.market_id=?
                ORDER BY e.observed_at DESC LIMIT 1""",
                (market_id,),
            ).fetchone()
        if row is None or row["order_id"] is not None:
            return False
        payload = json.loads(row["payload"] or "{}")
        return bool(row["trade_eligible"] or payload.get("coverage_eligible"))

    def save_estimate(self, estimate: ProbabilityEstimate, repository: Any, *, parameter_version: str = "poly-v1") -> str:
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
                param_version={"poly": parameter_version, "entity_type": "polymarket"},
            )
        )
        return judgment_id

    def cash(self) -> float:
        with self._connect() as connection:
            row = connection.execute("SELECT cash FROM poly_paper_track WHERE id=1").fetchone()
        return float(row["cash"]) if row else 0.0

    def open_position_count(self, entry_mode: str | None = None) -> int:
        with self._connect() as connection:
            if entry_mode is None:
                row = connection.execute("SELECT COUNT(*) AS count FROM poly_positions WHERE status='open'").fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM poly_positions WHERE status='open' AND entry_mode=?",
                    (entry_mode,),
                ).fetchone()
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
            "entry_mode": order.entry_mode,
        }
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO poly_orders
                (id, market_id, estimate_id, token_id, direction, requested_notional,
                 status, reason, created_at, entry_mode, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    order.entry_mode,
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
                "entry_mode": fill.entry_mode,
            }
            connection.execute(
                """INSERT INTO poly_fills
                (id, order_id, market_id, direction, shares, price, fee, notional, filled_at, entry_mode, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    fill.entry_mode,
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
                 opened_at, status, entry_mode, payload) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)""",
                (
                    fill.market_id,
                    order.estimate_id,
                    fill.direction.value,
                    fill.shares,
                    fill.price,
                    fill.notional,
                    fill.filled_at.isoformat(),
                    fill.entry_mode,
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
            track = connection.execute("SELECT parameter_version FROM poly_paper_track WHERE id=1").fetchone()
        parameter_version = str(track["parameter_version"]) if track else "unknown"
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
                    param_version={"poly": parameter_version, "entity_type": "polymarket"},
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
                # end_at: 만기 분포 표기용 (WO-FCE-ENTRY-THROUGHPUT-01 작업 4) —
                # 4주 검증 창 안에 만기가 있는지가 "표본이 생길 수 있는가"를 결정한다.
                # market_probability: 미실현 산출용 (WO-FCE-OBSERVATION-INTEGRITY-01 Phase 4).
                """SELECT p.*, m.question, m.slug, m.end_at, m.market_probability FROM poly_positions p
                JOIN poly_markets m ON m.market_id=p.market_id ORDER BY p.opened_at DESC"""
            ).fetchall()
            fills = connection.execute("SELECT payload FROM poly_fills ORDER BY filled_at DESC LIMIT 20").fetchall()
            resolutions = connection.execute("SELECT * FROM poly_resolutions ORDER BY resolved_at DESC").fetchall()
            mode_counts = connection.execute(
                """SELECT entry_mode, COUNT(*) AS position_count,
                SUM(CASE WHEN status='resolved' THEN pnl ELSE 0 END) AS realized_pnl
                FROM poly_positions GROUP BY entry_mode ORDER BY entry_mode"""
            ).fetchall()
        track_payload = dict(track) if track else {}
        elapsed_days = 0
        # C5: 엔진이 멈춰 있던 날은 검증한 날이 아니다. 관측 흔적(poly_markets.observed_at)이 있는
        # 날만 경과로 센다 — WO-FCE-TOSS-US-STALL-01 작업 5(3트랙 공통 적용).
        clock: dict[str, Any] = {"calendar_days": 0, "effective_days": 0, "lost_days": 0, "label": "첫 수집 대기"}
        if track and bool(track["clock_valid"]) and track["started_at"]:
            # WO-FCE-OBSERVATION-INTEGRITY-01: 커버리지 게이트를 통과한 날만 검증일로 센다.
            # 이전 근거였던 poly_markets 는 PK 가 market_id 인 **upsert** 테이블이라 시계열이 아니다.
            with self._connect() as connection:
                try:
                    observed = connection.execute("SELECT day FROM observation_coverage WHERE track='poly' AND valid=1").fetchall()
                except sqlite3.OperationalError:
                    observed = []
            clock = _liveness_clock(
                _datetime(track["started_at"]),
                {str(row["day"]) for row in observed if row["day"]},
                datetime.now(timezone.utc),
            )
            elapsed_days = min(28, clock["effective_days"])
        track_payload["elapsed_days"] = elapsed_days
        track_payload["calendar_days"] = clock["calendar_days"]
        track_payload["lost_days"] = clock["lost_days"]
        track_payload["elapsed_label"] = clock["label"]
        position_payload, unrealized_summary, expiry_summary = _position_view(positions, validation_ends_at=track_payload.get("ends_at"))
        market_payload = []
        for row in markets:
            item = {key: row[key] for key in row.keys() if key not in {"payload", "estimate_payload", "category_rank"}}
            item["metadata"] = json.loads(row["payload"])
            item["estimate"] = json.loads(row["estimate_payload"]) if row["estimate_payload"] else None
            market_payload.append(item)
        return {
            "track": track_payload,
            "markets": market_payload,
            "positions": position_payload,
            # Phase 4: 정산 전이라도 현재 시장가 기준 미실현을 낸다. **정산 손익과 명확히 구분**한다 —
            # 미실현은 확정이 아니고, 섞어 표기하면 없는 성적을 있는 것처럼 보이게 한다(C3).
            "unrealized": unrealized_summary,
            "expiry": expiry_summary,
            "recent_fills": [json.loads(row["payload"]) for row in fills],
            "calibration": _calibration([dict(row) for row in resolutions]),
            "resolution_count": len(resolutions),
            "mode_performance": [dict(row) for row in mode_counts],
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
