from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

import pytest

from app.analyst.confluence import build_confluence
from app.analyst.signature_registry import record_transition
from app.api.deps import configure_runtime
from app.backtest.candidate_scoring import CANDIDATE_SENTINEL_POSITION_ID, score_candidates
from app.core.config import Settings
from app.db.models import BacktestStat, JudgmentLedgerEntry, JudgmentScore, WhaleEvent, WhaleWallet, utc_now
from app.db.repository import MemoryRepository, create_repository
from app.onchain.hyperliquid.collector import collect_whale_positions, event_from_fill, whale_signature_key
from app.onchain.hyperliquid.leaderboard import (
    discover_leaderboard_wallets,
    select_candidates,
    select_directional_cohort,
)
from app.onchain.service import add_whale_wallet, chart_onchain_context, whale_dashboard
from app.notify.alerts import AlertEngine
from app.notify.state import NotificationState
from app.exchange.mock import MockMarketDataProvider

ADDRESS = "0x1111111111111111111111111111111111111111"


class FakeSender:
    enabled = True

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_to_all(self, text: str, *, reply_markup=None) -> int:
        self.messages.append(text)
        return 1


class FakeHyperliquidClient:
    def clearinghouse_state(self, address: str) -> dict:
        assert address == ADDRESS
        return {
            "assetPositions": [
                {
                    "position": {
                        "coin": "BTC",
                        "szi": "2",
                        "entryPx": "60000",
                        "positionValue": "126000",
                        "unrealizedPnl": "6000",
                        "liquidationPx": "51000",
                    }
                }
            ]
        }

    def user_fills_by_time(self, address: str, *, start_time_ms: int, end_time_ms: int) -> list[dict]:
        now_ms = int(utc_now().timestamp() * 1000)
        return [
            {"coin": "BTC", "px": "63000", "sz": "2", "side": "B", "startPosition": "0", "time": now_ms - 1000, "tid": 1, "dir": "Open Long"},
            {"coin": "ETH", "px": "3000", "sz": "0.1", "side": "B", "startPosition": "0", "time": now_ms, "tid": 2, "dir": "Open Long"},
        ]


class FakeLeaderboardClient:
    def leaderboard(self) -> dict:
        return {
            "leaderboardRows": [
                {
                    "ethAddress": ADDRESS,
                    "accountValue": "5000000",
                    "displayName": "Directional One",
                    "windowPerformances": [["month", {"pnl": "900000", "roi": "0.18", "vlm": "80000000"}]],
                },
                {
                    "ethAddress": "0x2222222222222222222222222222222222222222",
                    "accountValue": "3000000",
                    "displayName": None,
                    "windowPerformances": [["month", {"pnl": "500000", "roi": "0.12", "vlm": "60000000"}]],
                },
                {
                    "ethAddress": "0x3333333333333333333333333333333333333333",
                    "accountValue": "4000000",
                    "windowPerformances": [["month", {"pnl": "-1", "roi": "-0.1", "vlm": "50000000"}]],
                },
            ]
        }


class FakeDirectionalPositionClient:
    positions = {
        ADDRESS: ("ETH", "2", "600000"),
        "0x2222222222222222222222222222222222222222": ("BTC", "3", "500000"),
        "0x3333333333333333333333333333333333333333": ("ETH", "-4", "800000"),
        "0x4444444444444444444444444444444444444444": ("BTC", "-5", "900000"),
    }

    def clearinghouse_state(self, address: str) -> dict:
        row = self.positions.get(address)
        if row is None:
            return {"assetPositions": []}
        coin, size, value = row
        return {"assetPositions": [{"position": {"coin": coin, "szi": size, "positionValue": value, "entryPx": "100"}}]}


def test_whale_runtime_and_alert_batch_defaults_are_30s_and_3m() -> None:
    settings = Settings()

    assert settings.hyperliquid_whale_poll_interval_seconds == 30
    assert settings.hyperliquid_whale_alert_batch_window_seconds == 180
    with pytest.raises(ValueError):
        Settings(hyperliquid_whale_poll_interval_seconds=29)


def test_fill_classification_open_close_and_flip() -> None:
    wallet = WhaleWallet(address=ADDRESS, label="테스트 고래")
    now_ms = int(utc_now().timestamp() * 1000)
    opened = event_from_fill(wallet, {"coin": "BTC", "px": "10", "sz": "2", "side": "B", "startPosition": "0", "time": now_ms, "tid": 1})
    closed = event_from_fill(wallet, {"coin": "BTC", "px": "10", "sz": "2", "side": "A", "startPosition": "2", "time": now_ms, "tid": 2})
    flipped = event_from_fill(wallet, {"coin": "BTC", "px": "10", "sz": "3", "side": "A", "startPosition": "2", "time": now_ms, "tid": 3})

    assert opened and (opened.event, opened.side) == ("open", "long")
    assert closed and (closed.event, closed.side) == ("close", "long")
    assert flipped and (flipped.event, flipped.side) == ("flip", "short")


