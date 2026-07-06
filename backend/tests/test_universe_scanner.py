from __future__ import annotations

from datetime import timedelta

from app.backtest.signatures import signature_key
from app.core.config import Settings
from app.db.models import BacktestStat, CatalogSymbol, UniverseDiscovery, WatchlistItem, utc_now
from app.db.repository import MemoryRepository
from app.scout import universe as universe_module
from app.scout.universe import build_universe, evaluate_discovery_gate, run_universe_scan, universe_rate_budget


def _settings(**overrides) -> Settings:
    defaults = {
        "universe_scanner_enabled": True,
        "universe_crypto_symbol_limit": 40,
        "universe_stock_symbol_limit": 40,
        "universe_round_robin_batch_size": 12,
        "universe_min_quote_volume_24h": 1_000_000,
        "universe_min_confidence": 70,
        "universe_backtest_min_sample": 30,
        "universe_backtest_min_win_1r_pct": 55,
        "universe_daily_alert_limit": 3,
        "universe_symbol_cooldown_hours": 48,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _signature(asset_class: str = "crypto") -> dict:
    signature = {
        "engine": "liquidity",
        "event_type": "sweep_low",
        "strength_class": "Strong",
        "direction": "long",
        "asset_class": asset_class,
        "timeframe": "4h",
    }
    signature["key"] = signature_key(signature)
    signature["label"] = "유동성 저점 스윕 · Strong · 롱"
    return signature


def _analysis_payload(symbol: str = "BTCUSDT", asset_class: str = "crypto", quote_volume: float = 2_000_000) -> dict:
    signature = _signature(asset_class)
    analysis = {
        "symbol": symbol,
        "timeframe": "4h",
        "asset_class": asset_class,
        "mark_price": 100,
        "liquidity": {
            "sweeps": [
                {
                    "confirmed": True,
                    "side": "sell_side",
                    "grade": "Strong",
                    "confidence": 74,
                }
            ],
            "htf_range_sweeps": [],
        },
        "price_levels": {"support": [], "resistance": []},
        "wyckoff_markers": [],
        "harmonic_patterns": [],
    }
    return {
        "analysis": analysis,
        "summary": {
            "symbol": symbol,
            "asset_class": asset_class,
            "mark_price": 100,
            "quote_volume_24h": quote_volume,
        },
        "historical_backtest": {
            "active_signatures": [signature],
            "stats": [],
        },
    }


def _repo_with_catalog(symbols: list[tuple[str, str]]) -> MemoryRepository:
    repo = MemoryRepository()
    repo.replace_symbol_catalog(
        [
            CatalogSymbol(
                symbol=symbol,
                base_coin=symbol.replace("USDT", ""),
                quote_coin="USDT",
                status="normal",
                asset_class=asset_class,
            )
            for symbol, asset_class in symbols
        ]
    )
    return repo


def _seed_stat(repo: MemoryRepository, signature: dict, *, sample: int = 32, win: float = 59.0) -> None:
    repo.upsert_backtest_stat(
        BacktestStat(
            signature_key=signature["key"],
            symbol="BTCUSDT",
            asset_class=signature["asset_class"],
            scope="symbol",
            engine=signature["engine"],
            event_type=signature["event_type"],
            strength_class=signature["strength_class"],
            direction=signature["direction"],
            sample_size=sample,
            win_1r_pct=win,
            win_2r_pct=40.0,
            median_rr=1.6,
            payload={"signature": signature, "label": signature["label"]},
        )
    )


def test_universe_gate_blocks_each_quality_condition() -> None:
    settings = _settings()
    good_stat = {"sample_size": 32, "win_1r_pct": 59.0}

    assert (
        evaluate_discovery_gate(
            settings,
            confidence=69,
            stat=good_stat,
            quote_volume_24h=2_000_000,
            asset_class="crypto",
            earnings_blocked=False,
            daily_room=True,
            cooldown_active=False,
        ).quality_passed
        is False
    )
    assert (
        evaluate_discovery_gate(
            settings,
            confidence=74,
            stat={"sample_size": 29, "win_1r_pct": 59.0},
            quote_volume_24h=2_000_000,
            asset_class="crypto",
            earnings_blocked=False,
            daily_room=True,
            cooldown_active=False,
        ).quality_passed
        is False
    )
    assert (
        evaluate_discovery_gate(
            settings,
            confidence=74,
            stat={"sample_size": 32, "win_1r_pct": 54.9},
            quote_volume_24h=2_000_000,
            asset_class="crypto",
            earnings_blocked=False,
            daily_room=True,
            cooldown_active=False,
        ).quality_passed
        is False
    )
    assert (
        evaluate_discovery_gate(
            settings,
            confidence=74,
            stat=good_stat,
            quote_volume_24h=900_000,
            asset_class="crypto",
            earnings_blocked=False,
            daily_room=True,
            cooldown_active=False,
        ).quality_passed
        is False
    )
    assert (
        evaluate_discovery_gate(
            settings,
            confidence=74,
            stat=good_stat,
            quote_volume_24h=2_000_000,
            asset_class="stock",
            earnings_blocked=True,
            daily_room=True,
            cooldown_active=False,
        ).quality_passed
        is False
    )


def test_universe_scan_creates_alert_candidate_and_judgment_for_gate_pass() -> None:
    repo = _repo_with_catalog([("BTCUSDT", "crypto")])
    signature = _signature()
    _seed_stat(repo, signature)

    result = run_universe_scan(repo, _settings(), analysis_loader=lambda symbol, timeframe: _analysis_payload(symbol))

    assert len(result["_alert_candidate_objects"]) == 1
    discovery = repo.list_universe_discoveries()[0]
    assert discovery.status == "alerted"
    assert discovery.gate_passed is True
    assert discovery.sample_size == 32
    assert repo.list_judgments(position_id=universe_module.SCOUT_SENTINEL_POSITION_ID)[0].type == "universe_discovery"


def test_universe_daily_cap_stores_without_alerting() -> None:
    repo = _repo_with_catalog([("BTCUSDT", "crypto")])
    signature = _signature()
    _seed_stat(repo, signature)
    repo.upsert_universe_discovery(
        UniverseDiscovery(
            symbol="OLDUSDT",
            signature_key=signature["key"],
            signature=signature,
            status="alerted",
            gate_passed=True,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )

    result = run_universe_scan(repo, _settings(universe_daily_alert_limit=1), analysis_loader=lambda symbol, timeframe: _analysis_payload(symbol))

    assert result["_alert_candidate_objects"] == []
    discovery = repo.list_universe_discoveries(symbol="BTCUSDT")[0]
    assert discovery.status == "stored"
    failed_codes = [reason["code"] for reason in discovery.gate_reasons if not reason["passed"]]
    assert failed_codes == ["daily_alert_limit"]


def test_universe_symbol_cooldown_blocks_repeat_alert() -> None:
    repo = _repo_with_catalog([("BTCUSDT", "crypto")])
    signature = _signature()
    _seed_stat(repo, signature)
    repo.upsert_universe_discovery(
        UniverseDiscovery(
            symbol="BTCUSDT",
            signature_key=signature["key"],
            signature=signature,
            status="alerted",
            gate_passed=True,
            created_at=utc_now() - timedelta(hours=2),
            updated_at=utc_now() - timedelta(hours=2),
        )
    )

    result = run_universe_scan(repo, _settings(), analysis_loader=lambda symbol, timeframe: _analysis_payload(symbol))

    assert result["_alert_candidate_objects"] == []
    discovery = repo.list_universe_discoveries(symbol="BTCUSDT")[0]
    assert discovery.status == "stored"
    assert "symbol_cooldown" in [reason["code"] for reason in discovery.gate_reasons if not reason["passed"]]


def test_universe_build_excludes_existing_tracked_symbols() -> None:
    repo = _repo_with_catalog([("BTCUSDT", "crypto"), ("ETHUSDT", "crypto"), ("TSLAUSDT", "stock")])
    repo.upsert_watchlist_item(WatchlistItem(symbol="BTCUSDT", asset_class="crypto"))

    universe = build_universe(repo, _settings())

    symbols = [item["symbol"] for item in universe["symbols"]]
    assert "BTCUSDT" not in symbols
    assert {"ETHUSDT", "TSLAUSDT"}.issubset(set(symbols))
    assert {"symbol": "BTCUSDT", "reason": "already_tracked"} in universe["excluded"]


def test_universe_build_uses_ticker_quote_volume_ranking() -> None:
    repo = _repo_with_catalog([("AAAUSDT", "crypto"), ("BBBUSDT", "crypto"), ("CCCUSDT", "crypto")])

    universe = build_universe(
        repo,
        _settings(universe_crypto_symbol_limit=2),
        ticker_rows=[
            {"symbol": "AAAUSDT", "quote_volume_24h": 1_000_000},
            {"symbol": "BBBUSDT", "quote_volume_24h": 9_000_000},
            {"symbol": "CCCUSDT", "quote_volume_24h": 5_000_000},
        ],
    )

    assert [item["symbol"] for item in universe["symbols"]] == ["BBBUSDT", "CCCUSDT"]
    assert universe["symbols"][0]["quote_volume_24h"] == 9_000_000


def test_universe_round_robin_batches_symbols() -> None:
    universe_module._ROUND_ROBIN_CURSOR = 0
    repo = _repo_with_catalog([(f"SYM{index}USDT", "crypto") for index in range(5)])
    settings = _settings(universe_round_robin_batch_size=2)
    seen: list[str] = []

    def load(symbol: str, timeframe: str) -> dict:
        seen.append(symbol)
        return _analysis_payload(symbol)

    run_universe_scan(repo, settings, analysis_loader=load)
    run_universe_scan(repo, settings, analysis_loader=load)

    assert seen[:2] == ["SYM0USDT", "SYM1USDT"]
    assert seen[2:4] == ["SYM2USDT", "SYM3USDT"]


def test_universe_rate_budget_documents_round_robin() -> None:
    budget = universe_rate_budget(_settings(universe_round_robin_batch_size=12), 80)

    assert budget["requests_per_symbol"] == 3
    assert budget["requests_per_tick"] == 36
    assert budget["round_robin_required"] is True
    assert budget["full_cycle_minutes"] == 210
