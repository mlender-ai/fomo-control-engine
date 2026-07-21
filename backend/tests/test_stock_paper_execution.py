from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from typing import cast

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.db.migrations import run_migrations
from app.stock_paper.accounting import FeeSchedule
from app.stock_paper.broker import LiveBroker, PaperBroker, create_broker
from app.stock_paper.execution import ExecutionPolicy, execute_order, krx_tick_size
from app.stock_paper.models import (
    Currency,
    FillInvariantViolation,
    Market,
    MarketObservation,
    OrderStatus,
    Side,
    StockOrder,
)
from app.stock_paper.parameters import load_stock_parameters
from app.stock_paper.store import StockPaperStore
from app.stock_paper.universe import load_universe


NOW = datetime(2026, 7, 20, 14, 30, tzinfo=timezone.utc)


def order(*, market: Market = Market.US, side: Side = Side.BUY, quantity: int | float = 10) -> StockOrder:
    return StockOrder(
        symbol="AAPL" if market == Market.US else "005930",
        market=market,
        currency=Currency.USD if market == Market.US else Currency.KRW,
        side=side,
        quantity=cast(int, quantity),
        signal_at=NOW,
        signal_price=100.0,
    )


def observation(*, market: Market = Market.US, session_open: bool = True, **updates) -> MarketObservation:
    values = {
        "symbol": "AAPL" if market == Market.US else "005930",
        "market": market,
        "observed_at": NOW,
        "session_open": session_open,
        "session_open_price": 105.0,
        "minute_open": 104.0,
        "minute_high": 106.0,
        "minute_low": 99.0,
        "minute_close": 100.0,
        "minute_volume": 1_000.0,
        "bid": 99.9,
        "ask": 100.1,
    }
    values.update(updates)
    return MarketObservation(**values)


def migrated_store(tmp_path) -> StockPaperStore:
    path = tmp_path / "paper.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    connection.close()
    store = StockPaperStore(f"sqlite:///{path}")
    store.ensure_tracks(universe_version="test", initial_krw=10_000_000, initial_usd=100_000, now=NOW)
    return store


def test_session_gate_queues_then_gap_order_uses_observed_open() -> None:
    pending = execute_order(order(), observation(session_open=False))
    assert pending.order.status == OrderStatus.QUEUED
    assert pending.reason == "session_closed"

    opened = execute_order(pending.order, observation(session_open=True, bid=105.0, ask=105.0))
    assert opened.fill is not None
    assert opened.fill.price == 105.0


def test_whole_share_and_market_tick_rules_are_enforced() -> None:
    fractional = execute_order(order(quantity=1.5), observation())
    assert fractional.order.status == OrderStatus.REJECTED
    assert fractional.reason == "whole_share_required"
    assert krx_tick_size(64_321) == 100


def test_kr_price_limit_lock_is_unfilled() -> None:
    result = execute_order(
        order(market=Market.KR),
        observation(market=Market.KR, upper_limit=70_000, upper_locked=True, minute_close=70_000, bid=70_000, ask=None),
    )
    assert result.fill is None
    assert result.order.status == OrderStatus.QUEUED
    assert result.reason == "price_limit_locked"


@pytest.mark.parametrize(
    ("updates", "reason", "status"),
    [
        ({"vi_active": True}, "vi", OrderStatus.QUEUED),
        ({"halted": True}, "trading_halted", OrderStatus.QUEUED),
        ({"warnings": ("investment_risk",)}, "warning_hard_gate", OrderStatus.CANCELLED),
    ],
)
def test_vi_halt_and_warning_gates(updates, reason, status) -> None:
    result = execute_order(order(), observation(**updates))
    assert result.fill is None
    assert result.reason == reason
    assert result.order.status == status


def test_liquidity_cap_partially_fills_and_applies_half_spread() -> None:
    result = execute_order(order(quantity=100), observation(minute_volume=1_000, minute_close=100, bid=99.8, ask=100.2))
    assert result.fill is not None
    assert result.fill.quantity == 50
    assert result.fill.price == 100.2
    assert result.order.status == OrderStatus.PARTIAL
    assert result.order.remaining_quantity == 50
    assert result.reason == "liquidity_partial"