def test_collector_filters_noise_deduplicates_and_records_candidate() -> None:
    repo = MemoryRepository()
    settings = Settings(hyperliquid_whale_min_size_usd=100_000)
    repo.upsert_whale_wallet(WhaleWallet(address=ADDRESS, label="테스트 고래", last_polled_at=utc_now() - timedelta(minutes=3)))

    first = collect_whale_positions(repo, settings, FakeHyperliquidClient())
    second = collect_whale_positions(repo, settings, FakeHyperliquidClient())

    assert first["created"] == 1
    assert second["created"] == 0
    assert repo.list_whale_events()[0].symbol == "BTCUSDT"
    assert repo.list_whale_position_states(ADDRESS)[0]["side"] == "long"
    judgments = repo.list_judgments(next(iter(repo.judgments)))
    assert judgments[0].claim["signature_key"] == whale_signature_key(ADDRESS)
    assert judgments[0].claim["detected_after_fill"] is True


def test_wallet_limit_is_enforced_without_auto_registration() -> None:
    repo = MemoryRepository()
    settings = Settings(hyperliquid_whale_max_wallets=1)
    add_whale_wallet(repo, settings, ADDRESS, "A")

    try:
        add_whale_wallet(repo, settings, "0x2222222222222222222222222222222222222222", "B")
    except ValueError as exc:
        assert "최대 1개" in str(exc)
    else:
        raise AssertionError("wallet cap must reject the second address")


def test_leaderboard_discovery_selects_profitable_active_whales_and_preserves_manual_wallets() -> None:
    repo = MemoryRepository()
    manual = "0x9999999999999999999999999999999999999999"
    repo.upsert_whale_wallet(WhaleWallet(address=manual, label="내 지정", source="manual"))
    settings = Settings(hyperliquid_whale_max_wallets=2)

    result = discover_leaderboard_wallets(repo, settings, FakeLeaderboardClient())

    active = repo.list_whale_wallets(active=True, limit=10)
    assert result["rows_scanned"] == 3
    assert result["eligible_count"] == 2
    assert result["selected_count"] == 1
    assert {wallet.address for wallet in active} == {manual, ADDRESS}
    discovered = repo.get_whale_wallet(ADDRESS)
    assert discovered is not None
    assert discovered.source == "discovery"
    assert discovered.label == "Directional One"
    assert discovered.payload["discovery"]["month_roi"] == pytest.approx(0.18)


def test_leaderboard_filter_rejects_hft_turnover_and_loss_rows() -> None:
    rows = FakeLeaderboardClient().leaderboard()["leaderboardRows"] + [
        {
            "ethAddress": "0x4444444444444444444444444444444444444444",
            "accountValue": "1000000",
            "windowPerformances": [["month", {"pnl": "1000000", "roi": "1", "vlm": "500000000"}]],
        }
    ]
    selected = select_candidates(
        rows,
        {
            "min_account_usd": 1_000_000,
            "min_month_pnl_usd": 100_000,
            "min_month_roi": 0.02,
            "min_month_volume_usd": 10_000_000,
            "max_turnover": 250,
        },
    )
    assert [item["address"] for item in selected] == [ADDRESS, "0x2222222222222222222222222222222222222222"]


def test_directional_discovery_reserves_btc_and_eth_long_short_coverage() -> None:
    rows = []
    for index, address in enumerate(FakeDirectionalPositionClient.positions, start=1):
        rows.append(
            {
                "ethAddress": address,
                "accountValue": "5000000",
                "windowPerformances": [
                    ["week", {"pnl": str(100_000 + index), "roi": "0.04", "vlm": "20000000"}],
                    ["month", {"pnl": str(900_000 - index), "roi": "0.18", "vlm": "80000000"}],
                    ["allTime", {"pnl": str(2_000_000 + index), "roi": "0.8", "vlm": "200000000"}],
                ],
            }
        )

    class Leaderboard:
        def leaderboard(self) -> dict:
            return {"leaderboardRows": rows}

    repo = MemoryRepository()
    settings = Settings(
        hyperliquid_whale_max_wallets=4,
        hyperliquid_whale_directional_slots=4,
        hyperliquid_whale_discovery_scan_limit=10,
    )

    result = discover_leaderboard_wallets(repo, settings, Leaderboard(), FakeDirectionalPositionClient())

    assert result["position_scan"]["scanned_count"] == 4
    assert result["position_scan"]["active_focus_count"] == 4
    assert result["selected_count"] == 4
    assert result["selected_coverage"]["BTC"]["long_wallets"] == 1
    assert result["selected_coverage"]["BTC"]["short_wallets"] == 1
    assert result["selected_coverage"]["ETH"]["long_wallets"] == 1
    assert result["selected_coverage"]["ETH"]["short_wallets"] == 1
    assert {item["selection_reason"] for item in result["selected"]} == {
        "coverage:BTC:long",
        "coverage:BTC:short",
        "coverage:ETH:long",
        "coverage:ETH:short",
    }
    assert result["selected"][0]["week_pnl_usd"] > 0
    assert result["selected"][0]["all_time_pnl_usd"] > 0


