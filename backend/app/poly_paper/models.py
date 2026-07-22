from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4


class Category(StrEnum):
    CRYPTO = "crypto"
    MACRO = "macro"


class EstimateQuality(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class OutcomeSide(StrEnum):
    YES = "YES"
    NO = "NO"


@dataclass(frozen=True)
class Evidence:
    claim: str
    source: str
    observed_at: datetime
    value: float | str | None = None

    def payload(self) -> dict[str, Any]:
        value = asdict(self)
        value["observed_at"] = self.observed_at.astimezone(timezone.utc).isoformat()
        return value


@dataclass(frozen=True)
class PolyMarket:
    id: str
    slug: str
    question: str
    category: Category
    observed_at: datetime
    end_at: datetime | None
    active: bool
    closed: bool
    liquidity: float
    resolution_source: str | None
    description: str
    yes_token_id: str | None
    no_token_id: str | None
    yes_price: float | None
    no_price: float | None
    trade_eligible: bool
    exclusion_reason: str | None
    taker_fee_rate: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def market_probability(self) -> float | None:
        return self.yes_price


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBook:
    token_id: str
    observed_at: datetime
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    tick_size: float | None = None
    last_trade_price: float | None = None


@dataclass(frozen=True)
class ProbabilityEstimate:
    market_id: str
    observed_at: datetime
    market_probability: float
    estimated_probability: float
    confidence_low: float
    confidence_high: float
    quality: EstimateQuality
    base_rate: dict[str, Any]
    evidence: tuple[Evidence, ...]
    reasoning: str
    direction: OutcomeSide
    gross_edge: float
    effective_price: float | None = None
    after_cost_edge: float | None = None
    trade_eligible: bool = False
    exclusion_reason: str | None = None
    id: str = field(default_factory=lambda: str(uuid4()))

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "market_id": self.market_id,
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            "market_probability": self.market_probability,
            "estimated_probability": self.estimated_probability,
            "confidence_band": [self.confidence_low, self.confidence_high],
            "estimate_quality": self.quality.value,
            "base_rate": self.base_rate,
            "evidence": [item.payload() for item in self.evidence],
            "reasoning": self.reasoning,
            "direction": self.direction.value,
            "gross_edge": self.gross_edge,
            "effective_price": self.effective_price,
            "after_cost_edge": self.after_cost_edge,
            "trade_eligible": self.trade_eligible,
            "exclusion_reason": self.exclusion_reason,
            "entity_type": "polymarket",
        }


@dataclass(frozen=True)
class PaperOrder:
    market_id: str
    estimate_id: str
    token_id: str
    direction: OutcomeSide
    requested_notional: float
    created_at: datetime
    id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class PaperFill:
    order_id: str
    market_id: str
    direction: OutcomeSide
    shares: float
    price: float
    fee: float
    notional: float
    filled_at: datetime
    id: str = field(default_factory=lambda: str(uuid4()))


class FillInvariantViolation(RuntimeError):
    """A paper fill escaped the exact CLOB ask levels observed for it."""
