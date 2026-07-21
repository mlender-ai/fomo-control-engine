from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.api.deps import configure_runtime
from app.core.config import Settings
import app.db.maintenance as maintenance_module
from app.db.maintenance import enforce_retention, run_database_backup
import app.db.migrations as migration_module
from app.db.migrations import DatabaseMigrationError, run_migrations
from app.db.models import (
    AlertRecord,
    DerivativeDataSnapshot,
    DerivativeMetric,
    Direction,
    JudgmentLedgerEntry,
    LiquidationEvent,
    Position,
    PositionSnapshot,
    PositionStatus,
)
from app.db.repository import MemoryRepository, create_repository
from app.db.sqlite_utils import connect_sqlite
from app.derivatives.engine import flow_summary
from app.exchange.mock import MockMarketDataProvider
from app.marketdata.coinglass import CoinglassProvider
from app.marketdata.signals import build_derivative_signals, funding_state, percentile_rank
from app.notify.alerts import AlertEngine
from app.notify.state import NotificationState
from app.services import runtime as service
from app.services.runtime import _coinglass_budget


class FakeTelegramSender:
    enabled = True

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_to_all(self, text: str, *, reply_markup=None) -> int:
        self.messages.append(text)
        return 1


def test_migration_failure_aborts_startup(monkeypatch, tmp_path) -> None:
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / "0001_bad.sql").write_text("CREATE TABLE broken (", encoding="utf-8")
    monkeypatch.setattr(migration_module, "MIGRATIONS_DIR", migration_dir)

    with sqlite3.connect(tmp_path / "bad.db") as connection:
        with pytest.raises(DatabaseMigrationError):
            run_migrations(connection)


