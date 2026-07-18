from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.db.models import CatalogSymbol, Direction, InstrumentMap, Position, WatchlistItem
from app.db.repository import MemoryRepository
from app.toss.instrument_join import (
    approve_mapping,
    decorate_chart_analysis,
    reset_join_cache,
    sync_mapping_candidates,
)


def _position(symbol: str) -> Position:
    return Position(symbol=symbol, direction=Direction.long, entry_price=100, quantity=1)


def _catalog(symbol: str, base: str, *, rwa: bool, asset_class: str) -> CatalogSymbol:
    return CatalogSymbol(
        symbol=symbol,
        base_coin=base,
        quote_coin="USDT",
        asset_class=asset_class,
        source_category="bitget_rwa" if rwa else "perpetual",
        raw_metadata={"isRwa": "YES" if rwa else "NO"},
    )


class FakeTossClient:
    def __init__(self, stocks: list[dict[str, Any]], *, candles: list[dict[str, Any]] | None = None) -> None:
        self.stocks = stocks
        self.candles = candles or []
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((path, params))
        if path == "/api/v1/stocks":
            return {"result": self.stocks}
        if path == "/api/v1/prices":
            return {"result": [{"symbol": "SOXL", "lastPrice": "100", "timestamp": "2026-07-18T00:00:00Z"}]}
        if path == "/api/v1/candles":
            return {"result": {"candles": self.candles}}
        if path.startswith("/api/v1/market-calendar/"):
            return {"result": {}}
        if path.endswith("/warnings"):
            return {"result": [{"warningType": "investment_warning"}]}
        raise AssertionError(f"unexpected Toss path {path}")

    async def close(self) -> None:
        return None


def _factory(client: FakeTossClient):
    return lambda *args, **kwargs: client


def _settings() -> Settings:
    return Settings(toss_client_id="id", toss_client_secret="secret", database_url="memory://")


def test_identity_mismatch_is_rejected_instead_of_ticker_matched() -> None:
    repo = MemoryRepository()
    repo.add_position(_position("SOXLUSDT"))
    repo.replace_symbol_catalog([_catalog("SOXLUSDT", "SOXL", rwa=True, asset_class="stock")])
    client = FakeTossClient(
        [
            {
                "symbol": "SOXL",
                "englishName": "UNRELATED COMPANY",
                "market": "NASDAQ",
                "securityType": "STOCK",
                "leverageFactor": None,
            }
        ]
    )

    sync_mapping_candidates(repo, _settings(), client_factory=_factory(client))

    mapping = repo.get_instrument_map("SOXLUSDT")
    assert mapping is not None
    assert mapping.verification_status == "rejected"
    assert mapping.identity_match is False
    assert mapping.verification_evidence["ticker_only_match_used"] is False
    assert mapping.verification_evidence["checks"] == {
        "official_name": False,
        "exchange": False,
        "asset_type": False,
    }


def test_matching_identity_remains_pending_until_manual_approval() -> None:
    repo = MemoryRepository()
    repo.add_position(_position("SOXLUSDT"))
    repo.replace_symbol_catalog([_catalog("SOXLUSDT", "SOXL", rwa=True, asset_class="stock")])
    client = FakeTossClient(
        [
            {
                "symbol": "SOXL",
                "englishName": "DIREXION SHARES ETF TRUST DAILY SEMICONDUCTOR BULL 3X SHS",
                "market": "AMEX",
                "securityType": "ETF",
                "leverageFactor": "3",
            }
        ]
    )

    sync_mapping_candidates(repo, _settings(), client_factory=_factory(client))
    pending = repo.get_instrument_map("SOXLUSDT")
    assert pending is not None
    assert pending.identity_match is True
    assert pending.verification_status == "pending"

    untouched = decorate_chart_analysis(
        repo,
        _settings(),
        {"symbol": "SOXLUSDT", "mark_price": 101, "sentinel": True},
        client_factory=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("pending mapping called Toss")),
    )
    assert untouched == {"symbol": "SOXLUSDT", "mark_price": 101, "sentinel": True}

    verified = approve_mapping(repo, "SOXLUSDT")
    assert verified.verification_status == "verified"
    assert verified.verified_by == "manual"
    assert verified.verified_at is not None


