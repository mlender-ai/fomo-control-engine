from fastapi import APIRouter

from app.core.config import get_settings
from app.services import http_handlers as engine_runtime

from .service import poly_paper_dashboard, run_poly_paper_engine


router = APIRouter(prefix="/api/poly-paper", tags=["poly-paper"])


@router.get("/dashboard")
def dashboard() -> dict:
    """대시보드 + 트랙 자본 (WO-FCE-TRACK-CAPITAL-01 1-3).

    지금까지 `USDC 8,416.88` 만 보였고 그것이 10,000 에서 줄어든 것인지 알 수 없었다.
    시작값은 원장(`poly_paper_track.initial_cash`)에 **있었다** — 화면에 없었을 뿐이다.
    """
    from app.validation.track_capital import capital_for_response

    settings = get_settings()
    return {**poly_paper_dashboard(settings), "capital": capital_for_response(settings, ("poly",))}


@router.post("/run")
async def run_once() -> dict:
    return await run_poly_paper_engine(
        get_settings(),
        engine_runtime.market_provider,
        engine_runtime.repository,
    )