def test_directional_cohort_falls_back_to_quality_when_positions_are_unavailable() -> None:
    candidates = [
        {"address": ADDRESS, "quality_score": 2, "month_pnl_usd": 2, "focus_positions": []},
        {"address": "0x2222222222222222222222222222222222222222", "quality_score": 1, "month_pnl_usd": 1, "focus_positions": []},
    ]

    selected = select_directional_cohort(candidates, 2, directional_slots=2, focus_symbols=["BTC", "ETH"])

    assert [item["address"] for item in selected] == [item["address"] for item in candidates]
    assert all(item["selection_reason"] == "quality" for item in selected)


def test_directional_cohort_prefers_material_exposure_within_eligible_pool() -> None:
    smaller = {
        "address": ADDRESS,
        "quality_score": 99,
        "focus_positions": [{"coin": "ETH", "side": "short", "size_usd": 200_000}],
    }
    material = {
        "address": "0x2222222222222222222222222222222222222222",
        "quality_score": 80,
        "focus_positions": [{"coin": "ETH", "side": "short", "size_usd": 20_000_000}],
    }

    selected = select_directional_cohort([smaller, material], 1, directional_slots=1, focus_symbols=["ETH"])

    assert selected[0]["address"] == material["address"]
    assert selected[0]["selection_reason"] == "coverage:ETH:short"


def test_chart_markers_anchor_only_to_closed_candles_and_aggregate() -> None:
    repo = MemoryRepository()
    start = utc_now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=12)
    for index in range(2):
        repo.add_whale_event(
            WhaleEvent(
                wallet_address=ADDRESS if index == 0 else "0x2222222222222222222222222222222222222222",
                wallet_label=f"고래 {index + 1}",
                coin="BTC",
                symbol="BTCUSDT",
                side="long",
                event="open",
                size=2,
                size_usd=2_000_000 + index,
                entry_px=63000,
                event_at=start + timedelta(hours=1, minutes=index),
            )
        )
    candles = [{"time": int((start + timedelta(hours=hours)).timestamp())} for hours in (0, 4, 8)]

    context = chart_onchain_context(repo, "BTCUSDT", "4h", candles)

    assert len(context["markers"]) == 1
    assert context["markers"][0]["count"] == 2
    assert context["markers"][0]["time"] == candles[0]["time"]
    assert context["markers"][0]["event_time"] == int((start + timedelta(hours=1, minutes=1)).timestamp())
    assert context["markers"][0]["live"] is False
    assert context["markers"][0]["emphasized"] is False
    assert context["validated_evidence"] == []


