from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from app.db.models import DerivativeDataSnapshot, DerivativeMetric, LiquidationEvent


class DerivativeCollection(BaseModel):
    provider: str
    symbol: str
    metrics: list[DerivativeMetric] = Field(default_factory=list)
    liquidation_events: list[LiquidationEvent] = Field(default_factory=list)
    snapshot: DerivativeDataSnapshot | None = None
    feature_status: dict = Field(default_factory=dict)
    requests_used: int = 0
    errors: list[str] = Field(default_factory=list)


class DerivativesProvider(Protocol):
    source: str

    def collect(self, symbol: str) -> DerivativeCollection: ...


def coin_from_symbol(symbol: str) -> str:
    normalized = symbol.upper().replace("-", "").replace("_", "")
    for suffix in ("USDT", "USDC", "USD", "PERP"):
        if normalized.endswith(suffix) and len(normalized) > len(suffix):
            return normalized[: -len(suffix)]
    return normalized
