import pytest
from fastapi.testclient import TestClient

from app.services.scout_handlers import reset_scout_cache
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


def test_symbol_search_exposes_stock_puffs_with_asset_class(client: TestClient) -> None:
    response = client.get("/api/symbols", params={"query": "tsla"})
    assert response.status_code == 200
    matches = response.json()["symbols"]
    tsla = next(item for item in matches if item["symbol"] == "TSLAUSDT")
    assert tsla["asset_class"] == "stock"
    assert tsla["funding_rate_interval_hours"] == 8


def test_watchlist_crud(client: TestClient) -> None:
    created = client.post(
        "/api/watchlist",
        json={"symbol": "ethusdt", "note": "테스트", "default_timeframe": "1h"},
    )
    assert created.status_code == 200
    assert created.json()["item"]["symbol"] == "ETHUSDT"
    assert created.json()["item"]["asset_class"] == "crypto"

    stock = client.post("/api/watchlist", json={"symbol": "tslausdt"})
    assert stock.status_code == 200
    assert stock.json()["item"]["asset_class"] == "stock"

    listed = client.get("/api/watchlist")
    assert [item["symbol"] for item in listed.json()["items"]] == ["TSLAUSDT", "ETHUSDT"]

    removed = client.delete("/api/watchlist/ETHUSDT")
    assert removed.status_code == 200
    assert [item["symbol"] for item in client.get("/api/watchlist").json()["items"]] == ["TSLAUSDT"]
    client.delete("/api/watchlist/TSLAUSDT")

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


def test_stock_scout_analysis_includes_session_context(client: TestClient) -> None:
    response = client.get("/api/scout/TSLAUSDT/analysis", params={"timeframe": "4h"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis"]["asset_class"] == "stock"
    assert payload["analysis"]["session"]["label"] in {"본장", "확장 세션", "휴장"}
    assert payload["analysis"]["data_quality"]["session_excluded_candles"] >= 0


def test_scan_uses_cache_within_ttl(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import http_handlers as runtime

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
    for symbol in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "TSLAUSDT"):
        client.post("/api/watchlist", json={"symbol": symbol})
    response = client.post("/api/scout/scan", json={})
    rows = response.json()["rows"]
    proximities = [row.get("setup_proximity_pct") for row in rows if row.get("setup_proximity_pct") is not None]
    assert proximities == sorted(proximities)
    for row in rows:
        assert "long_score" in row and "short_score" in row
        assert "volume_state" in row and "as_of" in row
        assert row["asset_class"] in {"crypto", "stock"}


def test_entry_intent_api_creates_single_price_zone_and_cancels(client: TestClient) -> None:
    created = client.post(
        "/api/scout/TSLAUSDT/intents",
        json={
            "direction": "long",
            "price": 250,
            "conditions": ["price_in_zone", "sweep_confirmed"],
            "tolerance": "tight",
            "note": "테스트 의도",
        },
    )
    assert created.status_code == 200
    intent = created.json()["intent"]
    assert intent["symbol"] == "TSLAUSDT"
    assert intent["direction"] == "long"
    assert round(intent["zone_lower"], 2) == 248.75
    assert round(intent["zone_upper"], 2) == 251.25
    assert intent["conditions"] == ["price_in_zone", "sweep_confirmed"]
    assert intent["tolerance_pct"] == 0.5
    assert intent["expires_at"]

    listed = client.get("/api/scout/intents", params={"symbol": "TSLAUSDT", "status": "active"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["intents"]] == [intent["id"]]

    cancelled = client.post(f"/api/scout/intents/{intent['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["intent"]["status"] == "cancelled"


def test_entry_intent_api_enforces_per_symbol_cap(client: TestClient) -> None:
    for index in range(3):
        response = client.post(
            "/api/scout/TSLAUSDT/intents",
            json={
                "direction": "long",
                "zone_lower": 240 + index,
                "zone_upper": 241 + index,
            },
        )
        assert response.status_code == 200

    rejected = client.post(
        "/api/scout/TSLAUSDT/intents",
        json={
            "direction": "long",
            "zone_lower": 260,
            "zone_upper": 261,
        },
    )

    assert rejected.status_code == 409
    assert "심볼당 활성 의도" in rejected.json()["detail"]
