from __future__ import annotations

from datetime import datetime, timezone
import asyncio
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from app.db.migrations import run_migrations
from app.stock_paper import analysis as stock_analysis
from app.stock_paper.audit import audit_entry_gates
from app.stock_paper.models import Market
from app.stock_paper.parameters import load_stock_parameters
from app.stock_paper.policy import evaluate_stock_entry
from app.stock_paper.store import StockPaperStore
from app.stock_paper.universe import load_universe
from app.toss.client import TossReadOnlyClient
from app.toss.errors import TossAuthenticationError
from app.toss.signals import build_candidate, group_candidates
from app.toss.service import _session_state
from app.toss.store import TossStockStore


PARAMS_DIR = Path(__file__).parents[1] / "app" / "stock_paper" / "params"


def _store(tmp_path) -> StockPaperStore:
    path = tmp_path / "stock-v2.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    connection.close()
    store = StockPaperStore(f"sqlite:///{path}")
    store.ensure_tracks(universe_version="test", initial_krw=1_000_000, initial_usd=10_000)
    return store


def test_observation_candidates_are_visible_but_not_tradable() -> None:
    universe = load_universe()
    assert universe.classify(Market.US, "QQQ") == (False, "benchmark_proxy")
    assert universe.classify(Market.US, "AAPL") == (True, "universe_member")
    candidate = build_candidate(
        market="US",
        symbol="QQQ",
        name="QQQ",
        price=600,
        observed_at=datetime.now(timezone.utc).isoformat(),
        market_rank=1,
        retail_rank=30,
        warnings=[],
        tradable=False,
        role="benchmark_proxy",
    )
    assert candidate is not None
    assert group_candidates([candidate])
    assert [item for item in [candidate] if item["tradable"]] == []


def test_signature_and_earnings_are_recorded_not_silent_passes() -> None:
    parameters = load_stock_parameters()
    analysis = {
        "status": "analyzed",
        "entry_score": 90,
        "rr_ratio": 2,
        "invalidation": {"price": 100},
        "signature_status": "unvalidated",
        "confluence": {
            "stance_state": {"stance": "long_leaning", "flipped": True, "transitioning": False},
            "long_evidence": [{"id": index} for index in range(5)],
            "short_evidence": [],
        },
    }
    decision = evaluate_stock_entry(analysis, data_fresh=True, parameters=parameters)
    assert decision.enter is True
    assert decision.gate_results["validated_signature"] == {
        "status": "recorded",
        "measured_value": "unvalidated",
        "threshold": "record_only",
        "required": False,
    }
    assert decision.gate_results["earnings_gate"]["status"] == "not_evaluable"


def test_stock_v3_replaces_momentary_flip_with_stable_long_without_lowering_thresholds() -> None:
    v2 = load_stock_parameters(PARAMS_DIR / "stock-v2.json")
    v3 = load_stock_parameters(PARAMS_DIR / "stock-v3.json")
    assert v2.stance_gate_mode == "confirmed_flip"
    assert v3.stance_gate_mode == "stable_long"
    assert (
        v3.min_evidence,
        v3.min_checklist_passed,
        v3.min_checklist_total,
        v3.min_rr,
        v3.min_entry_score,
    ) == (
        v2.min_evidence,
        v2.min_checklist_passed,
        v2.min_checklist_total,
        v2.min_rr,
        v2.min_entry_score,
    )

    analysis = {
        "status": "analyzed",
        "entry_score": 90,
        "rr_ratio": 2,
        "invalidation": {"price": 100},
        "confluence": {
            "stance_state": {"stance": "long_leaning", "flipped": False, "transitioning": False},
            "long_evidence": [{"id": index} for index in range(5)],
            "short_evidence": [],
        },
    }
    assert evaluate_stock_entry(analysis, data_fresh=True, parameters=v2).gate_results["confirmed_flip"]["status"] == "rejected"
    v3_decision = evaluate_stock_entry(analysis, data_fresh=True, parameters=v3)
    assert v3_decision.enter is True
    assert v3_decision.gate_results["confirmed_flip"]["measured_value"]["flipped"] is False
    analysis["confluence"]["stance_state"]["transitioning"] = True
    assert evaluate_stock_entry(analysis, data_fresh=True, parameters=v3).gate_results["confirmed_flip"]["status"] == "rejected"


