from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import httpx
import pytest

from app.db.migrations import run_migrations
from app.db.repository import MemoryRepository
from app.core.config import Settings
from app.exchange.mock import MockMarketDataProvider
from app.poly_paper.broker import PaperBroker
from app.poly_paper.client import PolymarketPublicClient, _balanced_categories, parse_market
from app.poly_paper.estimator import attach_execution_cost, estimate_market_probability
from app.poly_paper.models import (
    BookLevel,
    Category,
    EstimateQuality,
    Evidence,
    OrderBook,
    OutcomeSide,
    PaperOrder,
    PolyMarket,
    ProbabilityEstimate,
)
from app.poly_paper.store import POLY_LEDGER_POSITION_ID, PolyPaperStore
from app.poly_paper.service import run_poly_paper_engine
from app.poly_paper import service as poly_service
from app.poly_paper.estimator import EstimationResult


NOW = datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)


def _market(**overrides) -> PolyMarket:
    base = PolyMarket(
        id="btc-100k",
        slug="will-bitcoin-be-above-100000",
        question="Will Bitcoin be above $100,000 on July 30?",
        category=Category.CRYPTO,
        observed_at=NOW,
        end_at=NOW + timedelta(days=8),
        active=True,
        closed=False,
        liquidity=250_000,
        resolution_source="https://example.test/official",
        description="Resolves Yes when the official reference is above the threshold.",
        yes_token_id="yes-token",
        no_token_id="no-token",
        yes_price=0.52,
        no_price=0.48,
        trade_eligible=True,
        exclusion_reason=None,
        taker_fee_rate=0.07,
        raw={},
    )
    return replace(base, **overrides)


def _store(tmp_path: Path) -> PolyPaperStore:
    path = tmp_path / "poly.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    fill_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(poly_fills)").fetchall()}
    assert "fee" in fill_columns
    connection.close()
    store = PolyPaperStore(f"sqlite:///{path}")
    store.ensure_track(initial_cash=10_000, parameter_version="poly-v1", now=NOW)
    return store


def _estimate() -> ProbabilityEstimate:
    return ProbabilityEstimate(
        market_id="btc-100k",
        observed_at=NOW,
        market_probability=0.52,
        estimated_probability=0.70,
        confidence_low=0.62,
        confidence_high=0.78,
        quality=EstimateQuality.HIGH,
        base_rate={"model": "test"},
        evidence=(Evidence("observed", "test:public", NOW, 1),),
        reasoning="test evidence only",
        direction=OutcomeSide.YES,
        gross_edge=0.18,
        effective_price=0.54,
        after_cost_edge=0.16,
        trade_eligible=True,
    )


@pytest.mark.asyncio
async def test_public_client_has_only_read_market_surface() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/events":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "event-1",
                        "category": "crypto",
                        "markets": [
                            {
                                "id": "1",
                                "question": "Will Bitcoin be above $100,000?",
                                "active": True,
                                "closed": False,
                                "liquidity": "100000",
                                "resolutionSource": "official",
                                "endDate": "2026-08-01T00:00:00Z",
                                "outcomes": '["Yes", "No"]',
                                "outcomePrices": '["0.60", "0.40"]',
                                "clobTokenIds": '["yes", "no"]',
                            }
                        ],
                    }
                ],
            )
        if request.url.path == "/book":
            return httpx.Response(
                200,
                json={"asset_id": "yes", "timestamp": "1785552000000", "bids": [{"price": "0.59", "size": "10"}], "asks": [{"price": "0.61", "size": "10"}]},
            )
        raise AssertionError(f"unexpected public path {request.url.path}")

    client = PolymarketPublicClient(
        gamma_base_url="https://gamma.test",
        clob_base_url="https://clob.test",
        transport=httpx.MockTransport(handler),
    )
    markets = await client.list_markets()
    book = await client.get_order_book("yes")
    assert markets[0].market_probability == 0.60
    assert book.asks[0] == BookLevel(price=0.61, size=10)
    method_names = {name.lower() for name in dir(PolymarketPublicClient) if not name.startswith("_")}
    assert method_names == {"get_market", "get_order_book", "list_markets"}
    assert not any(term in method_names for term in {"order", "wallet", "position", "cancel", "approve"})


