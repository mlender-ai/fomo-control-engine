from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.backtest.signatures import signatures_from_analysis
from app.db.models import AutonomyLog, BacktestStat, Direction, EntryIntent, JudgmentLedgerEntry, MarketCandle, WatchlistItem
from app.db.repository import MemoryRepository, SQLiteRepository
from app.paper.policy import PaperPolicy, apply_exit_decision, evaluate_entry, evaluate_exit, open_trade
from app.paper.service import (
    ENTRY_GATE_VERSION,
    _candidate_bootstrap_active,
    _candidate_bootstrap_relaxed_active,
    _gate_diagnostic_event,
    _paper_target_plan,
    _repair_pre_tp_pressure_exits,
    _signature_gate_evaluation,
    audit_atr_target_reachability,
    paper_benchmark,
    paper_gate_funnel,
    paper_exit_monitor,
    paper_scoreboard,
    paper_universe,
    start_paper_benchmark,
    run_paper_engine,
)


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

    accepted_with_one_na_item = evaluate_entry(
        stance_state={"stance": "long_leaning", "flipped": True, "transitioning": False},
        direction=Direction.long,
        evidence_count=4,
        checklist_passed=5,
        checklist_total=5,
        rr_ratio=1.5,
        survives_to_invalidation=True,
        validated_signature=True,
        signature_ci_low_pct=50,
        earnings_clear=True,
        data_fresh=True,
        confirmed_bar=True,
        policy=policy,
    )
    assert accepted_with_one_na_item.enter is True

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

    too_close = evaluate_entry(
        stance_state={"stance": "long_leaning", "flipped": True, "transitioning": False},
        direction=Direction.long,
        evidence_count=4,
        checklist_passed=5,
        checklist_total=6,
        rr_ratio=None,
        invalidation_hygiene=False,
        survives_to_invalidation=True,
        validated_signature=True,
        signature_ci_low_pct=50,
        earnings_clear=True,
        data_fresh=True,
        confirmed_bar=True,
        policy=policy,
    )
    assert too_close.enter is False
    assert "invalidation_hygiene" in too_close.rejection_reasons


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
    assert trade.gross_pnl_usdt == pytest.approx(25.5)
    assert trade.costs_usdt == pytest.approx(0.27 + 110 * 1.5 * 0.0009 + 107 * 1.5 * 0.0009)
    assert trade.net_pnl_usdt == pytest.approx(trade.gross_pnl_usdt - trade.costs_usdt)


def test_take_profit_pressure_cannot_close_before_first_profit() -> None:
    policy = PaperPolicy(take_profit_pressure_bars=2)
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

    first = evaluate_exit(
        trade,
        bar=candle(1, close=98),
        stance_state={"stance": "long_leaning", "flipped": False},
        take_profit_pressure="high",
        prior_high_pressure_streak=0,
        policy=policy,
    )
    second = evaluate_exit(
        trade,
        bar=candle(2, close=97),
        stance_state={"stance": "long_leaning", "flipped": False},
        take_profit_pressure="high",
        prior_high_pressure_streak=first.high_pressure_streak,
        policy=policy,
    )

    assert (first.action, first.high_pressure_streak) == ("hold", 0)
    assert (second.action, second.reason, second.high_pressure_streak) == ("hold", "none", 0)


def test_exit_timestamp_never_precedes_processing_time_entry() -> None:
    policy = PaperPolicy()
    trade = open_trade(
        trade_id=TRADE_ID,
        symbol="TESTUSDT",
        timeframe="4h",
        asset_class="crypto",
        direction=Direction.long,
        bar=candle(0, close=100),
        invalidation_price=99,
        take_profit_price=105,
        evidence={},
        checklist={},
        stance_snapshot={},
        signature_snapshot={},
        policy=policy,
    ).model_copy(update={"entry_at": BASE_TIME + timedelta(hours=2)})
    bar = candle(0, close=98)

    decision = evaluate_exit(
        trade,
        bar=bar,
        stance_state={"stance": "long_leaning", "flipped": False, "transitioning": False},
        take_profit_pressure="low",
        prior_high_pressure_streak=0,
        policy=policy,
    )
    closed = apply_exit_decision(trade, decision=decision, bar=bar, policy=policy)

    assert closed.exit_at == trade.entry_at
    assert closed.updated_at == trade.entry_at


def test_legacy_pre_tp_pressure_exit_is_audited_and_excluded_from_benchmark() -> None:
    repo = MemoryRepository()
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
        signature_snapshot={
            "gate_mode": "candidate_bootstrap",
            "qualified": {"signature_key": "harmonic:prz_touch:test"},
        },
        policy=PaperPolicy(),
    ).model_copy(
        update={
            "status": "closed",
            "remaining_quantity": 0.0,
            "exit_bar_at": candle(2, close=98).timestamp,
            "exit_at": candle(2, close=98).timestamp,
            "exit_price": 98.0,
            "exit_reason": "take_profit_pressure",
            "net_pnl_usdt": -6.0,
            "net_return_pct": -6.0,
            "loss_tags": ["validated_signature_failed:unknown", "exit:take_profit_pressure"],
        }
    )
    repo.upsert_paper_trade(trade)

    assert _repair_pre_tp_pressure_exits(repo) == 1
    repaired = repo.get_paper_trade(TRADE_ID)
    assert repaired is not None
    assert repaired.loss_tags == ["policy_invalid:pre_tp_pressure_exit", "exit:take_profit_pressure"]

    scoreboard = paper_scoreboard(repo, _settings(), now=BASE_TIME + timedelta(days=1))
    assert scoreboard["engine"]["trade_count"] == 0
    assert scoreboard["engine"]["audited_trade_count"] == 1
    assert scoreboard["engine"]["policy_invalid_count"] == 1
    assert scoreboard["engine"]["net_return_pct"] == 0
    assert scoreboard["equity_curve"]["engine"] == []


