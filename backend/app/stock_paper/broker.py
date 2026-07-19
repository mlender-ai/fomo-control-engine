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
            self.store.record_event(
                order.market,
                "unfilled",
                symbol=order.symbol,
                order_id=order.id,
                reason=result.reason,
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
