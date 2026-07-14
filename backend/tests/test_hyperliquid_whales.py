from __future__ import annotations

from datetime import timedelta

import pytest

from app.analyst.confluence import build_confluence
from app.analyst.signature_registry import record_transition
from app.api.deps import configure_runtime
from app.core.config import Settings
from app.db.models import BacktestStat, WhaleEvent, WhaleWallet, utc_now
from app.db.repository import MemoryRepository, create_repository
from app.onchain.hyperliquid.collector import collect_whale_positions, event_from_fill, whale_signature_key
from app.onchain.service import add_whale_wallet, chart_onchain_context
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
    assert context["markers"][0]["emphasized"] is False
    assert context["validated_evidence"] == []


def test_only_validated_wallet_enters_confluence() -> None:
    repo = MemoryRepository()
    wallet = WhaleWallet(address=ADDRESS, label="테스트 고래")
    repo.upsert_whale_wallet(wallet)
    repo.upsert_whale_position_state(ADDRESS, "BTC", {"wallet_address": ADDRESS, "wallet_label": wallet.label, "coin": "BTC", "symbol": "BTCUSDT", "side": "long", "size_usd": 5_000_000, "entry_px": 60000, "as_of": utc_now().isoformat()})
    candles = [{"time": int((utc_now() - timedelta(hours=8)).timestamp())}]
    candidate = chart_onchain_context(repo, "BTCUSDT", "4h", candles)
    assert candidate["validated_evidence"] == []

    key = whale_signature_key(ADDRESS)
    record_transition(repo, signature_key=key, previous="candidate", new="validated", transition="validate", reason="test approval", evidence={"sample_size": 30, "win_1r_ci": [55, 80]}, autonomous=False)
    validated = chart_onchain_context(repo, "BTCUSDT", "4h", candles)
    analysis = {"validated_onchain_evidence": validated["validated_evidence"], "candles": candles, "data_quality": {}}
    confluence = build_confluence(symbol="BTCUSDT", timeframe="4h", analysis=analysis)

    assert len(validated["validated_evidence"]) == 1
    assert any(item["engine"] == "onchain" for item in confluence["long_evidence"])


def test_sqlite_repository_persists_whale_data(tmp_path) -> None:
    repo = create_repository(f"sqlite:///{tmp_path / 'whales.db'}")
    wallet = WhaleWallet(address=ADDRESS, label="테스트 고래")
    repo.upsert_whale_wallet(wallet)
    event = WhaleEvent(wallet_address=ADDRESS, wallet_label=wallet.label, coin="BTC", symbol="BTCUSDT", side="short", event="open", size=1, size_usd=100_000, entry_px=60_000, event_at=utc_now())
    assert repo.add_whale_event(event) is True

    reopened = create_repository(f"sqlite:///{tmp_path / 'whales.db'}")
    assert reopened.get_whale_wallet(ADDRESS).label == "테스트 고래"
    assert reopened.list_whale_events(symbol="BTCUSDT")[0].id == event.id


@pytest.mark.asyncio
async def test_whale_alert_uses_existing_state_machine_and_candidate_tone() -> None:
    repo = MemoryRepository()
    configure_runtime(repo=repo, provider=MockMarketDataProvider())
    sender = FakeSender()
    engine = AlertEngine(Settings(database_url="memory://", telegram_bot_token="token", telegram_chat_id="123"), sender, NotificationState())
    event = WhaleEvent(wallet_address=ADDRESS, wallet_label="테스트 고래", coin="BTC", symbol="BTCUSDT", side="long", event="open", size=2, size_usd=2_000_000, entry_px=63_000, event_at=utc_now()).model_dump(mode="json")
    dashboard = {"wallets": [{"address": ADDRESS, "review": {"state": "candidate", "sample_size": 4, "win_1r_pct": None}}]}

    assert await engine.evaluate_whale_events([event], dashboard) == 1
    assert await engine.evaluate_whale_events([event], dashboard) == 0
    assert "미검증 관측" in sender.messages[0]
    assert "따라가기 신호가 아닙니다" in sender.messages[0]
    alert = repo.list_alerts()[0]
    assert alert.rule_id == "whale_entry"
    assert alert.severity == "info"
