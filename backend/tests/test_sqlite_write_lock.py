"""2026-09-10 침묵 (16시간) 회귀.

## 무엇이 있었나

사용자가 포지션을 종료했는데 텔레그램이 오지 않았다. `sync_positions` 와
**`deliver_alerts` 둘 다** "timeout after 450s" 로 죽어 있었고 마지막 발송은 16시간 전이었다.
알림을 `sync_positions` 에서 분리했는데(ALERT-SILENCE-01 3-1) **분리된 잡도 죽었다.**

faulthandler 덤프가 원인을 그대로 보여줬다:

    보유: sqlite_utils.executemany → trade_cache.store_fills → refresh_derivative_data
    대기: 5개 스레드 (`_acquire_write_lock`) ← 알림 경로 포함

`SQLITE_WRITE_LOCK.acquire()` 에 타임아웃이 없어 **무한 대기**였고, 매달린 스레드는 잡
타임아웃까지 아무 흔적도 남기지 않았다. 대량 캐시 쓰기 하나가 알림 전체를 죽였다.

## 그리고 펄스가 거짓을 말했다

재기동 1분 뒤, 포지션 2개를 보유한 상태에서 이 펄스가 발송됐다(`delivered=1`):

    보유 포지션 없음 — 감시 정상 동작 중입니다.

`sync_positions` 가 아직 한 번도 성공하지 않아 원장이 비어 있었을 뿐이다. 1분 뒤 같은
워커가 그 두 포지션의 진입 알림을 보냈다.

**그 절반은 이 파일에 없다.** 같은 시각 다른 세션이 `position_visibility` 판정으로 더
완전하게 고쳤다(`test_open_position_visibility.py`) — `gap_lines`·`empty_note` 로 "없음"에
출처를 붙이고 `all_normal` 위장까지 막는다. 중복 단언을 두면 두 수리가 서로를 막으므로
여기서는 락 절만 지킨다.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from app.db import sqlite_utils
# ── 쓰기 락 ────────────────────────────────────────────────────────────


def test_write_lock_acquire_has_a_finite_timeout() -> None:
    """**무한 대기 금지.** 이것이 16시간 침묵의 기전이었다."""
    assert sqlite_utils.WRITE_LOCK_TIMEOUT_SECONDS > 0
    assert sqlite_utils.WRITE_LOCK_TIMEOUT_SECONDS < 300


def test_timeout_raises_an_operational_error_subclass() -> None:
    """기존 `except sqlite3.Error` 경로가 그대로 받아야 한다 — 알 수 없는 예외로 터지면 안 된다."""
    assert issubclass(sqlite_utils.SQLiteWriteLockTimeout, sqlite3.OperationalError)
    assert issubclass(sqlite_utils.SQLiteWriteLockTimeout, sqlite3.Error)


def test_a_blocked_writer_fails_instead_of_hanging(tmp_path, monkeypatch) -> None:
    """락이 잡혀 있으면 **기다리다 매달리지 않고** 제한 시간 뒤 실패한다."""
    monkeypatch.setattr(sqlite_utils, "WRITE_LOCK_TIMEOUT_SECONDS", 0.3)
    path = tmp_path / "t.db"
    setup = sqlite_utils.connect_sqlite(path)
    setup.execute("CREATE TABLE t (a INTEGER)")
    setup.commit()
    setup.close()

    holder_ready = threading.Event()
    release = threading.Event()

    def hold() -> None:
        sqlite_utils.SQLITE_WRITE_LOCK.acquire()
        holder_ready.set()
        release.wait(5)
        sqlite_utils.SQLITE_WRITE_LOCK.release()

    thread = threading.Thread(target=hold, daemon=True)
    thread.start()
    assert holder_ready.wait(5)
    try:
        connection = sqlite_utils.connect_sqlite(path)
        started = time.monotonic()
        with pytest.raises(sqlite_utils.SQLiteWriteLockTimeout):
            connection.execute("INSERT INTO t (a) VALUES (1)")
        elapsed = time.monotonic() - started
        assert elapsed < 5, "제한 시간 안에 끝나야 한다 — 넘으면 다시 매달린 것이다"
        connection.close()
    finally:
        release.set()
        thread.join(5)


def test_reads_never_take_the_write_lock(tmp_path) -> None:
    """WAL 에서 읽기는 쓰기와 겹칠 수 있다. 읽기가 락을 요구하면 API 가 워커에 묶인다."""
    path = tmp_path / "r.db"
    setup = sqlite_utils.connect_sqlite(path)
    setup.execute("CREATE TABLE t (a INTEGER)")
    setup.commit()
    setup.close()

    sqlite_utils.SQLITE_WRITE_LOCK.acquire()
    try:
        reader = sqlite_utils.connect_sqlite(path)
        assert reader.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
        reader.close()
    finally:
        sqlite_utils.SQLITE_WRITE_LOCK.release()


def test_holder_is_recorded_for_diagnosis(tmp_path) -> None:
    """지난 침묵에서 "누가 잡고 있었나"에 답할 수 있는 것이 덤프뿐이었다."""
    path = tmp_path / "h.db"
    connection = sqlite_utils.connect_sqlite(path)
    connection.execute("CREATE TABLE t (a INTEGER)")
    holder = sqlite_utils.write_lock_holder()
    assert holder.get("thread")
    assert holder.get("acquired_at")
    connection.close()
    assert sqlite_utils.write_lock_holder() == {}, "해제하면 보유자 기록도 지워야 한다"


def test_bulk_cache_write_is_chunked() -> None:
    """한 트랜잭션으로 수만 행을 쓰면 그동안 알림이 멈춘다. 청크마다 락을 놓아준다."""
    from app.exchange.bitget import trade_cache

    assert 0 < trade_cache._STORE_CHUNK_ROWS <= 10000
    src = __import__("pathlib").Path(trade_cache.__file__).read_text()
    body = src.split("def store_fills")[1]
    assert "_STORE_CHUNK_ROWS" in body, "store_fills 가 청크를 쓰지 않는다"


def test_fetch_state_is_written_after_the_rows() -> None:
    """먼저 쓰면 부분 적재가 "수집 완료"로 표시돼 **조용한 구멍**이 남는다."""
    from app.exchange.bitget import trade_cache

    body = __import__("pathlib").Path(trade_cache.__file__).read_text().split("def store_fills")[1]
    rows_at = body.index("INSERT OR REPLACE INTO bitget_trade_fills")
    state_at = body.index("INSERT OR REPLACE INTO bitget_trade_fill_fetch_state")
    assert rows_at < state_at
