import pytest
from fastapi.testclient import TestClient

from app.api.scout_routes import reset_scout_cache
from app.main import app


@pytest.fixture(autouse=True)
def clear_scout_cache():
    reset_scout_cache()
    yield
    reset_scout_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_symbol_search_builds_catalog_from_provider(client: TestClient) -> None:
    response = client.get("/api/symbols", params={"query": "btc"})
    assert response.status_code == 200
    symbols = [item["symbol"] for item in response.json()["symbols"]]
    assert "BTCUSDT" in symbols

    empty_query = client.get("/api/symbols")
    assert empty_query.status_code == 200
    assert len(empty_query.json()["symbols"]) >= 5


def test_watchlist_crud(client: TestClient) -> None:
    created = client.post("/api/watchlist", json={"symbol": "ethusdt", "note": "테스트", "default_timeframe": "1h"})
    assert created.status_code == 200
    assert created.json()["item"]["symbol"] == "ETHUSDT"

    listed = client.get("/api/watchlist")
    assert [item["symbol"] for item in listed.json()["items"]] == ["ETHUSDT"]

    removed = client.delete("/api/watchlist/ETHUSDT")
    assert removed.status_code == 200
    assert client.get("/api/watchlist").json()["items"] == []

    missing = client.delete("/api/watchlist/ETHUSDT")
    assert missing.status_code == 404


def test_scout_analysis_returns_scenarios_without_position(client: TestClient) -> None:
    response = client.get("/api/scout/BTCUSDT/analysis", params={"timeframe": "4h"})
    assert response.status_code == 200
    payload = response.json()
    analysis = payload["analysis"]
    assert analysis["position_id"] is None
    assert analysis["direction"] is None
    assert set(analysis["scenarios"].keys()) == {"long", "short"}
    assert payload["summary"]["long_score"] >= 0
    assert payload["summary"]["short_score"] >= 0


def test_scan_uses_cache_within_ttl(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import routes as runtime

    client.post("/api/watchlist", json={"symbol": "BTCUSDT"})
    client.post("/api/watchlist", json={"symbol": "ETHUSDT"})

    calls = {"count": 0}
    original = runtime.market_provider.get_snapshot

    def counting_get_snapshot(symbol: str, timeframe: str = "4h"):
        calls["count"] += 1
        return original(symbol, timeframe)

    monkeypatch.setattr(runtime.market_provider, "get_snapshot", counting_get_snapshot)

    first = client.post("/api/scout/scan", json={})
    assert first.status_code == 200
    assert first.json()["count"] == 2
    assert calls["count"] == 2

    second = client.post("/api/scout/scan", json={})
    assert second.status_code == 200
    assert calls["count"] == 2  # 캐시 히트 — 재계산 없음

    forced = client.post("/api/scout/scan", json={"force": True})
    assert forced.status_code == 200
    assert calls["count"] == 4  # 강제 재스캔 — 캐시 미스


def test_scan_rows_sorted_by_setup_proximity(client: TestClient) -> None:
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        client.post("/api/watchlist", json={"symbol": symbol})
    response = client.post("/api/scout/scan", json={})
    rows = response.json()["rows"]
    proximities = [row.get("setup_proximity_pct") for row in rows if row.get("setup_proximity_pct") is not None]
    assert proximities == sorted(proximities)
    for row in rows:
        assert "long_score" in row and "short_score" in row
        assert "volume_state" in row and "as_of" in row
