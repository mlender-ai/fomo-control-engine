"""2026-08-22 장애 회귀 — 읽기 경로가 연결 단계에서 쓰기 락을 요구하면 안 된다.

## 사고

`configure_sqlite_connection` 이 연결할 때마다 `PRAGMA auto_vacuum=INCREMENTAL` 을 실행했다.
그 pragma 는 DB 헤더에 쓰므로 쓰기 락을 요구하고, 긴 쓰기 트랜잭션 중에는
`busy_timeout` 5초를 넘겨 `database is locked` 를 던진다. 그 예외가 **쿼리 실행 전에 연결을
죽여서** 읽기 엔드포인트까지 전부 500 이 됐다:

    GET /api/stock-paper/dashboard   행 → 프론트 "API request failed: 500"
    GET /api/live/positions          500 database is locked
    GET /api/system/worker           500 database is locked

그리고 운영 DB 는 이미 `auto_vacuum=2` 였다 — 아무것도 바꾸지 않으면서 락만 요구했다.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.db.sqlite_utils import _AUTO_VACUUM_INCREMENTAL, configure_sqlite_connection, connect_sqlite


class _SpyConnection(sqlite3.Connection):
    """실행된 SQL 을 기록하고, 원하면 특정 문장에서 락 예외를 던진다.

    `sqlite3.Connection.execute` 는 읽기 전용 속성이라 몽키패치가 안 된다 — 서브클래스가
    유일한 방법이다.
    """

    def _spy_init(self, *, fail_on: str | None = None) -> None:
        self.seen: list[str] = []
        self.fail_on = fail_on

    def execute(self, sql, *args, **kwargs):
        self.seen.append(str(sql))
        if self.fail_on and self.fail_on in str(sql):
            raise sqlite3.OperationalError("database is locked")
        return super().execute(sql, *args, **kwargs)


def _spy(path: str, *, fail_on: str | None = None) -> _SpyConnection:
    connection = sqlite3.connect(path, factory=_SpyConnection)
    connection.row_factory = sqlite3.Row
    connection._spy_init(fail_on=fail_on)
    return connection


def _wal_db(path: str, *, auto_vacuum: bool) -> None:
    """테스트용 DB.

    ⚠️ `auto_vacuum` 은 **첫 페이지가 써지기 전에** 설정해야 한다. `journal_mode=WAL` 을
    먼저 걸면 그 시점에 페이지가 생겨 pragma 가 조용히 no-op 이 된다(실측). 순서가 규약이다.
    """
    boot = sqlite3.connect(path)
    if auto_vacuum:
        boot.execute("PRAGMA auto_vacuum=INCREMENTAL")
    boot.execute("PRAGMA journal_mode=WAL")
    boot.execute("CREATE TABLE t (x INTEGER)")
    boot.commit()
    if auto_vacuum:
        assert boot.execute("PRAGMA auto_vacuum").fetchone()[0] == _AUTO_VACUUM_INCREMENTAL, "픽스처 전제 실패"
    boot.close()


def test_new_database_gets_incremental_auto_vacuum(tmp_path) -> None:
    """의도는 보존된다 — 빈 DB(신규 설치·CI)는 INCREMENTAL 로 만든다."""
    path = str(tmp_path / "new.db")
    connection = connect_sqlite(path)
    try:
        assert connection.execute("PRAGMA auto_vacuum").fetchone()[0] == _AUTO_VACUUM_INCREMENTAL
    finally:
        connection.close()


def test_already_incremental_database_skips_the_write(tmp_path) -> None:
    """이미 INCREMENTAL 이면 쓰기를 시도하지 않는다 — 그것이 장애의 직접 원인이었다."""
    path = str(tmp_path / "wal.db")
    _wal_db(path, auto_vacuum=True)

    connection = _spy(path)
    try:
        configure_sqlite_connection(connection)
        attempted = list(connection.seen)
    finally:
        connection.close()

    assert any("auto_vacuum" in sql and "=" not in sql for sql in attempted), "읽기는 해야 한다"
    assert not any("auto_vacuum=INCREMENTAL" in sql for sql in attempted), "쓰기를 시도하면 락에 걸린다"


def test_connect_survives_an_active_writer(tmp_path) -> None:
    """운영 조건(WAL + 진행 중 쓰기)에서 연결과 읽기가 살아 있어야 한다."""
    path = str(tmp_path / "wal.db")
    _wal_db(path, auto_vacuum=True)

    holder = sqlite3.connect(path)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO t VALUES (1)")
    try:
        connection = connect_sqlite(path)
        try:
            assert connection.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
        finally:
            connection.close()
    finally:
        holder.rollback()
        holder.close()


def test_lock_during_the_pragma_write_does_not_kill_the_connection(tmp_path) -> None:
    """바꿔야 하는 DB 에서 락에 걸려도 연결은 살아야 한다 — 파일 크기 설정 하나 때문에 죽지 않는다."""
    path = str(tmp_path / "plain.db")
    _wal_db(path, auto_vacuum=False)

    connection = _spy(path, fail_on="auto_vacuum=INCREMENTAL")
    try:
        # 예외가 새어 나오면 이 호출이 터진다 — 그것이 사고의 형태였다.
        assert configure_sqlite_connection(connection) is connection
    finally:
        connection.close()


def test_busy_timeout_is_still_set(tmp_path) -> None:
    """대기 예산은 유지된다 — 이 수리가 타임아웃을 없앤 것이 아니다."""
    path = str(tmp_path / "wal.db")
    _wal_db(path, auto_vacuum=True)
    connection = connect_sqlite(path)
    try:
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5_000
    finally:
        connection.close()


@pytest.mark.parametrize("auto_vacuum", [True, False])
def test_configure_always_returns_the_connection(tmp_path, auto_vacuum: bool) -> None:
    path = str(tmp_path / f"db-{auto_vacuum}.db")
    _wal_db(path, auto_vacuum=auto_vacuum)
    connection = sqlite3.connect(path)
    try:
        assert configure_sqlite_connection(connection) is connection
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# 2026-08-24 후속 — stock_paper_events 리텐션
# ---------------------------------------------------------------------------


def _events_db(path: str, rows: int) -> None:
    boot = sqlite3.connect(path)
    boot.execute(
        """CREATE TABLE stock_paper_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT NOT NULL, symbol TEXT,
            order_id TEXT, event_type TEXT NOT NULL, reason TEXT,
            observed_at TEXT NOT NULL, payload TEXT NOT NULL)"""
    )
    boot.executemany(
        "INSERT INTO stock_paper_events (market, event_type, reason, observed_at, payload) VALUES ('KR','unfilled','session_closed','2026-08-14','{}')",
        [() for _ in range(rows)],
    )
    boot.commit()
    boot.close()


def test_retention_trims_to_the_row_cap(tmp_path) -> None:
    """폭주한 표를 유계로 만든다 — 2,528만 행이 45초 쿼리의 원인이었다."""
    from app.db.maintenance import _trim_stock_paper_events

    path = str(tmp_path / "ev.db")
    _events_db(path, 5_000)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        result = _trim_stock_paper_events(connection, keep_rows=1_000, delete_budget=1_000_000)
        connection.commit()
        assert result["stock_paper_events_deleted"] == 4_000
        assert connection.execute("SELECT COUNT(*) FROM stock_paper_events").fetchone()[0] == 1_000
    finally:
        connection.close()


def test_retention_respects_the_delete_budget(tmp_path) -> None:
    """한 번에 2,500만 행을 지우면 쓰기 락으로 API 를 다시 내린다 — 나눠 지운다."""
    from app.db.maintenance import _trim_stock_paper_events

    path = str(tmp_path / "ev.db")
    _events_db(path, 5_000)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        result = _trim_stock_paper_events(connection, keep_rows=1_000, delete_budget=1_500)
        connection.commit()
        assert result["stock_paper_events_deleted"] == 1_500
        assert result["stock_paper_events_more_pending"] is True
        assert connection.execute("SELECT COUNT(*) FROM stock_paper_events").fetchone()[0] == 3_500
    finally:
        connection.close()


def test_retention_keeps_the_newest_rows(tmp_path) -> None:
    """최신을 남긴다 — 오래된 것을 지운다."""
    from app.db.maintenance import _trim_stock_paper_events

    path = str(tmp_path / "ev.db")
    _events_db(path, 3_000)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        _trim_stock_paper_events(connection, keep_rows=500, delete_budget=1_000_000)
        connection.commit()
        lowest = connection.execute("SELECT MIN(id), MAX(id) FROM stock_paper_events").fetchone()
        assert lowest[1] == 3_000, "최신은 남아야 한다"
        assert lowest[0] > 2_000, "오래된 것이 지워져야 한다"
    finally:
        connection.close()


def test_retention_is_a_noop_below_the_cap(tmp_path) -> None:
    from app.db.maintenance import _trim_stock_paper_events

    path = str(tmp_path / "ev.db")
    _events_db(path, 100)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        result = _trim_stock_paper_events(connection, keep_rows=1_000, delete_budget=1_000)
        assert result["stock_paper_events_deleted"] == 0
        assert result["stock_paper_events_remaining"] == 100
    finally:
        connection.close()


def test_retention_tolerates_a_missing_table(tmp_path) -> None:
    """표가 없는 설치(CI·신규)에서 리텐션이 죽으면 안 된다."""
    from app.db.maintenance import _trim_stock_paper_events

    connection = sqlite3.connect(str(tmp_path / "empty.db"))
    connection.row_factory = sqlite3.Row
    try:
        assert _trim_stock_paper_events(connection, keep_rows=100, delete_budget=100)["stock_paper_events_deleted"] == 0
    finally:
        connection.close()


def test_event_retention_defaults_are_bounded() -> None:
    from app.core.config import Settings

    settings = Settings(database_url="memory://")
    assert settings.db_stock_paper_event_retention_rows > 0
    assert settings.db_stock_paper_event_delete_budget > 0
