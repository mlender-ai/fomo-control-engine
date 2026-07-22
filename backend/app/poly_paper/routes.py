from fastapi import APIRouter

from app.core.config import get_settings
from app.services import http_handlers as engine_runtime

from .service import poly_paper_dashboard, run_poly_paper_engine


router = APIRouter(prefix="/api/poly-paper", tags=["poly-paper"])


@router.get("/dashboard")
def dashboard() -> dict:
    return poly_paper_dashboard(get_settings())


@router.post("/run")
async def run_once() -> dict:
    return await run_poly_paper_engine(
        get_settings(),
        engine_runtime.market_provider,
        engine_runtime.repository,
    )
