from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.backtest.signatures import signatures_from_analysis
from app.db.models import Direction, MarketCandle, WatchlistItem
from app.db.repository import MemoryRepository, SQLiteRepository
from app.paper.policy import PaperPolicy, apply_exit_decision, evaluate_entry, evaluate_exit, open_trade
from app.paper.service import paper_scoreboard, run_paper_engine


BASE_TIME = datetime(2026, 7, 12, 0, 0, tzinfo=timezone.utc)
TRADE_ID = UUID("00000000-0000-0000-0000-000000000063")


def candle(offset: int, *, close: float, high: float | None = None, low: float | None = None) -> MarketCandle:
    return MarketCandle(
        timestamp=BASE_TIME + timedelta(hours=4 * offset),
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=1_000,
    )


def test_entry_requires_every_gate() -> None:
    policy = PaperPolicy()
    accepted = evaluate_entry(
        stance_state={"stance": "long_leaning", "flipped": True, "transitioning": False},
        direction=Direction.long,
        evidence_count=4,
        checklist_passed=5,
        checklist_total=6,
        rr_ratio=1.5,
        survives_to_invalidation=True,
        validated_signature=True,
        signature_ci_low_pct=50,
        earnings_clear=True,
        data_fresh=True,
        confirmed_bar=True,
        policy=policy,
    )
    assert accepted.enter is True

    rejected = evaluate_entry(
        stance_state={"stance": "long_leaning", "flipped": True, "transitioning": False},
        direction=Direction.long,
        evidence_count=3,
        checklist_passed=5,
        checklist_total=6,
        rr_ratio=1.5,
        survives_to_invalidation=True,
        validated_signature=True,
        signature_ci_low_pct=50,
        earnings_clear=True,
        data_fresh=True,
        confirmed_bar=True,
        policy=policy,
    )
    assert rejected.enter is False
    assert rejected.rejection_reasons == ("evidence",)


def test_deterministic_partial_take_profit_then_pressure_exit_with_costs() -> None:
    policy = PaperPolicy(taker_fee_pct=0.06, slippage_pct=0.03)
    trade = open_trade(
        trade_id=TRADE_ID,
        symbol="TESTUSDT",
        timeframe="4h",
        asset_class="crypto",
        direction=Direction.long,
        bar=candle(0, close=100),
        invalidation_price=95,
        take_profit_price=110,
        evidence={"items": [{"claim": "confirmed"}]},
        checklist={"passed": 6, "total": 6},
        stance_snapshot={"stance": "long_leaning"},
        signature_snapshot={"signature_key": "liquidity:sweep_low:strong:long:crypto:4h"},
        policy=policy,
    )
    assert trade.entry_price == 100
    assert trade.quantity == pytest.approx(3.0)
    assert trade.costs_usdt == pytest.approx(0.27)

    tp_bar = candle(1, close=108, high=111, low=104)
    decision = evaluate_exit(
        trade,
        bar=tp_bar,
        stance_state={"stance": "long_leaning", "flipped": False},
        take_profit_pressure="low",
        prior_high_pressure_streak=0,
        policy=policy,
    )
    assert (decision.action, decision.reason) == ("partial", "take_profit_1")
    trade = apply_exit_decision(trade, decision=decision, bar=tp_bar, policy=policy)
    assert trade.remaining_quantity == pytest.approx(1.5)
    assert trade.stop_price == 100

    first_high = candle(2, close=107.5, high=109, low=103)
    first = evaluate_exit(
        trade,
        bar=first_high,
        stance_state={"stance": "long_leaning", "flipped": False},
        take_profit_pressure="high",
        prior_high_pressure_streak=0,
        policy=policy,
    )
    assert first.action == "hold"
    trade = apply_exit_decision(trade, decision=first, bar=first_high, policy=policy)

    second_high = candle(3, close=107, high=108, low=102)
    second = evaluate_exit(
        trade,
        bar=second_high,
        stance_state={"stance": "long_leaning", "flipped": False},
        take_profit_pressure="high",
        prior_high_pressure_streak=first.high_pressure_streak,
        policy=policy,
    )
    assert (second.action, second.reason) == ("close", "take_profit_pressure")
    trade = apply_exit_decision(trade, decision=second, bar=second_high, policy=policy)
    assert trade.status == "closed"
    assert trade.exit_price == 107
    assert trade.gross_pnl_usdt == pytest.approx(22.5)
    assert trade.costs_usdt == pytest.approx(0.27 + 108 * 1.5 * 0.0009 + 107 * 1.5 * 0.0009)
    assert trade.net_pnl_usdt == pytest.approx(trade.gross_pnl_usdt - trade.costs_usdt)


def test_paper_repository_migration_and_reopen(tmp_path) -> None:
    path = tmp_path / "paper.db"
    repo = SQLiteRepository(path)
    trade = open_trade(
        trade_id=TRADE_ID,
        symbol="BTCUSDT",
        timeframe="4h",
        asset_class="crypto",
        direction=Direction.short,
        bar=candle(0, close=100),
        invalidation_price=105,
        take_profit_price=90,
        evidence={},
        checklist={},
        stance_snapshot={},
        signature_snapshot={},
        policy=PaperPolicy(),
    )
    repo.upsert_paper_trade(trade)
    assert repo.upsert_paper_engine_state("BTCUSDT", "4h", {"last_bar_at": BASE_TIME.isoformat()}) is True
    assert repo.upsert_paper_engine_state("BTCUSDT", "4h", {"last_bar_at": BASE_TIME.isoformat()}) is False

    reopened = SQLiteRepository(path)
    assert reopened.get_paper_trade(TRADE_ID).symbol == "BTCUSDT"
    assert reopened.get_paper_engine_state("BTCUSDT", "4h")["last_bar_at"] == BASE_TIME.isoformat()


