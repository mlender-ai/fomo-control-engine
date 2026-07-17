from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query

from app.services import runtime as service

router = APIRouter()


@router.get("/api/derivatives/{symbol}")
def derivative_flow(symbol: str) -> dict:
    return service.latest_flow(symbol)


@router.get("/api/derivatives/{symbol}/liquidation-heatmap")
def realized_liquidation_heatmap(symbol: str, window_hours: int = 72) -> dict:
    return service.liquidation_heatmap(symbol, window_hours)


@router.get("/api/liq/heatmap")
def unified_liquidation_heatmap(
    symbol: str,
    tf: str = "4h",
    range_key: Literal["12H", "24H", "3D", "1W", "1M"] = Query("3D", alias="range"),
    side: Literal["all", "long", "short"] = "all",
    size: Literal["all", "q2_plus", "q3_plus", "q4", "10x", "25x", "50x", "100x"] = "all",
    min_size: float | None = Query(None, ge=0),
    mode: Literal["persist", "event"] = "persist",
    price_bins: int = Query(120, ge=32, le=240),
    source: Literal["realized", "coinglass_est"] = "realized",
    from_at: datetime | None = Query(None, alias="from"),
    to_at: datetime | None = Query(None, alias="to"),
) -> dict:
    return service.unified_liquidation_heatmap(
        symbol,
        timeframe=tf,
        range_key=range_key,
        side=side,
        size_filter=size,
        min_size=min_size,
        mode=mode,
        price_bins=price_bins,
        source=source,
        from_at=from_at,
        to_at=to_at,
    )


@router.post("/api/derivatives/{symbol}/liquidation-heatmap/refresh")
def refresh_realized_liquidation_heatmap(symbol: str, window_hours: int = 72) -> dict:
    return service.refresh_liquidation_heatmap(symbol, window_hours)


@router.post("/api/derivatives/refresh")
def refresh_derivatives() -> dict:
    return service.refresh_derivative_data()


@router.get("/api/system/database")
def database_status() -> dict:
    return {
        "recent_maintenance": [event.model_dump(mode="json") for event in service.runtime.repository.list_database_maintenance_events(limit=20)],
    }
