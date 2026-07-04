from __future__ import annotations

from dataclasses import dataclass

from app.db.models import Position


@dataclass(frozen=True)
class PositionPnl:
    pnl_percent: float
    source: str


def calculate_computed_pnl_percent(position: Position, current_price: float) -> float:
    if position.entry_price <= 0:
        return 0.0
    if position.direction == "long":
        return ((current_price - position.entry_price) / position.entry_price) * 100 * position.leverage
    return ((position.entry_price - current_price) / position.entry_price) * 100 * position.leverage


def resolve_position_pnl_percent(position: Position, current_price: float | None) -> PositionPnl:
    if position.unrealized_pl is not None and position.margin_size is not None and position.margin_size > 0:
        return PositionPnl(pnl_percent=(position.unrealized_pl / position.margin_size) * 100, source="exchange")
    if current_price is None:
        return PositionPnl(pnl_percent=position.pnl_percent, source=position.pnl_source or "computed")
    return PositionPnl(pnl_percent=calculate_computed_pnl_percent(position, current_price), source="computed")
