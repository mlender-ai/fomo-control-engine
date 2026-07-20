from fastapi import APIRouter

from app.core.config import get_settings

from .models import Market
from .service import run_stock_paper_engine, stock_paper_dashboard, stock_paper_entry_chart, universe_payload


router = APIRouter(prefix="/api/stock-paper", tags=["stock-paper"])


@router.get("/dashboard")
def dashboard() -> dict:
    return stock_paper_dashboard(get_settings())


@router.get("/universe")
def universe() -> dict:
    return universe_payload()


@router.get("/entry-chart")
def entry_chart(market: Market, symbol: str) -> dict:
    return stock_paper_entry_chart(get_settings(), market, symbol)


@router.post("/run")
def run_once() -> dict:
    return run_stock_paper_engine(get_settings())