def test_paper_exit_monitor_reports_live_mark_to_market_and_target_distances() -> None:
    trade = open_trade(
        trade_id=TRADE_ID,
        symbol="TESTUSDT",
        timeframe="4h",
        asset_class="crypto",
        direction=Direction.long,
        bar=candle(0, close=100),
        invalidation_price=95,
        take_profit_price=104,
        take_profit_2_price=108,
        evidence={},
        checklist={},
        stance_snapshot={},
        signature_snapshot={},
        policy=PaperPolicy(),
    )

    monitor = paper_exit_monitor(trade, 102)

    assert monitor is not None
    assert monitor["mark_price"] == 102
    assert monitor["unrealized_pnl_usdt"] == pytest.approx(6.0)
    assert monitor["mark_net_pnl_usdt"] == pytest.approx(5.73)
    assert monitor["mark_net_return_pct"] == pytest.approx(5.73)
    assert monitor["invalidation_distance_pct"] < 0
    assert monitor["take_profit_distance_pct"] > 0
    assert monitor["take_profit_2_distance_pct"] > monitor["take_profit_distance_pct"]


def test_atr_targets_use_nearer_structural_level_and_staged_execution_rr() -> None:
    rows = [
        {
            "time": int(candle(index, close=100, high=102, low=98).timestamp.timestamp()),
            "open": 100,
            "high": 102,
            "low": 98,
            "close": 100,
            "volume": 1_000,
        }
        for index in range(16)
    ]
    plan = _paper_target_plan(
        {"candles": rows},
        {"bar_state": {"provisional": False}},
        bar=candle(15, close=100, high=102, low=98),
        direction=Direction.long,
        invalidation_price=99,
        action_plan={"take_profit": [{"price": 106}]},
        policy=PaperPolicy(take_profit_atr_k1=1.0, take_profit_atr_k2=2.0),
    )

    assert plan["atr"] == pytest.approx(4.0)
    assert plan["take_profit_1"] == pytest.approx(104.0)
    assert plan["take_profit_2"] == pytest.approx(106.0)
    assert plan["take_profit_2_source"] == "action_plan_nearer"
    assert plan["execution_invalidation"] == pytest.approx(99.0)
    assert plan["staged_reward_distance"] == pytest.approx(5.0)
    assert plan["rr_ratio"] == pytest.approx(5.0)
    assert plan["rr_eligible"] is True


def test_far_structural_invalidation_is_capped_at_one_atr_for_paper_execution() -> None:
    rows = [
        {
            "time": int(candle(index, close=100, high=102, low=98).timestamp.timestamp()),
            "open": 100,
            "high": 102,
            "low": 98,
            "close": 100,
            "volume": 1_000,
        }
        for index in range(16)
    ]
    plan = _paper_target_plan(
        {"candles": rows},
        {"bar_state": {"provisional": False}},
        bar=candle(15, close=100, high=102, low=98),
        direction=Direction.long,
        invalidation_price=80,
        action_plan={"take_profit": [{"price": 120}]},
        policy=PaperPolicy(take_profit_atr_k1=1.0, take_profit_atr_k2=2.0),
    )

    assert plan["thesis_invalidation"] == 80
    assert plan["execution_invalidation"] == pytest.approx(96.0)
    assert plan["execution_invalidation_source"] == "atr_risk_cap"
    assert plan["rr_ratio"] == pytest.approx(1.5)
    assert plan["rr_eligible"] is True


def test_tp2_closes_remainder_at_target_price() -> None:
    policy = PaperPolicy()
    trade = open_trade(
        trade_id=TRADE_ID,
        symbol="TESTUSDT",
        timeframe="4h",
        asset_class="crypto",
        direction=Direction.long,
        bar=candle(0, close=100),
        invalidation_price=99,
        take_profit_price=104,
        take_profit_2_price=106,
        evidence={},
        checklist={},
        stance_snapshot={"stance": "long_leaning"},
        signature_snapshot={},
        policy=policy,
    )
    first_bar = candle(1, close=103, high=105, low=101)
    trade = apply_exit_decision(
        trade,
        decision=evaluate_exit(
            trade,
            bar=first_bar,
            stance_state={"stance": "long_leaning", "flipped": False},
            take_profit_pressure="low",
            prior_high_pressure_streak=0,
            policy=policy,
        ),
        bar=first_bar,
        policy=policy,
    )
    second_bar = candle(2, close=105, high=107, low=103)
    decision = evaluate_exit(
        trade,
        bar=second_bar,
        stance_state={"stance": "long_leaning", "flipped": False},
        take_profit_pressure="low",
        prior_high_pressure_streak=0,
        policy=policy,
    )
    closed = apply_exit_decision(trade, decision=decision, bar=second_bar, policy=policy)

    assert (decision.action, decision.reason, decision.execution_price) == ("close", "take_profit_2", 106)
    assert closed.status == "closed"
    assert closed.partial_exit_price == 104
    assert closed.exit_price == 106


def test_holding_limit_extends_while_confirmed_stance_remains_valid() -> None:
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
        policy=PaperPolicy(),
    ).model_copy(update={"holding_bars": 29})

    decision = evaluate_exit(
        trade,
        bar=candle(1, close=101),
        stance_state={"stance": "long_leaning", "flipped": False, "transitioning": False},
        take_profit_pressure="low",
        prior_high_pressure_streak=0,
        policy=PaperPolicy(),
    )

    assert (decision.action, decision.reason) == ("hold", "none")


