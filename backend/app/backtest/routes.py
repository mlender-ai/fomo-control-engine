from fastapi import APIRouter, HTTPException

from app.backtest.stance_validation import StanceHistoryUnavailable
from app.services import runtime as service


router = APIRouter(prefix="/api/backtest/stance", tags=["backtest"])


@router.get("")
def dashboard() -> dict:
    return service.stance_backtest_dashboard()


@router.post("/refresh")
def refresh() -> dict:
    try:
        return service.refresh_stance_backtests()
    except StanceHistoryUnavailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
