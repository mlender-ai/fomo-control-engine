"""WO-FCE-SAMPLE-VIABILITY-01 — 표본 생성 능력 판정 회귀.

배경: 직전 WO 가 유효 관측일을 정직하게 세게 만들었더니, **유효 관측일을 다 채워도
검증이 되지 않는 트랙**이 드러났다. 폴리는 보유 9건 중 8건이 2027-01-01 만기라 검증 창
안에 정산되는 건이 0이었다 — 28일을 다 채워도 채점 가능한 표본이 0이다.

고정하는 명제:
1. 진입률의 분모는 유효 관측일이다 (달력일로 나누면 정지 구간이 비율을 희석한다)
2. 만기가 전부 검증 창 밖이면 `STRUCTURALLY_BLOCKED` 다 — 관측일을 더 쌓아도 해결되지 않는다
3. 관측 창이 짧으면 판정하지 않는다 (`INSUFFICIENT_DATA`), 추정 계수를 만들지 않는다
4. 검증 완료는 3조건(유효일·표본·국면)을 **모두** 만족해야 한다
5. 가장 뒤처진 조건이 그 트랙의 상태다 — 유효일만 보고 "거의 다 됐다"고 말하지 않는다
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from app.db.migrations import run_migrations
from app.validation import sample_viability as sv

NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "viability.db")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    yield connection
    connection.close()


def _valid_days(connection: sqlite3.Connection, track: str, days: list[str]) -> None:
    connection.executemany(
        """INSERT INTO observation_coverage (day, track, coverage_pct, valid, trading_day, computed_at)
        VALUES (?, ?, 100.0, 1, 1, '2026-08-06T00:00:00+00:00')""",
        [(day, track) for day in days],
    )
    connection.commit()


def _paper_trade(connection: sqlite3.Connection, *, entry: str, exit_at: str | None, regime: str | None = None) -> None:
    payload = {"entry_evidence": {"market_regime": regime} if regime else {}}
    connection.execute(
        "INSERT INTO paper_trades (id, symbol, timeframe, status, entry_bar_at, exit_at, updated_at, payload) VALUES (?,?,?,?,?,?,?,?)",
        (f"{entry}-{regime}", "BTCUSDT", "4h", "closed" if exit_at else "open", entry, exit_at, entry, json.dumps(payload)),
    )
    connection.commit()


# ── 1. 진입률의 분모 ────────────────────────────────────────────────────


def test_entry_rate_denominator_is_effective_days_not_calendar(conn) -> None:
    """정지 구간을 분모에 넣으면 진입률이 희석돼 '선정 기준 문제'로 오진한다."""
    _valid_days(conn, "crypto", ["2026-08-01", "2026-08-02"])
    _paper_trade(conn, entry="2026-08-01T00:00:00+00:00", exit_at="2026-08-02T00:00:00+00:00")
    _paper_trade(conn, entry="2026-08-02T00:00:00+00:00", exit_at=None, regime="uptrend")

    row = sv.track_sample_viability(conn, "crypto", now=NOW)

    assert row["effective_days"] == 2
    assert row["entries_per_effective_day"] == 1.0, "유효일 2일 · 진입 2건이면 1.0 이다"


def test_entries_outside_effective_days_are_not_counted(conn) -> None:
    """유실일의 진입을 분자에 넣으면 분모(유효일)와 창이 어긋난다."""
    _valid_days(conn, "crypto", ["2026-08-01"])
    _paper_trade(conn, entry="2026-08-01T00:00:00+00:00", exit_at=None)
    _paper_trade(conn, entry="2026-07-20T00:00:00+00:00", exit_at=None, regime="x")

    row = sv.track_sample_viability(conn, "crypto", now=NOW)

    assert row["entries_total"] == 2
    assert row["entries_in_effective_days"] == 1


def test_rate_confidence_interval_widens_on_small_windows(conn) -> None:
    """관측 창이 짧아 비율이 불안정하면 그 사실을 신뢰구간으로 남긴다(1-1)."""
    narrow = sv.poisson_rate_ci(2, 3)
    wide = sv.poisson_rate_ci(20, 30)

    assert narrow[1] - narrow[0] > wide[1] - wide[0]
    assert sv.poisson_rate_ci(0, 7)[0] == 0.0, "관측 0건의 하한은 0이다"


# ── 2. 구조적 차단 (폴리) ───────────────────────────────────────────────


def _poly_position(connection: sqlite3.Connection, market_id: str, end_at: str) -> None:
    connection.execute(
        """INSERT INTO poly_markets (market_id, slug, question, category, observed_at, end_at, active, closed, liquidity, payload)
        VALUES (?,?,?,'crypto','2026-08-01T00:00:00+00:00',?,1,0,50000,'{}')""",
        (market_id, market_id, market_id, end_at),
    )
    connection.execute(
        """INSERT INTO poly_estimates (id, judgment_id, market_id, observed_at, category, market_probability,
        estimated_probability, confidence_low, confidence_high, estimate_quality, direction, gross_edge, payload)
        VALUES (?,?,?,'2026-08-01T00:00:00+00:00','crypto',0.5,0.6,0.5,0.7,'medium','YES',0.1,'{}')""",
        (f"est-{market_id}", f"judg-{market_id}", market_id),
    )
    connection.execute(
        """INSERT INTO poly_positions (market_id, estimate_id, direction, shares, average_price, cost, opened_at, status, payload)
        VALUES (?,?, 'YES', 10, 0.5, 5, '2026-08-01T00:00:00+00:00', 'open', '{}')""",
        (market_id, f"est-{market_id}"),
    )
    connection.commit()


def _poly_track(connection: sqlite3.Connection, ends_at: str) -> None:
    connection.execute(
        """INSERT INTO poly_paper_track (id, currency, parameter_version, started_at, ends_at, initial_cash, cash, created_at, updated_at)
        VALUES (1, 'USDC', 'poly-v3', '2026-07-22T00:00:00+00:00', ?, 1000, 900, '2026-07-22T00:00:00+00:00', '2026-08-06T00:00:00+00:00')""",
        (ends_at,),
    )
    connection.commit()


def test_poly_is_structurally_blocked_when_every_expiry_is_past_the_deadline(conn) -> None:
    """실측 재현: 보유가 전부 검증 종료 이후 만기면 28일을 다 채워도 표본이 0이다."""
    _poly_track(conn, "2026-08-19T00:00:00+00:00")
    _poly_position(conn, "m1", "2027-01-01T00:00:00+00:00")
    _poly_position(conn, "m2", "2027-01-01T00:00:00+00:00")
    _valid_days(conn, "poly", [f"2026-07-{day:02d}" for day in range(24, 31)])

    row = sv.track_sample_viability(conn, "poly", now=NOW)

    assert row["verdict"] == sv.VERDICT_STRUCTURALLY_BLOCKED
    assert "검증 창 내 정산 예정 0건" in row["verdict_reason"]


def test_poly_is_not_blocked_when_an_expiry_lands_inside_the_window(conn) -> None:
    """반증 가능해야 한다 — 창 안 만기가 하나라도 있으면 구조적 차단이 아니다."""
    _poly_track(conn, "2026-08-19T00:00:00+00:00")
    _poly_position(conn, "m1", "2027-01-01T00:00:00+00:00")
    _poly_position(conn, "m2", "2026-08-10T00:00:00+00:00")
    _valid_days(conn, "poly", [f"2026-07-{day:02d}" for day in range(24, 31)])

    block = sv.poly_structural_block(conn, now=NOW)

    assert block["blocked"] is False
    assert block["settling_within_validation"] == 1


def test_structural_block_is_not_claimed_without_a_deadline(conn) -> None:
    """검증 종료일을 모르면 판정하지 않는다 — 모르는 것을 차단으로 단정하지 않는다."""
    _poly_position(conn, "m1", "2027-01-01T00:00:00+00:00")
    assert sv.poly_structural_block(conn, now=NOW)["blocked"] is False


# ── 3. 판정 등급 ────────────────────────────────────────────────────────


def test_short_window_is_insufficient_data_not_a_verdict(conn) -> None:
    _valid_days(conn, "crypto", ["2026-08-05"])
    assert sv.track_sample_viability(conn, "crypto", now=NOW)["verdict"] == sv.VERDICT_INSUFFICIENT_DATA


def test_zero_entries_reports_zero_and_does_not_estimate(conn) -> None:
    """실적이 0인 트랙을 유니버스 크기로 추정하지 않는다 — 0은 0으로 적는다."""
    _valid_days(conn, "stock_us", ["2026-08-03", "2026-08-04", "2026-08-05"])

    row = sv.track_sample_viability(conn, "stock_us", now=NOW)

    assert (row["entries_in_effective_days"], row["projected_samples_at_target"]) == (0, 0)
    assert row["verdict"] == sv.VERDICT_INSUFFICIENT_DATA
    assert "진입 0건" in row["verdict_reason"]


def test_slow_track_is_not_dressed_up_as_viable(conn) -> None:
    """D+28 예상 표본이 30 미만이면 SLOW 다. 목표 표본 수를 낮춰 통과시키지 않는다."""
    days = [f"2026-07-{day:02d}" for day in range(20, 30)]
    _valid_days(conn, "crypto", days)
    for index, day in enumerate(days[:4]):
        _paper_trade(conn, entry=f"{day}T00:00:00+00:00", exit_at=f"{day}T12:00:00+00:00", regime=f"r{index}")

    row = sv.track_sample_viability(conn, "crypto", now=NOW)

    assert row["verdict"] == sv.VERDICT_SLOW
    assert row["projected_samples_at_target"] < sv.TARGET_SAMPLES
    assert row["sample_sufficient"] is False


def test_viable_requires_reaching_the_target_sample(conn) -> None:
    days = [(date(2026, 7, 1) + timedelta(days=offset)).isoformat() for offset in range(20)]
    _valid_days(conn, "crypto", days)
    for index, day in enumerate(days):
        for slot in range(3):
            _paper_trade(conn, entry=f"{day}T0{slot}:00:00+00:00", exit_at=f"{day}T12:00:00+00:00", regime=f"r{index}-{slot}")

    row = sv.track_sample_viability(conn, "crypto", now=NOW)

    assert row["verdict"] == sv.VERDICT_VIABLE
    assert row["projected_samples_at_target"] >= sv.TARGET_SAMPLES


# ── 4. 검증 완료 정의 (3조건) ───────────────────────────────────────────


def test_completion_requires_all_three_conditions(conn) -> None:
    """유효일만 채워도 완료가 아니다 — 표본과 국면이 함께 충족돼야 한다."""
    days = [(date(2026, 7, 1) + timedelta(days=offset)).isoformat() for offset in range(28)]
    _valid_days(conn, "crypto", days)

    completion = sv.validation_completion(conn, "crypto", now=NOW)

    assert completion["conditions"]["effective_days"]["met"] is True
    assert completion["complete"] is False
    assert "채점 가능 표본" in completion["unmet"]


def test_completion_line_names_the_laggard_condition(conn) -> None:
    """가장 뒤처진 조건이 그 트랙의 상태다 — 유효일만 크게 표시하지 않는다."""
    _valid_days(conn, "crypto", [(date(2026, 7, 1) + timedelta(days=offset)).isoformat() for offset in range(14)])

    completion = sv.validation_completion(conn, "crypto", now=NOW)

    assert completion["laggard"] == "scored_samples"
    assert "표본 0/30" in completion["line"]
    assert "미달" in completion["line"]


def test_regime_condition_counts_entry_time_labels_only(conn) -> None:
    """국면은 진입 시점에 기록된 라벨로만 센다 — 사후 라벨링은 룩어헤드다."""
    _valid_days(conn, "crypto", ["2026-08-01", "2026-08-02", "2026-08-03"])
    _paper_trade(conn, entry="2026-08-01T00:00:00+00:00", exit_at="2026-08-02T00:00:00+00:00", regime="uptrend")
    _paper_trade(conn, entry="2026-08-02T00:00:00+00:00", exit_at="2026-08-03T00:00:00+00:00", regime="downtrend")

    regimes = sv.regime_coverage(conn, "crypto")

    assert regimes["distinct"] == 2
    assert regimes["observed"] == ["downtrend", "uptrend"]


def test_regime_unavailable_stays_unmet_rather_than_assumed(conn) -> None:
    """라벨이 없는 트랙을 '충족'으로 처리하면 완료 정의가 무의미해진다."""
    completion = sv.validation_completion(conn, "poly", now=NOW)

    assert completion["regimes"]["available"] is False
    assert completion["conditions"]["regimes"]["met"] is False


def test_blocked_track_line_says_structurally_blocked(conn) -> None:
    _poly_track(conn, "2026-08-19T00:00:00+00:00")
    _poly_position(conn, "m1", "2027-01-01T00:00:00+00:00")
    _valid_days(conn, "poly", [f"2026-07-{day:02d}" for day in range(24, 31)])

    completion = sv.validation_completion(conn, "poly", now=NOW)

    assert "구조적 차단" in completion["line"]


# ── 5. 진술 규칙 ────────────────────────────────────────────────────────


def test_statement_uses_effective_days_and_samples_never_calendar_weeks(conn) -> None:
    """✕ '3주 검증 중' / ○ '유효 관측일 14/28, 채점 가능 표본 4/30 — 표본 부족'."""
    _valid_days(conn, "crypto", [(date(2026, 7, 1) + timedelta(days=offset)).isoformat() for offset in range(14)])

    statement = sv.track_sample_viability(conn, "crypto", now=NOW)["statement"]

    assert "유효 관측일 14/28" in statement
    assert "채점 가능 표본" in statement and "표본 부족" in statement
    assert "주" not in statement.replace("주식", ""), "캘린더 주 단위 진술 금지"


def test_report_lists_blocked_tracks(conn) -> None:
    _poly_track(conn, "2026-08-19T00:00:00+00:00")
    _poly_position(conn, "m1", "2027-01-01T00:00:00+00:00")
    _valid_days(conn, "poly", [f"2026-07-{day:02d}" for day in range(24, 31)])

    report = sv.sample_viability_report(conn, now=NOW)

    assert report["blocked_tracks"] == ["poly"]
    assert set(report["tracks"]) == set(sv.TRACK_SAMPLE_SPECS)
