from __future__ import annotations

import gzip
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.core.config import Settings
from app.db.models import DatabaseMaintenanceEvent, utc_now
from app.db.repository import Repository
from app.db.sqlite_utils import SQLITE_WRITE_LOCK, connect_sqlite

logger = logging.getLogger(__name__)


def sqlite_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///"):
        return None
    return Path(database_url.removeprefix("sqlite:///")).expanduser()


def run_database_backup(settings: Settings, repo: Repository) -> dict:
    source_path = sqlite_path(settings.database_url)
    if not settings.db_backup_enabled or source_path is None:
        event = DatabaseMaintenanceEvent(
            event_type="backup",
            status="skipped",
            message="SQLite backup disabled or database is not sqlite.",
            details={"database_url": settings.database_url},
        )
        repo.add_database_maintenance_event(event)
        return event.model_dump(mode="json")
    if not source_path.exists():
        event = DatabaseMaintenanceEvent(
            event_type="backup",
            status="error",
            message="SQLite database file does not exist.",
            details={"path": str(source_path)},
        )
        repo.add_database_maintenance_event(event)
        return event.model_dump(mode="json")

    backup_dir = Path(settings.db_backup_dir).expanduser()
    backup_dir.mkdir(parents=True, exist_ok=True)
    date_key = datetime.now(timezone.utc).strftime("%Y%m%d")
    final_path = backup_dir / f"fce_{date_key}.db.gz"
    temp_db_path = backup_dir / f".fce_{date_key}.tmp.db"
    try:
        with SQLITE_WRITE_LOCK:
            source = connect_sqlite(source_path)
            try:
                target = sqlite3.connect(temp_db_path)
                try:
                    source.backup(target)
                finally:
                    target.close()
            finally:
                source.close()
        table_counts = sqlite_table_counts(temp_db_path)
        with (
            temp_db_path.open("rb") as raw_file,
            gzip.open(final_path, "wb") as gz_file,
        ):
            gz_file.writelines(raw_file)
        restore_counts = smoke_test_backup(final_path)
        pruned = prune_old_backups(backup_dir, keep_days=settings.db_backup_keep_days)
        event = DatabaseMaintenanceEvent(
            event_type="backup",
            status="ok",
            message="SQLite gzip backup created and smoke-tested.",
            details={
                "path": str(final_path),
                "bytes": final_path.stat().st_size,
                "table_counts": table_counts,
                "restore_table_counts": restore_counts,
                "pruned_backups": pruned,
            },
            created_at=utc_now(),
        )
    except Exception as exc:
        logger.exception("database backup failed", extra={"path": str(source_path)})
        event = DatabaseMaintenanceEvent(
            event_type="backup",
            status="error",
            message=f"SQLite backup failed: {type(exc).__name__}: {exc}",
            details={"path": str(source_path)},
            created_at=utc_now(),
        )
    finally:
        temp_db_path.unlink(missing_ok=True)
    repo.add_database_maintenance_event(event)
    return event.model_dump(mode="json")


def enforce_retention(settings: Settings, repo: Repository) -> dict:
    source_path = sqlite_path(settings.database_url)
    details: dict[str, object]
    if source_path is not None and source_path.exists():
        with SQLITE_WRITE_LOCK, _connect(source_path) as connection:
            details = _apply_sqlite_retention(connection, settings)
    else:
        retention_days = max(1, int(settings.db_retention_days))
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        details = {
            "cutoff": cutoff.isoformat(),
            "derivative_snapshots_deleted": repo.delete_derivative_snapshots_before(cutoff),
            "sqlite_retention": "skipped",
        }
    logger.info("database retention applied", extra=details)
    event = DatabaseMaintenanceEvent(
        event_type="retention",
        status="ok",
        message="Retention applied without deleting judgment ledger or review data.",
        details=details,
        created_at=utc_now(),
    )
    repo.add_database_maintenance_event(event)
    return event.model_dump(mode="json")


def run_database_maintenance(settings: Settings, repo: Repository) -> dict:
    backup = run_database_backup(settings, repo)
    retention = enforce_retention(settings, repo)
    return {"backup": backup, "retention": retention}


def smoke_test_backup(backup_path: Path) -> dict[str, int]:
    with NamedTemporaryFile(suffix=".db", delete=True) as temp_file:
        with gzip.open(backup_path, "rb") as gz_file:
            temp_file.write(gz_file.read())
            temp_file.flush()
        return sqlite_table_counts(Path(temp_file.name))


