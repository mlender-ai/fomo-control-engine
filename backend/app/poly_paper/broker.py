from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .models import FillInvariantViolation, OrderBook, PaperFill, PaperOrder


@dataclass(frozen=True)
class FillPreview:
    shares: float
    contract_notional: float
    fee: float
    notional: float
    vwap: float
    effective_price: float
    min_price: float
    max_price: float
    partial: bool


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    fill: PaperFill | None
    reason: str | None


class PaperBroker:
    """Paper-only CLOB depth walker. No live broker contract exists."""

    def __init__(self, *, max_observed_ask_fraction: float = 0.05) -> None:
        if not 0 < max_observed_ask_fraction <= 1:
            raise ValueError("max_observed_ask_fraction must be in (0, 1]")
        self.max_observed_ask_fraction = max_observed_ask_fraction

    def preview(self, book: OrderBook, requested_notional: float, *, taker_fee_rate: float = 0.0) -> FillPreview | None:
        if requested_notional <= 0 or not book.asks:
            return None
        if taker_fee_rate < 0:
            raise ValueError("taker_fee_rate cannot be negative")
        total_ask_shares = sum(level.size for level in book.asks)
        share_cap = total_ask_shares * self.max_observed_ask_fraction
        remaining_notional = requested_notional
        remaining_shares = share_cap
        consumed: list[tuple[float, float]] = []
        for level in book.asks:
            if remaining_notional <= 1e-12 or remaining_shares <= 1e-12:
                break
            unit_cost = level.price + taker_fee_per_share(level.price, taker_fee_rate)
            shares = min(level.size, remaining_shares, remaining_notional / unit_cost)
            if shares <= 0:
                continue
            consumed.append((level.price, shares))
            remaining_notional -= shares * unit_cost
            remaining_shares -= shares
        if not consumed:
            return None
        shares = sum(size for _, size in consumed)
        contract_notional = sum(price * size for price, size in consumed)
        fee = sum(taker_fee(size, price, taker_fee_rate) for price, size in consumed)
        notional = contract_notional + fee
        prices = [price for price, _ in consumed]
        return FillPreview(
            shares=shares,
            contract_notional=contract_notional,
            fee=fee,
            notional=notional,
            vwap=contract_notional / shares,
            effective_price=notional / shares,
            min_price=min(prices),
            max_price=max(prices),
            partial=notional + 1e-9 < requested_notional,
        )

    def place(self, order: PaperOrder, book: OrderBook, *, taker_fee_rate: float = 0.0) -> ExecutionResult:
        preview = self.preview(book, order.requested_notional, taker_fee_rate=taker_fee_rate)
        if preview is None:
            return ExecutionResult("rejected", None, "orderbook_liquidity_missing")
        if not preview.min_price - 1e-12 <= preview.vwap <= preview.max_price + 1e-12:
            raise FillInvariantViolation(f"VWAP {preview.vwap} escaped observed asks [{preview.min_price}, {preview.max_price}]")
        fill = PaperFill(
            order_id=order.id,
            market_id=order.market_id,
            direction=order.direction,
            shares=preview.shares,
            price=preview.vwap,
            fee=preview.fee,
            notional=preview.notional,
            filled_at=datetime.now(timezone.utc),
        )
        return ExecutionResult("partial" if preview.partial else "filled", fill, "liquidity_cap" if preview.partial else None)


def taker_fee(shares: float, price: float, fee_rate: float) -> float:
    return shares * taker_fee_per_share(price, fee_rate)


def taker_fee_per_share(price: float, fee_rate: float) -> float:
    return fee_rate * price * (1 - price)
