from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from app.core.config import Settings
from app.db.migrations import run_migrations
from app.stock_paper.models import Currency, Market, PaperFill, Side
from app.stock_paper.service import run_stock_paper_engine, stock_paper_dashboard, stock_paper_entry_chart
from app.stock_paper.store import StockPaperStore
from app.toss.store import TossStockStore
from app.stock_paper import service as stock_service
from app.stock_paper.models import MarketObservation


def settings_for(tmp_path) -> Settings:
    path = tmp_path / "stock-track.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    connection.close()
    return Settings(
        database_url=f"sqlite:///{path}",
        stock_paper_engine_enabled=True,
        toss_stock_scout_enabled=True,
        toss_client_id="test-client",
        toss_client_secret="test-secret",
    )


def test_tracks_have_independent_clocks_and_never_merge_with_crypto(tmp_path) -> None:
    settings = settings_for(tmp_path)
    result = stock_paper_dashboard(settings)
    assert {track["market"] for track in result["tracks"]} == {"KR", "US"}
    assert {track["currency"] for track in result["tracks"]} == {"KRW", "USD"}
    assert all(track["elapsed_days"] == 0 for track in result["tracks"])
    assert all(track["ends_at"] > track["started_at"] for track in result["tracks"])
    assert "crypto" not in result
    assert result["live_orders_enabled"] is False


def test_shared_entry_policy_missing_evidence_is_logged_not_filled(tmp_path) -> None:
    settings = settings_for(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    candidate = {
        "market": "US",
        "symbol": "AAPL",
        "price": 225.0,
        "observed_at": now,
        "warning_badges": [],
        "signals": [{"type": "momentum", "tone": "candidate", "change_pct": 2.5}],
    }
    result = run_stock_paper_engine(
        settings,
        {"US": {"market_state": "open", "groups": {"momentum": [candidate]}}, "KR": {"market_state": "closed", "groups": {}}},
    )
    dashboard = stock_paper_dashboard(settings)
    us = next(track for track in dashboard["tracks"] if track["market"] == "US")
    assert result["evaluated"] == 1
    assert result["rejected"] == 1
    assert dashboard["fill_count"] == 0
    assert us["rejection_reasons"]["evidence"] == 1


def test_benchmark_and_fx_keep_real_observation_metadata(tmp_path) -> None:
    settings = settings_for(tmp_path)
    store = StockPaperStore(settings.database_url)
    now = datetime(2026, 7, 20, 1, 0, tzinfo=timezone.utc)
    store.ensure_tracks(universe_version="test", initial_krw=100_000_000, initial_usd=100_000, now=now)
    store.update_benchmark(Market.US, 600.0, now)
    store.update_benchmark(Market.US, 606.0, now)
    store.record_fx(
        {
            "result": {
                "baseCurrency": "USD",
                "quoteCurrency": "KRW",
                "rate": "1380.5",
                "validFrom": now.isoformat(),
                "validUntil": now.isoformat(),
            }
        },
        now,
    )
    dashboard = store.dashboard()
    us = next(track for track in dashboard["tracks"] if track["market"] == "US")
    assert us["benchmark_return_pct"] == 1.0
    assert us["benchmark_proxy_symbol"] == "QQQ"


def test_entry_chart_joins_only_persisted_fill_and_observed_candles(tmp_path) -> None:
    settings = settings_for(tmp_path)
    stock_store = StockPaperStore(settings.database_url)
    stock_store.ensure_tracks(universe_version="test", initial_krw=100_000_000, initial_usd=100_000)
    filled_at = datetime(2026, 7, 20, 14, 31, 20, tzinfo=timezone.utc)
    stock_store.save_fill(
        PaperFill(
            order_id="order-aapl",
            symbol="AAPL",
            market=Market.US,
            currency=Currency.USD,
            side=Side.BUY,
            quantity=3,
            price=205.25,
            filled_at=filled_at,
            gross_amount=615.75,
            commission=0.15,
            transaction_tax=0,
            fx_rate_to_krw=None,
            fx_observed_at=None,
        )
    )
    candles = [
        {
            "opened_at": datetime(2026, 7, 20, 14, minute, tzinfo=timezone.utc).isoformat(),
            "open": 204 + minute / 100,
            "high": 206,
            "low": 203,
            "close": 205 + minute / 100,
            "volume": 1_000,
        }
        for minute in (29, 30, 31, 32)
    ]
    TossStockStore(settings.database_url).upsert_candles("US", "AAPL", "1m", "toss", filled_at.isoformat(), candles)

    result = stock_paper_entry_chart(settings, Market.US, "aapl")

    assert result["timeframe"] == "1m"
    assert result["source"] == "toss"
    assert [item["opened_at"] for item in result["candles"]] == [item["opened_at"] for item in candles]
    assert result["fills"] == [stock_store.list_fills()[0].payload()]
    assert result["empty_reason"] is None


def test_entry_chart_does_not_invent_data_for_unfilled_symbol(tmp_path) -> None:
    settings = settings_for(tmp_path)
    result = stock_paper_entry_chart(settings, Market.KR, "005930")
    assert result["candles"] == []
    assert result["fills"] == []
    assert result["empty_reason"] == "paper_fill_missing"


def test_coverage_lane_enters_with_real_execution_invariants_and_separate_mode(tmp_path, monkeypatch) -> None:
    settings = settings_for(tmp_path)
    now = datetime.now(timezone.utc)
    candidate = {
        "market": "US",
        "symbol": "AAPL",
        "price": 100.0,
        "observed_at": now.isoformat(),
        "warning_badges": [],
        "tradable": True,
        "role": "universe_member",
        "signals": [{"type": "universe_coverage", "tone": "observation"}],
    }
    monkeypatch.setattr(
        stock_service,
        "analyze_stock_candidate",
        lambda *_args, **_kwargs: {
            "status": "analyzed",
            "entry_score": 41,
            "rr_ratio": None,
            "invalidation": None,
            "source": "observed-test",
            "confluence": {"stance_state": {"stance": "neutral", "transitioning": False}, "long_evidence": [], "short_evidence": []},
        },
    )
    monkeypatch.setattr(
        stock_service,
        "_observation",
        lambda *_args, **_kwargs: MarketObservation(
            symbol="AAPL",
            market=Market.US,
            observed_at=now,
            session_open=True,
            minute_open=100,
            minute_high=101,
            minute_low=99,
            minute_close=100,
            minute_volume=10_000,
            bid=99.9,
            ask=100.1,
        ),
    )
    result = run_stock_paper_engine(
        settings,
        {
            "US": {"status": "observed", "market_state": "open", "observed_at": now.isoformat(), "coverage_candidates": [candidate]},
            "KR": {"status": "closed", "market_state": "closed", "observed_at": now.isoformat()},
        },
    )
    dashboard = stock_paper_dashboard(settings)
    assert result["coverage_attempted"] == 1
    assert result["coverage_entered"] == 1
    assert dashboard["recent_fills"][0]["entry_mode"] == "coverage"
    assert dashboard["recent_fills"][0]["price"] == 100.1
    strict = next(item for item in dashboard["mode_performance"] if item["market"] == "US" and item["entry_mode"] == "strict_signal")
    assert strict["position_count"] == 0
