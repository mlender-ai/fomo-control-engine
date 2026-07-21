import pytest
from fastapi.testclient import TestClient

from app.services import runtime as service
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
    refreshed = client.post("/api/symbols/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["catalog_status"]["count"] >= 5

    response = client.get("/api/symbols", params={"query": "btc"})
    assert response.status_code == 200
    symbols = [item["symbol"] for item in response.json()["symbols"]]
    assert "BTCUSDT" in symbols

    empty_query = client.get("/api/symbols")
    assert empty_query.status_code == 200
    assert len(empty_query.json()["symbols"]) >= 5
    assert empty_query.json()["catalog_status"]["last_error"] is None


def test_symbol_search_exposes_stock_puffs_with_asset_class(client: TestClient) -> None:
    client.post("/api/symbols/refresh")
    response = client.get("/api/symbols", params={"query": "tsla"})
    assert response.status_code == 200
    matches = response.json()["symbols"]
    tsla = next(item for item in matches if item["symbol"] == "TSLAUSDT")
    assert tsla["asset_class"] == "stock"
    assert tsla["funding_rate_interval_hours"] == 8


def test_symbol_search_matches_seeded_based_and_sox(client: TestClient) -> None:
    from app.db.models import CatalogSymbol
    from app.services import http_handlers

    http_handlers.repository.replace_symbol_catalog(
        [
            CatalogSymbol(symbol="BASEDUSDT", base_coin="BASED", quote_coin="USDT", asset_class="crypto"),
            CatalogSymbol(symbol="SOXLUSDT", base_coin="SOXL", quote_coin="USDT", asset_class="stock"),
        ]
    )

    based = client.get("/api/symbols", params={"query": "based"}).json()
    sox = client.get("/api/symbols", params={"query": "sox"}).json()

    assert [item["symbol"] for item in based["symbols"]] == ["BASEDUSDT"]
    assert [item["symbol"] for item in sox["symbols"]] == ["SOXLUSDT"]
    assert based["catalog_status"]["count"] == 2


def test_empty_catalog_reports_collection_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import scout_handlers

    monkeypatch.setattr(scout_handlers._provider(), "list_contracts", lambda: [])
    refreshed = client.post("/api/symbols/refresh").json()["catalog_status"]
    searched = client.get("/api/symbols", params={"query": "btc"}).json()

    assert refreshed["count"] == 0
    assert "빈 심볼 목록" in refreshed["last_error"]
    assert searched["symbols"] == []
    assert searched["catalog_status"]["last_error"] == refreshed["last_error"]


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


def test_telegram_scout_tracking_normalizes_adds_and_stops(client: TestClient) -> None:
    payload = service.start_scout_tracking("btc")

    assert payload["symbol"] == "BTCUSDT"
    assert payload["tracking"]["active"] is True
    assert [item["symbol"] for item in client.get("/api/watchlist").json()["items"]] == ["BTCUSDT"]
    status = service.scout_tracking_status()
    assert status["count"] == 1
    assert status["items"][0]["symbol"] == "BTCUSDT"

    stopped = service.stop_scout_tracking("btc")
    assert stopped["symbol"] == "BTCUSDT"
    assert stopped["removed"] is True
    assert client.get("/api/watchlist").json()["items"] == []


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
    assert payload["historical_backtest"]["disclaimer"].startswith("과거 통계")
    assert analysis["historical_backtest"]["source"] in {"cache", "replay", "insufficient_candles", "disabled"}
    stance = payload["analyst_briefing"]["confluence"]["stance"]
    expected = {
        "long_leaning": "상방",
        "short_leaning": "하방",
        "conflicted": "판단불가",
        "insufficient": "판단불가",
    }[stance]
    assert analysis["one_liners"]["overall_stance"] == expected


def test_scout_backtest_endpoint_returns_descriptive_stats(client: TestClient) -> None:
    response = client.get("/api/scout/BTCUSDT/backtest", params={"timeframe": "4h"})
    assert response.status_code == 200
    payload = response.json()["historical_backtest"]
    assert payload["symbol"] == "BTCUSDT"
    assert payload["disclaimer"] == "과거 통계 · 미래 보장 아님 · 수수료·슬리피지 반영(net)"
    assert isinstance(payload["active_signatures"], list)
    assert isinstance(payload["stats"], list)


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

    # 심볼 2개 스냅샷 + 알트(ETH) 1개에 대한 BTC 레짐 병기 스냅샷 1회 (WO-36 §5, 15분 캐시).
    first = client.post("/api/scout/scan", json={})
    assert first.status_code == 200
    assert first.json()["count"] == 2
    assert calls["count"] == 3

    second = client.post("/api/scout/scan", json={})
    assert second.status_code == 200
    assert calls["count"] == 3  # 캐시 히트 — 재계산 없음

    # 강제 재스캔은 심볼 캐시만 무효화한다. BTC 레짐은 자체 TTL 캐시라 재조회 안 함.
    forced = client.post("/api/scout/scan", json={"force": True})
    assert forced.status_code == 200
    assert calls["count"] == 5


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


def test_scan_tracking_view_is_union_of_active_intents_and_armed_setups(client: TestClient) -> None:
    setup_response = client.post(
        "/api/scout/BTCUSDT/setups",
        json={"trigger_price": 60_000, "label": "구조 지지", "condition": "지지 반응 확인"},
    )
    intent_response = client.post(
        "/api/scout/ETHUSDT/intents",
        json={"direction": "long", "zone_lower": 1_700, "zone_upper": 1_800},
    )
    assert setup_response.status_code == 200
    assert intent_response.status_code == 200

    payload = client.post("/api/scout/scan", json={}).json()
    tracked = {item["symbol"]: item for item in payload["tracked"]}

    assert set(tracked) == {"BTCUSDT", "ETHUSDT"}
    assert tracked["BTCUSDT"]["setup_ids"] == [setup_response.json()["setup"]["id"]]
    assert tracked["ETHUSDT"]["intent_ids"] == [intent_response.json()["intent"]["id"]]
    assert tracked["BTCUSDT"]["tracking_source"] == "manual"
    assert tracked["ETHUSDT"]["tracking_source"] == "manual"
    assert {row["symbol"] for row in payload["rows"]} == {"BTCUSDT", "ETHUSDT"}
    client.post(f"/api/scout/setups/{setup_response.json()['setup']['id']}/disarm")
    client.post(f"/api/scout/intents/{intent_response.json()['intent']['id']}/cancel")


def test_scan_tracking_view_includes_plain_watchlist_items(client: TestClient) -> None:
    added = client.post("/api/watchlist", json={"symbol": "NBISUSDT", "asset_class": "stock"})
    assert added.status_code == 200

    payload = client.post("/api/scout/scan", json={}).json()
    tracked = [item for item in payload["tracked"] if item["symbol"] == "NBISUSDT"]

    assert len(tracked) == 1
    assert tracked[0]["tracking_source"] == "manual"
    assert tracked[0]["one_line"] == "추적 조건 확인 중"


def test_watch_intent_is_idempotent_and_has_no_zone(client: TestClient) -> None:
    first = client.post("/api/scout/SOXLUSDT/intents", json={"kind": "watch", "timeframe": "4h"})
    second = client.post("/api/scout/SOXLUSDT/intents", json={"kind": "watch", "timeframe": "4h"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["intent"]["id"] == second.json()["intent"]["id"]
    assert first.json()["intent"]["kind"] == "watch"
    assert first.json()["intent"]["direction"] is None
    assert first.json()["intent"]["zone_lower"] is None
    assert second.json()["created"] is False


def test_stock_scout_is_read_only_and_honest_when_unconfigured(client: TestClient, monkeypatch) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "toss_stock_scout_enabled", False)
    monkeypatch.setattr(settings, "toss_client_id", "")
    monkeypatch.setattr(settings, "toss_client_secret", "")

    response = client.get("/api/scout/stocks/KR")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "credentials_required"
    assert payload["read_only_label"] == "Toss 데이터 · 주문 실행 없음"
    assert payload["groups"] == {}
