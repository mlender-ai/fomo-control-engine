from fastapi import APIRouter

from app.core.config import get_settings

from .models import Market
from .service import run_stock_paper_engine, stock_paper_dashboard, stock_paper_entry_chart, universe_payload


router = APIRouter(prefix="/api/stock-paper", tags=["stock-paper"])


@router.get("/dashboard")
def dashboard() -> dict:
    """대시보드 + 트랙 자본 (WO-FCE-TRACK-CAPITAL-01 1-3).

    자본은 계산 계층에서 붙인다 — 스토어가 이미 NAV 를 내지만 그것은 트랙별 형식이고,
    4트랙이 같은 필드로 읽히려면 공통 모듈이 산출해야 한다.
    """
    from app.validation.track_capital import capital_for_response

    settings = get_settings()
    return {**stock_paper_dashboard(settings), "capital": capital_for_response(settings, ("stock_kr", "stock_us"))}


@router.get("/universe")
def universe() -> dict:
    return universe_payload()


@router.get("/entry-chart")
def entry_chart(market: Market, symbol: str) -> dict:
    return stock_paper_entry_chart(get_settings(), market, symbol)


@router.post("/run")
def run_once() -> dict:
    return run_stock_paper_engine(get_settings())
