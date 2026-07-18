from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.migrations import run_migrations
from app.db.models import Direction, Position
from app.db.sqlite_utils import connect_sqlite
from app.positions.deepdive import build_entry_snapshot_claim, build_position_deepdive, deepdive_judgment_claim
from app.toss.store import TossStockStore


def _toss_candles(count: int = 100) -> list[dict]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        {
            "opened_at": (start + timedelta(days=index)).isoformat(),
            "open": 100 + index * 0.2,
            "high": 101 + index * 0.2,
            "low": 99 + index * 0.2,
            "close": 100 + index * 0.2,
            "volume": 1_000_000,
        }
        for index in range(count)
    ]


def _raw_analysis() -> dict:
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    candles = [
        {
            "time": int((start + timedelta(hours=index * 4)).timestamp()),
            "open": 120 + index * 0.05,
            "high": 121 + index * 0.05,
            "low": 119 + index * 0.05,
            "close": 120 + index * 0.05,
            "volume": 1000,
        }
        for index in range(48)
    ]
    return {
        "symbol": "SOXLUSDT",
        "mark_price": 124,
        "candles": candles,
        "one_liners": {
            "overall_stance": "하방",
            "lines": [
                {"module_label": "레벨", "stance": "하방", "phrase": "저항 아래 정체"},
                {"module_label": "지표", "stance": "하방", "phrase": "하락 우세"},
            ],
        },
        "price_levels": {
            "support": [{"price": 118}],
            "resistance": [{"price": 130}],
            "invalidation": [{"price": 116.7}],
        },
        "derivatives": {"latest": {"funding_rate": 0.001, "open_interest": 12_000_000}},
    }


def _joined(raw: dict, *, market_state: str = "open") -> dict:
    return {
        **raw,
        "underlying_join": {
            "status": "joined",
            "toss_symbol": "SOXL",
            "underlying_name": "DIREXION SHARES ETF TRUST DAILY SEMICONDUCTOR BULL 3X SHS",
            "toss_exchange": "AMEX",
            "underlying_kind": "leveraged_etf",
            "underlying_leverage_factor": 3,
            "market_state": market_state,
            "stale": market_state != "open",
            "basis_pct": 1.25,
            "flow_status": "unavailable_us",
            "flow_note": "Toss US 투자자별 수급 미제공 · 해당 신호 비활성",
            "raw_candles": _toss_candles(),
        },
    }


def test_deepdive_cross_signals_require_multiple_sources_and_expose_soxl_stack() -> None:
    now = datetime(2026, 4, 10, 12, tzinfo=timezone.utc)
    position = Position(
        symbol="SOXLUSDT",
        direction=Direction.long,
        entry_price=129.24,
        quantity=5,
        leverage=10,
        mark_price=124,
        liquidation_price=90,
        planned_stop_price=116.7,
        thesis_text="반도체 기초 구조 회복 관측",
    )
    raw = _raw_analysis()
    joined = _joined(raw)
    entry = build_entry_snapshot_claim(position, raw, joined, captured_at=now)
    payload = build_position_deepdive(
        position,
        raw,
        joined,
        {
            "truth_label": "실제 청산 · 예상 아님",
            "n_events": 42,
            "sample_low": False,
            "top_zones": [
                {
                    "price_low": 119,
                    "price_high": 121,
                    "price_mid": 120,
                    "total_usd_estimated": 250_000,
                    "events": 12,
                }
            ],
        },
        {
            "invalidation": {"price": 116.7},
            "engine_invalidation": None,
            "verdict_state": "weakening",
        },
        entry,
        ledger={"latest_judgment_id": "j1", "outcomes": [], "performance": [], "horizons": [1, 5, 20], "score_policy": "test"},
        now=now,
    )

    assert payload["status"] == "ready"
    assert len(payload["cross_signals"]) == 5
    assert all(len(signal["sources"]) >= 2 for signal in payload["cross_signals"])
    assert all(signal["moat_reason"] for signal in payload["cross_signals"])
    basis = next(signal for signal in payload["cross_signals"] if signal["id"] == "basis_behavior")
    assert len(basis["data"]["sparkline"]) >= 2
    leverage = next(signal for signal in payload["cross_signals"] if signal["id"] == "leverage_stack")
    assert leverage["data"]["effective_exposure_multiple"] == 30
    assert leverage["data"]["decay_20d_estimate_pct"] is not None
    assert payload["risk"]["market_reading"]["position_alignment"] == "opposed"
    assert payload["risk"]["market_reading"]["reversal_condition"]["price"] == 130
    assert payload["risk"]["partial_exit_simulation"][1]["remaining_quantity"] == 2.5
    assert deepdive_judgment_claim(payload, position, 124)["expected_move"] == "down"


