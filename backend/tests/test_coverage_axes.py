"""WO-FCE-SAMPLE-VIABILITY-01 PHASE 3 — 거래일 달력 + 커버리지 저하 축 분해 회귀.

배경: KR 커버리지 평균 54.2%, 유효 관측일 5/28 — 가장 느린 트랙이다. 원인을 모른 채
수집 주기를 올리는 것은 추측이지 수리가 아니다. 직전 WO 에서 "US 45시간을 절전 탓으로
돌리면 오귀인"이라는 지적이 있었고, 같은 함정을 피하려면 축을 먼저 분리해야 한다.

고정하는 명제:
1. 확정 휴장일은 분모에서 빠진다 (휴장을 유실로 세면 고칠 대상을 잘못 짚는다)
2. **미확정** 휴장 후보는 분모를 바꾸지 않는다 (추정으로 유실일을 지우면 진행도가 부풀려진다)
3. KR 정규장은 절전 구간과 구조적으로 겹치지 않는다 — 절전 귀인은 불가능하다
4. 관측 0건인 날을 '세션 경계 결함'으로 오귀인하지 않는다 (그건 종일 정지다)
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.migrations import run_migrations
from app.worker import market_calendar, observation as obs

NOW = datetime(2026, 8, 6, 23, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "axes.db")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    yield connection
    connection.close()


def _fill(connection: sqlite3.Connection, table: str, market: str, start: datetime, end: datetime, *, step_seconds: int = 60) -> None:
    cursor = start
    rows = []
    while cursor < end:
        rows.append((market, "*", cursor.isoformat(), "{}"))
        cursor += timedelta(seconds=step_seconds)
    connection.executemany(f"INSERT INTO {table} (market, symbol, observed_at, payload) VALUES (?, ?, ?, ?)", rows)
    connection.commit()


def _fill_crypto(connection: sqlite3.Connection, start: datetime, end: datetime, *, step_seconds: int = 300) -> None:
    cursor = start
    rows = []
    index = 0
    while cursor < end:
        rows.append((f"snap-{index}", "BTCUSDT", "4h", "mock", cursor.isoformat(), "{}"))
        cursor += timedelta(seconds=step_seconds)
        index += 1
    connection.executemany("INSERT INTO market_snapshots (id, symbol, timeframe, provider, created_at, payload) VALUES (?,?,?,?,?,?)", rows)
    connection.commit()


# ── 1. 휴장 달력 ────────────────────────────────────────────────────────


def test_confirmed_holiday_leaves_the_denominator(conn) -> None:
    """휴장일을 유실로 세면 54.2%가 실제보다 나쁘게 나오고, 멀쩡한 수집기를 고치게 된다."""
    row = obs.daily_coverage(conn, "stock_us", date(2026, 7, 3), now=NOW)  # Independence Day 관측일

    assert row["trading_day"] == 0
    assert "휴장" in (row["reason"] or "")


def test_pending_holiday_does_not_change_the_denominator(conn) -> None:
    """추정으로 분모를 바꾸면 진짜 유실일이 사라져 검증 진행도가 부풀려진다."""
    row = obs.daily_coverage(conn, "stock_kr", date(2026, 7, 17), now=NOW)

    assert row["trading_day"] == 1, "미확정 후보는 여전히 거래일로 세고 유실로 남긴다"
    assert row["valid"] == 0
    assert "확인 필요" in (row["reason"] or "")


def test_early_close_shortens_the_session_window(conn) -> None:
    """조기 폐장을 모르면 닫힌 시간이 통째로 '공백'이 되어 유실일이 된다."""
    bounds = obs.session_bounds("stock_us", date(2026, 11, 27), now=datetime(2026, 11, 30, tzinfo=timezone.utc))

    assert bounds is not None
    assert bounds[1].hour == 18, "13:00 ET = 18:00 UTC (EST)"


def test_holiday_audit_reports_candidates_without_applying_them(conn) -> None:
    audit = market_calendar.holiday_audit("KR", [date(2026, 7, 16), date(2026, 7, 17)])

    assert audit["confirmed_holidays_in_list"] == []
    assert audit["pending_holiday_candidates"][0]["day"] == "2026-07-17"
    assert "확인 필요" in audit["verdict"]


# ── 2. 절전 귀인 ────────────────────────────────────────────────────────


def test_kr_session_cannot_be_explained_by_host_sleep(conn) -> None:
    """KR 정규장(00:00~06:30 UTC)은 절전 구간(17:00~20:00 UTC)과 겹치지 않는다.

    실측을 볼 것도 없이 구조적으로 그렇다. 이 사실을 고정해 두지 않으면 다음 사람이
    또 KR 유실을 절전 탓으로 돌린다.
    """
    axes = obs.gap_axes(conn, "stock_kr", date(2026, 8, 5), now=NOW)

    assert axes["axes"]["host_sleep"]["structural_overlap_seconds"] == 0
    assert axes["axes"]["host_sleep"]["explanation_possible"] is False


def test_us_session_overlaps_the_sleep_window(conn) -> None:
    """대조군 — US 정규장 후반은 실제로 겹친다. 축 판별이 무조건 False 를 내는 게 아니다."""
    axes = obs.gap_axes(conn, "stock_us", date(2026, 8, 5), now=NOW)

    assert axes["axes"]["host_sleep"]["explanation_possible"] is True


# ── 3. 경계 결함 vs 종일 정지 ───────────────────────────────────────────


def test_zero_observation_day_is_not_labelled_a_session_edge_defect(conn) -> None:
    """관측 0건은 경계 결함이 아니라 종일 정지다 — 오귀인하면 롤오버를 또 고치게 된다."""
    axes = obs.gap_axes(conn, "stock_kr", date(2026, 8, 5), now=NOW)

    assert axes["observations"] == 0
    assert axes["axes"]["session_edge"]["edge_dominant"] is False


def test_rollover_style_truncation_shows_as_edge_dominant(conn) -> None:
    """장 시작 90분만 수집한 날은 뒤쪽 공백이 지배적이다 — 경계 결함의 지문."""
    day = date(2026, 8, 5)
    _fill(conn, "toss_quotes", "KR", datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc), datetime(2026, 8, 5, 1, 30, tzinfo=timezone.utc))

    axes = obs.gap_axes(conn, "stock_kr", day, now=NOW)

    assert axes["axes"]["session_edge"]["trailing_gap_seconds"] > 3 * 3600
    assert axes["axes"]["session_edge"]["edge_dominant"] is True


def test_track_specific_failure_separates_collector_from_host(conn) -> None:
    """호스트(24/7 트랙)는 살아 있는데 KR 만 비었다면 원인은 수집기·소스다."""
    _fill_crypto(conn, datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc), datetime(2026, 8, 5, 7, 0, tzinfo=timezone.utc))

    axes = obs.gap_axes(conn, "stock_kr", date(2026, 8, 5), now=NOW)

    assert axes["axes"]["source_response"]["host_alive"] is True
    assert axes["axes"]["source_response"]["track_specific_failure"] is True


def test_axis_breakdown_summarises_every_axis(conn) -> None:
    days = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]

    breakdown = obs.axis_breakdown(conn, "stock_kr", days, now=NOW)

    assert set(breakdown) >= {"holiday", "host_sleep", "session_edge", "source_response"}
    assert "겹치지 않는다" in breakdown["host_sleep"]["verdict"]