def sqlite_table_counts(path: Path) -> dict[str, int]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        counts: dict[str, int] = {}
        for (table_name,) in rows:
            counts[str(table_name)] = int(connection.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0])
        return counts


def prune_old_backups(backup_dir: Path, keep_days: int) -> list[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, keep_days))
    deleted: list[str] = []
    for path in backup_dir.glob("fce_*.db.gz"):
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if modified >= cutoff:
            continue
        path.unlink()
        deleted.append(str(path))
    return deleted


def _apply_sqlite_retention(connection: sqlite3.Connection, settings: Settings) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    derivative_cutoff = now - timedelta(days=max(1, int(settings.db_retention_days)))
    deriv_metric_cutoff = now - timedelta(days=max(1, int(settings.db_deriv_metrics_raw_days)))
    trade_fill_cutoff = now - timedelta(days=max(1, int(settings.db_trade_fill_retention_days)))
    liquidation_cutoff = now - timedelta(days=max(1, int(settings.db_liquidation_event_retention_days)))
    alert_cutoff = now - timedelta(days=max(1, int(settings.db_alert_retention_days)))
    heartbeat_cutoff = now - timedelta(days=max(1, int(settings.db_worker_heartbeat_retention_days)))
    closed_snapshot_cutoff = now - timedelta(days=max(1, int(settings.db_closed_snapshot_retention_days)))

    details: dict[str, object] = {
        "derivative_cutoff": derivative_cutoff.isoformat(),
        "deriv_metric_cutoff": deriv_metric_cutoff.isoformat(),
        "trade_fill_cutoff": trade_fill_cutoff.isoformat(),
        "liquidation_event_cutoff": liquidation_cutoff.isoformat(),
        "alert_cutoff": alert_cutoff.isoformat(),
        "worker_heartbeat_cutoff": heartbeat_cutoff.isoformat(),
        "closed_snapshot_cutoff": closed_snapshot_cutoff.isoformat(),
    }
    details.update(
        _downsample_closed_position_snapshots(
            connection,
            closed_snapshot_cutoff,
            max(1, int(settings.db_snapshot_downsample_minutes)),
        )
    )
    details.update(
        _downsample_derivative_metrics(
            connection,
            deriv_metric_cutoff,
            max(1, int(settings.db_deriv_metrics_downsample_minutes)),
        )
    )
    details["derivative_snapshots_deleted"] = _delete(
        connection,
        "DELETE FROM derivative_snapshots WHERE as_of < ?",
        (derivative_cutoff.isoformat(),),
    )
    details["liquidation_events_deleted"] = _delete_if_table(
        connection,
        "liquidation_events",
        "DELETE FROM liquidation_events WHERE bucket_start < ?",
        (liquidation_cutoff.isoformat(),),
    )
    details["trade_fills_deleted"] = _delete_if_table(
        connection,
        "bitget_trade_fills",
        "DELETE FROM bitget_trade_fills WHERE timestamp < ?",
        (trade_fill_cutoff.isoformat(),),
    )
    details["trade_fill_fetch_state_deleted"] = _delete_if_table(
        connection,
        "bitget_trade_fill_fetch_state",
        "DELETE FROM bitget_trade_fill_fetch_state WHERE fetched_at < ?",
        (trade_fill_cutoff.isoformat(),),
    )
    details["alerts_deleted"] = _delete_expired_alerts(connection, alert_cutoff)
    details["worker_heartbeat_deleted"] = _delete_if_table(
        connection,
        "worker_heartbeat",
        "DELETE FROM worker_heartbeat WHERE updated_at < ?",
        (heartbeat_cutoff.isoformat(),),
    )
    return details


