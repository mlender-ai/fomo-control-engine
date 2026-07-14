from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import runtime as service

router = APIRouter(prefix="/api/onchain", tags=["onchain"])


class WhaleWalletRequest(BaseModel):
    address: str
    label: str | None = None


@router.get("/whales")
def list_whales() -> dict:
    return service.whale_dashboard()


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


@router.post("/collect")
def collect_once() -> dict:
    return service.collect_whales()