def test_fill_range_invariant_stops_track_and_logs_failure(tmp_path) -> None:
    store = migrated_store(tmp_path)
    broker = PaperBroker(store)
    with pytest.raises(FillInvariantViolation):
        broker.place(order(), observation(minute_low=99, minute_high=100.05, bid=99.8, ask=100.4))
    track = next(item for item in store.dashboard()["tracks"] if item["market"] == "US")
    assert track["status"] == "stopped"
    assert track["stop_reason"] == "fill_price_outside_observed_range"
    assert track["rejection_reasons"]["fill_price_outside_observed_range"] >= 1


def test_kr_sell_fill_records_transaction_tax(tmp_path) -> None:
    store = migrated_store(tmp_path)
    broker = PaperBroker(
        store,
        ExecutionPolicy(fee_schedule=FeeSchedule(kr_commission_rate=0.0, us_commission_rate=0.0, kr_sell_transaction_tax_rate=0.0015)),
    )
    bought = broker.place(
        order(market=Market.KR, side=Side.BUY),
        observation(market=Market.KR, minute_low=60_000, minute_high=70_000, minute_close=65_000, bid=65_000, ask=65_000),
    )
    assert bought.fill is not None
    result = broker.place(
        order(market=Market.KR, side=Side.SELL),
        observation(market=Market.KR, minute_low=60_000, minute_high=70_000, minute_close=65_000, bid=65_000, ask=65_000),
    )
    assert result.fill is not None
    assert result.fill.transaction_tax == result.fill.gross_amount * 0.0015
    assert next(fill for fill in broker.fills() if fill.side == Side.SELL).transaction_tax == result.fill.transaction_tax
    stored = next(item for item in store.dashboard()["recent_fills"] if item["side"] == "sell")
    assert stored["transaction_tax"] > 0
    assert stored["currency"] == "KRW"


def test_paper_broker_cannot_open_a_short_position(tmp_path) -> None:
    store = migrated_store(tmp_path)
    broker = PaperBroker(store)
    result = broker.place(order(side=Side.SELL), observation())
    assert result.fill is None
    assert result.order.status == OrderStatus.REJECTED
    assert result.reason == "long_only_sell_exceeds_position"
    assert store.dashboard()["fill_count"] == 0


def test_partial_sell_can_finish_without_crossing_long_only_gate(tmp_path) -> None:
    store = migrated_store(tmp_path)
    broker = PaperBroker(store)
    bought = broker.place(order(quantity=100), observation(minute_volume=4_000))
    assert bought.order.status == OrderStatus.FILLED
    first = broker.place(order(side=Side.SELL, quantity=100), observation(minute_volume=1_000))
    assert first.order.status == OrderStatus.PARTIAL
    assert first.order.remaining_quantity == 50
    second = broker.place(first.order, observation(minute_volume=1_000))
    assert second.order.status == OrderStatus.FILLED
    assert store.position_quantity(Market.US, "AAPL") == 0


def test_live_broker_is_contract_only_and_true_setting_fails() -> None:
    assert getattr(LiveBroker, "_is_protocol", False) is True
    with pytest.raises(ValidationError, match="stock paper must beat its benchmark"):
        Settings(stock_live_trading_enabled=True)


def test_broker_factory_never_returns_live_broker(tmp_path) -> None:
    store = migrated_store(tmp_path)
    assert isinstance(create_broker(live_trading_enabled=False, store=store), PaperBroker)
    with pytest.raises(RuntimeError, match="sealed"):
        create_broker(live_trading_enabled=True, store=store)


def test_versioned_universe_has_two_independent_100_name_tracks() -> None:
    universe = load_universe()
    assert len(universe.instruments) == 200
    assert len(universe.for_market(Market.KR)) == 100
    assert len(universe.for_market(Market.US)) == 100
    assert universe.entry_allowed(Market.KR, "005930", ["investment_risk"]) == (False, "warning_hard_gate")
    parameters = load_stock_parameters()
    assert parameters.version == "stock-v2"
    assert parameters.signature_gate_mode == "record_only"
    assert parameters.earnings_gate_mode == "not_evaluable"
    assert parameters.long_only is True
