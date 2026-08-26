from __future__ import annotations

import gzip
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.core.config import Settings
from app.db.models import AlertRecord, DatabaseMaintenanceEvent, utc_now
from app.db.repository import Repository
from app.db.sqlite_utils import SQLITE_WRITE_LOCK, connect_sqlite

logger = logging.getLogger(__name__)

PERMANENT_TABLES = (
    "judgment_ledger",
    "judgment_scores",
    "paper_trades",
    "paper_engine_states",
    "paper_gate_funnel",
    "backtest_stats",
    "trades",
    "autonomy_log",
)


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
        _record_backup_event(repo, event)
        return event.model_dump(mode="json")
    if not source_path.exists():
        event = DatabaseMaintenanceEvent(
            event_type="backup",
            status="error",
            message="SQLite database file does not exist.",
            details={"path": str(source_path)},
        )
        _record_backup_event(repo, event)
        return event.model_dump(mode="json")

    backup_dir = Path(settings.db_backup_dir).expanduser()
    backup_dir.mkdir(parents=True, exist_ok=True)
    date_key = datetime.now(timezone.utc).strftime("%Y%m%d")
    final_path = backup_dir / f"fce_{date_key}.db.gz"
    temp_db_path = backup_dir / f".fce_{date_key}.tmp.db"
    temp_gzip_path = backup_dir / f".fce_{date_key}.tmp.db.gz"
    temp_db_path.unlink(missing_ok=True)
    temp_gzip_path.unlink(missing_ok=True)
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
            gzip.open(temp_gzip_path, "wb") as gz_file,
        ):
            gz_file.writelines(raw_file)
        restore_counts = smoke_test_backup(temp_gzip_path)
        if restore_counts != table_counts:
            raise RuntimeError("restored backup table counts do not match backup source")
        temp_gzip_path.replace(final_path)
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
        temp_gzip_path.unlink(missing_ok=True)
    _record_backup_event(repo, event)
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
    permanent_before = _table_counts(connection, PERMANENT_TABLES)
    derivative_cutoff = now - timedelta(days=max(1, int(settings.db_retention_days)))
    deriv_metric_cutoff = now - timedelta(days=max(1, int(settings.db_deriv_metrics_raw_days)))
    trade_fill_cutoff = now - timedelta(days=max(1, int(settings.db_trade_fill_retention_days)))
    liquidation_cutoff = now - timedelta(days=max(1, int(settings.db_liquidation_event_retention_days)))
    alert_cutoff = now - timedelta(days=max(1, int(settings.db_alert_retention_days)))
    heartbeat_cutoff = now - timedelta(days=max(1, int(settings.db_worker_heartbeat_retention_days)))
    closed_snapshot_cutoff = now - timedelta(days=max(1, int(settings.db_closed_snapshot_retention_days)))
    depth_observation_cutoff = now - timedelta(days=max(1, int(settings.db_depth_observation_retention_days)))

    details: dict[str, object] = {
        "derivative_cutoff": derivative_cutoff.isoformat(),
        "deriv_metric_cutoff": deriv_metric_cutoff.isoformat(),
        "trade_fill_cutoff": trade_fill_cutoff.isoformat(),
        "liquidation_event_cutoff": liquidation_cutoff.isoformat(),
        "alert_cutoff": alert_cutoff.isoformat(),
        "worker_heartbeat_cutoff": heartbeat_cutoff.isoformat(),
        "closed_snapshot_cutoff": closed_snapshot_cutoff.isoformat(),
        "depth_observation_cutoff": depth_observation_cutoff.isoformat(),
    }
    details.update(
        _downsample_closed_position_snapshots(
            connection,
            closed_snapshot_cutoff,
            max(1, int(settings.db_snapshot_downsample_minutes)),
        )
    )
    details.update(
        _downsample_open_position_snapshots(
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
    details.update(_delete_expired_reports(connection, derivative_cutoff))
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
    # WO-FCE-RISK-SIZING-01 Phase 4-1. 호가 관측은 건당 payload 가 크므로 반드시 만료시킨다.
    details["execution_depth_observations_deleted"] = _delete_if_table(
        connection,
        "execution_depth_observations",
        "DELETE FROM execution_depth_observations WHERE observed_at < ?",
        (depth_observation_cutoff.isoformat(),),
    )
    # WO-FCE-REPLAY-DEPTH-01 4-2 후속 · C7: 리텐션이 수집 잡 경로에만 있으면 **잡이 꺼져 있는
    # 동안(기본값이 꺼짐이다) 상한도 함께 멈춘다.** 날짜가 아니라 봉 수로 자르는 이유는 재판정에
    # 필요한 것이 "최근 N일"이 아니라 "연속된 N봉"이기 때문이다.
    details["stance_history_candles_deleted"] = _cap_stance_history_candles(
        connection,
        max(200, int(settings.replay_history_retention_bars)),
    )
    details["stance_history_retention_bars"] = max(200, int(settings.replay_history_retention_bars))
    # 2026-08-24 장애 후속: 행 수 상한으로 자른다. 날짜 창으로는 하루에 몰린 폭주를 못 막는다.
    details.update(
        _trim_stock_paper_events(
            connection,
            keep_rows=max(1_000, int(getattr(settings, "db_stock_paper_event_retention_rows", 200_000))),
            delete_budget=max(10_000, int(getattr(settings, "db_stock_paper_event_delete_budget", 2_000_000))),
        )
    )
    details["alerts_deleted"] = _delete_expired_alerts(connection, alert_cutoff)
    details["worker_heartbeat_deleted"] = _delete_if_table(
        connection,
        "worker_heartbeat",
        "DELETE FROM worker_heartbeat WHERE updated_at < ?",
        (heartbeat_cutoff.isoformat(),),
    )
    permanent_after = _table_counts(connection, PERMANENT_TABLES)
    if permanent_after != permanent_before:
        raise RuntimeError("retention attempted to mutate a permanent table")
    details["permanent_tables_verified"] = True
    details["permanent_table_counts"] = permanent_after
    # 삭제로 생긴 빈 페이지를 OS로 반환(파일 실제 축소). auto_vacuum=INCREMENTAL DB 에서만 동작(그 외 무해 no-op).
    # 12.8GB 비대 사건(2026-07-23): 리텐션 DELETE 는 있었으나 회수가 없어 파일이 안 줄었다.
    try:
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA incremental_vacuum")
        connection.commit()
        details["incremental_vacuum"] = "ok"
    except Exception as exc:  # 회수 실패가 리텐션 자체를 무효화하지 않게 격리
        details["incremental_vacuum"] = f"skipped: {type(exc).__name__}: {exc}"
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
    deleted_total = _delete_by_ids(connection, "deriv_metrics", "id", delete_ids)
    _verify_remaining_ids(
        connection,
        table="deriv_metrics",
        id_column="id",
        where="as_of < ?",
        params=(cutoff.isoformat(),),
        expected=keep_ids,
    )
    return {
        "deriv_metrics_before_downsample": len(rows),
        "deriv_metrics_after_downsample": len(rows) - deleted_total,
        "deriv_metrics_deleted": deleted_total,
        "deriv_metrics_downsample_minutes": bucket_minutes,
        "deriv_metrics_aggregate_verified": True,
    }


def _delete_expired_reports(connection: sqlite3.Connection, cutoff: datetime) -> dict[str, object]:
    """리텐션 사각지대였던 reports 를 정리한다 — 심볼별 최신 1건과 참조분은 보존.

    2026-07-30 dbstat 실측: 9.4GB 중 reports 가 2.37GB(26%)였고 리텐션이 아예 없었다.
    읽기 경로는 latest_report(symbol)·recent_reports(limit)·get_report(id) 뿐이라
    심볼별 최신과 research_runs 참조분을 남기면 조회 동작은 그대로 보존된다.
    """
    preserved: set[str] = set()
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_runs'").fetchone():
        preserved.update(
            str(row["report_id"])
            for row in connection.execute("SELECT report_id FROM research_runs WHERE report_id IS NOT NULL").fetchall()
            if row["report_id"]
        )
    preserved.update(
        str(row["id"])
        for row in connection.execute(
            """
            SELECT r.id
            FROM reports AS r
            JOIN (SELECT symbol, MAX(created_at) AS max_created FROM reports GROUP BY symbol) AS latest
              ON latest.symbol = r.symbol AND latest.max_created = r.created_at
            """
        ).fetchall()
    )
    old_ids = [str(row["id"]) for row in connection.execute("SELECT id FROM reports WHERE created_at < ?", (cutoff.isoformat(),)).fetchall()]
    delete_ids = [report_id for report_id in old_ids if report_id not in preserved]
    return {
        "reports_deleted": _delete_by_ids(connection, "reports", "id", delete_ids),
        "reports_preserved": len(preserved),
    }


def _downsample_open_position_snapshots(connection: sqlite3.Connection, cutoff: datetime, bucket_minutes: int) -> dict[str, object]:
    """열린 포지션의 오래된 스냅샷도 다운샘플한다.

    기존 다운샘플은 ``status != 'open'`` 만 대상이라 열린 포지션의 스냅샷은 나이와
    무관하게 영구 누적됐다(2026-07-30 실측: 열린 4개 포지션이 78,954행/905MB).
    최근 구간은 손대지 않고 cutoff 이전만 버킷당 1건으로 줄인다.
    """
    positions = connection.execute("SELECT id FROM positions WHERE status = 'open'").fetchall()
    preserved_snapshot_ids = {
        str(row["snapshot_id"])
        for row in connection.execute("SELECT snapshot_id FROM position_insights WHERE snapshot_id IS NOT NULL").fetchall()
        if row["snapshot_id"]
    }
    deleted_total = 0
    before_total = 0
    for position in positions:
        rows = connection.execute(
            "SELECT id, created_at FROM position_snapshots WHERE position_id = ? AND created_at < ? ORDER BY created_at ASC",
            (position["id"], cutoff.isoformat()),
        ).fetchall()
        before_total += len(rows)
        keep_ids: set[str] = set()
        buckets: set[int] = set()
        for row in rows:
            snapshot_id = str(row["id"])
            if snapshot_id in preserved_snapshot_ids:
                keep_ids.add(snapshot_id)
                continue
            bucket = int(_parse_dt(row["created_at"]).timestamp() // (bucket_minutes * 60))
            if bucket not in buckets:
                buckets.add(bucket)
                keep_ids.add(snapshot_id)
        delete_ids = [str(row["id"]) for row in rows if str(row["id"]) not in keep_ids]
        deleted_total += _delete_by_ids(connection, "position_snapshots", "id", delete_ids)
        _verify_remaining_ids(
            connection,
            table="position_snapshots",
            id_column="id",
            where="position_id = ? AND created_at < ?",
            params=(str(position["id"]), cutoff.isoformat()),
            expected=keep_ids,
        )
    return {
        "open_positions_downsampled": len(positions),
        "open_position_snapshots_before_downsample": before_total,
        "open_position_snapshots_after_downsample": before_total - deleted_total,
        "open_position_snapshots_deleted": deleted_total,
        "open_position_snapshot_aggregate_verified": True,
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
        deleted_total += _delete_by_ids(connection, "position_snapshots", "id", delete_ids)
        _verify_remaining_ids(
            connection,
            table="position_snapshots",
            id_column="id",
            where="position_id = ?",
            params=(str(position["id"]),),
            expected=keep_ids,
        )
        after_total += len(rows) - len(delete_ids)
    return {
        "closed_positions_downsampled": len(positions),
        "position_snapshots_before_downsample": before_total,
        "position_snapshots_after_downsample": after_total,
        "position_snapshots_deleted": deleted_total,
        "position_snapshot_downsample_minutes": bucket_minutes,
        "position_snapshot_aggregate_verified": True,
    }


def _record_backup_event(repo: Repository, event: DatabaseMaintenanceEvent) -> None:
    repo.add_database_maintenance_event(event)
    if event.status != "error":
        return
    try:
        repo.add_alert(
            AlertRecord(
                rule_id="database_backup_failed",
                severity="warn",
                payload={
                    "message": event.message,
                    "details": event.details,
                    "maintenance_event_id": str(event.id),
                },
                fired_at=event.created_at,
                created_at=event.created_at,
            )
        )
    except Exception:
        logger.exception("failed to persist database backup warning alert")


def _table_counts(connection: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, int]:
    existing = {str(row["name"]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
    return {table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in tables if table in existing}


def _verify_remaining_ids(
    connection: sqlite3.Connection,
    *,
    table: str,
    id_column: str,
    where: str,
    params: tuple[str, ...],
    expected: set[str],
) -> None:
    actual = {
        str(row[id_column])
        for row in connection.execute(
            f'SELECT "{id_column}" FROM "{table}" WHERE {where}',
            params,
        ).fetchall()
    }
    if actual != expected:
        raise RuntimeError(f"{table} downsample aggregate verification failed")


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
    return _delete_by_ids(connection, "alerts", "id", delete_ids)


def _cap_stance_history_candles(connection: sqlite3.Connection, keep_bars: int) -> int:
    """심볼·타임프레임당 최신 `keep_bars` 봉만 남긴다 (C7).

    `history_backfill` 도 매 수집 직후 같은 상한을 건다. 여기서 한 번 더 거는 이유는 그 잡이
    **기본값 꺼짐**이기 때문이다 — 리텐션이 수집 경로에만 있으면 잡을 켜기 전까지 상한이
    존재하지 않는다. DB 12.8GB 비대 선례가 그 형태였다.
    """
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", ("stance_history_candles",)).fetchone()
    if not exists:
        return 0
    pairs = connection.execute("SELECT symbol, timeframe FROM stance_history_candles GROUP BY symbol, timeframe").fetchall()
    deleted = 0
    for row in pairs:
        deleted += _delete(
            connection,
            """DELETE FROM stance_history_candles
               WHERE symbol=? AND timeframe=? AND opened_at NOT IN (
                   SELECT opened_at FROM stance_history_candles
                   WHERE symbol=? AND timeframe=?
                   ORDER BY opened_at DESC LIMIT ?
               )""",
            (row["symbol"], row["timeframe"], row["symbol"], row["timeframe"], keep_bars),
        )
    return deleted


# 행 수 상한의 **대상**. 여기 없는 event_type 은 리텐션이 건드리지 않는다.
#
# WO-FCE-STOCK-STATUS-01 D1: 처음 구현은 `event_type` 을 보지 않고 id 오름차순으로 지웠다.
# `unfilled` 이 2,087만 행인데 `track_stopped`·`invariant_failure` 는 **각 1행**이므로,
# 스팸을 지우면서 사건 증거가 함께 사라졌다. 실제로 US 트랙이
# `fill_price_outside_observed_range` 로 정지해 있는데 **언제·어느 주문에서 터졌는지
# 조회할 수 없는 상태**가 됐다 — 화면의 "빨간 줄 하나가 전부"가 이것이다.
#
# 스팸 억제와 증거 보존은 같은 규칙으로 처리할 수 없다. 목록에 적는 행위가 검토 지점이다.
TRIMMABLE_STOCK_EVENT_TYPES: tuple[str, ...] = ("unfilled",)
_TRIMMABLE_PLACEHOLDERS = ",".join("?" for _ in TRIMMABLE_STOCK_EVENT_TYPES)


def _trim_stock_paper_events(connection: sqlite3.Connection, *, keep_rows: int, delete_budget: int) -> dict[str, object]:
    """`stock_paper_events` 를 최신 `keep_rows` 행으로 자른다 (2026-08-24 장애 후속).

    ## 왜 행 수인가

    2026-08-14 하루에 `KR/session_closed` 가 **25,287,541행** 쏟아졌다. 날짜 기반 창은 그날이
    창 안에 있는 동안 아무것도 지우지 않고, 창을 벗어나면 한꺼번에 2,500만 행을 지운다 —
    둘 다 나쁘다. 행 수 상한은 폭주 형태와 무관하게 표를 유계로 유지한다.

    ## 왜 예산을 두는가

    2,500만 행을 한 트랜잭션에 지우면 WAL 이 폭증하고 쓰기 락을 길게 잡는다. 그것이
    **이번 장애와 같은 형태**로 API 를 다시 내린다. 실행마다 `delete_budget` 만큼만 지우고
    여러 실행에 걸쳐 수렴시킨다.

    `id` 는 INTEGER PRIMARY KEY(rowid)이므로 경계 비교가 인덱스 없이도 저렴하다.
    """
    if not _table_exists(connection, "stock_paper_events"):
        return {"stock_paper_events_deleted": 0, "stock_paper_events_remaining": None}
    row = connection.execute(
        f"SELECT MAX(id), COUNT(*) FROM stock_paper_events WHERE event_type IN ({_TRIMMABLE_PLACEHOLDERS})",
        TRIMMABLE_STOCK_EVENT_TYPES,
    ).fetchone()
    if row is None or row[0] is None:
        return {"stock_paper_events_deleted": 0, "stock_paper_events_remaining": 0}
    max_id = int(row[0])
    total = int(row[1])
    if total <= keep_rows:
        return {"stock_paper_events_deleted": 0, "stock_paper_events_remaining": total}
    # 남길 경계: 최신 keep_rows 행. 그보다 오래된 것을 예산만큼 지운다.
    boundary = max_id - keep_rows
    cursor = connection.execute(
        f"""DELETE FROM stock_paper_events
        WHERE id IN (
            SELECT id FROM stock_paper_events
            WHERE id <= ? AND event_type IN ({_TRIMMABLE_PLACEHOLDERS})
            LIMIT ?
        )""",
        (boundary, *TRIMMABLE_STOCK_EVENT_TYPES, delete_budget),
    )
    deleted = int(cursor.rowcount or 0)
    return {
        "stock_paper_events_deleted": deleted,
        "stock_paper_events_remaining": total - deleted,
        "stock_paper_events_keep_rows": keep_rows,
        "stock_paper_events_trimmable_types": list(TRIMMABLE_STOCK_EVENT_TYPES),
        "stock_paper_events_preserved": "정지·체결·청산 신호는 삭제 대상이 아니다 — 사건 증거다",
        # 예산에 걸려 남은 분량은 다음 실행이 이어서 지운다 — 침묵하지 않는다.
        "stock_paper_events_more_pending": (total - deleted) > keep_rows,
    }


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return bool(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _delete_if_table(connection: sqlite3.Connection, table: str, query: str, params: tuple[str, ...]) -> int:
    exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)).fetchone()
    if not exists:
        return 0
    return _delete(connection, query, params)


def _delete(connection: sqlite3.Connection, query: str, params: tuple = ()) -> int:
    cursor = connection.execute(query, params)
    return int(cursor.rowcount or 0)


# 단일 IN (...) 은 SQLITE_MAX_VARIABLE_NUMBER(구버전 999)에 걸려 리텐션 트랜잭션 전체를
# 롤백시킨다 — reports 는 18만 행 규모라 청크 없이는 아무것도 지우지 못한다.
_DELETE_CHUNK_SIZE = 900


def _delete_by_ids(connection: sqlite3.Connection, table: str, id_column: str, ids: list[str]) -> int:
    deleted = 0
    for start in range(0, len(ids), _DELETE_CHUNK_SIZE):
        chunk = ids[start : start + _DELETE_CHUNK_SIZE]
        placeholders = ",".join("?" for _ in chunk)
        deleted += _delete(
            connection,
            f'DELETE FROM "{table}" WHERE "{id_column}" IN ({placeholders})',
            tuple(chunk),
        )
    return deleted


def _connect(path: Path) -> sqlite3.Connection:
    return connect_sqlite(path)


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
