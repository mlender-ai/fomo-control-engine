from datetime import timedelta

from app.api.deps import configure_runtime
from app.db.models import MarketSnapshot, WatchlistItem, utc_now
from app.db.repository import MemoryRepository
from app.exchange.bitget.client import BitgetClient
from app.exchange.bitget.provider import BitgetMarketDataProvider
from app.exchange.bitget.schemas import BitgetPosition
from app.exchange.mock import MockMarketDataProvider


class SyncClosingBitgetProvider(BitgetMarketDataProvider):
    def __init__(self) -> None:
        super().__init__(BitgetClient(api_key="key", api_secret="secret", passphrase="passphrase"))
        self.positions: list[BitgetPosition] = []
        self.snapshots = MockMarketDataProvider()

    def get_snapshot(self, symbol: str, timeframe: str = "4h") -> MarketSnapshot:
        return self.snapshots.get_snapshot(symbol, timeframe)

    def get_positions(self) -> list[BitgetPosition]:
        return self.positions

    def get_trade_flow(self, symbol: str, timeframe: str, candles: list) -> dict | None:
        return None


def test_live_position_analysis_insight_memo_and_exit_flow(client) -> None:
    report_response = client.post("/api/reports", json={"symbol": "BTCUSDT", "timeframe": "4h"})
    report = report_response.json()

    position_response = client.post(
        "/api/positions",
        json={
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": report["price"],
            "quantity": 0.02,
            "leverage": 2,
            "entry_report_id": report["id"],
            "memo": "initial memo",
            "entry_memo": "상승 구조 지지 확인 후 진입",
            "planned_stop_price": report["price"] * 0.96,
            "planned_take_profit_price": report["price"] * 1.08,
            "thesis_text": "Higher low가 유지되어야 한다.",
        },
    )
    assert position_response.status_code == 200
    position = position_response.json()

    live_response = client.get("/api/live/positions")
    assert live_response.status_code == 200
    live_payload = live_response.json()
    assert live_payload["open_count"] == 1
    assert live_payload["positions"][0]["state"]["position"]["symbol"] == "BTCUSDT"

    analyze_response = client.post(f"/api/live/positions/{position['id']}/analyze")
    assert analyze_response.status_code == 200
    analyzed = analyze_response.json()
    assert analyzed["latest_snapshot"]["position_id"] == position["id"]
    assert 0 <= analyzed["state"]["health_score"] <= 100

    insight_response = client.post(f"/api/live/positions/{position['id']}/insight")
    assert insight_response.status_code == 200
    insight_payload = insight_response.json()
    assert insight_payload["latest_insight"]["position_id"] == position["id"]
    assert insight_payload["latest_insight"]["insight_source"] == "template"
    assert insight_payload["latest_insight"]["action_plan"]["invalidation"]["price"] == report["price"] * 0.96
    assert "단정하지 않습니다" not in insight_payload["latest_insight"]["insight_text"]

    memo_response = client.patch(
        f"/api/live/positions/{position['id']}/memo",
        json={
            "memo": "updated memo",
            "entry_memo": "업데이트된 진입 논리",
            "planned_stop_price": report["price"] * 0.95,
            "planned_take_profit_price": report["price"] * 1.1,
            "thesis_text": "지지 이탈 시 논리 약화",
        },
    )
    assert memo_response.status_code == 200
    assert memo_response.json()["memo"] == "updated memo"

    events_response = client.get(f"/api/live/positions/{position['id']}/events")
    assert events_response.status_code == 200
    event_types = {event["event_type"] for event in events_response.json()["events"]}
    assert {"ai_insight", "memo_updated"}.issubset(event_types)

    exit_response = client.post(
        f"/api/live/positions/{position['id']}/record-exit",
        json={
            "exit_price": report["price"] * 1.03,
            "exit_reason": "테스트 수동 이탈 기록",
            "memo": "거래소 주문이 아닌 내부 기록",
        },
    )
    assert exit_response.status_code == 200
    trade = exit_response.json()
    assert trade["memo"] == "거래소 주문이 아닌 내부 기록"
    assert trade["pnl_percent"] > 0

    trade_response = client.get(f"/api/trades/{trade['id']}")
    assert trade_response.status_code == 200
    assert trade_response.json()["id"] == trade["id"]

    timeline_response = client.get(f"/api/trades/{trade['id']}/timeline")
    assert timeline_response.status_code == 200
    timeline = timeline_response.json()
    assert timeline["trade"]["id"] == trade["id"]
    assert len(timeline["snapshots"]) >= 1
    assert any(event["event_type"] == "exit_recorded" for event in timeline["events"])


