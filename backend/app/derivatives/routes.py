from __future__ import annotations

from fastapi import APIRouter

from app.services import runtime as service

router = APIRouter()


@router.get("/api/derivatives/{symbol}")
def derivative_flow(symbol: str) -> dict:
    return service.latest_flow(symbol)


@router.post("/api/derivatives/refresh")
def refresh_derivatives() -> dict:
    return service.refresh_derivative_data()


@router.get("/api/system/database")
def database_status() -> dict:
    return {
        "recent_maintenance": [event.model_dump(mode="json") for event in service.runtime.repository.list_database_maintenance_events(limit=20)],
    }
