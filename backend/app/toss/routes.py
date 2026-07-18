from fastapi import APIRouter, Query

from app.core.config import get_settings

from .service import collect_market, latest_status
from .store import TossStockStore

router = APIRouter()


@router.get("/api/scout/stocks/{market}")
async def stock_scout(market: str, refresh: bool = Query(False)) -> dict:
    normalized = market.upper()
    if normalized not in {"KR", "US"}:
        return {"status": "invalid_market", "message": "market은 KR 또는 US여야 합니다."}
    settings = get_settings()
    store = TossStockStore(settings.database_url)
    if refresh and settings.toss_stock_scout_enabled and settings.toss_client_id and settings.toss_client_secret:
        return await collect_market(settings, normalized)
    return latest_status(settings, normalized, store)