def test_live_position_chart_analysis_contract(client) -> None:
    report = client.post("/api/reports", json={"symbol": "BTCUSDT", "timeframe": "4h"}).json()
    position_response = client.post(
        "/api/positions",
        json={
            "symbol": "BTCUSDT",
            "direction": "long",
            "entry_price": report["price"],
            "quantity": 0.02,
            "leverage": 3,
            "planned_stop_price": report["price"] * 0.96,
        },
    )
    assert position_response.status_code == 200
    position = position_response.json()

    response = client.get(f"/api/live/positions/{position['id']}/chart-analysis")
    assert response.status_code == 200
    payload = response.json()
    assert payload["position_id"] == position["id"]
    assert payload["timeframe"] == "4h"
    assert len(payload["candles"]) >= 100
    assert payload["candles"] == sorted(payload["candles"], key=lambda candle: candle["time"])
    assert payload["price_levels"]["entry"] == position["entry_price"]
    assert payload["price_levels"]["mark"] > 0
    assert isinstance(payload["price_levels"]["support"], list)
    assert isinstance(payload["price_levels"]["resistance"], list)
    assert payload["volume_profile"]["method"] == "ohlcv_estimated"
    assert len(payload["volume_profile"]["bins"]) > 0
    assert payload["volume_profile"]["poc_price"] > 0
    assert all("buy_volume" not in item and "sell_volume" not in item for item in payload["volume_profile"]["bins"] if item["method"] == "ohlcv_estimated")
    assert payload["trade_flow"]["method"] == "data_unavailable"
    assert payload["volume_xray"]["relative_volume"] > 0
    assert payload["volume_xray"]["method"] == "data_unavailable"
    assert isinstance(payload["volume_xray"]["notes"], list)
    assert isinstance(payload["wyckoff_markers"], list)


def test_bitget_sync_auto_records_missing_position_exit(client) -> None:
    repo = MemoryRepository()
    provider = SyncClosingBitgetProvider()
    provider.positions = [
        BitgetPosition(
            symbol="PLTRUSDT",
            hold_side="long",
            margin_coin="USDT",
            total=2,
            leverage=3,
            open_price_avg=100,
            mark_price=112,
            unrealized_pl=24,
            margin_size=200,
            created_at=utc_now() - timedelta(hours=4),
        )
    ]
    configure_runtime(repo=repo, provider=provider)

    first_sync = client.post("/api/live/positions/sync")
    assert first_sync.status_code == 200
    assert first_sync.json()["created"] == 1

    provider.positions = []
    # WO-44 Part C: 1틱 부재는 가짜 종료(sync 간극·거래소 일시 오류)일 수 있어 종료 확정 금지.
    second_sync = client.post("/api/live/positions/sync")
    assert second_sync.status_code == 200
    first_miss = second_sync.json()
    assert first_miss["missing_from_exchange"] == 1
    assert first_miss["auto_closed"] == 0
    positions_after_first_miss = client.get("/api/positions").json()
    assert positions_after_first_miss[0]["status"] == "open"

    # 2틱 연속 부재 → 종료 확정 + 종료 기록.
    third_sync = client.post("/api/live/positions/sync")
    assert third_sync.status_code == 200
    sync_payload = third_sync.json()
    assert sync_payload["missing_from_exchange"] == 1
    assert sync_payload["auto_closed"] == 1
    assert sync_payload["positions"] == []
    assert sync_payload["open_count"] == 0
    assert sync_payload["closed_positions"][0]["position"]["symbol"] == "PLTRUSDT"

    positions = client.get("/api/positions").json()
    assert positions[0]["symbol"] == "PLTRUSDT"
    assert positions[0]["status"] == "closed"

    trades = client.get("/api/trades").json()
    assert len(trades) == 1
    assert trades[0]["symbol"] == "PLTRUSDT"
    assert trades[0]["exit_price"] == 112
    assert "Bitget read-only sync" in trades[0]["exit_reason"]

    live = client.get("/api/live/positions").json()
    assert live["positions"] == []
    assert live["open_count"] == 0


def test_bitget_sync_clears_scout_tracking_for_open_position(client) -> None:
    repo = MemoryRepository()
    provider = SyncClosingBitgetProvider()
    provider.positions = [
        BitgetPosition(
            symbol="PLTRUSDT",
            hold_side="long",
            margin_coin="USDT",
            total=2,
            leverage=3,
            open_price_avg=100,
            mark_price=112,
            unrealized_pl=24,
            margin_size=200,
            created_at=utc_now() - timedelta(hours=4),
        )
    ]
    repo.upsert_watchlist_item(WatchlistItem(symbol="PLTRUSDT", asset_class="stock"))
    configure_runtime(repo=repo, provider=provider)

    sync = client.post("/api/live/positions/sync")

    assert sync.status_code == 200
    assert sync.json()["created"] == 1
    assert sync.json()["scout_tracking_removed"] == ["PLTRUSDT"]
    assert repo.list_watchlist() == []
