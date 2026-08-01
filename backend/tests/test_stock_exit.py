"""WO-FCE-STOCK-EXIT-01 회귀 가드.

최대 금지사항은 **무효화 가격에 마법 체결(갭 무시)**이다. 손절이 실제보다 좋아 보이면 그
성적으로 자동매매를 판단했을 때 실거래에서 그대로 손실이 된다. 갭 테스트가 이 파일의 심장이다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.db.migrations import run_migrations
from app.stock_paper.broker import PaperBroker
from app.stock_paper.exit_policy import (
    StockExitParameters,
    TakeProfitStep,
    evaluate_stock_exit,
    load_exit_parameters,
)
from app.stock_paper.execution import ExecutionPolicy, execute_order
from app.stock_paper.models import Currency, Market, MarketObservation, OrderStatus, Side, StockOrder
from app.stock_paper.store import StockPaperStore


def _params(**overrides) -> StockExitParameters:
    base = {
        "version": "test-exit",
        "invalidation_basis": "close",
        "max_holding_days": 10,
        "tp_ladder": (TakeProfitStep(1, 0.4), TakeProfitStep(2, 0.35), TakeProfitStep(3, 0.25)),
        "breakeven_stop_after_tp1": True,
        "forced_exit_warnings": ("거래정지", "정리매매"),
        "max_carryover_days": 3,
    }
    base.update(overrides)
    return StockExitParameters(**base)


def _plan(invalidation: float = 100.0, targets: tuple[float, ...] = (120.0, 140.0, 160.0)) -> dict:
    return {
        "invalidation_price": invalidation,
        "targets": [{"level": index + 1, "price": price} for index, price in enumerate(targets)],
        "realized_levels": [],
    }


def _observation(**overrides) -> MarketObservation:
    base = {
        "symbol": "AAPL",
        "market": Market.US,
        "observed_at": datetime(2026, 7, 31, 14, 30, tzinfo=timezone.utc),
        "session_open": True,
        "session_open_price": 92.0,
        "minute_open": 92.0,
        "minute_high": 93.0,
        "minute_low": 91.0,
        "minute_close": 92.5,
        "minute_volume": 100_000.0,
        "bid": None,
        "ask": None,
        "upper_limit": None,
        "lower_limit": None,
        "upper_locked": False,
        "lower_locked": False,
        "vi_active": False,
        "halted": False,
        "warnings": (),
        "fx_rate_to_krw": None,
        "fx_observed_at": None,
    }
    base.update(overrides)
    return MarketObservation(**base)


def _sell(quantity: int = 10, reason: str | None = None, market: Market = Market.US) -> StockOrder:
    return StockOrder(
        symbol="AAPL",
        market=market,
        currency=Currency.USD if market == Market.US else Currency.KRW,
        side=Side.SELL,
        quantity=quantity,
        signal_at=datetime(2026, 7, 30, 20, 0, tzinfo=timezone.utc),
        signal_price=100.0,  # 무효화선. 체결가로 쓰이면 안 된다.
        reason=reason,
    )


# ══════════════════════════════════════════════════════════════
# C1 핵심 증명 — 갭 체결 (무효화 가격 마법 체결 금지)
# ══════════════════════════════════════════════════════════════


def test_gap_exit_fills_at_next_session_open_not_invalidation_price() -> None:
    """무효화선 100 이탈, 다음 시가 92 → **92에 체결**되어야 한다(100 아님).

    WO 최대 금지사항의 직접 증명. 이 테스트가 깨지면 손절 성적이 실제보다 좋아진다.
    """
    queued = _sell(reason="session_closed")
    observation = _observation(session_open=True, session_open_price=92.0, minute_low=91.0, minute_high=93.0)

    result = execute_order(queued, observation)

    assert result.fill is not None
    assert result.fill.price == 92.0, "장외 대기 청산은 다음 세션 시가로 체결되어야 한다"
    assert result.fill.price != queued.signal_price, "무효화 가격에 마법 체결되면 안 된다"
    # 갭 손실이 갭 손실로 남는다 — 신호가 100 대비 8 손실이 기록에 그대로 반영된다.
    assert result.fill.price < 100.0


def test_intraday_exit_uses_observed_close_not_signal_price() -> None:
    """장중 청산도 신호가가 아니라 관측된 분봉 종가에서 파생된다."""
    order = _sell()
    observation = _observation(minute_close=95.0, minute_low=94.0, minute_high=96.0)

    result = execute_order(order, observation)

    assert result.fill is not None
    assert result.fill.price == 95.0
    assert result.fill.price != order.signal_price


def test_exit_fill_price_stays_within_observed_range() -> None:
    """체결가 invariant: 관측된 봉 범위를 벗어나면 예외로 엔진이 정지한다."""
    from app.stock_paper.models import FillInvariantViolation

    order = _sell()
    # 종가가 범위 밖인 모순된 관측 — 방어적으로 반드시 터져야 한다.
    broken = _observation(minute_close=200.0, minute_low=90.0, minute_high=95.0)
    with pytest.raises(FillInvariantViolation):
        execute_order(order, broken)


def test_session_closed_exit_is_queued_not_filled() -> None:
    """장외 청산 신호는 체결되지 않고 대기한다 — 다음 개장에 처리."""
    result = execute_order(_sell(), _observation(session_open=False))
    assert result.fill is None
    assert result.order.status == OrderStatus.QUEUED
    assert result.reason == "session_closed"


# ══════════════════════════════════════════════════════════════
# 체결 정직성 — 상하한가·VI·유동성·거래세
# ══════════════════════════════════════════════════════════════


def test_kr_lower_limit_lock_blocks_sell_with_reason() -> None:
    """KR 하한가 잠김에서는 매도 미체결 — 빠져나올 수 없음을 정직하게 기록한다."""
    order = _sell(market=Market.KR)
    observation = _observation(market=Market.KR, symbol="AAPL", lower_locked=True)
    result = execute_order(order, observation)
    assert result.fill is None
    assert result.reason == "price_limit_locked"


def test_vi_defers_exit() -> None:
    result = execute_order(_sell(), _observation(vi_active=True))
    assert result.fill is None
    assert result.reason == "vi"


def test_halt_defers_exit() -> None:
    result = execute_order(_sell(), _observation(halted=True))
    assert result.fill is None
    assert result.reason == "trading_halted"


def test_liquidity_cap_produces_partial_exit() -> None:
    """청산 수량이 직전 1분 거래량의 5%를 넘으면 부분 체결된다."""
    order = _sell(quantity=1000)
    result = execute_order(order, _observation(minute_volume=2000.0), ExecutionPolicy(max_minute_volume_ratio=0.05))
    assert result.fill is not None
    assert result.fill.quantity == 100  # 2000 × 5%
    assert result.order.status == OrderStatus.PARTIAL
    assert result.order.remaining_quantity == 900


def test_kr_sell_records_transaction_tax() -> None:
    """KR 매도는 증권거래세가 반영된다(매수에는 없다)."""
    order = _sell(market=Market.KR, quantity=10)
    result = execute_order(order, _observation(market=Market.KR))
    assert result.fill is not None
    assert result.fill.transaction_tax > 0

    buy = StockOrder(
        symbol="AAPL",
        market=Market.KR,
        currency=Currency.KRW,
        side=Side.BUY,
        quantity=10,
        signal_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
    )
    buy_result = execute_order(buy, _observation(market=Market.KR))
    assert buy_result.fill is not None
    assert buy_result.fill.transaction_tax == 0


# ══════════════════════════════════════════════════════════════
# 청산 트리거 4종 (우선순위: 강제 > 무효화 > TP > 시간)
# ══════════════════════════════════════════════════════════════


def test_forced_liquidation_wins_over_everything() -> None:
    # 목표가에 도달했어도 거래정지면 강제 청산이 이긴다.
    decision = evaluate_stock_exit(
        plan=_plan(),
        close_price=150.0,
        average_price=110.0,
        holding_days=1,
        warnings=("거래정지",),
        parameters=_params(),
    )
    assert decision.should_exit
    assert decision.trigger == "forced_liquidation"
    assert decision.fraction == 1.0


def test_invalidation_breach_closes_full_position() -> None:
    decision = evaluate_stock_exit(plan=_plan(invalidation=100.0), close_price=99.0, average_price=110.0, holding_days=1, parameters=_params())
    assert decision.should_exit
    assert decision.trigger == "invalidation_breach"
    assert decision.fraction == 1.0


def test_invalidation_uses_close_not_intraday_touch() -> None:
    # 종가가 무효화선 위면 유지 — 장중 꼬리로 손절되지 않는다.
    decision = evaluate_stock_exit(plan=_plan(invalidation=100.0), close_price=101.0, average_price=110.0, holding_days=1, parameters=_params())
    assert not decision.should_exit


def test_take_profit_ladder_partial_then_final() -> None:
    first = evaluate_stock_exit(plan=_plan(), close_price=121.0, average_price=110.0, holding_days=1, parameters=_params())
    assert first.should_exit
    assert first.trigger == "take_profit"
    assert first.level == 1
    assert first.fraction < 1.0  # 분할 청산

    final = evaluate_stock_exit(
        plan=_plan(),
        close_price=161.0,
        average_price=110.0,
        holding_days=1,
        realized_levels=(1, 2),
        parameters=_params(),
    )
    assert final.level == 3
    assert final.fraction == 1.0  # 마지막 단계는 전량


def test_realized_level_is_not_repeated() -> None:
    decision = evaluate_stock_exit(
        plan=_plan(),
        close_price=121.0,
        average_price=110.0,
        holding_days=1,
        realized_levels=(1,),
        parameters=_params(),
    )
    # TP1은 이미 실현됐고 TP2(140) 미달 → 청산 없음
    assert not decision.should_exit


def test_breakeven_stop_after_tp1() -> None:
    """TP1 실현 후 잔여분은 본전을 지킨다 — 무효화선보다 평단이 높으면 평단이 손절선."""
    decision = evaluate_stock_exit(
        plan=_plan(invalidation=100.0),
        close_price=109.0,
        average_price=110.0,
        holding_days=1,
        realized_levels=(1,),
        parameters=_params(breakeven_stop_after_tp1=True),
    )
    assert decision.should_exit
    assert decision.trigger == "invalidation_breach"
    assert decision.detail["breakeven"] is True


def test_time_stop_closes_stale_position() -> None:
    """8일 방치 재발 방지 안전판."""
    decision = evaluate_stock_exit(plan=_plan(), close_price=110.0, average_price=110.0, holding_days=10, parameters=_params(max_holding_days=10))
    assert decision.should_exit
    assert decision.trigger == "time_stop"


def test_holds_when_nothing_triggers() -> None:
    decision = evaluate_stock_exit(plan=_plan(), close_price=110.0, average_price=110.0, holding_days=1, parameters=_params())
    assert not decision.should_exit
    assert decision.trigger == "none"


def test_missing_mark_holds_rather_than_guessing() -> None:
    """마크가 없으면 추측해서 청산하지 않는다."""
    decision = evaluate_stock_exit(plan=_plan(), close_price=None, average_price=110.0, holding_days=99, parameters=_params())
    assert not decision.should_exit
    assert decision.detail["reason"] == "mark_missing"


# ══════════════════════════════════════════════════════════════
# 파라미터 · SELL 경로 존재 증명
# ══════════════════════════════════════════════════════════════


def test_exit_parameters_load_and_validate() -> None:
    parameters = load_exit_parameters()
    assert parameters.version == "stock-exit-v1"
    assert parameters.invalidation_basis == "close"
    assert abs(sum(step.fraction for step in parameters.tp_ladder) - 1.0) < 0.01


def test_sell_order_creation_path_exists() -> None:
    """수용 기준: Side.SELL 주문을 **생성**하는 경로가 존재한다."""
    from pathlib import Path

    source = (Path(__file__).parents[1] / "app" / "stock_paper" / "service.py").read_text()
    assert "side=Side.SELL" in source, "매도 주문을 생성하는 코드가 있어야 한다"
    assert "_process_exits" in source


def test_broker_rejects_sell_exceeding_position(tmp_path) -> None:
    path = tmp_path / "exit.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    connection.close()
    store = StockPaperStore(f"sqlite:///{path}")
    store.ensure_tracks(universe_version="v1", initial_krw=1e8, initial_usd=1e5)
    broker = PaperBroker(store)

    result = broker.place(_sell(quantity=5), _observation())

    assert result.fill is None
    assert result.reason == "long_only_sell_exceeds_position"


def test_exit_plan_persists_and_reloads(tmp_path) -> None:
    path = tmp_path / "plan.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    connection.execute(
        """INSERT INTO stock_paper_positions (market, symbol, quantity, average_price, currency, updated_at)
        VALUES ('US','AAPL',10,110.0,'USD','2026-07-31T00:00:00+00:00')"""
    )
    connection.commit()
    connection.close()
    store = StockPaperStore(f"sqlite:///{path}")

    opened = datetime(2026, 7, 31, tzinfo=timezone.utc)
    store.set_exit_plan(Market.US, "AAPL", _plan(), opened)
    rows = store.open_positions(Market.US)

    assert len(rows) == 1
    assert rows[0]["exit_plan"]["invalidation_price"] == 100.0
    assert rows[0]["opened_at"] is not None

    store.record_realized_level(Market.US, "AAPL", 1)
    assert store.open_positions(Market.US)[0]["exit_plan"]["realized_levels"] == [1]

    store.mark_excluded_from_stats(Market.US, "AAPL", "void_no_exit_path")
    excluded = store.excluded_symbols(Market.US)
    assert excluded and excluded[0]["exclusion_reason"] == "void_no_exit_path"


def test_holding_days_from_opened_at() -> None:
    from app.stock_paper.service import _holding_days

    assert _holding_days(None) == 0
    past = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    assert _holding_days(past) == 8


def test_crypto_exit_path_untouched() -> None:
    """C3: 크립토 청산 경로는 주식 청산 모듈을 임포트하지 않는다."""
    from pathlib import Path

    root = Path(__file__).parents[1] / "app" / "paper"
    source = "\n".join(path.read_text() for path in root.glob("*.py"))
    assert "stock_paper.exit_policy" not in source
    assert "stock_paper" not in source


# ══════════════════════════════════════════════════════════════
# 작업 7.1 — end-to-end: 무효화 이탈 주입 → SELL 생성·체결·기록
# ══════════════════════════════════════════════════════════════


def _seeded_store(tmp_path, *, close_price: float, opened_days_ago: int = 1):
    """보유 포지션 1건 + 청산계획 + 마크가 있는 저장소를 만든다."""
    path = tmp_path / "e2e.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    opened = (datetime.now(timezone.utc) - timedelta(days=opened_days_ago)).isoformat()
    connection.execute(
        """INSERT INTO stock_paper_positions (market, symbol, quantity, average_price, currency, updated_at, opened_at)
        VALUES ('US','AAPL',10,110.0,'USD',?,?)""",
        (opened, opened),
    )
    connection.execute(
        "INSERT INTO stock_paper_marks (market, symbol, price, observed_at) VALUES ('US','AAPL',?,?)",
        (close_price, datetime.now(timezone.utc).isoformat()),
    )
    connection.commit()
    connection.close()
    store = StockPaperStore(f"sqlite:///{path}")
    store.ensure_tracks(universe_version="v1", initial_krw=1e8, initial_usd=1e5)
    store.set_exit_plan(Market.US, "AAPL", _plan(invalidation=100.0), datetime.fromisoformat(opened))
    return store


def test_end_to_end_invalidation_breach_creates_and_fills_sell(tmp_path, monkeypatch) -> None:
    """무효화선 100 아래 종가 95 주입 → SELL 주문이 생성·체결·기록되어야 한다.

    작업 7.1의 직접 증명 — 판단 유닛이 아니라 `_process_exits` 전체 경로를 탄다.
    """
    from app.stock_paper import service as stock_service

    store = _seeded_store(tmp_path, close_price=95.0)
    broker = PaperBroker(store)
    observation = _observation(minute_close=95.0, minute_low=94.0, minute_high=96.0)
    monkeypatch.setattr(stock_service, "_observation", lambda *args, **kwargs: observation)

    events: list[dict] = []
    counters = stock_service._process_exits(broker, store, None, {"US": {"market_state": "open"}}, events)

    assert counters["triggered"] == 1, "무효화 이탈이 청산을 발화시켜야 한다"
    assert counters["filled"] == 1, "SELL 주문이 실제로 체결되어야 한다"
    assert store.position_quantity(Market.US, "AAPL") == 0, "포지션이 정리되어야 한다"
    sells = [fill for fill in store.list_fills() if fill.side == Side.SELL]
    assert len(sells) == 1
    assert sells[0].price == 95.0  # 관측 종가에서 파생 — 무효화가 100이 아니다
    closed = [event for event in events if event["kind"] == "closed"]
    assert closed and closed[0]["detail"]["trigger"] == "invalidation_breach"


def test_end_to_end_gap_exit_fills_at_session_open(tmp_path, monkeypatch) -> None:
    """장외 이탈 → 대기 → 다음 세션 시가 92 체결. C1의 end-to-end 증명."""
    from app.stock_paper import service as stock_service

    store = _seeded_store(tmp_path, close_price=95.0)
    broker = PaperBroker(store)

    closed_session = _observation(session_open=False)
    monkeypatch.setattr(stock_service, "_observation", lambda *args, **kwargs: closed_session)
    stock_service._process_exits(broker, store, None, {"US": {"market_state": "closed"}}, [])
    assert store.position_quantity(Market.US, "AAPL") == 10, "장외에서는 체결되지 않는다"
    queued = [order for order in store.list_orders() if order.side == Side.SELL]
    assert queued and queued[0].reason == "session_closed"

    gap_open = _observation(session_open=True, session_open_price=92.0, minute_low=91.0, minute_high=93.0)
    monkeypatch.setattr(stock_service, "_observation", lambda *args, **kwargs: gap_open)
    processed = stock_service._process_pending_orders(broker, None, {"US": {"market_state": "open"}})

    assert processed >= 1
    sells = [fill for fill in store.list_fills() if fill.side == Side.SELL]
    assert sells, "다음 세션에 체결되어야 한다"
    assert sells[0].price == 92.0, "무효화가(100)가 아니라 다음 세션 시가(92)에 체결"


def test_end_to_end_time_stop_closes_stale_position(tmp_path, monkeypatch) -> None:
    """8일 방치 재발 방지 — 보유일 상한 초과 시 실제로 정리된다."""
    from app.stock_paper import service as stock_service

    store = _seeded_store(tmp_path, close_price=110.0, opened_days_ago=15)
    broker = PaperBroker(store)
    monkeypatch.setattr(
        stock_service,
        "_observation",
        lambda *args, **kwargs: _observation(minute_close=110.0, minute_low=109.0, minute_high=111.0),
    )

    events: list[dict] = []
    counters = stock_service._process_exits(broker, store, None, {"US": {"market_state": "open"}}, events)

    assert counters["filled"] == 1
    assert store.position_quantity(Market.US, "AAPL") == 0
    assert [event for event in events if event["detail"].get("trigger") == "time_stop"]