def test_unsupported_or_unattributed_questions_do_not_emit_probability() -> None:
    provider = MockMarketDataProvider()
    unsupported = _market(question="Will Bitcoin be mentioned at the conference?")
    assert estimate_market_probability(unsupported, provider, now=NOW).estimate is None
    macro = _market(category=Category.MACRO, question="Will the Fed cut rates?")
    result = estimate_market_probability(macro, provider, now=NOW)
    assert result.estimate is None
    assert result.reason == "macro_base_rate_provider_unavailable"


def test_crypto_threshold_estimate_has_base_rate_sources_and_time() -> None:
    result = estimate_market_probability(_market(), MockMarketDataProvider(), now=NOW)
    assert result.reason is None
    assert result.estimate is not None
    estimate = result.estimate
    assert 0 < estimate.estimated_probability < 1
    assert estimate.base_rate["model"] == "lognormal_zero_drift_v1"
    assert len(estimate.evidence) == 4
    assert all(item.source and item.observed_at.tzinfo for item in estimate.evidence)
    assert estimate.quality in {EstimateQuality.MEDIUM, EstimateQuality.HIGH}


def test_touch_and_terminal_questions_use_different_probability_events() -> None:
    provider = MockMarketDataProvider()
    terminal = estimate_market_probability(_market(question="Will Bitcoin be above $100,000 on July 30?"), provider, now=NOW)
    touch = estimate_market_probability(_market(question="Will Bitcoin hit $100,000 by July 30?"), provider, now=NOW)
    assert terminal.estimate is not None and touch.estimate is not None
    assert terminal.estimate.base_rate["event"] == "terminal_above"
    assert touch.estimate.base_rate["event"] == "upper_touch"
    assert touch.estimate.estimated_probability >= terminal.estimate.estimated_probability


def test_after_cost_edge_uses_executable_price_not_midpoint() -> None:
    estimate = _estimate()
    priced = attach_execution_cost(estimate, effective_price=0.68, minimum_edge=0.05, quality_allowed=True)
    assert priced.gross_edge == pytest.approx(0.18)
    assert priced.after_cost_edge == pytest.approx(0.02)
    assert priced.trade_eligible is False
    assert priced.exclusion_reason == "after_cost_edge_low"


def test_paper_broker_walks_observed_asks_and_caps_liquidity() -> None:
    book = OrderBook(
        token_id="yes-token",
        observed_at=NOW,
        bids=(BookLevel(0.49, 100),),
        asks=(BookLevel(0.50, 100), BookLevel(0.55, 100)),
    )
    broker = PaperBroker(max_observed_ask_fraction=0.05)
    order = PaperOrder("btc-100k", "estimate", "yes-token", OutcomeSide.YES, 100, NOW)
    result = broker.place(order, book)
    assert result.status == "partial"
    assert result.reason == "liquidity_cap"
    assert result.fill is not None
    assert result.fill.shares == pytest.approx(10)
    assert result.fill.price == pytest.approx(0.50)
    assert result.fill.fee == 0
    assert min(level.price for level in book.asks) <= result.fill.price <= max(level.price for level in book.asks)


