"""2026-09-17 — 10.8GB 백업이 전역 쓰기 락을 잡아 알림을 죽였다.

## 무엇이 있었나

사용자가 익절했는데 텔레그램이 오지 않았다. 원장에는 `closed` 로 들어와 있었다 —
**감지는 됐고 발송이 막힌 것**이다. 종료 4건이 알림 없이 지나갔다:

    09-17T08:33 NEARUSDT · 09-17T08:15 ETHUSDT
    09-16T13:27 NEARUSDT · 09-16T04:20 BTCUSDT

`sync_positions` 와 `deliver_alerts` 가 "timeout after 450s" 로 연속 실패(31·19회),
간격이 720초로 백오프돼 있었고 `loop_lag` 는 136초였다.

라이브 faulthandler 덤프가 원인을 보여줬다:

    Thread ...: app/db/maintenance.py line 81 in run_database_backup

## 읽기가 쓰기 락을 잡고 있었다

    with SQLITE_WRITE_LOCK:      # 전역 쓰기 락
        source.backup(target)    # 10.83 GB 복사

백업은 **읽기**다. SQLite 백업 API 는 온라인 백업이라 WAL 에서 쓰기와 공존한다.
그런데 락을 잡고 수십 분을 복사했고, 그동안 알림 원장 기록(쓰기)이 전부 막혔다.

`_acquire_write_lock` 의 30초 타임아웃은 연결 객체 경로에만 걸린다 — 이 직접 `with` 문은
그 보호 밖이었다.

## 같은 자리에서 드러난 둘째

`BitgetTradeFillCache._lock` 이 **곧 `SQLITE_WRITE_LOCK`** 이었다. 2026-09-10 에
`executemany` 를 청크로 나눴지만 바깥에서 이 락을 계속 쥐고 있어 **청크가 무의미했다** —
조각내 놓고 자물쇠는 그대로 들고 있었다.
"""

from __future__ import annotations

import sqlite3
import threading
import time

from app.db import maintenance
from app.db.sqlite_utils import SQLITE_WRITE_LOCK, connect_sqlite
from app.exchange.bitget import trade_cache as trade_cache_module


def test_backup_does_not_hold_the_global_write_lock() -> None:
    """**읽기는 쓰기 락을 잡지 않는다.** 이것이 이 수리의 원칙이다."""
    import inspect

    src = inspect.getsource(maintenance.run_database_backup)
    body = src.split("temp_gzip_path.unlink")[1]
    # 주석에서 이 이름을 **언급**하는 것과 코드에서 **쓰는** 것은 다르다. 코드 줄만 본다.
    code = [ln for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    assert not any("with SQLITE_WRITE_LOCK" in ln for ln in code), "백업이 다시 전역 쓰기 락을 잡는다"
    assert any("source.backup(" in ln for ln in code)


def test_backup_yields_between_steps() -> None:
    """한 번에 다 복사하면 락 없이도 I/O 를 독점해 다른 작업을 굶긴다."""
    import inspect

    src = inspect.getsource(maintenance.run_database_backup)
    assert "pages=" in src and "sleep=" in src
    assert maintenance._BACKUP_PAGES_PER_STEP > 0
    assert maintenance._BACKUP_SLEEP_SECONDS > 0


def test_sqlite_backup_accepts_the_step_arguments(tmp_path) -> None:
    """서명이 바뀌면 백업이 통째로 죽는다 — 백업 실패는 조용하다."""
    source_path = tmp_path / "s.db"
    source = connect_sqlite(source_path)
    source.execute("CREATE TABLE t (a INTEGER)")
    source.executemany("INSERT INTO t (a) VALUES (?)", [(i,) for i in range(500)])
    source.commit()
    target = sqlite3.connect(tmp_path / "t.db")
    source.backup(target, pages=maintenance._BACKUP_PAGES_PER_STEP, sleep=maintenance._BACKUP_SLEEP_SECONDS)
    assert target.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 500
    target.close()
    source.close()


def test_a_write_can_proceed_while_a_backup_reads(tmp_path) -> None:
    """**이것이 사용자가 잃은 것이다** — 백업 중에도 알림 원장 기록이 되어야 한다."""
    source_path = tmp_path / "s.db"
    setup = connect_sqlite(source_path)
    setup.execute("CREATE TABLE t (a INTEGER)")
    setup.executemany("INSERT INTO t (a) VALUES (?)", [(i,) for i in range(5000)])
    setup.commit()
    setup.close()

    done = threading.Event()
    failure: list[BaseException] = []

    def backup() -> None:
        try:
            source = connect_sqlite(source_path)
            target = sqlite3.connect(tmp_path / "b.db")
            source.backup(target, pages=1, sleep=0.001)
            target.close()
            source.close()
        except Exception as exc:
            # 삼키지 않고 모아 둔다 — 스레드에서 터진 예외는 조용히 사라지고,
            # 그러면 "쓰기가 빨랐다"는 판정이 거짓이 된다.
            failure.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=backup, daemon=True)
    thread.start()
    started = time.monotonic()
    writer = connect_sqlite(source_path)
    writer.execute("INSERT INTO t (a) VALUES (99999)")
    writer.commit()
    writer.close()
    elapsed = time.monotonic() - started
    done.wait(30)
    thread.join(5)
    assert not failure, failure
    assert elapsed < 10, f"백업이 쓰기를 막았다 ({elapsed:.1f}s)"


def test_trade_cache_no_longer_borrows_the_global_write_lock() -> None:
    """빌려 쓰면 대량 적재 내내 **DB 전체의 쓰기**가 막힌다 — 청크가 무의미해진다."""
    cache = trade_cache_module.BitgetTradeFillCache(":memory:")
    assert cache._lock is not SQLITE_WRITE_LOCK


def test_trade_cache_still_serializes_itself() -> None:
    """직렬화를 없애면 같은 창을 두 스레드가 동시에 적재한다."""
    cache = trade_cache_module.BitgetTradeFillCache(":memory:")
    assert cache._lock.acquire(timeout=1)
    cache._lock.release()


def test_store_fills_chunking_is_still_in_place() -> None:
    """청크가 사라지면 한 트랜잭션이 다시 길어진다."""
    import pathlib

    body = pathlib.Path(trade_cache_module.__file__).read_text().split("def store_fills")[1]
    assert "_STORE_CHUNK_ROWS" in body
    assert "SQLITE_WRITE_LOCK" not in body
