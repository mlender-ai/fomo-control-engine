from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import httpx
import pytest

from app.toss.client import TossReadOnlyClient, assert_allowed_path
from app.toss.errors import TossEdgeBlocked, TossMaintenance, TossPathNotAllowed
from app.toss.signals import (
    build_candidate,
    investor_flow_signal,
    momentum_signal,
    orderbook_change_signal,
    price_limit_signal,
    resample_candles,
)
from app.toss.service import (
    _backfill_next_daily_candle,
    _candidate_evidence_cache,
    _daily_backfill_cursor,
    _daily_backfilled_on,
    _load_candidate_evidence,
    _orderbook_imbalance,
)
from app.toss.store import TossStockStore
from app.db.repository import SQLiteRepository


def test_readonly_whitelist_blocks_non_market_paths_before_http() -> None:
    assert_allowed_path("/api/v1/prices")
    assert_allowed_path("/api/v1/stocks/005930/warnings")
    with pytest.raises(TossPathNotAllowed):
        assert_allowed_path("/api/v1/orders")
    with pytest.raises(TossPathNotAllowed):
        assert_allowed_path("/api/v1/account")


@pytest.mark.asyncio
async def test_expired_token_is_refreshed_once() -> None:
    token_calls = 0
    price_calls = 0
    token_bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls, price_calls
        if request.url.path == "/oauth2/token":
            token_calls += 1
            token_bodies.append(request.content.decode())
            return httpx.Response(200, json={"access_token": f"token-{token_calls}", "expires_in": 3600})
        price_calls += 1
        if price_calls == 1:
            return httpx.Response(401, json={"code": "expired-token"})
        return httpx.Response(200, json={"data": []})

    client = TossReadOnlyClient("id", "secret", transport=httpx.MockTransport(handler))
    try:
        assert await client.get("/api/v1/prices") == {"data": []}
        assert token_calls == 2
        assert price_calls == 2
        assert all("grant_type=client_credentials" in body and "client_id=id" in body and "client_secret=secret" in body for body in token_bodies)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_edge_blocked_has_actionable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(403, headers={"X-Request-Id": "req-1"}, json={"code": "edge-blocked"})

    client = TossReadOnlyClient("id", "secret", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(TossEdgeBlocked, match="허용 IP") as caught:
            await client.get("/api/v1/prices")
        assert caught.value.request_id == "req-1"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_maintenance_is_distinct_from_generic_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(500, headers={"X-Request-Id": "maintenance-1"}, json={"code": "maintenance"})

    client = TossReadOnlyClient("id", "secret", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(TossMaintenance, match="15분") as caught:
            await client.get("/api/v1/prices")
        assert caught.value.request_id == "maintenance-1"
    finally:
        await client.close()


def test_warning_hard_gate_excludes_risky_stock() -> None:
    assert (
        build_candidate(
            market="KR",
            symbol="000000",
            name="위험 종목",
            price=1000,
            observed_at="2026-07-18T00:00:00+00:00",
            market_rank=2,
            retail_rank=40,
            warnings=["투자위험"],
        )
        is None
    )


def test_four_hour_resample_preserves_ohlcv() -> None:
    start = datetime(2026, 7, 18, tzinfo=timezone.utc)
    rows = [
        {
            "opened_at": (start + timedelta(minutes=index)).isoformat(),
            "open": 100 + index,
            "high": 102 + index,
            "low": 99 + index,
            "close": 101 + index,
            "volume": 10,
        }
        for index in range(240)
    ]
    result = resample_candles(rows, 240)
    assert result == [
        {
            "opened_at": start.isoformat(),
            "open": 100.0,
            "high": 341.0,
            "low": 99.0,
            "close": 340.0,
            "volume": 2400.0,
        }
    ]


def test_stock_evidence_signals_remain_independent() -> None:
    candles = [{"close": 100}, {"close": 103}]
    flow = [
        {
            "result": {
                "records": [
                    {
                        "foreigner": {"buyAmount": "300", "sellAmount": "100"},
                        "institution": {"buyAmount": "200", "sellAmount": "150"},
                    }
                ]
            }
        }
    ]
    assert momentum_signal(candles)["type"] == "momentum"
    assert orderbook_change_signal(0.4, 0.55)["type"] == "orderbook_change"
    assert price_limit_signal(96, 100)["type"] == "price_limit_risk"
    assert investor_flow_signal(flow, 5)["type"] == "investor_flow"


@pytest.mark.asyncio
async def test_candidate_evidence_is_collected_and_throttled_to_fifteen_seconds(tmp_path) -> None:
    database_path = tmp_path / "evidence.db"
    SQLiteRepository(str(database_path))
    store = TossStockStore(f"sqlite:///{database_path}")
    key = ("KR", "005930")
    _candidate_evidence_cache.pop(key, None)
    _orderbook_imbalance.pop(key, None)

    class StubClient:
        def __init__(self) -> None:
            self.paths: list[str] = []

        async def get(self, path: str, *, params=None):
            self.paths.append(path)
            if path.endswith("orderbook"):
                return {"result": {"bids": [{"volume": "60"}], "asks": [{"volume": "40"}]}}
            if path.endswith("price-limits"):
                return {"result": {"upperLimitPrice": "100"}}
            if path.endswith("candles"):
                return {
                    "result": {
                        "candles": [
                            {
                                "timestamp": "2026-07-18T09:00:00+09:00",
                                "openPrice": "90",
                                "highPrice": "94",
                                "lowPrice": "89",
                                "closePrice": "93",
                                "volume": "10",
                            },
                            {
                                "timestamp": "2026-07-18T09:01:00+09:00",
                                "openPrice": "93",
                                "highPrice": "96",
                                "lowPrice": "92",
                                "closePrice": "95",
                                "volume": "20",
                            },
                        ]
                    }
                }
            return {"result": []}

    client = StubClient()
    first = await _load_candidate_evidence(client, store, "KR", "005930", "2026-07-18T00:01:00+00:00")
    second = await _load_candidate_evidence(client, store, "KR", "005930", "2026-07-18T00:01:05+00:00")
    assert first == second
    assert sorted(client.paths) == sorted(["/api/v1/orderbook", "/api/v1/trades", "/api/v1/price-limits", "/api/v1/candles", "/api/v1/candles"])
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM toss_quotes").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(DISTINCT timeframe) FROM toss_candles").fetchone()[0] == 6


@pytest.mark.asyncio
async def test_daily_backfill_rotates_cold_universe_without_duplicate_daily_calls(tmp_path) -> None:
    database_path = tmp_path / "daily.db"
    SQLiteRepository(str(database_path))
    store = TossStockStore(f"sqlite:///{database_path}")
    _daily_backfill_cursor["US"] = 0
    _daily_backfilled_on.clear()

    class StubClient:
        def __init__(self) -> None:
            self.symbols: list[str] = []

        async def get(self, path: str, *, params=None):
            assert path == "/api/v1/candles"
            self.symbols.append(params["symbol"])
            return {
                "result": {
                    "candles": [
                        {
                            "timestamp": "2026-07-18T00:00:00Z",
                            "openPrice": "100",
                            "highPrice": "103",
                            "lowPrice": "99",
                            "closePrice": "102",
                            "volume": "1000",
                        }
                    ]
                }
            }

    client = StubClient()
    assert await _backfill_next_daily_candle(client, store, "US", ["AAPL", "MSFT"], "2026-07-19T01:00:00Z") == "AAPL"
    assert await _backfill_next_daily_candle(client, store, "US", ["AAPL", "MSFT"], "2026-07-19T01:00:10Z") == "MSFT"
    assert await _backfill_next_daily_candle(client, store, "US", ["AAPL", "MSFT"], "2026-07-19T01:00:20Z") is None
    assert client.symbols == ["AAPL", "MSFT"]
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM toss_candles WHERE timeframe='1d'").fetchone()[0] == 2


def test_judgment_snapshot_deduplicates_and_records_t_plus_one(tmp_path) -> None:
    database_path = tmp_path / "toss.db"
    SQLiteRepository(str(database_path))
    store = TossStockStore(f"sqlite:///{database_path}")
    observed_at = datetime(2026, 7, 17, tzinfo=timezone.utc)
    candidate = {
        "entity_type": "stock_kr",
        "symbol": "005930",
        "price": 100.0,
        "observed_at": observed_at.isoformat(),
        "source": "Toss Securities Open API",
    }
    signal = {"type": "attention_gap", "gap": 30}
    judgment_id = store.record_judgment(candidate, signal)
    assert judgment_id is not None
    assert store.record_judgment(candidate, signal) is None
    assert store.record_due_outcomes({"005930": 110.0}, observed_at + timedelta(days=1, minutes=1)) == 1
    performance = store.performance("stock_kr")
    assert performance[0]["horizon_days"] == 1
    assert performance[0]["n"] == 1
    assert performance[0]["avg_return_pct"] == pytest.approx(10.0)
    assert performance[0]["sample_low"] is True