def test_toss_flow_is_inactive_when_underlying_market_is_closed() -> None:
    now = datetime(2026, 4, 11, 12, tzinfo=timezone.utc)
    position = Position(symbol="SOXLUSDT", direction=Direction.long, entry_price=125, quantity=1, leverage=2)
    raw = _raw_analysis()
    joined = _joined(raw, market_state="closed")
    entry = build_entry_snapshot_claim(position, raw, joined, captured_at=now)
    payload = build_position_deepdive(
        position,
        raw,
        joined,
        {},
        {"invalidation": None, "engine_invalidation": None},
        entry,
        ledger={"latest_judgment_id": "j2", "outcomes": [], "performance": [], "horizons": [1, 5, 20], "score_policy": "test"},
        now=now,
    )

    flow = next(signal for signal in payload["cross_signals"] if signal["id"] == "underlying_flow_alignment")
    assert flow["status"] == "unavailable"
    assert flow["data"]["reason"] == "market_closed"
    assert payload["risk"]["invalidation_price"] == 116.7
    assert payload["risk"]["invalidation_distance_pct"] is not None
    assert payload["risk"]["partial_exit_simulation"][0]["invalidation_risk_notional"] is not None


def test_negative_basis_moving_toward_zero_is_contraction() -> None:
    now = datetime(2026, 4, 11, 12, tzinfo=timezone.utc)
    position = Position(symbol="SOXLUSDT", direction=Direction.long, entry_price=125, quantity=1, leverage=10)
    raw = _raw_analysis()
    raw["candles"] = [
        {
            "time": int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp()),
            "open": 98,
            "high": 99,
            "low": 97,
            "close": 98,
            "volume": 1_000,
        }
    ]
    joined = _joined(raw)
    joined["underlying_join"]["basis_pct"] = -0.5
    entry = build_entry_snapshot_claim(position, raw, joined, captured_at=now)

    payload = build_position_deepdive(
        position,
        raw,
        joined,
        {},
        {},
        entry,
        ledger={"latest_judgment_id": "j3", "outcomes": [], "performance": [], "horizons": [1, 5, 20], "score_policy": "test"},
        now=now,
    )

    basis = next(signal for signal in payload["cross_signals"] if signal["id"] == "basis_behavior")
    assert basis["data"]["state"] == "contracting"
    assert basis["data"]["width_change_pct_points"] < 0
    assert basis["data"]["signed_change_pct_points"] > 0
    assert basis["reading"] == "괴리폭 축소 관측"


def test_position_judgment_records_real_elapsed_t1_and_marks_low_sample(tmp_path) -> None:
    database_path = tmp_path / "deepdive.db"
    with connect_sqlite(str(database_path)) as connection:
        run_migrations(connection)
    store = TossStockStore(f"sqlite:///{database_path}")
    observed = datetime(2026, 4, 1, 12, tzinfo=timezone.utc)
    inserted = store.record_position_judgment(
        judgment_id="position-cycle-1",
        symbol="SOXLUSDT",
        observed_at=observed.isoformat(),
        price=100,
        evidence={
            "position_id": "position-1",
            "expected_move": "down",
            "cross_signals": [
                {
                    "id": "basis_behavior",
                    "label": "베이시스 행동",
                    "status": "active",
                    "data": {"state": "expanding", "current_pct": -2.0, "width_change_pct_points": 1.2},
                }
            ],
        },
    )

    assert inserted is True
    assert store.due_position_symbols(observed + timedelta(hours=23)) == []
    assert store.due_position_symbols(observed + timedelta(days=1, minutes=1)) == ["SOXLUSDT"]
    assert store.record_due_outcomes({"SOXLUSDT": 95}, now=observed + timedelta(hours=23)) == 0
    assert store.record_due_outcomes({"SOXLUSDT": 90}, now=observed + timedelta(days=1, minutes=1)) == 1
    assert store.due_position_symbols(observed + timedelta(days=1, minutes=2)) == []
    assert store.due_position_symbols(observed + timedelta(days=5, minutes=1)) == ["SOXLUSDT"]
    outcomes = store.outcomes_for_judgment("position-cycle-1")
    assert outcomes[0]["horizon_days"] == 1
    assert outcomes[0]["return_pct"] == pytest.approx(-10)
    performance = store.position_performance("position-1")
    assert performance[0] == {"horizon_days": 1, "n": 1, "hit_rate_pct": 100.0, "sample_low": True}
    signal_performance = store.position_signal_performance("position-1")
    assert signal_performance[0] == {
        "signal_id": "basis_behavior",
        "signal_label": "베이시스 행동",
        "horizon_days": 1,
        "n": 1,
        "hit_rate_pct": 100.0,
        "sample_low": True,
    }