def test_atr_target_reachability_audit_reports_timeout_rate() -> None:
    candles = [candle(index, close=100 + (index % 8), high=102 + (index % 8), low=98 + (index % 8)) for index in range(90)]

    audit = audit_atr_target_reachability(candles, atr_multiplier=1.0, max_holding_bars=30)

    assert audit["samples"] > 0
    assert audit["reach_rate_pct"] is not None
    assert audit["time_decay_rate_pct"] is not None
    assert audit["reached"] + sum(audit["misses_by_direction"].values()) == audit["samples"]


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
    record = {
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "bar_at": BASE_TIME.isoformat(),
        "gates": {"confirmed_flip": False},
        "entered": False,
        "rejected_at": "confirmed_flip",
    }
    assert repo.upsert_paper_gate_funnel(record) is True
    assert repo.upsert_paper_gate_funnel(record) is False
    assert SQLiteRepository(path).list_paper_gate_funnel(symbol="BTCUSDT")[0]["bar_at"] == BASE_TIME.isoformat()


def test_worker_engine_opens_once_per_confirmed_bar_and_records_ledger() -> None:
    repo = MemoryRepository()
    repo.upsert_watchlist_item(WatchlistItem(symbol="TESTUSDT", asset_class="crypto"))
    analysis = {
        "symbol": "TESTUSDT",
        "timeframe": "4h",
        "asset_class": "crypto",
        "mark_price": 100,
        "candles": [{"time": int(BASE_TIME.timestamp()), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000}],
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
            "invalidation": {"price": 99},
            "take_profit": [{"price": 110}],
        },
    }
    settings = _settings()

    def load(_symbol, _timeframe):
        return payload

    def simulate(_symbol, _timeframe, _direction, _entry):
        return simulation

    # WO-63 may already have advanced the legacy engine state before the
    # dedicated funnel table is deployed. The current confirmed bar must be
    # evaluated once to bootstrap observability, then remain idempotent.
    repo.upsert_paper_engine_state("TESTUSDT", "4h", {"last_bar_at": BASE_TIME.isoformat()})
    first = run_paper_engine(repo, settings, analysis_loader=load, simulation_loader=simulate, now=BASE_TIME)
    second = run_paper_engine(repo, settings, analysis_loader=load, simulation_loader=simulate, now=BASE_TIME)

    assert first["opened"] == 1
    assert first["events"][0]["kind"] == "opened"
    assert second["opened"] == 0
    assert second["events"] == []
    assert second["skipped_same_bar"] == 1
    funnel_rows = repo.list_paper_gate_funnel(symbol="TESTUSDT")
    assert len(funnel_rows) == 1
    assert funnel_rows[0]["entered"] is True
    trade = repo.list_paper_trades(status="open")[0]
    assert trade.entry_evidence["items"]
    assert repo.list_judgments(trade.id)[0].type == "paper_trade_entry"