def test_chart_marks_confirmed_fill_in_open_window_as_live(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = MemoryRepository()
    fixed_now = datetime(2026, 7, 21, 12, 30, tzinfo=timezone.utc)
    monkeypatch.setattr("app.onchain.service.utc_now", lambda: fixed_now)
    event_at = datetime(2026, 7, 21, 12, 15, tzinfo=timezone.utc)
    repo.add_whale_event(
        WhaleEvent(
            wallet_address=ADDRESS,
            wallet_label="실시간 고래",
            coin="BTC",
            symbol="BTCUSDT",
            side="short",
            event="open",
            size=2,
            size_usd=250_000,
            entry_px=64_321,
            event_at=event_at,
        )
    )
    candles = [{"time": int(datetime(2026, 7, 21, hour, tzinfo=timezone.utc).timestamp())} for hour in (4, 8, 12)]

    context = chart_onchain_context(repo, "BTCUSDT", "4h", candles)

    assert len(context["markers"]) == 1
    marker = context["markers"][0]
    assert marker["time"] == candles[1]["time"]
    assert marker["event_time"] == int(event_at.timestamp())
    assert marker["price"] == 64_321
    assert marker["side"] == "short"
    assert marker["live"] is True


def test_chart_keeps_latest_eight_event_groups_not_largest_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = MemoryRepository()
    fixed_now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.onchain.service.utc_now", lambda: fixed_now)
    start = datetime(2026, 7, 21, 0, 0, tzinfo=timezone.utc)
    candles = [{"time": int((start + timedelta(hours=index)).timestamp())} for index in range(10)]
    for index in range(9):
        repo.add_whale_event(
            WhaleEvent(
                wallet_address=ADDRESS,
                wallet_label="최근순 고래",
                coin="BTC",
                symbol="BTCUSDT",
                side="long" if index % 2 == 0 else "short",
                event="open",
                size=1,
                size_usd=9_000_000 if index == 0 else 100_000 + index,
                entry_px=60_000 + index,
                event_at=start + timedelta(hours=index, minutes=10),
            )
        )

    markers = chart_onchain_context(repo, "BTCUSDT", "1h", candles)["markers"]

    assert len(markers) == 8
    assert markers[0]["event_time"] == int((start + timedelta(hours=1, minutes=10)).timestamp())
    assert markers[-1]["event_time"] == int((start + timedelta(hours=8, minutes=10)).timestamp())
    assert all(marker["size_usd"] < 9_000_000 for marker in markers)


def test_whale_dashboard_reports_current_exposure_and_signed_flow() -> None:
    repo = MemoryRepository()
    repo.upsert_whale_wallet(
        WhaleWallet(
            address=ADDRESS,
            label="테스트 고래",
            source="discovery",
            payload={"discovery": {"leaderboard_rank": 250, "selection_rank": 1}},
        )
    )
    repo.upsert_whale_position_state(
        ADDRESS,
        "BTC",
        {
            "wallet_address": ADDRESS,
            "wallet_label": "테스트 고래",
            "coin": "BTC",
            "symbol": "BTCUSDT",
            "side": "long",
            "size_usd": 2_000_000,
            "entry_px": 60_000,
            "as_of": utc_now().isoformat(),
        },
    )
    repo.add_whale_event(
        WhaleEvent(
            wallet_address=ADDRESS,
            wallet_label="테스트 고래",
            coin="ETH",
            symbol="ETHUSDT",
            side="short",
            event="open",
            size=200,
            size_usd=500_000,
            entry_px=2_500,
            event_at=utc_now(),
        )
    )

    dashboard = whale_dashboard(repo, Settings())

    assert dashboard["flow"]["current_long_usd"] == 2_000_000
    assert dashboard["flow"]["current_short_usd"] == 0
    assert dashboard["flow"]["flow_24h_usd"] == -500_000
    assert dashboard["flow"]["event_count_24h"] == 1
    assert dashboard["flow"]["symbols"][0]["symbol"] == "BTCUSDT"
    assert dashboard["symbol_activity"]["BTCUSDT"]["long_usd"] == 2_000_000
    assert dashboard["symbol_activity"]["BTCUSDT"]["long_wallet_count"] == 1
    assert dashboard["symbol_activity"]["BTCUSDT"]["positions"][0]["wallet_address"] == ADDRESS
    assert dashboard["symbol_activity"]["BTCUSDT"]["positions"][0]["leaderboard_rank"] == 250
    assert dashboard["symbol_activity"]["BTCUSDT"]["positions"][0]["selection_rank"] == 1
    assert dashboard["symbol_activity"]["ETHUSDT"]["recent_events"][0]["side"] == "short"
    assert dashboard["symbol_activity"]["ETHUSDT"]["recent_events"][0]["event"] == "open"


def test_whale_dashboard_rate_budget_covers_configured_wallet_capacity() -> None:
    dashboard = whale_dashboard(
        MemoryRepository(),
        Settings(hyperliquid_whale_poll_interval_seconds=30, hyperliquid_whale_max_wallets=20),
    )

    assert dashboard["rate_budget"]["poll_interval_seconds"] == 30
    assert dashboard["rate_budget"]["estimated_max_weight_per_minute"] == 880
    assert dashboard["rate_budget"]["within_official_budget"] is True


def test_whale_dashboard_compacts_bursts_balances_instruments_and_preserves_flip_meaning() -> None:
    repo = MemoryRepository()
    repo.upsert_whale_wallet(WhaleWallet(address=ADDRESS, label="리더보드 고래 #151", source="discovery"))
    now = utc_now()
    for index in range(4):
        repo.add_whale_event(
            WhaleEvent(
                wallet_address=ADDRESS,
                wallet_label="리더보드 고래 #151",
                coin="XYZ:SNDK",
                symbol="",
                side="short",
                event="increase",
                size=1,
                size_usd=100_000 + index,
                entry_px=210 + index,
                event_at=now - timedelta(seconds=index * 5),
            )
        )
    repo.add_whale_event(
        WhaleEvent(
            wallet_address=ADDRESS,
            wallet_label="리더보드 고래 #151",
            coin="BTC",
            symbol="BTCUSDT",
            side="short",
            event="flip",
            size=3,
            size_usd=208_000,
            entry_px=65_651,
            event_at=now - timedelta(minutes=2),
        )
    )
    repo.add_whale_event(
        WhaleEvent(
            wallet_address=ADDRESS,
            wallet_label="리더보드 고래 #151",
            coin="BTC",
            symbol="BTCUSDT",
            side="long",
            event="flip",
            size=2,
            size_usd=142_000,
            entry_px=64_005,
            event_at=now - timedelta(minutes=3),
        )
    )

    dashboard = whale_dashboard(repo, Settings())

    assert len(repo.list_whale_events(limit=20)) == 6
    assert [event["instrument"] for event in dashboard["recent_events"][:2]] == ["XYZ:SNDK", "BTCUSDT"]
    sndk = next(event for event in dashboard["recent_events"] if event["instrument"] == "XYZ:SNDK")
    assert sndk["fill_count"] == 4
    assert sndk["size_usd"] == 400_006
    btc_actions = {event["action_label"] for event in dashboard["recent_events"] if event["instrument"] == "BTCUSDT"}
    assert btc_actions == {"숏→롱 전환", "롱→숏 전환"}
    assert dashboard["flow_by_instrument"]["XYZ:SNDK"]["event_count_24h"] == 4
    assert dashboard["flow_by_instrument"]["BTCUSDT"]["event_count_24h"] == 2
    assert dashboard["recent_events_by_instrument"]["XYZ:SNDK"][0]["fill_count"] == 4


def test_instrument_history_is_not_truncated_by_global_tape_limit() -> None:
    repo = MemoryRepository()
    repo.upsert_whale_wallet(WhaleWallet(address=ADDRESS, label="테스트 고래"))
    now = utc_now()
    for index in range(22):
        coin = f"XYZ:T{index:02d}"
        repo.add_whale_event(
            WhaleEvent(
                wallet_address=ADDRESS,
                wallet_label="테스트 고래",
                coin=coin,
                symbol="",
                side="long",
                event="open",
                size=1,
                size_usd=100_000 + index,
                entry_px=100 + index,
                event_at=now - timedelta(minutes=index),
            )
        )

    dashboard = whale_dashboard(repo, Settings())

    assert len(dashboard["recent_events"]) == 20
    assert "XYZ:T21" not in {event["instrument"] for event in dashboard["recent_events"]}
    assert dashboard["recent_events_by_instrument"]["XYZ:T21"][0]["coin"] == "XYZ:T21"


def test_raw_instrument_can_be_queried_and_rendered_on_chart(tmp_path) -> None:
    repo = create_repository(f"sqlite:///{tmp_path / 'raw-instrument.db'}")
    repo.upsert_whale_wallet(WhaleWallet(address=ADDRESS, label="테스트 고래"))
    event_at = utc_now() - timedelta(minutes=10)
    event = WhaleEvent(
        wallet_address=ADDRESS,
        wallet_label="테스트 고래",
        coin="XYZ:SNDK",
        symbol="",
        side="short",
        event="open",
        size=10,
        size_usd=200_000,
        entry_px=210,
        event_at=event_at,
    )
    assert repo.add_whale_event(event) is True
    repo.upsert_whale_position_state(
        ADDRESS,
        "XYZ:SNDK",
        {
            "wallet_address": ADDRESS,
            "wallet_label": "테스트 고래",
            "coin": "XYZ:SNDK",
            "symbol": "",
            "side": "short",
            "size_usd": 200_000,
            "entry_px": 210,
            "as_of": utc_now().isoformat(),
        },
    )
    candle_time = int((event_at - timedelta(minutes=5)).timestamp())

    queried = repo.list_whale_events(symbol="XYZ:SNDK")
    context = chart_onchain_context(repo, "XYZ:SNDK", "15m", [{"time": candle_time}])
    activity = whale_dashboard(repo, Settings())["symbol_activity"]["XYZ:SNDK"]

    assert queried[0].id == event.id
    assert context["supported"] is True
    assert context["markers"][0]["items"][0]["coin"] == "XYZ:SNDK"
    assert activity["short_usd"] == 200_000
    assert activity["recent_events"][0]["coin"] == "XYZ:SNDK"


def test_whale_dashboard_exposes_four_week_follow_performance() -> None:
    repo = MemoryRepository()
    repo.upsert_whale_wallet(WhaleWallet(address=ADDRESS, label="테스트 고래"))
    judgment = JudgmentLedgerEntry(
        judgment_id="whale:test-score",
        position_id=CANDIDATE_SENTINEL_POSITION_ID,
        source_type="hyperliquid_fill",
        as_of=utc_now() - timedelta(days=14),
        type="candidate_signature",
        claim={"signature_key": whale_signature_key(ADDRESS), "engine": "whale", "direction": "long"},
    )
    repo.add_judgment(judgment)
    repo.add_judgment_score(
        JudgmentScore(
            judgment_id=judgment.judgment_id,
            position_id=CANDIDATE_SENTINEL_POSITION_ID,
            judgment_type="candidate_signature",
            outcome="correct",
            detail="1R",
            metrics={"realized_rr": 1.25},
        )
    )

    review = whale_dashboard(repo, Settings())["wallets"][0]["review"]

    assert review["trust_status"] == "validating"
    assert review["sample_size"] == 1
    assert review["win_1r_pct"] == 100.0
    assert review["cumulative_return_r"] == 1.25
    assert review["average_return_r"] == 1.25
    assert review["validation_days"] == 14
    assert review["validation_remaining_days"] == 14


def test_whale_promotion_requires_full_four_week_calendar() -> None:
    repo = MemoryRepository()
    key = whale_signature_key(ADDRESS)
    repo.upsert_backtest_stat(
        BacktestStat(
            signature_key=key,
            symbol="__WHALE__",
            timeframe="4h",
            asset_class="crypto",
            scope="all",
            engine="whale",
            event_type="whale_entry",
            strength_class="candidate",
            direction="neutral",
            payload={"label": "테스트 고래", "signature": {"wallet_address": ADDRESS}},
        )
    )
    for index in range(30):
        judgment_id = f"whale:calendar:{index}"
        repo.add_judgment(
            JudgmentLedgerEntry(
                id=uuid5(NAMESPACE_URL, judgment_id),
                judgment_id=judgment_id,
                position_id=CANDIDATE_SENTINEL_POSITION_ID,
                source_type="hyperliquid_fill",
                as_of=utc_now() - timedelta(days=7),
                type="candidate_signature",
                claim={"signature_key": key, "engine": "whale", "direction": "long"},
            )
        )
        repo.add_judgment_score(
            JudgmentScore(
                id=uuid5(NAMESPACE_URL, f"score:{judgment_id}"),
                judgment_id=judgment_id,
                position_id=CANDIDATE_SENTINEL_POSITION_ID,
                judgment_type="candidate_signature",
                outcome="correct",
                detail="1R",
                metrics={"win_1r": True, "realized_rr": 1.0},
            )
        )

    review = score_candidates(repo, Settings(), engines={"whale"})["signatures"][0]

    assert review["sample_size"] == 30
    assert review["validation_window"]["elapsed_days"] == 7
    assert review["validation_window"]["calendar_complete"] is False
    assert review["promotion_eligible"] is False

    for judgment in repo.list_judgments(CANDIDATE_SENTINEL_POSITION_ID, limit=100):
        repo.add_judgment(judgment.model_copy(update={"as_of": utc_now() - timedelta(days=29)}))
    matured = score_candidates(repo, Settings(), engines={"whale"})["signatures"][0]

    assert matured["validation_window"]["calendar_complete"] is True
    assert matured["promotion_eligible"] is True


def test_historical_short_exit_does_not_become_current_short_exposure() -> None:
    repo = MemoryRepository()
    repo.upsert_whale_wallet(WhaleWallet(address=ADDRESS, label="테스트 고래"))
    repo.upsert_whale_position_state(
        ADDRESS,
        "ETH",
        {
            "wallet_address": ADDRESS,
            "wallet_label": "테스트 고래",
            "coin": "ETH",
            "symbol": "ETHUSDT",
            "side": "long",
            "size_usd": 2_000_000,
            "entry_px": 1_800,
            "as_of": utc_now().isoformat(),
        },
    )
    repo.add_whale_event(
        WhaleEvent(
            wallet_address=ADDRESS,
            wallet_label="테스트 고래",
            coin="ETH",
            symbol="ETHUSDT",
            side="short",
            event="close",
            size=1_000,
            size_usd=1_900_000,
            entry_px=1_900,
            event_at=utc_now(),
        )
    )

    activity = whale_dashboard(repo, Settings())["symbol_activity"]["ETHUSDT"]

    assert activity["long_usd"] == 2_000_000
    assert activity["short_usd"] == 0
    assert activity["short_wallet_count"] == 0
    assert activity["recent_events"][0]["side"] == "short"
    assert activity["recent_events"][0]["event"] == "close"


def test_only_validated_wallet_enters_confluence() -> None:
    repo = MemoryRepository()
    wallet = WhaleWallet(address=ADDRESS, label="테스트 고래")
    repo.upsert_whale_wallet(wallet)
    repo.upsert_whale_position_state(
        ADDRESS,
        "BTC",
        {
            "wallet_address": ADDRESS,
            "wallet_label": wallet.label,
            "coin": "BTC",
            "symbol": "BTCUSDT",
            "side": "long",
            "size_usd": 5_000_000,
            "entry_px": 60000,
            "as_of": utc_now().isoformat(),
        },
    )
    candles = [{"time": int((utc_now() - timedelta(hours=8)).timestamp())}]
    candidate = chart_onchain_context(repo, "BTCUSDT", "4h", candles)
    assert candidate["validated_evidence"] == []

    key = whale_signature_key(ADDRESS)
    record_transition(
        repo,
        signature_key=key,
        previous="candidate",
        new="validated",
        transition="validate",
        reason="test approval",
        evidence={"sample_size": 30, "win_1r_ci": [55, 80]},
        autonomous=False,
    )
    validated = chart_onchain_context(repo, "BTCUSDT", "4h", candles)
    analysis = {"validated_onchain_evidence": validated["validated_evidence"], "candles": candles, "data_quality": {}}
    confluence = build_confluence(symbol="BTCUSDT", timeframe="4h", analysis=analysis)

    assert len(validated["validated_evidence"]) == 1
    assert any(item["engine"] == "onchain" for item in confluence["long_evidence"])


def test_sqlite_repository_persists_whale_data(tmp_path) -> None:
    repo = create_repository(f"sqlite:///{tmp_path / 'whales.db'}")
    wallet = WhaleWallet(address=ADDRESS, label="테스트 고래")
    repo.upsert_whale_wallet(wallet)
    event = WhaleEvent(
        wallet_address=ADDRESS,
        wallet_label=wallet.label,
        coin="BTC",
        symbol="BTCUSDT",
        side="short",
        event="flip",
        size=1,
        size_usd=100_000,
        entry_px=60_000,
        event_at=utc_now(),
    )
    assert repo.add_whale_event(event) is True

    reopened = create_repository(f"sqlite:///{tmp_path / 'whales.db'}")
    assert reopened.get_whale_wallet(ADDRESS).label == "테스트 고래"
    assert reopened.list_whale_events(symbol="BTCUSDT")[0].id == event.id


@pytest.mark.asyncio
async def test_whale_alert_batches_same_wallet_fills_for_three_minutes() -> None:
    repo = MemoryRepository()
    configure_runtime(repo=repo, provider=MockMarketDataProvider())
    sender = FakeSender()
    started_at = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
    clock = {"now": started_at}
    # CI 는 FCE_TELEGRAM_ALERTS_ENABLED=false 를 주입 — 테스트는 명시적으로 켠다.
    engine = AlertEngine(
        Settings(
            database_url="memory://", telegram_bot_token="token", telegram_chat_id="123", telegram_alerts_enabled=True, telegram_quiet_hours_enabled=False
        ),
        sender,
        NotificationState(),
        now_provider=lambda: clock["now"],
    )
    first = WhaleEvent(
        wallet_address=ADDRESS,
        wallet_label="테스트 고래",
        coin="BTC",
        symbol="BTCUSDT",
        side="long",
        event="flip",
        size=2,
        size_usd=2_000_000,
        entry_px=63_000,
        event_at=started_at,
    ).model_dump(mode="json")
    second = WhaleEvent(
        wallet_address=ADDRESS,
        wallet_label="테스트 고래",
        coin="ETH",
        symbol="ETHUSDT",
        side="short",
        event="open",
        size=100,
        size_usd=190_000,
        entry_px=1_900,
        event_at=started_at + timedelta(minutes=2, seconds=59),
    ).model_dump(mode="json")
    dashboard = {
        "wallets": [
            {
                "address": ADDRESS,
                "review": {
                    "state": "candidate",
                    "trust_status": "validating",
                    "sample_size": 4,
                    "win_1r_pct": 50.0,
                    "cumulative_return_r": 0.5,
                    "validation_days": 9,
                    "validation_remaining_days": 19,
                },
            }
        ]
    }

    assert await engine.evaluate_whale_events([first], dashboard) == 0
    clock["now"] = started_at + timedelta(minutes=2, seconds=59)
    assert await engine.evaluate_whale_events([second, first], dashboard) == 0
    clock["now"] = started_at + timedelta(minutes=3)
    assert await engine.evaluate_whale_events([], dashboard) == 1
    assert await engine.evaluate_whale_events([], dashboard) == 0
    assert len(sender.messages) == 1
    assert "3분 다중체결 2건 · 2종목" in sender.messages[0]
    assert "미검증 관측" in sender.messages[0]
    assert "숏→롱 전환" in sender.messages[0]
    assert "ETH" in sender.messages[0]
    assert "숏 신규" in sender.messages[0]
    assert "추종 승률 50.0%" in sender.messages[0]
    assert "누적 +0.50R" in sender.messages[0]
    assert "따라가기 신호가 아닙니다" in sender.messages[0]
    alert = repo.list_alerts()[0]
    assert alert.rule_id == "whale_entry"
    assert alert.severity == "info"
    assert alert.payload["fill_count"] == 2
    assert len(alert.payload["event_ids"]) == 2


@pytest.mark.asyncio
async def test_whale_alert_drops_single_fill_and_keeps_increase_in_multi_fill() -> None:
    repo = MemoryRepository()
    configure_runtime(repo=repo, provider=MockMarketDataProvider())
    sender = FakeSender()
    started_at = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
    clock = {"now": started_at}
    engine = AlertEngine(
        Settings(
            database_url="memory://", telegram_bot_token="token", telegram_chat_id="123", telegram_alerts_enabled=True, telegram_quiet_hours_enabled=False
        ),
        sender,
        NotificationState(),
        now_provider=lambda: clock["now"],
    )
    single = WhaleEvent(
        wallet_address=ADDRESS,
        wallet_label="테스트 고래",
        coin="SOL",
        symbol="SOLUSDT",
        side="long",
        event="open",
        size=10,
        size_usd=1_000,
        entry_px=100,
        event_at=started_at,
    ).model_dump(mode="json")
    dashboard = {"wallets": [{"address": ADDRESS, "review": {"state": "candidate"}}]}

    assert await engine.evaluate_whale_events([single], dashboard) == 0
    clock["now"] = started_at + timedelta(minutes=3)
    assert await engine.evaluate_whale_events([], dashboard) == 0
    assert sender.messages == []
    assert engine.state.whale_alert_events == {}

    first_increase = WhaleEvent(
        wallet_address=ADDRESS,
        wallet_label="테스트 고래",
        coin="HYPE",
        symbol="HYPEUSDT",
        side="short",
        event="increase",
        size=1,
        size_usd=60,
        entry_px=60,
        event_at=clock["now"],
    ).model_dump(mode="json")
    second_increase = WhaleEvent(
        wallet_address=ADDRESS,
        wallet_label="테스트 고래",
        coin="HYPE",
        symbol="HYPEUSDT",
        side="short",
        event="increase",
        size=2,
        size_usd=124,
        entry_px=62,
        event_at=clock["now"] + timedelta(minutes=1),
    ).model_dump(mode="json")
    assert await engine.evaluate_whale_events([first_increase], dashboard) == 0
    clock["now"] += timedelta(minutes=1)
    assert await engine.evaluate_whale_events([second_increase], dashboard) == 0
    clock["now"] += timedelta(minutes=2)
    assert await engine.evaluate_whale_events([], dashboard) == 1
    assert "HYPE" in sender.messages[0]
    assert "숏 증액 2건" in sender.messages[0]
    assert "184" in sender.messages[0]


@pytest.mark.asyncio
async def test_whale_alert_batch_survives_restart_and_excludes_outside_window(tmp_path) -> None:
    repo = MemoryRepository()
    configure_runtime(repo=repo, provider=MockMarketDataProvider())
    state_path = tmp_path / "notification.json"
    settings = Settings(
        database_url="memory://",
        telegram_bot_token="token",
        telegram_chat_id="123",
        telegram_alerts_enabled=True,
        telegram_quiet_hours_enabled=False,
        notification_state_path=str(state_path),
    )
    sender = FakeSender()
    started_at = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
    clock = {"now": started_at}
    first = WhaleEvent(
        wallet_address=ADDRESS,
        wallet_label="테스트 고래",
        coin="BTC",
        symbol="BTCUSDT",
        side="long",
        event="open",
        size=1,
        size_usd=60_000,
        entry_px=60_000,
        event_at=started_at,
    ).model_dump(mode="json")
    outside = WhaleEvent(
        wallet_address=ADDRESS,
        wallet_label="테스트 고래",
        coin="ETH",
        symbol="ETHUSDT",
        side="long",
        event="open",
        size=1,
        size_usd=2_000,
        entry_px=2_000,
        event_at=started_at + timedelta(minutes=3, seconds=1),
    ).model_dump(mode="json")
    dashboard = {"wallets": [{"address": ADDRESS, "review": {"state": "candidate"}}]}
    first_engine = AlertEngine(settings, sender, NotificationState(), now_provider=lambda: clock["now"])

    assert await first_engine.evaluate_whale_events([first], dashboard) == 0
    restored = NotificationState()
    restored.load(str(state_path))
    assert len(restored.whale_alert_events[ADDRESS]) == 1
    second_engine = AlertEngine(settings, sender, restored, now_provider=lambda: clock["now"])
    clock["now"] = started_at + timedelta(minutes=3, seconds=1)
    assert await second_engine.evaluate_whale_events([outside], dashboard) == 0
    clock["now"] = started_at + timedelta(minutes=6, seconds=1)
    assert await second_engine.evaluate_whale_events([], dashboard) == 0
    assert sender.messages == []
    assert restored.whale_alert_events == {}
