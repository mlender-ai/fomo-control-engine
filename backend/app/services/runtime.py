from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.api import routes as runtime
from app.api import scout_routes
from app.api.scout_routes import ScanRequest, SimulateRequest
from app.db.models import Position, PositionStatus
from app.review.engine import build_calibration_summary, generate_calibration_suggestions


@dataclass(frozen=True)
class SymbolMatch:
    position: Position | None
    candidates: list[Position]


def provider_name() -> str:
    return runtime._provider_name()


def sync_and_analyze_positions() -> dict[str, Any]:
    """Sync Bitget positions and analyze open positions using the same route path."""
    return runtime.sync_live_positions()


def list_live_positions(*, store_snapshot: bool = False) -> dict[str, Any]:
    all_positions = runtime.repository.list_positions()
    open_positions = [position for position in all_positions if position.status == PositionStatus.open]
    payloads: list[dict[str, Any]] = []
    for position in open_positions:
        try:
            payloads.append(runtime._live_position_payload(position, store_snapshot=store_snapshot))
        except HTTPException:
            continue
    return {
        "provider": provider_name(),
        "positions": payloads,
        "open_count": len(open_positions),
        "needs_exit_record_count": len(
            [position for position in all_positions if position.status in {PositionStatus.missing_from_exchange, PositionStatus.needs_exit_record}]
        ),
        "timestamp": runtime.utc_now(),
    }


def live_position_detail(position_id: UUID) -> dict[str, Any]:
    position = runtime.repository.get_position(position_id)
    if position is None:
        raise LookupError("Position not found")
    return runtime._live_position_detail(position)


def create_position_insight(position_id: UUID, *, auto_generated: bool = False) -> dict[str, Any]:
    position = runtime.repository.get_position(position_id)
    if position is None:
        raise LookupError("Position not found")
    payload = runtime._live_position_payload(position, store_snapshot=True)
    snapshot = runtime.PositionSnapshot.model_validate(payload["latest_snapshot"])
    insight = runtime._create_and_store_position_insight(position, snapshot, auto_generated=auto_generated)
    status = runtime._insight_status(insight, snapshot)
    return {**payload, "latest_insight": runtime._insight_payload(insight, status), "insight_status": status}


def match_position_symbol(query: str) -> SymbolMatch:
    needle = query.strip().upper()
    if not needle:
        return SymbolMatch(None, [])
    open_positions = [position for position in runtime.repository.list_positions() if position.status == PositionStatus.open]
    exact = [position for position in open_positions if position.symbol.upper() == needle or position.symbol.upper().replace("USDT", "") == needle]
    if len(exact) == 1:
        return SymbolMatch(exact[0], exact)
    partial = [position for position in open_positions if needle in position.symbol.upper()]
    if len(partial) == 1:
        return SymbolMatch(partial[0], partial)
    return SymbolMatch(None, exact or partial)


def scout_scan(limit: int = 5) -> dict[str, Any]:
    payload = scout_routes.scan_watchlist(ScanRequest(force=False))
    rows = payload.get("rows", [])
    return {**payload, "rows": rows[:limit]}


def simulate_entry(symbol: str, direction: str, leverage: float, entry_price: float | None = None) -> dict[str, Any]:
    return scout_routes._simulate(SimulateRequest(symbol=symbol, direction=direction, leverage=leverage, entry_price=entry_price))


def recent_reviews(limit: int = 3) -> list[Any]:
    return runtime.repository.list_trades()[:limit]


def calibration_snapshot() -> dict[str, Any]:
    scores = runtime.repository.list_judgment_scores(limit=2000)
    for suggestion in generate_calibration_suggestions(scores):
        if runtime.repository.get_calibration_suggestion(suggestion.id) is None:
            runtime.repository.add_calibration_suggestion(suggestion)
    return build_calibration_summary(scores, runtime.repository.list_calibration_suggestions(limit=100))
