from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import runtime as service

router = APIRouter(prefix="/api/onchain", tags=["onchain"])


class WhaleWalletRequest(BaseModel):
    address: str
    label: str | None = None


@router.get("/whales")
def list_whales() -> dict:
    """고래 대시보드 + 추종 트랙 자본 (WO-FCE-TRACK-CAPITAL-01 1-3).

    추종 트랙의 시작 자본은 **미상**이다 — 동시 보유 상한을 강제하지 않아 유도할 수 없다.
    미상을 미상으로 내고 수익률은 만들지 않는다(C7).
    """
    from app.core.config import get_settings
    from app.validation.track_capital import capital_for_response

    return {**service.whale_dashboard(), "capital": capital_for_response(get_settings(), ("whale_follow",))}


@router.post("/whales")
def add_whale(payload: WhaleWalletRequest) -> dict:
    try:
        return {"wallet": service.add_whale_wallet(payload.address, payload.label)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/whales/{address}")
def remove_whale(address: str) -> dict:
    if not service.remove_whale_wallet(address):
        raise HTTPException(status_code=404, detail="등록된 고래 지갑이 아닙니다.")
    return {"removed": address.lower()}


@router.get("/follow/eligibility")
def follow_eligibility() -> dict:
    """관찰 자격 판정 (Phase 6-1). 탈락 지갑도 사유와 함께 나온다(C10)."""
    return service.whale_follow_eligibility()


@router.get("/follow/trades")
def follow_trades(status: str | None = None, symbol: str | None = None, limit: int = 200) -> dict:
    """추종 트랙 원장. `paper_trades` 와 분리돼 있다(C3). 자격 종류별 분리 집계 포함."""
    return service.whale_follow_trades(status=status, symbol=symbol, limit=limit)


@router.post("/follow/run")
def follow_run_once() -> dict:
    """추종 트랙 1회 실행. 알림은 워커 경로에서만 발송된다 — 이 엔드포인트는 후보를 보내지 않는다."""
    payload = service.run_whale_follow_engine()
    payload.pop("_alert_candidate_objects", None)
    return payload


@router.post("/collect")
def collect_once() -> dict:
    return service.collect_whales()


@router.post("/discover")
def discover_once() -> dict:
    try:
        return service.discover_whales()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