def test_pure_crypto_targets_never_construct_toss_client() -> None:
    repo = MemoryRepository()
    repo.add_position(_position("BTCUSDT"))
    repo.upsert_watchlist_item(WatchlistItem(symbol="ETHUSDT", asset_class="crypto"))
    repo.replace_symbol_catalog(
        [
            _catalog("BTCUSDT", "BTC", rwa=False, asset_class="crypto"),
            _catalog("ETHUSDT", "ETH", rwa=False, asset_class="crypto"),
        ]
    )

    state = sync_mapping_candidates(
        repo,
        _settings(),
        client_factory=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("crypto called Toss")),
    )

    assert state["items"] == []
    assert {target["symbol"] for target in state["targets"]} == {"BTCUSDT", "ETHUSDT"}
    assert all(target["join_eligible"] is False for target in state["targets"])


def test_stale_crypto_mapping_cannot_be_approved_or_decorated() -> None:
    repo = MemoryRepository()
    repo.add_position(_position("BTCUSDT"))
    repo.replace_symbol_catalog([_catalog("BTCUSDT", "BTC", rwa=False, asset_class="crypto")])
    repo.upsert_instrument_map(
        InstrumentMap(
            bitget_symbol="BTCUSDT",
            underlying_name="Bitcoin",
            underlying_kind="stock",
            toss_symbol="BTC",
            toss_exchange="NASDAQ",
            identity_match=True,
        )
    )

    with pytest.raises(HTTPException, match="Bitget RWA"):
        approve_mapping(repo, "BTCUSDT")
    untouched = decorate_chart_analysis(
        repo,
        _settings(),
        {"symbol": "BTCUSDT", "mark_price": 100, "sentinel": True},
        client_factory=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("crypto called Toss")),
    )

    assert untouched == {"symbol": "BTCUSDT", "mark_price": 100, "sentinel": True}


def test_verified_join_keeps_bitget_price_and_exposes_toss_raw_values() -> None:
    reset_join_cache()
    repo = MemoryRepository()
    repo.add_position(_position("SOXLUSDT"))
    repo.replace_symbol_catalog([_catalog("SOXLUSDT", "SOXL", rwa=True, asset_class="stock")])
    stock_client = FakeTossClient(
        [
            {
                "symbol": "SOXL",
                "englishName": "DIREXION SHARES ETF TRUST DAILY SEMICONDUCTOR BULL 3X SHS",
                "market": "AMEX",
                "securityType": "ETF",
                "leverageFactor": "3",
            }
        ]
    )
    sync_mapping_candidates(repo, _settings(), client_factory=_factory(stock_client))
    approve_mapping(repo, "SOXLUSDT")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [
        {
            "timestamp": (start + timedelta(days=index)).isoformat(),
            "openPrice": str(80 + index * 0.1),
            "highPrice": str(82 + index * 0.1),
            "lowPrice": str(79 + index * 0.1),
            "closePrice": str(81 + index * 0.1),
            "volume": str(1_000 + index),
        }
        for index in range(200)
    ]
    join_client = FakeTossClient([], candles=candles)

    joined = decorate_chart_analysis(
        repo,
        _settings(),
        {
            "symbol": "SOXLUSDT",
            "mark_price": 102,
            "price_levels": {"entry": 98, "mark": 102, "liquidation": 70},
            "derivatives": {"source_status": "ok"},
        },
        client_factory=_factory(join_client),
    )

    context = joined["underlying_join"]
    assert context["status"] == "joined", context
    assert joined["mark_price"] == 102
    assert context["bitget_price"] == 102
    assert context["toss_price"] == 100
    assert context["basis_pct"] == pytest.approx(2)
    assert context["stale"] is True
    assert context["leverage_note"].startswith("3x")
    assert context["flow_status"] == "unavailable_us"
    assert context["warning_gate_blocked"] is False
    assert context["warning_badges"] == ["investment_warning"]
    assert len(context["raw_candles"]) == 200
    assert joined["derivatives"] == {"source_status": "ok"}
    assert joined["price_levels"]["mark"] == 102