def test_candidate_bootstrap_requires_scored_active_candidate_and_tags_trade() -> None:
    repo = MemoryRepository()
    repo.upsert_watchlist_item(WatchlistItem(symbol="TESTUSDT", asset_class="crypto"))
    key = "fvg:gap_formed:candidate:long:crypto:4h"
    candidate_stat = BacktestStat(
        signature_key=key,
        symbol="TESTUSDT",
        timeframe="4h",
        asset_class="crypto",
        engine="fvg",
        event_type="gap_formed",
        strength_class="candidate",
        direction="long",
        sample_size=14,
        win_1r_pct=50.0,
        payload={"signature": {"engine": "fvg", "strength_class": "candidate"}},
    )
    repo.upsert_backtest_stat(candidate_stat)
    repo.add_judgment(
        JudgmentLedgerEntry(
            judgment_id="candidate:TESTUSDT:4h:fvg:active",
            position_id=UUID(int=0),
            source_type="candidate_signature",
            source_id="fvg:active",
            as_of=BASE_TIME,
            type="candidate_signature",
            claim={
                "symbol": "TESTUSDT",
                "timeframe": "4h",
                "engine": "fvg",
                "event_type": "gap_formed",
                "direction": "long",
            },
        )
    )
    payload = {
        "analysis": {
            "symbol": "TESTUSDT",
            "timeframe": "4h",
            "asset_class": "crypto",
            "candles": [{"time": int(BASE_TIME.timestamp()), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000}],
        },
        "historical_backtest": {"stats": [], "event_stats": []},
        "analyst_briefing": {
            "confluence": {
                "stance": "long_leaning",
                "stance_state": {"stance": "long_leaning", "flipped": True, "transitioning": False, "last_bar_at": BASE_TIME.isoformat()},
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
        "action_plan": {"invalidation": {"price": 99}, "take_profit": [{"price": 110}]},
    }

    below_floor = _signature_gate_evaluation(repo, _settings(), payload["analysis"], payload, Direction.long)
    assert below_floor["signature_gate"] is False
    repo.upsert_backtest_stat(candidate_stat.model_copy(update={"sample_size": 15}))

    result = run_paper_engine(
        repo,
        _settings(),
        analysis_loader=lambda _symbol, _timeframe: payload,
        simulation_loader=lambda _symbol, _timeframe, _direction, _entry: simulation,
        now=BASE_TIME,
    )

    assert result["opened"] == 1
    trade = repo.list_paper_trades(status="open")[0]
    assert trade.entry_evidence["signature_gate_mode"] == "candidate_bootstrap"
    assert trade.entry_evidence["candidate_bootstrap"] is True
    assert trade.signature_snapshot["bootstrap_thresholds"] == {"min_sample_size": 15, "min_win_1r_pct": 50.0}
    funnel = repo.list_paper_gate_funnel(symbol="TESTUSDT")[0]
    assert funnel["signature_gate_mode"] == "candidate_bootstrap"


def test_candidate_bootstrap_auto_disables_after_three_validated_promotions() -> None:
    repo = MemoryRepository()
    for index in range(3):
        repo.add_autonomy_log(
            AutonomyLog(
                signature_key=f"candidate:{index}",
                previous_state="candidate",
                new_state="validated",
                transition="validate",
                reason="approved",
                autonomous=False,
                evidence={"sample_size": 30},
            )
        )

    assert _candidate_bootstrap_active(repo, _settings(), now=BASE_TIME) is False


def test_candidate_bootstrap_relaxes_only_for_first_two_weeks_and_tags_trade() -> None:
    repo = MemoryRepository()
    repo.upsert_watchlist_item(WatchlistItem(symbol="TESTUSDT", asset_class="crypto"))
    start_paper_benchmark(repo, now=BASE_TIME)
    key = "fvg:gap_formed:candidate:long:crypto:4h"
    repo.upsert_backtest_stat(
        BacktestStat(
            signature_key=key,
            symbol="TESTUSDT",
            timeframe="4h",
            asset_class="crypto",
            engine="fvg",
            event_type="gap_formed",
            strength_class="candidate",
            direction="long",
            sample_size=8,
            win_1r_pct=45.0,
            payload={"signature": {"engine": "fvg", "strength_class": "candidate"}},
        )
    )
    repo.add_judgment(
        JudgmentLedgerEntry(
            judgment_id="candidate:TESTUSDT:4h:fvg:relaxed",
            position_id=UUID(int=0),
            source_type="candidate_signature",
            source_id="fvg:relaxed",
            as_of=BASE_TIME,
            type="candidate_signature",
            claim={"symbol": "TESTUSDT", "timeframe": "4h", "engine": "fvg", "event_type": "gap_formed", "direction": "long"},
        )
    )
    payload = _candidate_entry_payload()
    simulation = _candidate_entry_simulation()
    now = BASE_TIME + timedelta(hours=1)

    assert _candidate_bootstrap_relaxed_active(repo, _settings(), now=now) is True
    gate = _signature_gate_evaluation(repo, _settings(), payload["analysis"], payload, Direction.long, now=now)
    assert gate["gate_mode"] == "candidate_bootstrap_relaxed"

    result = run_paper_engine(
        repo,
        _settings(),
        analysis_loader=lambda _symbol, _timeframe: payload,
        simulation_loader=lambda _symbol, _timeframe, _direction, _entry: simulation,
        now=now,
    )

    assert result["opened"] == 1
    trade = repo.list_paper_trades(status="open")[0]
    assert trade.entry_evidence["bootstrap_relaxed"] is True
    assert trade.signature_snapshot["bootstrap_thresholds"] == {"min_sample_size": 8, "min_win_1r_pct": 45.0}
    assert repo.list_paper_gate_funnel(symbol="TESTUSDT")[0]["bootstrap_relaxed"] is True
    assert _candidate_bootstrap_relaxed_active(repo, _settings(), now=BASE_TIME + timedelta(days=14)) is False


def test_candidate_bootstrap_pools_exact_signature_across_symbol_stats() -> None:
    repo = MemoryRepository()
    start_paper_benchmark(repo, now=BASE_TIME)
    key = "fvg:gap_formed:candidate:long:crypto:4h"
    for symbol in ("AAAUSDT", "BBBUSDT"):
        repo.upsert_backtest_stat(
            BacktestStat(
                signature_key=key,
                symbol=symbol,
                timeframe="4h",
                asset_class="crypto",
                engine="fvg",
                event_type="gap_formed",
                strength_class="candidate",
                direction="long",
                sample_size=4,
                win_1r_pct=50.0,
            )
        )
    repo.upsert_backtest_stat(
        BacktestStat(
            signature_key=key,
            symbol="ALL",
            timeframe="4h",
            asset_class="crypto",
            scope="all",
            engine="fvg",
            event_type="candidate_review",
            strength_class="candidate",
            direction="long",
            sample_size=100,
            win_1r_pct=100.0,
            payload={"candidate_review": True},
        )
    )
    repo.add_judgment(
        JudgmentLedgerEntry(
            judgment_id="candidate:TESTUSDT:4h:fvg:pooled",
            position_id=UUID(int=0),
            source_type="candidate_signature",
            source_id="fvg:pooled",
            as_of=BASE_TIME,
            type="candidate_signature",
            claim={"symbol": "TESTUSDT", "timeframe": "4h", "engine": "fvg", "event_type": "gap_formed", "direction": "long"},
        )
    )

    gate = _signature_gate_evaluation(
        repo,
        _settings(),
        _candidate_entry_payload()["analysis"],
        _candidate_entry_payload(),
        Direction.long,
        now=BASE_TIME + timedelta(hours=1),
    )

    assert gate["gate_mode"] == "candidate_bootstrap_relaxed"
    assert gate["qualified"]["stat"]["sample_size"] == 8
    assert gate["qualified"]["stat"]["win_1r_pct"] == 50.0
    assert gate["qualified"]["stat"]["source_stats_count"] == 2
    assert gate["qualified"]["stat"]["source_symbols"] == ["AAAUSDT", "BBBUSDT"]


def test_signature_gate_upgrade_reevaluates_rejected_bar_once() -> None:
    repo = MemoryRepository()
    repo.upsert_watchlist_item(WatchlistItem(symbol="TESTUSDT", asset_class="crypto"))
    start_paper_benchmark(repo, now=BASE_TIME)
    key = "fvg:gap_formed:candidate:long:crypto:4h"
    repo.upsert_backtest_stat(
        BacktestStat(
            signature_key=key,
            symbol="TESTUSDT",
            timeframe="4h",
            asset_class="crypto",
            engine="fvg",
            event_type="gap_formed",
            strength_class="candidate",
            direction="long",
            sample_size=8,
            win_1r_pct=50.0,
        )
    )
    repo.add_judgment(
        JudgmentLedgerEntry(
            judgment_id="candidate:TESTUSDT:4h:fvg:upgrade",
            position_id=UUID(int=0),
            source_type="candidate_signature",
            source_id="fvg:upgrade",
            as_of=BASE_TIME,
            type="candidate_signature",
            claim={"symbol": "TESTUSDT", "timeframe": "4h", "engine": "fvg", "event_type": "gap_formed", "direction": "long"},
        )
    )
    repo.upsert_paper_engine_state("TESTUSDT", "4h", {"last_bar_at": BASE_TIME.isoformat()})
    repo.upsert_paper_gate_funnel(
        {
            "symbol": "TESTUSDT",
            "timeframe": "4h",
            "bar_at": BASE_TIME.isoformat(),
            "rejected_at": "signature_gate",
            "entered": False,
        }
    )
    now = BASE_TIME + timedelta(hours=1)

    first = run_paper_engine(
        repo,
        _settings(),
        analysis_loader=lambda _symbol, _timeframe: _candidate_entry_payload(),
        simulation_loader=lambda _symbol, _timeframe, _direction, _entry: _candidate_entry_simulation(),
        now=now,
    )
    second = run_paper_engine(
        repo,
        _settings(),
        analysis_loader=lambda _symbol, _timeframe: _candidate_entry_payload(),
        simulation_loader=lambda _symbol, _timeframe, _direction, _entry: _candidate_entry_simulation(),
        now=now,
    )

    assert first["opened"] == 1
    assert second["opened"] == 0
    assert second["skipped_same_bar"] == 1
    assert repo.get_paper_engine_state("TESTUSDT", "4h")["entry_gate_version"] == ENTRY_GATE_VERSION


def test_flip_block_logs_explain_each_failed_gate_and_checklist_rates() -> None:
    repo = MemoryRepository()
    repo.upsert_watchlist_item(WatchlistItem(symbol="TESTUSDT", asset_class="crypto"))
    payload = _candidate_entry_payload()
    simulation = _candidate_entry_simulation()
    simulation.update(
        {
            "checklist": [
                {"key": "rr", "label": "손익비", "status": "fail"},
                {"key": "htf", "label": "상위 TF 정렬", "status": "pass"},
            ],
            "checklist_passed": 1,
            "checklist_total": 2,
            "invalidation_too_close": True,
            "action_plan": {"invalidation": {"price": 90}, "take_profit": [{"price": 102.5}]},
        }
    )

    result = run_paper_engine(
        repo,
        _settings(),
        analysis_loader=lambda _symbol, _timeframe: payload,
        simulation_loader=lambda _symbol, _timeframe, _direction, _entry: simulation,
        now=BASE_TIME,
    )

    assert result["opened"] == 0
    logs = repo.list_entry_block_logs(symbol="TESTUSDT")
    assert {item["failed_gate"] for item in logs} >= {"checklist", "invalidation_hygiene", "signature_gate"}
    assert "체크리스트 1/2" in next(item["detail"] for item in logs if item["failed_gate"] == "checklist")
    assert "candidate" in next(item["detail"] for item in logs if item["failed_gate"] == "signature_gate")
    funnel = paper_gate_funnel(repo, now=BASE_TIME + timedelta(hours=1))
    checklist_stage = next(item for item in funnel["stages"] if item["id"] == "checklist")
    assert checklist_stage["rejection_top3"][0]["count"] == 1
    rates = {item["key"]: item for item in funnel["checklist_pass_rates"]}
    assert rates["rr"]["pass_rate_pct"] == 0.0
    assert rates["htf"]["pass_rate_pct"] == 100.0


@pytest.mark.parametrize(
    ("bar", "stance_state", "holding_bars", "expected_kind", "expected_reason"),
    [
        (candle(1, close=94, high=100, low=94), {"stance": "long_leaning", "flipped": False}, 0, "closed", "invalidation_breach"),
        (candle(1, close=108, high=111, low=104), {"stance": "long_leaning", "flipped": False}, 0, "partial", "take_profit_1"),
        (candle(1, close=99), {"stance": "short_leaning", "flipped": True}, 0, "closed", "opposite_stance_flip"),
        (candle(1, close=101), {"stance": "conflicted", "flipped": False}, 29, "closed", "time_decay"),
    ],
)
def test_paper_worker_executes_all_exit_paths_and_records_reason_and_pnl(
    bar: MarketCandle,
    stance_state: dict,
    holding_bars: int,
    expected_kind: str,
    expected_reason: str,
) -> None:
    repo = MemoryRepository()
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
        signature_snapshot={"signature_key": "validated:test"},
        policy=PaperPolicy(),
    ).model_copy(update={"holding_bars": holding_bars})
    repo.upsert_paper_trade(trade)
    payload = {
        "analysis": {
            "symbol": "TESTUSDT",
            "timeframe": "4h",
            "asset_class": "crypto",
            "mark_price": bar.close,
            "candles": [{"time": int(bar.timestamp.timestamp()), "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close, "volume": bar.volume}],
        },
        "historical_backtest": {"stats": [], "event_stats": []},
        "analyst_briefing": {"confluence": {"stance": stance_state["stance"], "stance_state": stance_state}},
        "gauges": {"bar_state": {"provisional": False}},
    }

    result = run_paper_engine(
        repo,
        _settings(),
        analysis_loader=lambda _symbol, _timeframe: payload,
        simulation_loader=lambda *_args: {},
        now=bar.timestamp,
    )

    assert result["events"][0]["kind"] == expected_kind
    assert result["events"][0]["reason"] == expected_reason
    saved = repo.get_paper_trade(TRADE_ID)
    assert saved is not None
    if expected_kind == "partial":
        assert saved.status == "open"
        assert saved.partial_exit_at == bar.timestamp
        assert saved.stop_price == saved.entry_price
    else:
        assert saved.status == "closed"
        assert saved.exit_reason == expected_reason
        assert saved.exit_at == bar.timestamp
        assert isinstance(saved.net_pnl_usdt, float)
        assert repo.list_judgment_scores(trade_id=None, position_id=trade.id)[0].metrics["net_pnl_usdt"] == saved.net_pnl_usdt


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
    assert "대결 판정" in result["fairness_note"]


def test_time_decay_is_neutral_and_excluded_from_win_rate() -> None:
    repo = MemoryRepository()
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
        policy=PaperPolicy(),
    )
    closed = apply_exit_decision(
        trade,
        decision=evaluate_exit(
            trade.model_copy(update={"holding_bars": 29}),
            bar=candle(1, close=101),
            stance_state={"stance": "conflicted", "flipped": False, "transitioning": False},
            take_profit_pressure="low",
            prior_high_pressure_streak=0,
            policy=PaperPolicy(),
        ),
        bar=candle(1, close=101),
        policy=PaperPolicy(),
    )
    repo.upsert_paper_trade(closed)

    metrics = paper_scoreboard(repo, _settings(), now=BASE_TIME + timedelta(days=1))["engine"]

    assert closed.exit_reason == "time_decay"
    assert closed.net_pnl_usdt > 0
    assert metrics["trade_count"] == 1
    assert metrics["scored_trade_count"] == 0
    assert metrics["neutral_count"] == 1
    assert metrics["win_rate_pct"] is None


def test_benchmark_anchor_and_manual_tracking_join_paper_universe() -> None:
    repo = MemoryRepository()
    now = BASE_TIME + timedelta(days=3)
    repo.upsert_entry_intent(
        EntryIntent(
            symbol="SOXLUSDT",
            kind="watch",
            direction=None,
            zone_lower=None,
            zone_upper=None,
            conditions=[],
            expires_at=now + timedelta(days=14),
        )
    )

    started = start_paper_benchmark(repo, now=now)

    assert started["created"] is True
    assert started["target_count"] == 1
    assert paper_benchmark(repo)["started_at"] == now.isoformat()
    assert ("SOXLUSDT", "4h") in paper_universe(repo)
    assert paper_scoreboard(repo, _settings(), now=now)["benchmark"]["started"] is True


def test_benchmark_bootstrap_opens_current_stance_once_without_flip() -> None:
    repo = MemoryRepository()
    repo.upsert_watchlist_item(WatchlistItem(symbol="TESTUSDT", asset_class="crypto"))
    benchmark_started_at = BASE_TIME + timedelta(minutes=1)
    first_run_at = BASE_TIME + timedelta(minutes=2)
    start_paper_benchmark(repo, now=benchmark_started_at)
    analysis = {
        "symbol": "TESTUSDT",
        "timeframe": "4h",
        "asset_class": "crypto",
        "candles": [{"time": int(BASE_TIME.timestamp()), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000}],
        "liquidity": {"sweeps": [{"confirmed": True, "side": "sell_side", "type": "sweep", "grade": "Strong"}]},
    }
    signature = signatures_from_analysis(analysis)[0]
    payload = {
        "analysis": analysis,
        "historical_backtest": {"stats": [{"signature_key": signature["key"], "signature": signature, "sample_size": 40, "win_1r_ci": [55.0, 70.0]}]},
        "analyst_briefing": {
            "confluence": {
                "stance": "long_leaning",
                "stance_state": {"stance": "long_leaning", "flipped": False, "transitioning": False},
                "long_evidence": [{"claim": str(index)} for index in range(3)],
                "short_evidence": [],
            }
        },
        "gauges": {"bar_state": {"provisional": False}},
    }
    simulation = {
        "rr_ratio": 1.2,
        "survives_to_invalidation": True,
        "checklist": [{"status": "pass"}] * 3 + [{"status": "fail"}] * 2,
        "checklist_passed": 3,
        "checklist_total": 5,
        "action_plan": {"invalidation": {"price": 99}, "take_profit": [{"price": 106}]},
    }

    first = run_paper_engine(
        repo,
        _settings(),
        analysis_loader=lambda _symbol, _timeframe: payload,
        simulation_loader=lambda _symbol, _timeframe, _direction, _entry: simulation,
        now=first_run_at,
    )
    seeded = repo.list_paper_trades(status="open")[0]
    repo.upsert_paper_trade(seeded.model_copy(update={"holding_bars": 3, "updated_at": first_run_at}))
    second = run_paper_engine(
        repo,
        _settings(),
        analysis_loader=lambda _symbol, _timeframe: payload,
        simulation_loader=lambda _symbol, _timeframe, _direction, _entry: simulation,
        now=first_run_at + timedelta(minutes=1),
    )

    assert first["opened"] == 1
    assert second["opened"] == 0
    trades = repo.list_paper_trades(status="open")
    assert len(trades) == 1
    assert trades[0].entry_evidence["entry_mode"] == "validation_bootstrap"
    assert trades[0].entry_evidence["benchmark_started_at"] == benchmark_started_at.isoformat()
    assert trades[0].entry_bar_at == BASE_TIME
    assert trades[0].entry_at == first_run_at
    assert trades[0].holding_bars == 3
    assert trades[0].stance_snapshot["flipped"] is False
    assert repo.list_paper_gate_funnel(symbol="TESTUSDT")[0]["entered"] is False


def test_benchmark_bootstrap_recovers_policy_invalid_seed_on_new_bar() -> None:
    repo = MemoryRepository()
    repo.upsert_watchlist_item(WatchlistItem(symbol="TESTUSDT", asset_class="crypto"))
    benchmark_started_at = BASE_TIME + timedelta(minutes=1)
    first_run_at = BASE_TIME + timedelta(minutes=2)
    start_paper_benchmark(repo, now=benchmark_started_at)
    analysis = {
        "symbol": "TESTUSDT",
        "timeframe": "4h",
        "asset_class": "crypto",
        "candles": [{"time": int(BASE_TIME.timestamp()), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000}],
        "liquidity": {"sweeps": [{"confirmed": True, "side": "sell_side", "type": "sweep", "grade": "Strong"}]},
    }
    signature = signatures_from_analysis(analysis)[0]
    payload = {
        "analysis": analysis,
        "historical_backtest": {"stats": [{"signature_key": signature["key"], "signature": signature, "sample_size": 40, "win_1r_ci": [55.0, 70.0]}]},
        "analyst_briefing": {
            "confluence": {
                "stance": "long_leaning",
                "stance_state": {"stance": "long_leaning", "flipped": False, "transitioning": False},
                "long_evidence": [{"claim": str(index)} for index in range(3)],
                "short_evidence": [],
            }
        },
        "gauges": {"bar_state": {"provisional": False}},
    }
    simulation = {
        "rr_ratio": 1.2,
        "survives_to_invalidation": True,
        "checklist": [{"status": "pass"}] * 3 + [{"status": "fail"}] * 2,
        "checklist_passed": 3,
        "checklist_total": 5,
        "action_plan": {"invalidation": {"price": 99}, "take_profit": [{"price": 106}]},
    }

    def load_analysis(_symbol: str, _timeframe: str) -> dict[str, object]:
        return payload

    def load_simulation(_symbol: str, _timeframe: str, _direction: str, _entry: float) -> dict[str, object]:
        return simulation

    first = run_paper_engine(
        repo,
        _settings(),
        analysis_loader=load_analysis,
        simulation_loader=load_simulation,
        now=first_run_at,
    )
    original = repo.list_paper_trades(status="open")[0]
    repo.upsert_paper_trade(
        original.model_copy(
            update={
                "status": "closed",
                "remaining_quantity": 0.0,
                "exit_at": first_run_at,
                "exit_bar_at": BASE_TIME,
                "exit_price": 98.0,
                "exit_reason": "take_profit_pressure",
                "loss_tags": ["policy_invalid:pre_tp_pressure_exit", "exit:take_profit_pressure"],
                "updated_at": first_run_at,
            }
        )
    )

    same_bar = run_paper_engine(
        repo,
        _settings(),
        analysis_loader=load_analysis,
        simulation_loader=load_simulation,
        now=first_run_at + timedelta(minutes=1),
    )
    analysis["candles"].append(
        {
            "time": int((BASE_TIME + timedelta(hours=4)).timestamp()),
            "open": 100,
            "high": 102,
            "low": 99,
            "close": 101,
            "volume": 1_100,
        }
    )
    recovered = run_paper_engine(
        repo,
        _settings(),
        analysis_loader=load_analysis,
        simulation_loader=load_simulation,
        now=BASE_TIME + timedelta(hours=4, minutes=2),
    )
    repeated = run_paper_engine(
        repo,
        _settings(),
        analysis_loader=load_analysis,
        simulation_loader=load_simulation,
        now=BASE_TIME + timedelta(hours=4, minutes=3),
    )

    assert first["opened"] == 1
    assert same_bar["opened"] == 0
    assert recovered["opened"] == 1
    assert repeated["opened"] == 0
    active = repo.list_paper_trades(status="open")
    assert len(active) == 1
    assert active[0].id != original.id
    assert active[0].entry_bar_at == BASE_TIME + timedelta(hours=4)
    assert active[0].entry_evidence["entry_mode"] == "validation_bootstrap_recovery"
    assert active[0].entry_evidence["recovered_policy_invalid_count"] == 1


def test_bootstrap_concurrency_duplicates_are_suppressed_without_scoring() -> None:
    repo = MemoryRepository()
    policy = PaperPolicy()
    common = {
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "asset_class": "crypto",
        "direction": Direction.short,
        "bar": candle(0, close=100),
        "invalidation_price": 105,
        "take_profit_price": 95,
        "evidence": {"entry_mode": "validation_bootstrap"},
        "checklist": {},
        "stance_snapshot": {"stance": "short_leaning"},
        "signature_snapshot": {},
        "policy": policy,
    }
    repo.upsert_paper_trade(open_trade(trade_id=UUID("00000000-0000-0000-0000-000000000064"), **common))
    repo.upsert_paper_trade(open_trade(trade_id=UUID("00000000-0000-0000-0000-000000000065"), **common))

    from app.paper.service import _suppress_duplicate_bootstrap_trades

    assert _suppress_duplicate_bootstrap_trades(repo, now=BASE_TIME + timedelta(minutes=1)) == 1
    assert len(repo.list_paper_trades(status="open")) == 1
    suppressed = repo.list_paper_trades(status="closed")
    assert len(suppressed) == 1
    assert suppressed[0].exit_reason == "duplicate_bootstrap_suppressed"
    assert paper_scoreboard(repo, _settings(), now=BASE_TIME + timedelta(days=1))["engine"]["trade_count"] == 0


def test_gate_funnel_is_sequential_and_diagnostic_fires_once() -> None:
    repo = MemoryRepository()
    now = BASE_TIME + timedelta(days=8)
    complete = {
        "confirmed_flip": True,
        "evidence": True,
        "checklist": True,
        "invalidation_hygiene": True,
        "risk_reward": True,
        "liquidation_safety": True,
        "action_levels": True,
        "signature_gate": True,
        "regime_gate": True,
        "event_window": True,
        "freshness": True,
    }
    for offset, (failed, entered) in enumerate((("confirmed_flip", False), ("checklist", False), ("", False))):
        gates = dict(complete)
        if failed:
            start = list(gates).index(failed)
            for gate in list(gates)[start:]:
                gates[gate] = False
        repo.upsert_paper_gate_funnel(
            {
                "symbol": f"TEST{offset}USDT",
                "timeframe": "4h",
                "bar_at": (now - timedelta(days=6) + timedelta(hours=offset)).isoformat(),
                "gates": gates,
                "entered": entered,
                "rejected_at": failed or None,
                "pill_diagnostics": ({"window_events": 2, "validated": 0, "confirmed": 2, "rendered": 0, "bottleneck": "validated"} if offset == 0 else {}),
                "event_pill_ids": [],
            }
        )
    repo.upsert_paper_gate_funnel(
        {
            "symbol": "BOOTUSDT",
            "timeframe": "4h",
            "bar_at": BASE_TIME.isoformat(),
            "gates": {gate: False for gate in complete},
            "entered": False,
            "rejected_at": "confirmed_flip",
        }
    )

    funnel = paper_gate_funnel(repo, now=now)
    by_id = {stage["id"]: stage["count"] for stage in funnel["stages"]}
    assert by_id["evaluated"] == 3
    assert by_id["confirmed_flip"] == 2
    assert by_id["checklist"] == 1
    assert by_id["signature_gate"] == 1
    assert funnel["top_rejection"]["label"] in {"스탠스 전환 미확정", "체크리스트 미달"}
    assert funnel["pill_diagnostics"]["bottleneck"] == "validated"
    assert funnel["pill_diagnostics"]["rendered"] == 0

    first = _gate_diagnostic_event(repo, now=now)
    second = _gate_diagnostic_event(repo, now=now + timedelta(minutes=5))
    assert first is not None and first["kind"] == "gate_diagnostic"
    assert second is None


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        paper_engine_enabled=True,
        paper_margin_usdt=100.0,
        paper_leverage=3.0,
        paper_max_open_positions=5,
        paper_min_evidence=4,
        paper_min_checklist_passed=5,
        paper_min_rr=1.5,
        paper_candidate_bootstrap_enabled=True,
        paper_candidate_bootstrap_min_sample=15,
        paper_candidate_bootstrap_min_win_1r_pct=50.0,
        paper_candidate_bootstrap_relaxed_days=14,
        paper_candidate_bootstrap_relaxed_min_sample=8,
        paper_candidate_bootstrap_relaxed_min_win_1r_pct=45.0,
        paper_candidate_bootstrap_disable_validated_count=3,
        paper_max_holding_bars=30,
        paper_poor_mdd_pct=10.0,
        backtest_taker_fee_pct=0.06,
        backtest_slippage_crypto_pct=0.03,
        backtest_slippage_stock_pct=0.08,
        backtest_slippage_index_pct=0.05,
        universe_backtest_min_ci_low_pct=50.0,
        signature_validated_min_sample=30,
    )


def _candidate_entry_payload() -> dict:
    return {
        "analysis": {
            "symbol": "TESTUSDT",
            "timeframe": "4h",
            "asset_class": "crypto",
            "candles": [{"time": int(BASE_TIME.timestamp()), "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1_000}],
        },
        "historical_backtest": {"stats": [], "event_stats": []},
        "analyst_briefing": {
            "confluence": {
                "stance": "long_leaning",
                "stance_state": {"stance": "long_leaning", "flipped": True, "transitioning": False, "last_bar_at": BASE_TIME.isoformat()},
                "long_evidence": [{"claim": str(index)} for index in range(4)],
                "short_evidence": [],
            }
        },
        "gauges": {"bar_state": {"provisional": False}},
    }


def _candidate_entry_simulation() -> dict:
    return {
        "rr_ratio": 2.0,
        "survives_to_invalidation": True,
        "invalidation_too_close": False,
        "checklist": [{"key": f"check-{index}", "label": f"체크 {index}", "status": "pass"} for index in range(6)],
        "checklist_passed": 6,
        "checklist_total": 6,
        "action_plan": {"invalidation": {"price": 99}, "take_profit": [{"price": 110}]},
    }
