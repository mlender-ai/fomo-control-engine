from fastapi import APIRouter, Query

from app.services import runtime as service


router = APIRouter(prefix="/api/paper", tags=["paper"])


@router.get("/trades")
def list_paper_trades(
    status: str | None = Query(default=None),
    symbol: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=5000),
) -> dict:
    return service.paper_trades(status=status, symbol=symbol, limit=limit)


@router.get("/scoreboard")
def get_paper_scoreboard() -> dict:
    return service.paper_scoreboard()


@router.get("/dashboard")
def get_paper_dashboard() -> dict:
    return service.paper_dashboard()


@router.post("/run")
def run_paper_once() -> dict:
    return service.run_paper_engine()