def test_worker_engine_opens_once_per_confirmed_bar_and_records_ledger() -> None:
    repo = MemoryRepository()
    repo.upsert_watchlist_item(WatchlistItem(symbol="TESTUSDT", asset_class="crypto"))
    analysis = {
        "symbol": "TESTUSDT",
        "timeframe": "4h",
        "asset_class": "crypto",
        "mark_price": 100,
        "candles": [
            {"time": int(BASE_TIME.timestamp()), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000}
        ],
        "liquidity": {
            "sweeps": [
                {
                    "id": "sweep-1",
                    "confirmed": True,
                    "side": "sell_side",
                    "type": "sweep",
                    "grade": "Strong",
                    "confidence": 80,
                    "timestamp": int(BASE_TIME.timestamp()),
                }
            ]
        },
    }
    signature = signatures_from_analysis(analysis)[0]
    historical = {
        "stats": [
            {
                "signature_key": signature["key"],
                "signature": signature,
                "sample_size": 40,
                "win_1r_ci": [55.0, 70.0],
            }
        ]
    }
    payload = {
        "analysis": analysis,
        "historical_backtest": historical,
        "analyst_briefing": {
            "confluence": {
                "stance": "long_leaning",
                "stance_state": {"stance": "long_leaning", "flipped": True, "transitioning": False},
                "long_evidence": [{"claim": str(index)} for index in range(4)],
                "short_evidence": [],
            }
        },
        "gauges": {"bar_state": {"provisional": False}},
    }
    simulation = {
        "rr_ratio": 2.0,
        "survives_to_invalidation": True,
        "checklist": [{"status": "pass"}] * 6,
        "checklist_passed": 6,
        "checklist_total": 6,
        "action_plan": {
            "invalidation": {"price": 95},
            "take_profit": [{"price": 110}],
        },
    }
    settings = _settings()

    def load(_symbol, _timeframe):
        return payload

    def simulate(_symbol, _timeframe, _direction, _entry):
        return simulation

    first = run_paper_engine(repo, settings, analysis_loader=load, simulation_loader=simulate, now=BASE_TIME)
    second = run_paper_engine(repo, settings, analysis_loader=load, simulation_loader=simulate, now=BASE_TIME)

    assert first["opened"] == 1
    assert first["events"][0]["kind"] == "opened"
    assert second["opened"] == 0
    assert second["events"] == []
    assert second["skipped_same_bar"] == 1
    trade = repo.list_paper_trades(status="open")[0]
    assert trade.entry_evidence["items"]
    assert repo.list_judgments(trade.id)[0].type == "paper_trade_entry"


def test_scoreboard_compares_ratios_and_never_enables_live_orders() -> None:
    repo = MemoryRepository()
    policy = PaperPolicy()
    trade = open_trade(
        trade_id=TRADE_ID,
        symbol="TESTUSDT",
        timeframe="4h",
        asset_class="crypto",
        direction=Direction.long,
        bar=candle(0, close=100),
        invalidation_price=95,
        take_profit_price=110,
        evidence={},
        checklist={},
        stance_snapshot={},
        signature_snapshot={},
        policy=policy,
    )
    closed = apply_exit_decision(
        trade,
        decision=evaluate_exit(
            trade,
            bar=candle(1, close=110, high=110, low=109),
            stance_state={"stance": "short_leaning", "flipped": True, "transitioning": False},
            take_profit_pressure="low",
            prior_high_pressure_streak=0,
            policy=policy,
        ),
        bar=candle(1, close=110, high=110, low=109),
        policy=policy,
    )
    # TP is partial by policy; close the remainder on the following opposite flip.
    closed = apply_exit_decision(
        closed,
        decision=evaluate_exit(
            closed,
            bar=candle(2, close=109),
            stance_state={"stance": "short_leaning", "flipped": True, "transitioning": False},
            take_profit_pressure="low",
            prior_high_pressure_streak=0,
            policy=policy,
        ),
        bar=candle(2, close=109),
        policy=policy,
    )
    repo.upsert_paper_trade(closed)
    result = paper_scoreboard(repo, _settings(), now=BASE_TIME + timedelta(days=1))
    assert result["engine"]["trade_count"] == 1
    assert result["engine"]["net_return_pct"] > 0
    assert result["equity_curve"]["engine"][-1]["return_pct"] == result["engine"]["net_return_pct"]
    assert result["live_orders_enabled"] is False
    assert "조건 상이" in result["fairness_note"]


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        paper_engine_enabled=True,
        paper_margin_usdt=100.0,
        paper_leverage=3.0,
        paper_max_open_positions=5,
        paper_min_evidence=4,
        paper_min_checklist_passed=5,
        paper_min_rr=1.5,
        paper_max_holding_bars=30,
        paper_poor_mdd_pct=10.0,
        backtest_taker_fee_pct=0.06,
        backtest_slippage_crypto_pct=0.03,
        backtest_slippage_stock_pct=0.08,
        backtest_slippage_index_pct=0.05,
        universe_backtest_min_ci_low_pct=50.0,
        signature_validated_min_sample=30,
    )
