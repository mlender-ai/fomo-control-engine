"""ENG-01 — 강제청산 모델 (Bitget 격리 공식 · 체결 · 갭 · 손절 순서 · invariant)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.db.models import Direction, MarketCandle, PaperTrade
from app.paper import liquidation
from app.paper.liquidation import PositionLossInvariantViolation, liquidation_price, trade_liquidation
from app.paper.policy import PaperPolicy, apply_exit_decision, evaluate_exit, ExitDecision

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _trade(direction: Direction = Direction.long, stop: float = 0.9) -> PaperTrade:
    return PaperTrade(
        id=uuid4(),
        symbol="ADAUSDT",
        timeframe="4h",
        direction=direction,
        entry_bar_at=T0,
        entry_at=T0,
        entry_price=1.0,
        margin_usdt=100.0,
        leverage=3.0,
        quantity=300.0,
        remaining_quantity=300.0,
        invalidation_price=stop,
        take_profit_price=1.2 if direction == Direction.long else 0.8,
        stop_price=stop,
        costs_usdt=0.27,
        net_pnl_usdt=-0.27,
    )


def _bar(o: float, h: float, l: float, c: float, i: int = 1) -> MarketCandle:
    return MarketCandle(symbol="ADAUSDT", timeframe="4h", timestamp=T0 + timedelta(hours=4 * i), open=o, high=h, low=l, close=c, volume=1.0)


def _policy() -> PaperPolicy:
    return PaperPolicy(stop_fill_mode="intrabar")


def test_bitget_isolated_formula():
    # 3배 롱 · MMR 0.66% · 테이커 0.06% → 1 × (1 − 1/3) ÷ (1 − 0.0072)
    assert liquidation_price(direction="long", entry=1.0, quantity=300, margin=100, mmr=0.0066) == pytest.approx(0.67150, abs=1e-4)
    assert liquidation_price(direction="short", entry=1.0, quantity=300, margin=100, mmr=0.0066) == pytest.approx(1.32380, abs=1e-4)


def test_funding_moves_liquidation_toward_entry():
    trade = _trade()
    before = trade_liquidation(trade, mmr=0.0066, funding_paid=0.0)
    after = trade_liquidation(trade, mmr=0.0066, funding_paid=6.0)
    assert after > before


def test_touch_liquidates_when_stop_is_outside():
    trade = _trade(stop=0.5)  # 손절이 청산가 바깥 — 청산이 먼저
    lp = trade_liquidation(trade, mmr=0.0066, funding_paid=0.0)
    decision = evaluate_exit(trade, bar=_bar(0.95, 0.96, 0.66, 0.7), stance_state={}, take_profit_pressure=None, prior_high_pressure_streak=0, policy=_policy(), liquidation_price=lp)
    assert decision.reason == "liquidation"
    assert decision.execution_price == pytest.approx(lp)
    closed = apply_exit_decision(trade, decision=decision, bar=_bar(0.95, 0.96, 0.66, 0.7), policy=_policy())
    assert closed.exit_reason == "liquidation"
    assert closed.gross_pnl_usdt == pytest.approx(-100.0)  # 격리 증거금 전부
    assert closed.net_return_pct == pytest.approx(-100.27, abs=0.01)


def test_gap_liquidates_at_open():
    trade = _trade(stop=0.9)
    lp = trade_liquidation(trade, mmr=0.0066, funding_paid=0.0)
    decision = evaluate_exit(trade, bar=_bar(0.6, 0.62, 0.55, 0.6), stance_state={}, take_profit_pressure=None, prior_high_pressure_streak=0, policy=_policy(), liquidation_price=lp)
    assert decision.reason == "liquidation"
    assert decision.execution_price == 0.6


def test_stop_first_when_closer_to_entry():
    trade = _trade(stop=0.9)
    lp = trade_liquidation(trade, mmr=0.0066, funding_paid=0.0)
    decision = evaluate_exit(trade, bar=_bar(0.95, 0.96, 0.66, 0.7), stance_state={}, take_profit_pressure=None, prior_high_pressure_streak=0, policy=_policy(), liquidation_price=lp)
    assert decision.reason == "invalidation_breach"
    assert decision.execution_price == 0.9


def test_no_liquidation_price_means_old_behaviour():
    trade = _trade(stop=0.9)
    decision = evaluate_exit(trade, bar=_bar(0.95, 0.96, 0.66, 0.7), stance_state={}, take_profit_pressure=None, prior_high_pressure_streak=0, policy=_policy())
    assert decision.reason == "invalidation_breach"


def test_invariant_position_loss_cannot_exceed_margin():
    trade = _trade(stop=0.5)
    # 청산 모델을 건너뛴 손절이 −50% 가격(−150% 증거금)에 체결되면 — 그 전에 청산됐어야 한다
    decision = ExitDecision("close", "invalidation_breach", 0, 0.5)
    with pytest.raises(PositionLossInvariantViolation):
        apply_exit_decision(trade, decision=decision, bar=_bar(0.95, 0.96, 0.5, 0.5), policy=_policy())


def test_track_halt_roundtrip():
    class Repo:
        def __init__(self):
            self.states = {}

        def get_paper_engine_state(self, symbol, timeframe):
            return self.states.get((symbol, timeframe))

        def upsert_paper_engine_state(self, symbol, timeframe, state):
            self.states[(symbol, timeframe)] = state
            return True

    repo = Repo()
    assert liquidation.track_halt(repo, "crypto") is None
    liquidation.halt_track(repo, "crypto", reason="position_loss_exceeds_margin", detail="x", at=T0)
    assert liquidation.track_halt(repo, "crypto")["reason"] == "position_loss_exceeds_margin"
