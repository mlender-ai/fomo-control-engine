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
    # WO-FCE-MAKE-IT-RUN-01 Phase 1 — **가격을 만든 봉과 검증하는 봉을 일치시킨다.**
    #
    # 이전에는 `session_closed` 큐 주문만 `session_open_price`(시가)로 체결가를 만들고
    # invariant 는 **현재 분봉** 범위로 검사했다. 시가는 개장 직후 분봉에만 있으므로
    # 체결이 그 분봉에서 일어나지 않으면 반드시 범위를 벗어난다 — 갭이 있는 날이면 확정이다.
    # US 트랙이 그렇게 정지했고 KR 큐 13,934건이 같은 실패를 대기 중이었다.
    #
    # **매매 로직이 아니라 논리 오류다.** 두 봉이 같아야 검사가 의미를 갖는다.
    base = observation.minute_close
    if base is None:
        return ExecutionResult(order=replace(order, status=OrderStatus.QUEUED, reason="market_data_missing"), reason="market_data_missing")
    half_spread = _half_spread(observation)
    raw_price = base + half_spread if order.side == Side.BUY else base - half_spread
    fill_price = _bounded_fill_price(raw_price, observation, market=order.market, side=order.side)
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


def _bounded_fill_price(raw_price: float, observation: MarketObservation, *, market: Market, side: Side) -> float:
    """호가 스프레드를 얹은 가격을 **실제 거래된 범위 안으로** 되돌린다.

    ## 왜 필요한가 — 두 번째 결함

    봉을 일치시켜도 터지는 자리가 남는다:

    ```
    minute_close == minute_high 인 분봉 + 매수 + 반 스프레드
      → raw = high + half_spread  →  범위 초과  →  💥
    ```

    분봉 종가가 고가와 같은 것은 흔하다(상승 마감). 그리고 `round_to_tick` 은 매수에서
    **올림**이라 한 틱 더 밀어 올린다. 첫 결함만 고치면 이것이 계속 정지를 만든다.

    ## invariant 를 완화하는 것이 아니다 (C1)

    검사는 그대로 남고 여전히 발화한다. 바뀌는 것은 **가격 모형**이다 —
    **그 분에 거래되지 않은 값으로는 체결될 수 없다.** 고가 위에서 사는 체결은 없다.
    범위 끝으로 자르는 것은 그 분의 **가장 불리한 실제 체결가**를 쓰는 것이므로 완화가
    아니라 현실 안에서의 보수적 선택이다.

    자른 뒤 틱은 **범위 안쪽으로** 맞춘다 — 매수 상단에서 올림하면 다시 나간다.

    잘라도 범위 밖이면(밴드가 한 틱보다 좁은 경우) 그대로 둔다. 그것은 모형 문제가 아니라
    **데이터 문제**이고, invariant 가 잡아야 한다.

    같은 이유로 **종가 자체가 자기 봉 범위 밖이면 아무것도 자르지 않는다.** 그 관측은
    모순이며 자르면 결함을 덮는다 — invariant 가 발화해야 하는 자리다.
    """
    price = round_to_tick(raw_price, market, side)
    high = observation.minute_high
    low = observation.minute_low
    base = observation.minute_close
    if high is None or low is None or base is None or not low <= base <= high:
        # **기저 관측이 이미 모순이다.** 종가가 자기 봉의 범위 밖이면 그것은 모형이 아니라
        # 데이터 결함이고, 자르면 결함을 덮는다. invariant 가 잡게 그대로 넘긴다.
        return price
    if price > high:
        # 상단으로 자르고 **내림**으로 틱을 맞춘다 — 올림하면 상단을 다시 넘는다.
        price = round_to_tick(high, market, Side.SELL)
    elif price < low:
        price = round_to_tick(low, market, Side.BUY)
    return price


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
