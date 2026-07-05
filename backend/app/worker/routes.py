from __future__ import annotations

from fastapi import APIRouter

from app.worker.runtime import get_worker_status

router = APIRouter()


@router.get("/api/system/worker")
def system_worker_status() -> dict:
    return get_worker_status()

