from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import sqlite3

import httpx
import pytest

from app.db.migrations import run_migrations
from app.stock_paper.models import Market
from app.stock_paper.parameters import load_stock_parameters
from app.stock_paper.policy import evaluate_stock_entry
from app.stock_paper.store import StockPaperStore
from app.stock_paper.universe import load_universe
from app.toss.client import TossReadOnlyClient
from app.toss.errors import TossAuthenticationError
from app.toss.signals import build_candidate, group_candidates


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
