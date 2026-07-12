from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.config import get_settings
from app.notify.telegram import TelegramSender
from app.services import runtime as service


router = APIRouter(prefix="/api/paper", tags=["paper"])


class BenchmarkStartRequest(BaseModel):
    reset: bool = False


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


@router.post("/benchmark/start")
async def start_benchmark(payload: BenchmarkStartRequest | None = None) -> dict:
    result = service.start_paper_benchmark(reset=bool(payload and payload.reset))
    if result.get("created"):
        start = str(result.get("started_at") or "")[:10]
        end = str(result.get("ends_at") or "")[:10]
        await TelegramSender(get_settings()).send_to_all(f"🏁 <b>4주 대결 개시</b> ({start}~{end}) · 대상 {result.get('target_count', 0)}심볼")
    return result
