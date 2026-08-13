"""WO-FCE-WINDOW-ANCHOR-01 — 검증 창 앵커 회귀.

배경: 검증 완료 판정에 **창 개념이 없었다.** `effective_days` 는 트랙의 역사상 모든 유효일
개수였고, 표본 SQL 8종 전부 시각 조건이 없었다. 그래서 페이퍼 재시작을 실행해도 스코어보드만
0으로 돌아가고 검증 카운터는 어제 값 그대로 남는다 — **재시작이 검증 경로에 도달하지 않는다.**

고정하는 명제:
1. **앵커가 없으면 지금과 완전히 같은 숫자가 나온다** (C7 — 이 WO 자체는 판정을 바꾸지 않는다)
2. 앵커가 있으면 그 이전 유효일·진입·표본이 계수에서 빠진다 (4트랙 8쿼리 전부)
3. **행은 하나도 지워지지 않는다** — 창은 필터지 삭제가 아니다 (C1)
4. 빠진 건수는 조회 가능하다 — 0건과 창 밖 40건은 다른 상태다 (C2)
5. 재시작은 4트랙 앵커를 **함께** 연다 (D3)
6. 스코어보드 창과 검증 창이 같은 시각을 가리킨다 (D3 재발 방지)
7. 목표일을 넘기면 `31/28 (목표일 경과)` 로 낸다 — 상한으로 가리지 않는다 (D4·C6)
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db.migrations import run_migrations
from app.db.repository import MemoryRepository
from app.paper import service as paper_service
from app.validation import sample_viability as sv
from app.validation import window_anchor as wa
from app.worker import observation

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)
ANCHOR = datetime(2026, 8, 10, tzinfo=timezone.utc)

TRACKED_TABLES = ("observation_coverage", "paper_trades", "stock_paper_fills", "poly_positions")


@pytest.fixture()
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "anchor.db")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    yield connection
    connection.close()


def _coverage(connection: sqlite3.Connection, track: str, days: list[str], *, valid: int = 1) -> None:
    connection.executemany(
        """INSERT INTO observation_coverage (day, track, coverage_pct, valid, trading_day, computed_at)
        VALUES (?, ?, 100.0, ?, 1, '2026-08-20T00:00:00+00:00')""",
        [(day, track, valid) for day in days],
    )
    connection.commit()


def _paper_trade(connection: sqlite3.Connection, *, entry: str, exit_at: str | None) -> None:
    connection.execute(
        "INSERT INTO paper_trades (id, symbol, timeframe, status, entry_bar_at, exit_at, updated_at, payload) VALUES (?,?,?,?,?,?,?,?)",
        (f"{entry}-{exit_at}", "BTCUSDT", "4h", "closed" if exit_at else "open", entry, exit_at, entry, json.dumps({})),
    )
    connection.commit()


def _stock_fill(connection: sqlite3.Connection, *, market: str, side: str, filled_at: str) -> None:
    connection.execute(
        "INSERT INTO stock_paper_fills (id, order_id, market, symbol, side, filled_at, payload) VALUES (?,?,?,?,?,?,?)",
        (f"{market}-{side}-{filled_at}", f"order-{market}-{side}-{filled_at}", market, "005930", side, filled_at, json.dumps({})),
    )
    connection.commit()


def _row_counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts = {}
    for table in TRACKED_TABLES:
        try:
            counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.OperationalError:
            counts[table] = -1
    return counts


def _seed_crypto(connection: sqlite3.Connection) -> None:
    """앵커(08-10) 앞뒤로 대칭 배치 — 무엇이 빠졌는지 셈으로 확인 가능하게."""
    _coverage(connection, "crypto", ["2026-08-05", "2026-08-06", "2026-08-07"])  # 창 밖 3일
    _coverage(connection, "crypto", ["2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"])  # 창 안 4일
    for day in ("2026-08-05", "2026-08-06", "2026-08-07"):
        _paper_trade(connection, entry=f"{day}T00:00:00+00:00", exit_at=f"{day}T12:00:00+00:00")
    for day in ("2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"):
        _paper_trade(connection, entry=f"{day}T00:00:00+00:00", exit_at=f"{day}T12:00:00+00:00")


# ── 1. 앵커 없음 = 현행 동작 (C7) ───────────────────────────────────────


def test_absent_anchor_reproduces_current_numbers(conn) -> None:
    """이 WO 자체가 기존 판정을 바꾸면 안 된다. 앵커 없음이 곧 생애 누적이다."""
    _seed_crypto(conn)

    result = sv.track_sample_viability(conn, "crypto", now=NOW)

    assert result["effective_days"] == 7  # 창 밖 3일 + 창 안 4일 전부
    assert result["entries_total"] == 7
    assert result["scored_samples"] == 7
    assert result["window"]["window_seq"] is None
    assert result["window_excluded"]["applied"] is False


def test_absent_anchor_keeps_every_track_unchanged(conn) -> None:
    for track in wa.VALIDATION_TRACKS:
        completion = sv.validation_completion(conn, track, now=NOW)
        assert completion["window"]["window_seq"] is None
        assert completion["window_excluded"]["applied"] is False


def test_verification_clock_without_anchor_is_unchanged() -> None:
    rows = [
        {"day": "2026-08-05", "track": "crypto", "valid": 1, "trading_day": 1, "coverage_pct": 100.0},
        {"day": "2026-08-11", "track": "crypto", "valid": 1, "trading_day": 1, "coverage_pct": 100.0},
    ]

    assert observation.verification_clock(rows) == observation.verification_clock(rows, anchor_day=None)


def test_since_clause_returns_the_original_sql_without_an_anchor() -> None:
    sql = "SELECT entry_bar_at AS t FROM paper_trades"

    assert wa.since_clause(None, inner_sql=sql) == (sql, ())


# ── 2. 앵커 적용 ────────────────────────────────────────────────────────


def test_anchor_excludes_prior_valid_days(conn) -> None:
    _seed_crypto(conn)
    wa.open_window(conn, "crypto", anchored_at=ANCHOR, reason="test")

    result = sv.track_sample_viability(conn, "crypto", now=NOW)

    assert result["effective_days"] == 4, "앵커 이전 3일이 유효일에서 빠져야 한다"
    assert result["window"]["window_seq"] == 1
    assert result["window"]["anchor_day"] == "2026-08-10"


def test_anchor_excludes_prior_entries_and_scored_samples(conn) -> None:
    _seed_crypto(conn)
    wa.open_window(conn, "crypto", anchored_at=ANCHOR, reason="test")

    result = sv.track_sample_viability(conn, "crypto", now=NOW)

    assert result["entries_total"] == 4
    assert result["scored_samples"] == 4


@pytest.mark.parametrize("track,market", [("stock_kr", "KR"), ("stock_us", "US")])
def test_anchor_filters_both_stock_queries(conn, track: str, market: str) -> None:
    """진입(매수)·표본(매도) 두 쿼리 모두 창 하한이 걸려야 한다."""
    for day in ("2026-08-05", "2026-08-12"):
        _stock_fill(conn, market=market, side="buy", filled_at=f"{day}T01:00:00+00:00")
        _stock_fill(conn, market=market, side="sell", filled_at=f"{day}T05:00:00+00:00")

    before = sv.track_sample_viability(conn, track, now=NOW)
    assert before["entries_total"] == 2 and before["scored_samples"] == 2

    wa.open_window(conn, track, anchored_at=ANCHOR)
    after = sv.track_sample_viability(conn, track, now=NOW)

    assert after["entries_total"] == 1
    assert after["scored_samples"] == 1


def test_every_track_spec_accepts_the_anchor_wrapper(conn) -> None:
    """8쿼리 전부 감싸도 SQL 이 성립해야 한다 — 하나라도 깨지면 그 트랙만 조용히 0이 된다."""
    anchor = wa.open_window(conn, "crypto", anchored_at=ANCHOR)

    for spec in sv.TRACK_SAMPLE_SPECS.values():
        for sql in (spec.entry_sql, spec.scored_sql):
            query, params = wa.since_clause(anchor, inner_sql=sql)
            conn.execute(query, params).fetchall()


# ── 3. 행은 지우지 않는다 (C1) ──────────────────────────────────────────


def test_opening_a_window_deletes_no_rows(conn) -> None:
    """창은 필터다. 재시작이 원장을 지우면 과거 검증을 영원히 재구성할 수 없다."""
    _seed_crypto(conn)
    _stock_fill(conn, market="KR", side="buy", filled_at="2026-08-05T01:00:00+00:00")
    before = _row_counts(conn)

    for track in wa.VALIDATION_TRACKS:
        wa.open_window(conn, track, anchored_at=ANCHOR, reason="restart")

    assert _row_counts(conn) == before


def test_prior_window_anchors_are_preserved(conn) -> None:
    """C2 — 과거 창의 앵커가 남아야 "그때 무엇을 어디부터 셌는가"를 나중에도 안다."""
    wa.open_window(conn, "crypto", anchored_at=ANCHOR, reason="first")
    wa.open_window(conn, "crypto", anchored_at=ANCHOR + timedelta(days=7), reason="second")

    history = wa.anchor_history(conn, "crypto")

    assert [item.window_seq for item in history] == [1, 2]
    assert [item.reason for item in history] == ["first", "second"]
    assert wa.current_anchor(conn, "crypto").window_seq == 2


# ── 4. 제외 건수 조회 (C2) ──────────────────────────────────────────────


def test_excluded_counts_are_queryable(conn) -> None:
    """ "0건"과 "창 밖 3건"은 완전히 다른 상태다. 숨기면 빈 화면이 고장인지 정상인지 모른다."""
    _seed_crypto(conn)
    wa.open_window(conn, "crypto", anchored_at=ANCHOR)

    excluded = sv.track_sample_viability(conn, "crypto", now=NOW)["window_excluded"]

    assert excluded["applied"] is True
    assert excluded["entries"] == 3
    assert excluded["scored_samples"] == 3
    assert excluded["coverage_days"] == 3
    assert "삭제되지 않았다" in excluded["note"]


# ── 5·6. 재시작이 4트랙 앵커를 연다 + 스코어보드와 정합 (D3) ────────────


def test_restart_opens_an_anchor_for_every_track() -> None:
    repo = MemoryRepository()
    now = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)

    paper_service.start_paper_benchmark(repo, reset=True, now=now)

    for track in wa.VALIDATION_TRACKS:
        window = repo.current_validation_window(track)
        assert window is not None, f"{track} 앵커가 열리지 않았다 — 그 트랙만 구 창에 남는다"
        assert window["anchored_at"] == now.isoformat()


def test_scoreboard_start_and_validation_anchor_point_at_the_same_moment() -> None:
    """두 창이 어긋나면 화면 둘이 서로 다른 창을 말한다 — D3 이 형태만 바꿔 재발한다."""
    repo = MemoryRepository()
    now = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)
    paper_service.start_paper_benchmark(repo, reset=True, now=now)

    alignment = paper_service.validation_window_alignment(repo)

    assert alignment["aligned"] is True
    assert alignment["misaligned_tracks"] == []
    assert alignment["scoreboard_started_at"] == now.isoformat()


def test_alignment_names_the_track_that_fell_behind() -> None:
    repo = MemoryRepository()
    now = datetime(2026, 8, 17, 0, 0, tzinfo=timezone.utc)
    paper_service.start_paper_benchmark(repo, reset=True, now=now)
    # 한 트랙만 다른 시각으로 다시 열어 어긋난 상태를 만든다.
    repo.open_validation_window("poly", anchored_at=now + timedelta(days=1), reason="drift")

    alignment = paper_service.validation_window_alignment(repo)

    assert alignment["aligned"] is False
    assert alignment["misaligned_tracks"] == ["poly"]


def test_restart_increments_the_window_sequence() -> None:
    repo = MemoryRepository()
    first = datetime(2026, 8, 17, tzinfo=timezone.utc)
    paper_service.start_paper_benchmark(repo, reset=True, now=first)
    paper_service.start_paper_benchmark(repo, reset=True, now=first + timedelta(days=28))

    assert repo.current_validation_window("crypto")["window_seq"] == 2
    assert len(repo.list_validation_windows("crypto")) == 2


# ── 7. 창 상태 · 목표일 경과 (D4·C6) ────────────────────────────────────


def test_window_state_names_the_overrun_case() -> None:
    assert wa.window_state(effective_days=10, target_days=28, complete=False) == wa.WINDOW_RUNNING
    assert wa.window_state(effective_days=31, target_days=28, complete=False) == wa.WINDOW_OVERRUN
    assert wa.window_state(effective_days=31, target_days=28, complete=True) == wa.WINDOW_COMPLETE


def test_effective_days_are_not_capped_at_the_target() -> None:
    """`28/28` 에서 멈춰 보이면 창이 이미 지났다는 사실이 화면에서 사라진다."""
    assert wa.effective_days_label(31, 28) == "31/28 (목표일 경과)"
    assert wa.effective_days_label(28, 28) == "28/28"
    assert wa.effective_days_label(10, 28) == "10/28"


def test_completion_reports_overrun_state_and_uncapped_days(conn) -> None:
    _coverage(conn, "crypto", [f"2026-07-{day:02d}" for day in range(1, 32)])
    for day in range(1, 32):
        _paper_trade(conn, entry=f"2026-07-{day:02d}T00:00:00+00:00", exit_at=None)

    completion = sv.validation_completion(conn, "crypto", now=NOW)

    assert completion["conditions"]["effective_days"]["current"] == 31
    assert completion["window_state"] == wa.WINDOW_OVERRUN
    assert completion["window_state_label"] == "목표일 경과·조건 미달"
    assert "31/28 (목표일 경과)" in completion["line"]
    assert "목표일 경과 · 미달" in completion["line"]


def test_completion_line_carries_the_window_sequence(conn) -> None:
    _seed_crypto(conn)
    wa.open_window(conn, "crypto", anchored_at=ANCHOR)

    line = sv.validation_completion(conn, "crypto", now=NOW)["line"]

    assert "창 1회차" in line and "앵커 2026-08-10" in line


def test_report_exposes_windows_for_every_track(conn) -> None:
    report = sv.sample_viability_report(conn, now=NOW)

    assert set(report["windows"]) == set(sv.TRACK_SAMPLE_SPECS)
    assert set(report["window_states"]) == set(sv.TRACK_SAMPLE_SPECS)


# ── 8. 범위 밖 무변경 (C4) ──────────────────────────────────────────────


def test_directional_judgment_packages_are_untouched() -> None:
    """방향 판정은 DIRECTIONAL-INTEGRITY 소관이다. 여기서 건드리면 두 WO 의 계측이 섞인다."""
    diff = subprocess.run(
        ["git", "diff", "origin/main", "--stat", "--", "backend/app/analyst/", "backend/app/structure/"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")

    assert diff.stdout.strip() == "", f"방향 판정 로직이 변경됐다:\n{diff.stdout}"


def test_no_delete_statement_reaches_the_ledger_tables() -> None:
    """C1 — 재시작 경로에 DELETE 가 생기면 창 이동이 아니라 삭제가 된다."""
    source = (REPO_ROOT / "backend" / "app" / "validation" / "window_anchor.py").read_text(encoding="utf-8")

    assert "DELETE" not in source.upper().replace("DELETED", "")


# ── 9. 문서 ─────────────────────────────────────────────────────────────


def test_runbook_lists_the_anchor_check() -> None:
    text = (REPO_ROOT / "docs" / "RESTART_RUNBOOK.md").read_text(encoding="utf-8")

    for item in ("앵커", "window_seq", "창 밖 제외", "행 삭제"):
        assert item in text, f"런북에 '{item}' 이 없다"