def test_us_calendar_uses_regular_market_only_not_day_pre_or_after() -> None:
    payload = {
        "result": {
            "today": {
                "dayMarket": {"startTime": "2026-07-22T00:00:00Z", "endTime": "2026-07-22T08:00:00Z"},
                "preMarket": {"startTime": "2026-07-22T08:00:00Z", "endTime": "2026-07-22T13:30:00Z"},
                "regularMarket": {"startTime": "2026-07-22T13:30:00Z", "endTime": "2026-07-22T20:00:00Z"},
                "afterMarket": {"startTime": "2026-07-22T20:00:00Z", "endTime": "2026-07-22T23:50:00Z"},
            }
        }
    }
    assert _session_state(payload, "US", now=datetime(2026, 7, 22, 3, tzinfo=timezone.utc)) == "closed"
    assert _session_state(payload, "US", now=datetime(2026, 7, 22, 14, tzinfo=timezone.utc)) == "open"
    assert _session_state(payload, "US", now=datetime(2026, 7, 22, 21, tzinfo=timezone.utc)) == "closed"


def test_kr_calendar_uses_integrated_regular_market() -> None:
    payload = {"result": {"today": {"integrated": {"regularMarket": {"startTime": "2026-07-22T00:00:00Z", "endTime": "2026-07-22T06:30:00Z"}}}}}
    assert _session_state(payload, "KR", now=datetime(2026, 7, 22, 3, tzinfo=timezone.utc)) == "open"
    assert _session_state(payload, "KR", now=datetime(2026, 7, 22, 7, tzinfo=timezone.utc)) == "closed"


def test_execution_observation_skips_empty_current_minute(tmp_path) -> None:
    path = tmp_path / "toss-observation.db"
    connection = sqlite3.connect(path)
    run_migrations(connection)
    connection.close()
    store = TossStockStore(f"sqlite:///{path}")
    observed_at = datetime.now(timezone.utc).isoformat()
    store.upsert_candles(
        "KR",
        "005930",
        "1m",
        "test",
        observed_at,
        [
            {
                "opened_at": "2026-07-22T15:20:00+09:00",
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "volume": 10_000,
            },
            {
                "opened_at": "2026-07-22T15:21:00+09:00",
                "open": 101,
                "high": 101,
                "low": 101,
                "close": 101,
                "volume": 0,
            },
        ],
    )

    observation = store.latest_execution_observation("KR", "005930", session_open=True)

    assert observation is not None
    assert observation["minute_volume"] == 10_000
    assert observation["minute_low"] == 99
    assert observation["minute_high"] == 102


def test_stock_v3_replay_keeps_entry_score_as_remaining_hard_gate(tmp_path) -> None:
    store = _store(tmp_path)
    observed_at = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
    store.save_analysis_snapshot(
        Market.US,
        "MU",
        observed_at=observed_at,
        parameter_version="stock-v2",
        payload={
            "status": "analyzed",
            "entry_score": 56,
            "rr_ratio": 6.2,
            "invalidation": {"price": 87},
            "confluence": {
                "stance_state": {"stance": "long_leaning", "flipped": False, "transitioning": False},
                "long_evidence": [{"engine": "structure"} for _ in range(9)],
                "short_evidence": [{"engine": "volume"} for _ in range(3)],
            },
        },
    )
    audit = audit_entry_gates(
        Path(store.path),
        source_version="stock-v2",
        policy_paths=(PARAMS_DIR / "stock-v2.json", PARAMS_DIR / "stock-v3.json"),
    )
    before, after = audit["policies"]
    assert before["gates"]["confirmed_flip"]["rejected"] == 1
    assert after["gates"]["confirmed_flip"]["passed"] == 1
    assert after["gates"]["entry_score"]["rejected"] == 1
    assert after["entered"] == 0


def test_equity_signal_availability_never_claims_derivatives_evidence(monkeypatch) -> None:
    rows = [
        {
            "opened_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "open": 100,
            "high": 102,
            "low": 99,
            "close": 101,
            "volume": 1_000,
        }
        for _ in range(100)
    ]
    store = SimpleNamespace(latest_candles=lambda *_args: rows)
    chart = {
        "scenarios": {"long": {"invalidation": {"price": 95, "distance_pct": -5}, "take_profit": [{"price": 111, "distance_pct": 10}]}},
    }
    confluence = {
        "stance_state": {"stance": "long_leaning", "flipped": False, "transitioning": False},
        "long_evidence": [{"engine": "structure"}],
        "short_evidence": [{"engine": "volume"}],
    }
    monkeypatch.setattr(stock_analysis, "build_chart_analysis", lambda _snapshot: chart)
    monkeypatch.setattr(stock_analysis, "build_analyst_briefing", lambda **_kwargs: {"confluence": confluence})
    monkeypatch.setattr(stock_analysis, "generate_report", lambda _snapshot: SimpleNamespace(entry_score=80))

    result = stock_analysis.analyze_stock_candidate(store, Market.US, "NVDA", current_price=101)
    evidence = [*result["confluence"]["long_evidence"], *result["confluence"]["short_evidence"]]
    assert all(item["engine"] != "derivatives" for item in evidence)
    assert result["asset_class"] == "equity"
    assert result["signal_availability"]["funding_rate"] == {"available": False, "used_by_evidence": False}
    assert result["signal_availability"]["open_interest"] == {"available": False, "used_by_evidence": False}


