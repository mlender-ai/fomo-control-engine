from __future__ import annotations

from dataclasses import dataclass, replace
import math

from app.toss.signals import warning_gate

from .accounting import FeeSchedule, calculate_fees
from .models import (
    ExecutionResult,
    FillInvariantViolation,
    Market,
    MarketObservation,
    OrderStatus,
    PaperFill,
    Side,
    StockOrder,
)


@dataclass(frozen=True)
class ExecutionPolicy:
    max_minute_volume_ratio: float = 0.05
    vi_policy: str = "queue"
    warning_policy: str = "cancel"
    fee_schedule: FeeSchedule = FeeSchedule()


def execute_order(order: StockOrder, observation: MarketObservation, policy: ExecutionPolicy = ExecutionPolicy()) -> ExecutionResult:
    if order.quantity <= 0 or int(order.quantity) != order.quantity:
        return _reject(order, "whole_share_required")
    if observation.symbol.upper() != order.symbol.upper() or observation.market != order.market:
        return _reject(order, "observation_mismatch")
    excluded, badges = warning_gate(observation.warnings)
    if excluded:
        return _defer_or_reject(order, "warning_hard_gate", policy.warning_policy)
    if observation.halted:
        return _defer_or_reject(order, "trading_halted", "queue")
    if observation.vi_active or any(value.startswith("vi") or value == "변동성완화장치" for value in badges):
        return _defer_or_reject(order, "vi", policy.vi_policy)
    if not observation.session_open:
        return ExecutionResult(order=replace(order, status=OrderStatus.QUEUED, reason="session_closed"), reason="session_closed")
    if order.market == Market.KR and ((order.side == Side.BUY and observation.upper_locked) or (order.side == Side.SELL and observation.lower_locked)):
        return ExecutionResult(order=replace(order, status=OrderStatus.QUEUED, reason="price_limit_locked"), reason="price_limit_locked")
    required = (observation.minute_high, observation.minute_low, observation.minute_volume)
    if any(value is None for value in required) or observation.minute_volume is None or observation.minute_volume <= 0:
        return ExecutionResult(order=replace(order, status=OrderStatus.QUEUED, reason="market_data_missing"), reason="market_data_missing")
    cap = math.floor(observation.minute_volume * policy.max_minute_volume_ratio)
    if cap <= 0:
        return ExecutionResult(order=replace(order, status=OrderStatus.QUEUED, reason="liquidity_zero"), reason="liquidity_zero")
    remaining = int(order.remaining_quantity or order.quantity)
    fill_quantity = min(remaining, cap)
    base = observation.session_open_price if order.reason == "session_closed" else observation.minute_close
    if base is None:
        return ExecutionResult(order=replace(order, status=OrderStatus.QUEUED, reason="market_data_missing"), reason="market_data_missing")
    half_spread = _half_spread(observation)
    raw_price = base + half_spread if order.side == Side.BUY else base - half_spread
    fill_price = round_to_tick(raw_price, order.market, order.side)
    if observation.minute_low is None or observation.minute_high is None or not observation.minute_low <= fill_price <= observation.minute_high:
        raise FillInvariantViolation(f"fill {fill_price} escaped observed range {observation.minute_low}..{observation.minute_high} for {order.symbol}")
    gross = fill_price * fill_quantity
    fees = calculate_fees(order.market, order.side, gross, policy.fee_schedule)
    next_remaining = remaining - fill_quantity
    status = OrderStatus.FILLED if next_remaining == 0 else OrderStatus.PARTIAL
    reason = None if status == OrderStatus.FILLED else "liquidity_partial"
    updated = replace(order, status=status, remaining_quantity=next_remaining, reason=reason)
    fill = PaperFill(
        order_id=order.id,
        symbol=order.symbol,
        market=order.market,
        currency=order.currency,
        side=order.side,
        quantity=fill_quantity,
        price=fill_price,
        filled_at=observation.observed_at,
        gross_amount=gross,
        commission=fees.commission,
        transaction_tax=fees.transaction_tax,
        fx_rate_to_krw=observation.fx_rate_to_krw,
        fx_observed_at=observation.fx_observed_at,
        entry_mode=order.entry_mode,
    )
    return ExecutionResult(order=updated, fill=fill, reason=reason)


def round_to_tick(price: float, market: Market, side: Side) -> float:
    tick = 0.01 if market == Market.US else krx_tick_size(price)
    scaled = price / tick
    rounded = math.ceil(scaled - 1e-12) if side == Side.BUY else math.floor(scaled + 1e-12)
    return round(rounded * tick, 8)


def krx_tick_size(price: float) -> float:
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def _half_spread(observation: MarketObservation) -> float:
    if observation.bid is None or observation.ask is None or observation.ask < observation.bid:
        return 0.0
    return (observation.ask - observation.bid) / 2


def _reject(order: StockOrder, reason: str) -> ExecutionResult:
    return ExecutionResult(order=replace(order, status=OrderStatus.REJECTED, reason=reason), reason=reason)


def _defer_or_reject(order: StockOrder, reason: str, behavior: str) -> ExecutionResult:
    status = OrderStatus.CANCELLED if behavior == "cancel" else OrderStatus.QUEUED
    return ExecutionResult(order=replace(order, status=status, reason=reason), reason=reason)
