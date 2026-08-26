from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from typing import Protocol, runtime_checkable

from .execution import ExecutionPolicy, execute_order
from .models import ExecutionResult, FillInvariantViolation, MarketObservation, OrderStatus, PaperFill, Side, StockOrder
from .store import StockPaperStore


class Broker(ABC):
    @abstractmethod
    def place(self, order: StockOrder, observation: MarketObservation | None = None) -> ExecutionResult: ...

    @abstractmethod
    def cancel(self, order_id: str) -> bool: ...

    @abstractmethod
    def positions(self) -> list[dict]: ...

    @abstractmethod
    def fills(self) -> list[PaperFill]: ...


@runtime_checkable
class LiveBroker(Protocol):
    """Future contract only. No implementation or registry exists in this WO."""

    def place(self, order: StockOrder, observation: MarketObservation | None = None) -> ExecutionResult: ...
    def cancel(self, order_id: str) -> bool: ...
    def positions(self) -> list[dict]: ...
    def fills(self) -> list[PaperFill]: ...


class PaperBroker(Broker):
    def __init__(self, store: StockPaperStore, policy: ExecutionPolicy = ExecutionPolicy()) -> None:
        self.store = store
        self.policy = policy

    def place(self, order: StockOrder, observation: MarketObservation | None = None) -> ExecutionResult:
        requested = int(order.remaining_quantity or order.quantity)
        if order.side == Side.SELL and requested > self.store.position_quantity(order.market, order.symbol):
            rejected = replace(order, status=OrderStatus.REJECTED, reason="long_only_sell_exceeds_position")
            self.store.save_order(rejected, observation.observed_at if observation else None)
            self.store.record_event(
                order.market,
                "unfilled",
                symbol=order.symbol,
                order_id=order.id,
                reason="long_only_sell_exceeds_position",
                observed_at=observation.observed_at if observation else None,
            )
            return ExecutionResult(rejected, reason="long_only_sell_exceeds_position")
        if observation is None:
            queued = replace(order, status=OrderStatus.QUEUED, reason="market_data_missing")
            self.store.save_order(queued)
            self.store.record_event(order.market, "unfilled", symbol=order.symbol, order_id=order.id, reason="market_data_missing")
            return ExecutionResult(queued, reason="market_data_missing")
        try:
            result = execute_order(order, observation, self.policy)
        except FillInvariantViolation as exc:
            self.store.stop_track(order.market, "fill_price_outside_observed_range", observation.observed_at)
            self.store.record_event(
                order.market,
                "invariant_failure",
                symbol=order.symbol,
                order_id=order.id,
                reason="fill_price_outside_observed_range",
                payload={"error": str(exc)},
                observed_at=observation.observed_at,
            )
            raise
        self.store.save_order(result.order, observation.observed_at)
        if result.fill:
            self.store.save_fill(result.fill)
            self.store.record_event(
                order.market,
                "fill" if result.order.status == OrderStatus.FILLED else "partial_fill",
                symbol=order.symbol,
                order_id=order.id,
                reason=result.reason,
                payload=result.fill.payload(),
                observed_at=observation.observed_at,
            )
        elif result.reason:
            # 중복 억제를 거친다 (WO-FCE-STOCK-STATUS-01 D2).
            #
            # 직접 `record_event` 를 부르던 것이 2,087만 행 폭주의 기전이었다. 큐에 남은
            # 주문은 매 틱 다시 `place()` 를 지나고, 세션이 열리지 않는 한 같은 사유로
            # 계속 미체결이다. 실측 2026-08-25: 큐 주문 **15,755건**(KR 13,836 · US 1,919)이
            # 전부 `session_closed` 였고, 화면의 "진입 거부 1,762만건"은 서로 다른 거부가
            # 아니라 **그 1.5만 건이 반복 계수된 값**이었다.
            #
            # `observed_at` 을 그대로 넘기므로, 시세가 멈춰 있으면 직전 기록과 차이가 0 이라
            # 억제되고, 시세가 갱신되면 다시 기록된다. 사건은 남고 반복만 사라진다.
            self.store.record_event_if_stale(
                order.market,
                "unfilled",
                symbol=order.symbol,
                reason=result.reason,
                payload={"order_id": order.id},
                observed_at=observation.observed_at,
            )
        return result

    def cancel(self, order_id: str) -> bool:
        match = next((item for item in self.store.list_orders() if item.id == order_id), None)
        if match is None or match.status in {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}:
            return False
        self.store.save_order(replace(match, status=OrderStatus.CANCELLED, reason="manual_cancel"))
        return True

    def positions(self) -> list[dict]:
        return list(self.store.dashboard()["positions"])

    def fills(self) -> list[PaperFill]:
        return self.store.list_fills()


def create_broker(*, live_trading_enabled: bool, store: StockPaperStore, policy: ExecutionPolicy = ExecutionPolicy()) -> Broker:
    if live_trading_enabled:
        raise RuntimeError("stock live trading is sealed: LiveBroker has no implementation")
    return PaperBroker(store, policy)