def test_validation_clock_starts_only_after_authenticated_observation(tmp_path) -> None:
    store = _store(tmp_path)
    before = store.dashboard()
    assert all(track["clock_valid"] == 0 and track["elapsed_days"] == 0 for track in before["tracks"])
    now = datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc)
    assert store.activate_clock(Market.US, parameter_version="stock-v2", observed_at=now) is True
    after = store.dashboard()
    us = next(track for track in after["tracks"] if track["market"] == "US")
    kr = next(track for track in after["tracks"] if track["market"] == "KR")
    assert us["clock_valid"] == 1 and us["started_at"] == now.isoformat()
    assert kr["clock_valid"] == 0


def test_validation_clock_restarts_once_when_policy_version_changes(tmp_path) -> None:
    store = _store(tmp_path)
    v2_at = datetime(2026, 7, 21, 1, 0, tzinfo=timezone.utc)
    v3_at = datetime(2026, 7, 22, 1, 0, tzinfo=timezone.utc)
    assert store.activate_clock(Market.US, parameter_version="stock-v2", observed_at=v2_at) is True
    assert store.activate_clock(Market.US, parameter_version="stock-v3", observed_at=v3_at) is True
    assert store.activate_clock(Market.US, parameter_version="stock-v3", observed_at=v3_at) is False
    us = next(track for track in store.dashboard()["tracks"] if track["market"] == "US")
    assert us["parameter_version"] == "stock-v3"
    assert us["started_at"] == v3_at.isoformat()
    with sqlite3.connect(store.path) as connection:
        restart = connection.execute("SELECT reason FROM stock_paper_events WHERE market='US' AND event_type='validation_clock_restarted'").fetchone()
    assert restart == ("parameter_version_changed:stock-v2->stock-v3",)


def test_rejection_ledger_keeps_every_failed_gate(tmp_path) -> None:
    store = _store(tmp_path)
    store.record_entry_rejection(Market.US, "AAPL", gate="risk_reward", measured_value=1.1, threshold=1.5)
    store.record_entry_rejection(Market.US, "AAPL", gate="confirmed_flip", measured_value=False, threshold=True)
    distribution = store.dashboard()["entry_rejection_distribution"]
    assert distribution["total"] == 2
    assert {row["gate"] for row in distribution["gates"]} == {"risk_reward", "confirmed_flip"}


def test_analysis_snapshot_serializes_datetime_values(tmp_path) -> None:
    store = _store(tmp_path)
    observed_at = datetime(2026, 7, 21, 2, 22, tzinfo=timezone.utc)

    store.save_analysis_snapshot(
        Market.US,
        "NBIS",
        observed_at=observed_at,
        parameter_version="stock-v2",
        payload={"status": "analyzed", "report": {"generated_at": observed_at}},
    )

    saved = store.latest_analysis_snapshot(Market.US, "NBIS")
    assert saved is not None
    assert saved["report"]["generated_at"] == observed_at.isoformat()


@pytest.mark.asyncio
async def test_repeated_401_preserves_safe_toss_error_details() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "secret-token", "expires_in": 3600})
        return httpx.Response(
            401,
            json={"error": {"code": "SCOPE_DENIED", "message": "market data permission denied", "requestId": "req-401"}},
        )

    client = TossReadOnlyClient("client", "secret", base_url="https://example.test", transport=httpx.MockTransport(handler))
    with pytest.raises(TossAuthenticationError) as caught:
        await client.get("/api/v1/prices", params={"symbols": "QQQ"})
    await client.close()
    assert calls >= 4
    assert caught.value.error_code == "SCOPE_DENIED"
    assert caught.value.error_message == "market data permission denied"
    assert caught.value.request_id == "req-401"
    assert "secret-token" not in str(caught.value)


@pytest.mark.asyncio
async def test_kr_us_clients_share_one_token_and_do_not_invalidate_each_other() -> None:
    issued = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued
        if request.url.path == "/oauth2/token":
            issued += 1
            return httpx.Response(200, json={"access_token": f"token-{issued}", "expires_in": 3600})
        assert request.headers["Authorization"] == f"Bearer token-{issued}"
        return httpx.Response(200, json={"result": []})

    transport = httpx.MockTransport(handler)
    kr = TossReadOnlyClient("shared-client-v2", "secret", base_url="https://example.test", transport=transport)
    us = TossReadOnlyClient("shared-client-v2", "secret", base_url="https://example.test", transport=transport)
    await asyncio.gather(
        kr.get("/api/v1/prices", params={"symbols": "005930"}),
        us.get("/api/v1/prices", params={"symbols": "QQQ"}),
    )
    await asyncio.gather(kr.close(), us.close())
    assert issued == 1
