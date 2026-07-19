from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


class Market(StrEnum):
    KR = "KR"
    US = "US"


class Currency(StrEnum):
    KRW = "KRW"
    USD = "USD"


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    QUEUED = "queued"
    PARTIAL = "partial"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class StockOrder:
    symbol: str
    market: Market
    currency: Currency
    side: Side
    quantity: int
    signal_at: datetime
    id: str = field(default_factory=lambda: str(uuid4()))
    status: OrderStatus = OrderStatus.QUEUED
    remaining_quantity: int | None = None
    signal_price: float | None = None
    reason: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.remaining_quantity is None:
            object.__setattr__(self, "remaining_quantity", self.quantity)

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value["market"] = self.market.value
        value["currency"] = self.currency.value
        value["side"] = self.side.value
        value["status"] = self.status.value
        value["signal_at"] = self.signal_at.astimezone(timezone.utc).isoformat()
        return value


@dataclass(frozen=True)
class MarketObservation:
    symbol: str
    market: Market
    observed_at: datetime
    session_open: bool
    minute_open: float | None
    minute_high: float | None
    minute_low: float | None
    minute_close: float | None
    minute_volume: float | None
    session_open_price: float | None = None
    bid: float | None = None
    ask: float | None = None
    upper_limit: float | None = None
    lower_limit: float | None = None
    upper_locked: bool = False
    lower_locked: bool = False
    vi_active: bool = False
    halted: bool = False
    warnings: tuple[str, ...] = ()
    fx_rate_to_krw: float | None = None
    fx_observed_at: datetime | None = None


@dataclass(frozen=True)
class PaperFill:
    order_id: str
    symbol: str
    market: Market
    currency: Currency
    side: Side
    quantity: int
    price: float
    filled_at: datetime
    gross_amount: float
    commission: float
    transaction_tax: float
    fx_rate_to_krw: float | None
    fx_observed_at: datetime | None
    id: str = field(default_factory=lambda: str(uuid4()))

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        for key in ("market", "currency", "side"):
            value[key] = getattr(self, key).value
        value["filled_at"] = self.filled_at.astimezone(timezone.utc).isoformat()
        value["fx_observed_at"] = self.fx_observed_at.astimezone(timezone.utc).isoformat() if self.fx_observed_at else None
        return value


@dataclass(frozen=True)
class ExecutionResult:
    order: StockOrder
    fill: PaperFill | None = None
    reason: str | None = None


class FillInvariantViolation(RuntimeError):
    """The simulated fill escaped the actually observed one-minute range."""