def test_probability_judgment_resolution_brier_cycle(tmp_path: Path) -> None:
    store = _store(tmp_path)
    repository = MemoryRepository()
    market = _market()
    estimate = _estimate()
    store.save_market(market)
    judgment_id = store.save_estimate(estimate, repository)
    resolved = replace(market, closed=True, active=False, yes_price=1.0, no_price=0.0)
    scored = store.settle_market(
        resolved,
        outcome=1,
        source="official-resolution",
        repository=repository,
        resolved_at=NOW + timedelta(days=8),
    )
    dashboard = store.dashboard()
    assert scored == 1
    assert dashboard["calibration"]["n"] == 1
    assert dashboard["calibration"]["mean_brier_score"] == pytest.approx((0.70 - 1) ** 2)
    assert dashboard["calibration"]["sample_sufficient"] is False
    assert dashboard["calibration"]["sample_warning"] == "표본 부족 · N=1/30"
    judgments = repository.list_judgments(POLY_LEDGER_POSITION_ID)
    scores = repository.list_judgment_scores(position_id=POLY_LEDGER_POSITION_ID)
    assert judgments[0].judgment_id == judgment_id
    assert judgments[0].claim["entity_type"] == "polymarket"
    assert scores[0].metrics["brier_score"] == pytest.approx(0.09)


def test_poly_package_does_not_import_existing_decision_engines() -> None:
    root = Path(__file__).parents[1] / "app" / "poly_paper"
    source = "\n".join(path.read_text() for path in root.glob("*.py"))
    forbidden = (
        "app.analyst",
        "app.structure",
        "app.positions.chart_analysis",
        "app.paper",
        "app.stock_paper",
        "from app.analyst",
        "from app.structure",
    )
    assert all(term not in source for term in forbidden)


def test_non_crypto_macro_market_is_not_added_to_universe() -> None:
    assert parse_market({"id": "sports", "question": "Will Team A win?", "category": "sports"}) is None


def test_universe_reserves_observation_capacity_for_both_categories() -> None:
    crypto = [_market(id=f"c{index}", liquidity=100_000 - index) for index in range(20)]
    macro = [_market(id=f"m{index}", category=Category.MACRO, liquidity=10_000 - index) for index in range(8)]
    selected = _balanced_categories(crypto + macro, 12)
    assert sum(item.category == Category.MACRO for item in selected) >= 4
    assert sum(item.category == Category.CRYPTO for item in selected) >= 4