def _downsample_derivative_metrics(connection: sqlite3.Connection, cutoff: datetime, bucket_minutes: int) -> dict[str, object]:
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = 'deriv_metrics'").fetchone()
    if not exists:
        return {
            "deriv_metrics_before_downsample": 0,
            "deriv_metrics_after_downsample": 0,
            "deriv_metrics_deleted": 0,
            "deriv_metrics_downsample_minutes": bucket_minutes,
        }
    rows = connection.execute(
        """
        SELECT id, symbol, source, as_of
        FROM deriv_metrics
        WHERE as_of < ?
        ORDER BY symbol ASC, source ASC, as_of ASC
        """,
        (cutoff.isoformat(),),
    ).fetchall()
    keep_ids: set[str] = set()
    buckets: set[tuple[str, str, int]] = set()
    for row in rows:
        as_of = _parse_dt(row["as_of"])
        bucket = int(as_of.timestamp() // (bucket_minutes * 60))
        key = (str(row["symbol"]).upper(), str(row["source"]), bucket)
        if key in buckets:
            continue
        buckets.add(key)
        keep_ids.add(str(row["id"]))
    delete_ids = [str(row["id"]) for row in rows if str(row["id"]) not in keep_ids]
    deleted_total = 0
    if delete_ids:
        placeholders = ",".join("?" for _ in delete_ids)
        deleted_total = _delete(
            connection,
            f"DELETE FROM deriv_metrics WHERE id IN ({placeholders})",
            tuple(delete_ids),
        )
    return {
        "deriv_metrics_before_downsample": len(rows),
        "deriv_metrics_after_downsample": len(rows) - deleted_total,
        "deriv_metrics_deleted": deleted_total,
        "deriv_metrics_downsample_minutes": bucket_minutes,
    }


def _downsample_closed_position_snapshots(connection: sqlite3.Connection, cutoff: datetime, bucket_minutes: int) -> dict[str, object]:
    positions = connection.execute(
        """
        SELECT id
        FROM positions
        WHERE status != 'open'
          AND closed_at IS NOT NULL
          AND closed_at < ?
        """,
        (cutoff.isoformat(),),
    ).fetchall()
    preserved_snapshot_ids = {
        str(row["snapshot_id"])
        for row in connection.execute("SELECT snapshot_id FROM position_insights WHERE snapshot_id IS NOT NULL").fetchall()
        if row["snapshot_id"]
    }
    deleted_total = 0
    before_total = 0
    after_total = 0
    for position in positions:
        rows = connection.execute(
            "SELECT id, created_at FROM position_snapshots WHERE position_id = ? ORDER BY created_at ASC",
            (position["id"],),
        ).fetchall()
        before_total += len(rows)
        keep_ids: set[str] = set()
        buckets: set[int] = set()
        for row in rows:
            snapshot_id = str(row["id"])
            if snapshot_id in preserved_snapshot_ids:
                keep_ids.add(snapshot_id)
                continue
            created_at = _parse_dt(row["created_at"])
            bucket = int(created_at.timestamp() // (bucket_minutes * 60))
            if bucket not in buckets:
                buckets.add(bucket)
                keep_ids.add(snapshot_id)
        delete_ids = [str(row["id"]) for row in rows if str(row["id"]) not in keep_ids]
        if delete_ids:
            placeholders = ",".join("?" for _ in delete_ids)
            deleted_total += _delete(
                connection,
                f"DELETE FROM position_snapshots WHERE id IN ({placeholders})",
                tuple(delete_ids),
            )
        after_total += len(rows) - len(delete_ids)
    return {
        "closed_positions_downsampled": len(positions),
        "position_snapshots_before_downsample": before_total,
        "position_snapshots_after_downsample": after_total,
        "position_snapshots_deleted": deleted_total,
        "position_snapshot_downsample_minutes": bucket_minutes,
    }


def _delete_expired_alerts(connection: sqlite3.Connection, cutoff: datetime) -> int:
    preserved_alert_ids: set[str] = set()
    judgment_rows = connection.execute("SELECT payload FROM judgment_ledger WHERE type = 'alert_fired'").fetchall()
    for row in judgment_rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        source_id = payload.get("source_id")
        if source_id:
            preserved_alert_ids.add(str(source_id))
    old_alert_rows = connection.execute("SELECT id FROM alerts WHERE fired_at < ?", (cutoff.isoformat(),)).fetchall()
    delete_ids = [str(row["id"]) for row in old_alert_rows if str(row["id"]) not in preserved_alert_ids]
    if not delete_ids:
        return 0
    placeholders = ",".join("?" for _ in delete_ids)
    return _delete(
        connection,
        f"DELETE FROM alerts WHERE id IN ({placeholders})",
        tuple(delete_ids),
    )


def _delete_if_table(connection: sqlite3.Connection, table: str, query: str, params: tuple[str, ...]) -> int:
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)).fetchone()
    if not exists:
        return 0
    return _delete(connection, query, params)


def _delete(connection: sqlite3.Connection, query: str, params: tuple = ()) -> int:
    cursor = connection.execute(query, params)
    return int(cursor.rowcount or 0)


def _connect(path: Path) -> sqlite3.Connection:
    return connect_sqlite(path)


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
