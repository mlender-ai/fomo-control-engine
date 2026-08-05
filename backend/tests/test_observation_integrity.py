"""WO-FCE-OBSERVATION-INTEGRITY-01 Phase 1 — 관측 무결성 게이트 회귀.

배경: 4주 검증을 시작한 지 3주가 지났는데 "온전히 관측된 날이 며칠인지 아무도 몰랐다".
매번 새 결함을 찾아 고쳤지만 "결함 없는 날이 며칠 쌓였는가"를 세는 장치가 없어
"표본이 오염됐으니 다시 세자"가 반복됐다.

고정하는 명제:
1. 하루가 검증일로 카운트되려면 커버리지 임계를 넘어야 한다
2. 부분 정지(장 시작 90분만 수집)는 반드시 유실일로 잡힌다 — 실측 2026-08-04 US 커버리지 23%
3. 비거래일은 분모에서 빠진다 (주말을 유실로 세면 거짓말이다)
4. 검증 시계는 첫 유효일부터 세고, 외삽은 실현 속도로만 한다
5. 증거 테이블은 append-only 여야 한다 — upsert 테이블은 시계열이 아니다
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.migrations import run_migrations
from app.worker import observation as obs

NOW = datetime(2026, 8, 6, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "obs.db")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    yield connection
    connection.close()


def _fill(connection: sqlite3.Connection, market: str, start: datetime, end: datetime, *, step_seconds: int = 60) -> None:
    cursor = start
    rows = []
    while cursor < end:
        rows.append((market, "*", cursor.isoformat(), "{}"))
        cursor += timedelta(seconds=step_seconds)
    connection.executemany("INSERT INTO toss_quotes (market, symbol, observed_at, payload) VALUES (?, ?, ?, ?)", rows)
    connection.commit()


# ── 1. 커버리지 게이트 ──────────────────────────────────────────────


def test_full_session_is_a_valid_day(conn) -> None:
    """US 정규장 13:30~20:00 UTC 전 구간 수집 → 유효 관측일."""
    day = date(2026, 8, 5)
    _fill(conn, "US", datetime(2026, 8, 5, 13, 30, tzinfo=timezone.utc), datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc))

    row = obs.daily_coverage(conn, "stock_us", day, now=NOW)

    assert row["coverage_pct"] == 100.0
    assert row["valid"] == 1
    assert row["reason"] is None


def test_rollover_truncation_is_caught_as_lost(conn) -> None:
    """실측 재현: KST 자정 롤오버로 13:30~14:57 만 수집한 날은 유실일이어야 한다.

    이 날들이 '검증했다'로 세어졌기 때문에 US 표본이 3주 내내 거짓이었다.
    """
    day = date(2026, 8, 4)
    _fill(conn, "US", datetime(2026, 8, 4, 13, 30, tzinfo=timezone.utc), datetime(2026, 8, 4, 14, 57, tzinfo=timezone.utc))

    row = obs.daily_coverage(conn, "stock_us", day, now=NOW)

    assert row["valid"] == 0
    assert row["coverage_pct"] < 30, f"부분 정지가 {row['coverage_pct']}% 로 통과하면 게이트가 무의미하다"
    assert "커버리지" in (row["reason"] or "")
    assert row["longest_gap_seconds"] > 3 * 3600


def test_zero_observation_day_is_lost_with_reason(conn) -> None:
    row = obs.daily_coverage(conn, "stock_us", date(2026, 8, 4), now=NOW)
    assert row["valid"] == 0
    assert row["observations"] == 0
    assert "관측 0건" in (row["reason"] or "")


def test_weekend_is_excluded_from_the_denominator(conn) -> None:
    """주말을 유실일로 세면 검증 속도가 거짓으로 나빠진다."""
    saturday = obs.daily_coverage(conn, "stock_us", date(2026, 8, 8), now=datetime(2026, 8, 10, tzinfo=timezone.utc))
    assert saturday["trading_day"] == 0
    assert saturday["valid"] == 0
    assert "비거래일" in (saturday["reason"] or "")


def test_future_session_is_not_counted_as_lost(conn) -> None:
    """아직 시작도 안 한 세션을 유실로 세면 오늘은 항상 실패한다."""
    before_open = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    row = obs.daily_coverage(conn, "stock_us", date(2026, 8, 5), now=before_open)
    assert row["trading_day"] == 0


def test_partial_today_only_requires_elapsed_time(conn) -> None:
    """진행 중인 날은 지난 시간까지만 요구한다 — 아니면 오늘은 늘 미달이다."""
    mid = datetime(2026, 8, 5, 16, 0, tzinfo=timezone.utc)
    _fill(conn, "US", datetime(2026, 8, 5, 13, 30, tzinfo=timezone.utc), mid)

    row = obs.daily_coverage(conn, "stock_us", date(2026, 8, 5), now=mid)

    assert row["valid"] == 1
    assert row["coverage_pct"] == 100.0


# ── 2. 검증 시계 ────────────────────────────────────────────────────


def _row(day: str, *, valid: int, coverage: float = 95.0, trading: int = 1) -> dict:
    return {"day": day, "track": "stock_us", "valid": valid, "coverage_pct": coverage, "trading_day": trading, "reason": None}


def test_clock_starts_at_first_valid_day() -> None:
    """수리 전 유실 구간은 검증 시계에 들어가지 않는다 — 관측이 성립하지 않았다."""
    rows = [_row("2026-08-01", valid=0, coverage=23.0), _row("2026-08-04", valid=0, coverage=23.0), _row("2026-08-05", valid=1)]

    clock = obs.verification_clock(rows)

    assert clock["first_valid_day"] == "2026-08-05"
    assert clock["effective_days"] == 1
    assert clock["lost_days"] == 0, "첫 유효일 이전은 유실로 세지 않는다(시계가 아직 시작 안 됨)"


def test_clock_counts_lost_days_after_start() -> None:
    rows = [_row("2026-08-03", valid=1), _row("2026-08-04", valid=0, coverage=23.0), _row("2026-08-05", valid=1)]

    clock = obs.verification_clock(rows)

    assert (clock["effective_days"], clock["lost_days"]) == (2, 1)
    assert "D+2/28" in clock["label"] and "유실 1일" in clock["label"]


def test_projection_uses_realized_rate_not_calendar() -> None:
    """달력 기준 외삽은 또 거짓말이 된다 — 실현 속도로만 남은 일수를 낸다."""
    rows = [_row(f"2026-07-{d:02d}", valid=1 if d % 2 == 0 else 0, coverage=95.0 if d % 2 == 0 else 10.0) for d in range(1, 21)]

    clock = obs.verification_clock(rows)

    assert clock["effective_rate"] == pytest.approx(0.5, abs=0.06)
    assert clock["projected_calendar_days_remaining"] > clock["remaining_effective_days"]


def test_clock_reports_not_started_honestly() -> None:
    clock = obs.verification_clock([_row("2026-08-04", valid=0, coverage=23.0)])
    assert clock["effective_days"] == 0
    assert "미개시" in clock["label"]


def test_weekend_rows_do_not_dilute_the_clock() -> None:
    rows = [_row("2026-08-05", valid=1), _row("2026-08-08", valid=0, trading=0), _row("2026-08-09", valid=0, trading=0)]
    clock = obs.verification_clock(rows)
    assert (clock["effective_days"], clock["lost_days"]) == (1, 0)


# ── 3. 증거 테이블 계약 ─────────────────────────────────────────────


def test_evidence_tables_must_be_append_only() -> None:
    """upsert 테이블(poly_markets, PK=market_id)을 근거로 쓰면 과거가 통째로 0이 된다.

    초기 구현에서 실제로 폴리가 '유효 0일'로 나왔고, 원인은 정지가 아니라 잘못된 근거였다.
    """
    assert obs.TRACK_SPECS["poly"].table == "poly_estimates"
    assert obs.TRACK_SPECS["poly"].table != "poly_markets"


def test_every_track_has_an_evidence_source() -> None:
    for key, spec in obs.TRACK_SPECS.items():
        assert spec.table and spec.column, f"{key} 에 관측 증거 테이블이 없다"


# ── 4. 수동 조치 항목 (절전) ────────────────────────────────────────


def test_host_sleep_is_not_double_counted_across_tracks() -> None:
    """같은 벽시계 공백을 4트랙에서 더하면 4배가 된다 — 24/7 트랙 하나로만 센다."""
    rows = [
        {"day": "2026-08-04", "track": "crypto", "late_session_gap_seconds": 3600},
        {"day": "2026-08-04", "track": "poly", "late_session_gap_seconds": 3600},
        {"day": "2026-08-04", "track": "stock_us", "late_session_gap_seconds": 10800},
        {"day": "2026-08-04", "track": "stock_kr", "late_session_gap_seconds": 0},
    ]

    items = obs.manual_action_items(rows)

    assert len(items) == 1
    assert items[0]["lost_seconds"] == 3600, "crypto 기준 1시간이어야 한다(4트랙 합산 금지)"
    assert items[0]["severity"] == "action_required"
    assert "caffeinate" in items[0]["remedy"]


def test_no_action_item_when_host_is_healthy() -> None:
    rows = [{"day": "2026-08-05", "track": "crypto", "late_session_gap_seconds": 0}, {"day": "2026-08-05", "track": "stock_us", "late_session_gap_seconds": 0}]
    assert obs.manual_action_items(rows) == []


# ── 5. 저장·소급 ────────────────────────────────────────────────────


def test_save_is_idempotent_and_recomputes(conn) -> None:
    day = date(2026, 8, 5)
    first = obs.daily_coverage(conn, "stock_us", day, now=NOW)
    obs.save_coverage(conn, [first])
    conn.commit()

    _fill(conn, "US", datetime(2026, 8, 5, 13, 30, tzinfo=timezone.utc), datetime(2026, 8, 5, 20, 0, tzinfo=timezone.utc))
    second = obs.daily_coverage(conn, "stock_us", day, now=NOW)
    obs.save_coverage(conn, [second])
    conn.commit()

    rows = conn.execute("SELECT coverage_pct, valid FROM observation_coverage WHERE track='stock_us' AND day=?", (day.isoformat(),)).fetchall()
    assert len(rows) == 1, "같은 날이 두 줄이면 집계가 이중으로 센다"
    assert rows[0]["valid"] == 1, "재계산 결과가 반영되어야 한다(소급 산출의 전제)"


def test_compute_range_covers_all_tracks(conn) -> None:
    rows = obs.compute_range(conn, date(2026, 8, 3), date(2026, 8, 5), now=NOW)
    assert {row["track"] for row in rows} == set(obs.TRACK_SPECS)
    assert len(rows) == 3 * len(obs.TRACK_SPECS)