def test_dashboard_keeps_crypto_and_macro_visible(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for index in range(50):
        store.save_market(_market(id=f"crypto-{index}", liquidity=100_000 - index))
    for index in range(20):
        store.save_market(
            _market(
                id=f"macro-{index}",
                category=Category.MACRO,
                question=f"Will macro observation {index} resolve Yes?",
                liquidity=10_000 - index,
            )
        )
    markets = store.dashboard()["markets"]
    assert sum(item["category"] == "crypto" for item in markets) == 30
    assert sum(item["category"] == "macro" for item in markets) == 10


def test_market_requires_binary_yes_no_and_parses_rule_source_and_fee() -> None:
    row = {
        "id": "fee-market",
        "question": "Will Bitcoin hit $150k by December 31?",
        "category": "crypto",
        "description": "The resolution source for this market is Binance BTC/USDT one-minute High prices.",
        "endDate": "2026-12-31T00:00:00Z",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.25", "0.75"]',
        "clobTokenIds": '["yes", "no"]',
        "feesEnabled": True,
        "feeSchedule": {"rate": 0.07},
    }
    parsed = parse_market(row)
    assert parsed is not None
    assert parsed.resolution_source == "Market rules: The resolution source for this market is Binance BTC/USDT one-minute High prices."
    assert parsed.taker_fee_rate == pytest.approx(0.07)
    assert parsed.trade_eligible is True
    non_binary = parse_market({**row, "outcomes": '["BTC", "ETH"]'})
    assert non_binary is not None
    assert non_binary.trade_eligible is False
    assert non_binary.exclusion_reason == "binary_yes_no_required"


def test_paper_broker_charges_protocol_taker_fee_in_cash_cost() -> None:
    book = OrderBook(token_id="yes", observed_at=NOW, bids=(), asks=(BookLevel(0.50, 1000),))
    order = PaperOrder("btc-100k", "estimate", "yes", OutcomeSide.YES, 100, NOW)
    result = PaperBroker(max_observed_ask_fraction=1).place(order, book, taker_fee_rate=0.07)
    assert result.fill is not None
    assert result.fill.fee > 0
    assert result.fill.notional == pytest.approx(result.fill.shares * result.fill.price + result.fill.fee)


@pytest.mark.asyncio
async def test_isolated_track_starts_and_enters_only_after_cost_positive_edge(tmp_path: Path) -> None:
    store = _store(tmp_path)
    repository = MemoryRepository()
    settings = Settings(
        database_url=f"sqlite:///{store.path}",
        polymarket_paper_enabled=True,
        polymarket_initial_usdc=10_000,
    )
    market = _market(yes_price=0.10, no_price=0.90)
    store.save_market(market)
    store.save_estimate(replace(_estimate(), observed_at=NOW - timedelta(minutes=1)), repository)
    assert store.latest_estimate_needs_execution_retry(market.id) is True

    class PublicFixture:
        async def list_markets(self, *, limit: int = 100) -> list[PolyMarket]:
            assert limit == settings.polymarket_market_limit
            return [market]

        async def get_order_book(self, token_id: str) -> OrderBook:
            assert token_id == "yes-token"
            return OrderBook(
                token_id=token_id,
                observed_at=NOW,
                bids=(BookLevel(0.10, 20_000),),
                asks=(BookLevel(0.11, 20_000), BookLevel(0.12, 20_000)),
            )

        async def get_market(self, market_id: str) -> PolyMarket | None:
            return None

    class WorkerThreadProvider:
        def __init__(self) -> None:
            self.delegate = MockMarketDataProvider()

        def get_snapshot(self, symbol: str, timeframe: str = "4h"):
            with pytest.raises(RuntimeError, match="no running event loop"):
                asyncio.get_running_loop()
            return self.delegate.get_snapshot(symbol, timeframe)

    result = await run_poly_paper_engine(
        settings,
        WorkerThreadProvider(),
        repository,
        client=PublicFixture(),
        now=NOW,
    )
    dashboard = store.dashboard()
    assert result["live_orders_enabled"] is False
    assert result["entered"] == 1
    assert dashboard["track"]["clock_valid"] == 1
    assert dashboard["track"]["currency"] == "USDC"
    assert dashboard["positions"][0]["status"] == "open"


@pytest.mark.asyncio
async def test_edge_low_market_enters_small_calibration_coverage_but_keeps_mode_label(tmp_path: Path, monkeypatch) -> None:
    store = _store(tmp_path)
    repository = MemoryRepository()
    settings = Settings(database_url=f"sqlite:///{store.path}", polymarket_paper_enabled=True, polymarket_initial_usdc=10_000)
    market = _market(yes_price=0.52, no_price=0.48, taker_fee_rate=0)
    estimate = replace(_estimate(), effective_price=None, after_cost_edge=None, trade_eligible=False, exclusion_reason=None)
    monkeypatch.setattr(poly_service, "estimate_market_probability", lambda *_args, **_kwargs: EstimationResult(estimate, None))

    class PublicFixture:
        async def list_markets(self, *, limit: int = 100) -> list[PolyMarket]:
            return [market]

        async def get_order_book(self, token_id: str) -> OrderBook:
            return OrderBook(token_id=token_id, observed_at=NOW, bids=(), asks=(BookLevel(0.68, 10_000),))

        async def get_market(self, market_id: str) -> PolyMarket | None:
            return None

    result = await run_poly_paper_engine(settings, MockMarketDataProvider(), repository, client=PublicFixture(), now=NOW)
    dashboard = store.dashboard()
    assert result["strict_entered"] == 0
    assert result["coverage_entered"] == 1
    assert dashboard["positions"][0]["entry_mode"] == "coverage_calibration"
    assert dashboard["recent_fills"][0]["entry_mode"] == "coverage_calibration"
    assert dashboard["positions"][0]["cost"] == pytest.approx(50)
