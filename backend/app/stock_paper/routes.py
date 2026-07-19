from fastapi import APIRouter

from app.core.config import get_settings

from .service import run_stock_paper_engine, stock_paper_dashboard, universe_payload


router = APIRouter(prefix="/api/stock-paper", tags=["stock-paper"])


@router.get("/dashboard")
def dashboard() -> dict:
    return stock_paper_dashboard(get_settings())


@router.get("/universe")
def universe() -> dict:
    return universe_payload()


@router.post("/run")
def run_once() -> dict:
    return run_stock_paper_engine(get_settings())