def test_sqlite_migration_and_derivative_snapshot_repository(tmp_path) -> None:
    db_path = tmp_path / "phase_b.db"
    repo = create_repository(f"sqlite:///{db_path}")
    snapshot = DerivativeDataSnapshot(
        symbol="BASEDUSDT",
        provider="bitget",
        tier="bitget_public",
        as_of=datetime(2026, 7, 6, 1, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 6, 1, 0, tzinfo=timezone.utc),
        open_interest=1_250_000,
        funding_rate=0.0008,
        long_short_ratio=1.18,
        source_status="ok",
    )
    newer_created = DerivativeDataSnapshot(
        symbol="BASEDUSDT",
        provider="bitget",
        tier="bitget_public",
        as_of=datetime(2026, 7, 6, 0, 30, tzinfo=timezone.utc),
        created_at=datetime(2026, 7, 6, 1, 5, tzinfo=timezone.utc),
        open_interest=1_260_000,
        funding_rate=0.0009,
        long_short_ratio=1.2,
        source_status="ok",
    )

    repo.add_derivative_snapshot(snapshot)
    repo.add_derivative_snapshot(newer_created)
    latest = repo.latest_derivative_snapshot("basedusdt", provider="bitget")

    assert latest is not None
    assert latest.symbol == "BASEDUSDT"
    assert latest.id == newer_created.id
    assert latest.open_interest == 1_260_000
    assert latest.funding_rate == 0.0009
    with sqlite3.connect(db_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        migrations = {row[0] for row in connection.execute("SELECT version FROM schema_version")}
    assert "derivative_snapshots" in tables
    assert "deriv_metrics" in tables
    assert "liquidation_events" in tables
    assert "database_maintenance_events" in tables
    assert "0001_baseline" in migrations
    assert "0002_derivative_metrics" in migrations


def test_database_backup_and_retention_records_events(tmp_path) -> None:
    db_path = tmp_path / "phase_b.db"
    backup_dir = tmp_path / "backups"
    repo = create_repository(f"sqlite:///{db_path}")
    old_snapshot = DerivativeDataSnapshot(
        symbol="BTCUSDT",
        provider="bitget",
        tier="bitget_public",
        as_of=datetime.now(timezone.utc) - timedelta(days=40),
        funding_rate=0.0001,
    )
    fresh_snapshot = DerivativeDataSnapshot(
        symbol="BTCUSDT",
        provider="bitget",
        tier="bitget_public",
        as_of=datetime.now(timezone.utc),
        funding_rate=0.0002,
    )
    repo.add_derivative_snapshot(old_snapshot)
    repo.add_derivative_snapshot(fresh_snapshot)
    settings = Settings(
        database_url=f"sqlite:///{db_path}",
        db_backup_dir=str(backup_dir),
        db_retention_days=30,
    )

    backup = run_database_backup(settings, repo)
    retention = enforce_retention(settings, repo)

    assert backup["status"] == "ok"
    assert backup_dir.exists()
    assert list(backup_dir.glob("fce_*.db.gz"))
    assert "restore_table_counts" in backup["details"]
    assert backup["details"]["restore_table_counts"] == backup["details"]["table_counts"]
    assert retention["details"]["derivative_snapshots_deleted"] == 1
    assert retention["details"]["permanent_tables_verified"] is True
    remaining = repo.list_derivative_snapshots("BTCUSDT")
    assert [item.id for item in remaining] == [fresh_snapshot.id]
    event_types = {event.event_type for event in repo.list_database_maintenance_events(limit=10)}
    assert {"backup", "retention"} <= event_types


def test_retention_downsamples_snapshots_and_preserves_judgment_data(tmp_path) -> None:
    db_path = tmp_path / "retention.db"
    repo = create_repository(f"sqlite:///{db_path}")
    now = datetime.now(timezone.utc)
    closed_position = repo.add_position(
        Position(
            symbol="BTCUSDT",
            direction=Direction.long,
            entry_price=100,
            quantity=1,
            status=PositionStatus.closed,
            opened_at=now - timedelta(days=45),
            closed_at=now - timedelta(days=35),
        )
    )
    open_position = repo.add_position(
        Position(
            symbol="ETHUSDT",
            direction=Direction.short,
            entry_price=100,
            quantity=1,
            status=PositionStatus.open,
            opened_at=now - timedelta(days=45),
        )
    )
    closed_snapshots = []
    for index in range(12):
        created_at = now - timedelta(days=40, minutes=10 * index)
        closed_snapshots.append(
            repo.add_position_snapshot(
                PositionSnapshot(
                    position_id=closed_position.id,
                    symbol="BTCUSDT",
                    as_of=created_at,
                    created_at=created_at,
                    mark_price=100 + index,
                    health_score=50,
                    status_label="closed",
                    risk_score=20,
                    score_json={},
                    analysis_json={},
                )
            )
        )
        repo.add_position_snapshot(
            PositionSnapshot(
                position_id=open_position.id,
                symbol="ETHUSDT",
                as_of=created_at,
                created_at=created_at,
                mark_price=100 - index,
                health_score=50,
                status_label="open",
                risk_score=20,
                score_json={},
                analysis_json={},
            )
        )
    old_alert = repo.add_alert(
        AlertRecord(
            symbol="BTCUSDT",
            rule_id="status_worsened",
            severity="warn",
            fired_at=now - timedelta(days=100),
            created_at=now - timedelta(days=100),
        )
    )
    preserved_alert = repo.add_alert(
        AlertRecord(
            symbol="BTCUSDT",
            position_id=closed_position.id,
            rule_id="invalidation_breach",
            severity="critical",
            fired_at=now - timedelta(days=100),
            created_at=now - timedelta(days=100),
        )
    )
    judgment = repo.add_judgment(
        JudgmentLedgerEntry(
            judgment_id="alert-preserved",
            position_id=closed_position.id,
            source_type="alert",
            source_id=str(preserved_alert.id),
            as_of=preserved_alert.fired_at,
            type="alert_fired",
            claim={"alert_id": str(preserved_alert.id)},
        )
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO bitget_trade_fills (symbol, trade_id, timestamp, payload, fetched_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "BTCUSDT",
                "old-fill",
                (now - timedelta(days=10)).isoformat(),
                "{}",
                (now - timedelta(days=10)).isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO worker_heartbeat (job_name, status, updated_at)
            VALUES (?, ?, ?)
            """,
            ("old_job", "ok", (now - timedelta(days=20)).isoformat()),
        )
    settings = Settings(database_url=f"sqlite:///{db_path}", db_backup_dir=str(tmp_path / "backups"))

    retention = enforce_retention(settings, repo)

    closed_remaining = repo.list_position_snapshots(closed_position.id, limit=50)
    open_remaining = repo.list_position_snapshots(open_position.id, limit=50)
    alerts = repo.list_alerts(limit=10)
    judgments = repo.list_judgments(closed_position.id)
    with sqlite3.connect(db_path) as connection:
        trade_fill_count = connection.execute("SELECT COUNT(*) FROM bitget_trade_fills").fetchone()[0]
        heartbeat_count = connection.execute("SELECT COUNT(*) FROM worker_heartbeat WHERE job_name = 'old_job'").fetchone()[0]

    assert retention["details"]["position_snapshots_deleted"] > 0
    assert retention["details"]["position_snapshot_aggregate_verified"] is True
    assert 1 <= len(closed_remaining) < len(closed_snapshots)
    assert len(open_remaining) == 12
    assert {alert.id for alert in alerts} == {preserved_alert.id}
    assert judgments[0].id == judgment.id
    assert old_alert.id not in {alert.id for alert in alerts}
    assert trade_fill_count == 0
    assert heartbeat_count == 0


def test_retention_preserves_permanent_ledger_and_competition_tables(tmp_path) -> None:
    db_path = tmp_path / "permanent.db"
    repo = create_repository(f"sqlite:///{db_path}")
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    statements = (
        (
            "INSERT INTO judgment_ledger (id, position_id, judgment_id, as_of, type, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("j1", "p1", "judgment-1", old, "paper_entry", old, "{}"),
        ),
        (
            "INSERT INTO judgment_scores (id, position_id, trade_id, judgment_id, outcome, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("js1", "p1", "t1", "judgment-1", "correct", old, "{}"),
        ),
        (
            "INSERT INTO paper_trades (id, symbol, timeframe, status, entry_bar_at, exit_at, updated_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("pt1", "BTCUSDT", "4h", "closed", old, old, old, "{}"),
        ),
        (
            "INSERT INTO paper_engine_states (symbol, timeframe, state, updated_at) VALUES (?, ?, ?, ?)",
            ("BTCUSDT", "4h", "{}", old),
        ),
        (
            "INSERT INTO paper_gate_funnel (symbol, timeframe, bar_at, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            ("BTCUSDT", "4h", old, "{}", old),
        ),
        (
            "INSERT INTO backtest_stats (id, signature_key, symbol, timeframe, asset_class, scope, generated_at, sample_size, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("bt1", "sig", "BTCUSDT", "4h", "crypto", "symbol", old, 30, "{}"),
        ),
        (
            "INSERT INTO trades (id, position_id, symbol, created_at, payload) VALUES (?, ?, ?, ?, ?)",
            ("t1", "p1", "BTCUSDT", old, "{}"),
        ),
        (
            "INSERT INTO autonomy_log (id, signature_key, new_state, transition, autonomous, created_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("a1", "sig", "validated", "promotion_applied", 0, old, "{}"),
        ),
    )
    with sqlite3.connect(db_path) as connection:
        for statement, params in statements:
            connection.execute(statement, params)

    result = enforce_retention(Settings(database_url=f"sqlite:///{db_path}"), repo)

    assert result["details"]["permanent_tables_verified"] is True
    assert result["details"]["permanent_table_counts"] == {
        "judgment_ledger": 1,
        "judgment_scores": 1,
        "paper_trades": 1,
        "paper_engine_states": 1,
        "paper_gate_funnel": 1,
        "backtest_stats": 1,
        "trades": 1,
        "autonomy_log": 1,
    }


def test_backup_failure_records_warn_alert(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "backup-failure.db"
    repo = create_repository(f"sqlite:///{db_path}")
    settings = Settings(database_url=f"sqlite:///{db_path}", db_backup_dir=str(tmp_path / "backups"))

    def fail_gzip(*_args, **_kwargs):
        raise OSError("gzip unavailable")

    monkeypatch.setattr(maintenance_module.gzip, "open", fail_gzip)
    result = run_database_backup(settings, repo)

    assert result["status"] == "error"
    warning = next(alert for alert in repo.list_alerts(limit=10) if alert.rule_id == "database_backup_failed")
    assert warning.severity == "warn"
    assert "gzip unavailable" in warning.payload["message"]
    assert not list((tmp_path / "backups").glob("fce_*.db.gz"))


def test_sqlite_uses_wal_and_busy_timeout(tmp_path) -> None:
    db_path = tmp_path / "sqlite-pragmas.db"
    create_repository(f"sqlite:///{db_path}")

    with connect_sqlite(db_path) as connection:
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        busy_timeout = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])

    assert journal_mode == "wal"
    assert busy_timeout == 5000
    assert Settings().db_trade_fill_retention_days == 7


def test_retention_downsamples_derivative_metrics_and_prunes_liquidations(
    tmp_path,
) -> None:
    db_path = tmp_path / "derivatives-retention.db"
    repo = create_repository(f"sqlite:///{db_path}")
    now = datetime.now(timezone.utc)
    for index in range(12):
        repo.add_derivative_metric(
            DerivativeMetric(
                symbol="BTCUSDT",
                source="bitget",
                tier="bitget_public",
                as_of=now - timedelta(days=120, hours=index),
                open_interest=1000 + index,
            )
        )
    old_event = repo.add_liquidation_event(
        LiquidationEvent(
            symbol="BTCUSDT",
            source="coinglass",
            interval="1h",
            bucket_start=now - timedelta(days=40),
            long_liquidation_usd=100,
            short_liquidation_usd=50,
        )
    )
    fresh_event = repo.add_liquidation_event(
        LiquidationEvent(
            symbol="BTCUSDT",
            source="coinglass",
            interval="1h",
            bucket_start=now,
            long_liquidation_usd=10,
            short_liquidation_usd=20,
        )
    )
    settings = Settings(
        database_url=f"sqlite:///{db_path}",
        db_deriv_metrics_raw_days=90,
        db_liquidation_event_retention_days=30,
    )

    retention = enforce_retention(settings, repo)

    metrics = repo.list_derivative_metrics("BTCUSDT", source="bitget", limit=50)
    liquidations = repo.list_liquidation_events("BTCUSDT", limit=10)

    assert retention["details"]["deriv_metrics_deleted"] > 0
    assert len(metrics) < 12
    assert {event.id for event in liquidations} == {fresh_event.id}
    assert old_event.id not in {event.id for event in liquidations}


def test_mock_provider_refresh_derivative_data_keeps_tier_lock() -> None:
    repo = MemoryRepository()
    configure_runtime(repo=repo, provider=MockMarketDataProvider())

    payload = service.refresh_derivative_data()
    latest = repo.latest_derivative_snapshot("BTCUSDT", provider="bitget")
    coinglass = repo.latest_derivative_snapshot("BTCUSDT", provider="coinglass")

    assert payload["enabled"] is True
    assert latest is not None
    assert latest.source_status == "locked"
    assert "Bitget provider is not active" in " ".join(latest.notes)
    assert coinglass is not None
    assert coinglass.source_status == "locked"


def test_flow_summary_classifies_funding_oi_and_ratio() -> None:
    summary = flow_summary(
        DerivativeDataSnapshot(
            symbol="ETHUSDT",
            provider="bitget",
            tier="bitget_public",
            funding_rate=0.012,
            open_interest_change_pct=3.4,
            long_short_ratio=1.3,
        )
    )

    assert summary["funding_state"] == "long_overheated"
    assert summary["oi_state"] == "rising"
    assert summary["long_short_state"] == "long_heavy"
    assert "펀딩 과열" in summary["headline"]


def test_coinglass_without_key_returns_locked_metric() -> None:
    collection = CoinglassProvider(Settings(database_url="memory://", coinglass_api_key="")).collect("BTCUSDT")

    assert collection.metrics[0].source_status == "locked"
    assert collection.snapshot is not None
    assert collection.snapshot.source_status == "locked"
    assert collection.requests_used == 0


def test_coinglass_provider_parses_metric_liquidations_and_heatmap(monkeypatch) -> None:
    provider = CoinglassProvider(Settings(database_url="memory://", coinglass_api_key="cg-key"))

    def fake_get(path, params, feature):
        payloads = {
            "subscription": {
                "code": "0",
                "data": {"level": "HOBBYIST", "expired": False},
            },
            "aggregated_oi": {
                "code": "0",
                "data": [
                    {
                        "exchange": "All",
                        "symbol": "BTC",
                        "open_interest_usd": 100_000_000,
                        "open_interest_quantity": 1500,
                        "open_interest_change_percent_24h": 2.5,
                    }
                ],
            },
            "top_trader_ls_ratio": {
                "code": "0",
                "data": [{"time": 1780000000000, "top_account_long_short_ratio": 1.7}],
            },
            "oi_weighted_funding": {
                "code": "0",
                "data": [{"time": 1780000000000, "close": "0.0008"}],
            },
            "liquidation_history": {
                "code": "0",
                "data": [
                    {
                        "time": 1780000000000,
                        "aggregated_long_liquidation_usd": 1200,
                        "aggregated_short_liquidation_usd": 900,
                    }
                ],
            },
            "liquidation_heatmap": {
                "code": "0",
                "data": {
                    "y_axis": [99.0, 100.0, 101.0],
                    "liquidation_leverage_data": [[1, 0, 50], [1, 2, 100]],
                    "price_candlesticks": [[1780000000, "100", "102", "99", "100", "1000"]],
                },
            },
        }
        return {"status": "ok", "payload": payloads[feature], "message": "ok"}

    monkeypatch.setattr(provider, "_get", fake_get)
    collection = provider.collect("BTCUSDT")
    metric = collection.metrics[0]

    assert collection.requests_used == 6
    assert metric.open_interest_value == 100_000_000
    assert metric.oi_change_pct == 2.5
    assert metric.top_ls == 1.7
    assert metric.oi_weighted_funding == 0.0008
    assert collection.liquidation_events[0].long_liquidation_usd == 1200
    assert collection.snapshot is not None
    assert collection.snapshot.liquidation_clusters[0]["sources"] == ["liq_cluster"]


def test_derivative_signals_use_percentile_and_four_quadrant_classification() -> None:
    base = datetime(2026, 7, 6, tzinfo=timezone.utc)
    history = [
        DerivativeMetric(
            symbol="BTCUSDT",
            source="bitget",
            tier="bitget_public",
            as_of=base - timedelta(days=index),
            funding=0.003 if index == 0 else 0.0001 * index,
            oi_change_pct=-2.0 if index == 0 else 0.1,
            taker_ls=1.4,
            raw_json={"price_change_pct_24h": 3.0} if index == 0 else {},
        )
        for index in range(20)
    ]

    signals = build_derivative_signals(history)

    assert signals["oi_price_divergence"]["state"] == "price_up_oi_down"
    assert signals["funding_state"]["state"] in {"overheated", "extreme"}
    assert signals["crowding_score"]["score"] > 0


def test_flat_zero_funding_history_is_neutral_not_extreme() -> None:
    base = datetime(2026, 7, 21, tzinfo=timezone.utc)
    history = [
        DerivativeMetric(
            symbol="NBISUSDT",
            source="bitget",
            tier="bitget_public",
            as_of=base - timedelta(minutes=index * 5),
            funding=0.0,
            funding_interval_hours=8,
        )
        for index in range(20)
    ]

    state = funding_state(history[0], history)
    signals = build_derivative_signals(history)

    assert percentile_rank(0.0, [0.0] * 20) == 50.0
    assert state is not None
    assert state["abs_percentile_30d"] == 50.0
    assert state["state"] == "neutral"
    assert signals["crowding_score"]["components"]["funding_percentile"] == 50.0


def test_coinglass_rate_budget_supports_twenty_symbols_at_five_minute_interval() -> None:
    settings = Settings(
        database_url="memory://",
        coinglass_api_key="key",
        derivative_tracking_interval_seconds=300,
        coinglass_rate_limit_per_minute=30,
        coinglass_requests_per_symbol=6,
    )

    budget = _coinglass_budget(settings, symbol_count=20)
    crowded = _coinglass_budget(settings, symbol_count=30)

    assert budget["requests_per_tick"] == 150
    assert budget["max_symbols_per_tick"] == 25
    assert budget["round_robin_required"] is False
    assert crowded["round_robin_required"] is True


@pytest.mark.asyncio
async def test_derivative_collection_hook_does_not_alert_without_position_direction() -> None:
    repository = MemoryRepository()
    configure_runtime(repo=repository, provider=MockMarketDataProvider())
    settings = Settings(
        database_url="memory://",
        telegram_alerts_enabled=True,
        telegram_bot_token="token",
        telegram_chat_id="123",
        alert_default_cooldown_minutes=1,
        alert_funding_extreme_abs_rate=0.01,
    )
    sender = FakeTelegramSender()
    state = NotificationState()
    clock = [datetime(2026, 7, 6, 1, 0, tzinfo=timezone.utc)]
    engine = AlertEngine(settings, sender, state, now_provider=lambda: clock[0])
    overheated = [
        {
            "symbol": "BASEDUSDT",
            "provider": "bitget",
            "as_of": clock[0].isoformat(),
            "funding_rate": 0.012,
        }
    ]

    assert await engine.evaluate_derivatives(overheated) == 0
    assert repository.list_alerts(limit=10) == []
    assert sender.messages == []
