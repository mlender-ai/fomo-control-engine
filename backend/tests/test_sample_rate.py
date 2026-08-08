"""WO-FCE-SAMPLE-RATE-01 — 속도 격차·역산·결정 대기 회귀.

배경: 관측·측정·판정 장치가 갖춰졌고 답이 나왔다 — **표본 생성 속도가 검증 완료 기준에
미달한다.** 이제 "30건을 어떻게 모을 것인가"에 기준을 낮추지 않고 답해야 한다.

고정하는 명제:
1. 진입만 봐서는 오진한다 — 보유기간·슬롯 회전이 표본 처리량을 제한한다
2. 부족분 역산은 세 축(진입률·관측일·보유기간)을 **대안**으로 낸다 (합산 아님)
3. 보유 표본이 적으면 평균을 만들지 않는다
4. 코드로 풀 수 없는 결정은 잊히지 않게 상시 노출한다
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.db.migrations import run_migrations
from app.validation import pending_decisions as pd
from app.validation import sample_rate as sr

NOW = datetime(2026, 8, 7, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "rate.db")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    yield connection
    connection.close()


def _trade(connection: sqlite3.Connection, trade_id: str, *, entry: str, exit_at: str | None) -> None:
    connection.execute(
        "INSERT INTO paper_trades (id, symbol, timeframe, status, entry_bar_at, exit_at, updated_at, payload) VALUES (?,?,?,?,?,?,?,?)",
        (trade_id, "BTCUSDT", "4h", "closed" if exit_at else "open", entry, exit_at, entry, json.dumps({})),
    )
    connection.commit()


def _fill(connection: sqlite3.Connection, fill_id: str, *, market: str, symbol: str, side: str, at: str) -> None:
    connection.execute(
        "INSERT INTO stock_paper_orders (id, market, symbol, side, status, signal_at, updated_at, payload) VALUES (?,?,?,?,?,?,?,?)",
        (f"order-{fill_id}", market, symbol, side, "filled", at, at, "{}"),
    )
    connection.execute(
        "INSERT INTO stock_paper_fills (id, order_id, market, symbol, side, filled_at, payload) VALUES (?,?,?,?,?,?,?)",
        (fill_id, f"order-{fill_id}", market, symbol, side, at, "{}"),
    )
    connection.commit()


def _coverage(connection: sqlite3.Connection, track: str, days: list[str]) -> None:
    connection.executemany(
        """INSERT INTO observation_coverage (day, track, coverage_pct, valid, trading_day, computed_at)
        VALUES (?, ?, 100.0, 1, 1, '2026-08-07T00:00:00+00:00')""",
        [(day, track) for day in days],
    )
    connection.commit()


# ── 1. 보유기간과 동시 보유 ─────────────────────────────────────────────


def test_holding_profile_measures_duration_and_capacity(conn) -> None:
    """슬롯이 유한하므로 표본 처리량은 보유기간에 갇힌다 — 둘 다 재야 한다."""
    for index in range(4):
        day = 1 + index
        _trade(conn, f"t{index}", entry=f"2026-08-0{day}T00:00:00+00:00", exit_at=f"2026-08-0{day + 2}T00:00:00+00:00")

    profile = sr.holding_profile(sr.holding_intervals(conn, "crypto"))

    assert profile["n"] == 4
    assert profile["mean_days"] == 2.0
    # 2일씩 겹쳐 열리므로 동시 보유가 1보다 크다.
    assert profile["observed_max_concurrent"] >= 2


def test_holding_profile_refuses_to_average_tiny_samples(conn) -> None:
    """1~2건의 평균은 숫자가 아니다."""
    _trade(conn, "t0", entry="2026-08-01T00:00:00+00:00", exit_at="2026-08-02T00:00:00+00:00")

    profile = sr.holding_profile(sr.holding_intervals(conn, "crypto"))

    assert profile["mean_days"] is None
    assert "신뢰할 수 없다" in profile["reason"]


def test_stock_intervals_pair_buys_with_later_sells(conn) -> None:
    """주식 원장에는 거래 단위가 없고 체결만 있다 — 심볼별 순서대로 짝짓는다."""
    _fill(conn, "f1", market="KR", symbol="005930", side="buy", at="2026-08-01T00:00:00+00:00")
    _fill(conn, "f2", market="KR", symbol="005930", side="sell", at="2026-08-03T00:00:00+00:00")

    intervals = sr.holding_intervals(conn, "stock_kr")

    assert len(intervals) == 1
    assert (intervals[0][1] - intervals[0][0]).days == 2


def test_unmatched_sell_is_discarded(conn) -> None:
    """짝이 없는 매도를 억지로 짝지으면 없는 거래가 생긴다."""
    _fill(conn, "f1", market="KR", symbol="005930", side="sell", at="2026-08-01T00:00:00+00:00")

    assert sr.holding_intervals(conn, "stock_kr") == []


# ── 2. 부족분 역산 ──────────────────────────────────────────────────────


def test_gap_reports_shortfall_against_target(conn) -> None:
    days = [(NOW.date() - timedelta(days=offset)).isoformat() for offset in range(1, 11)]
    _coverage(conn, "crypto", days)
    for index, day in enumerate(days[:4]):
        _trade(conn, f"t{index}", entry=f"{day}T00:00:00+00:00", exit_at=f"{day}T12:00:00+00:00")

    gap = sr.track_rate_gap(conn, "crypto", now=NOW)

    assert gap["current_samples"] == 4
    assert gap["shortfall"] > 0
    assert gap["target_samples"] == 30


def test_inversion_offers_three_alternatives(conn) -> None:
    """세 축은 대안이지 합산 대상이 아니다."""
    days = [(NOW.date() - timedelta(days=offset)).isoformat() for offset in range(1, 11)]
    _coverage(conn, "crypto", days)
    for index, day in enumerate(days):
        _trade(conn, f"t{index}", entry=f"{day}T00:00:00+00:00", exit_at=f"{day}T12:00:00+00:00")

    inversion = sr.track_rate_gap(conn, "crypto", now=NOW)["inversion"]

    assert set(inversion) == {"entry_rate", "effective_days", "holding_period"}
    assert inversion["entry_rate"]["multiplier"] is not None
    assert inversion["effective_days"]["additional_effective_days"] is not None


def test_inversion_says_it_cannot_invert_without_throughput(conn) -> None:
    """표본 생성 속도가 0이면 기간을 늘려도 표본이 늘지 않는다 — 숫자를 만들지 않는다."""
    _coverage(conn, "stock_us", [(NOW.date() - timedelta(days=offset)).isoformat() for offset in range(1, 6)])

    inversion = sr.track_rate_gap(conn, "stock_us", now=NOW)["inversion"]

    assert inversion["effective_days"]["additional_effective_days"] is None
    assert "표본이 늘지 않는다" in inversion["effective_days"]["reason"]


def test_ci_low_projection_is_not_more_optimistic(conn) -> None:
    """점추정만 보면 낙관에 기댄 계획이 된다."""
    days = [(NOW.date() - timedelta(days=offset)).isoformat() for offset in range(1, 11)]
    _coverage(conn, "crypto", days)
    for index, day in enumerate(days):
        _trade(conn, f"t{index}", entry=f"{day}T00:00:00+00:00", exit_at=f"{day}T12:00:00+00:00")

    gap = sr.track_rate_gap(conn, "crypto", now=NOW)

    assert gap["projected_samples_ci_low"] <= gap["projected_samples"]
    assert gap["shortfall_ci_low"] >= gap["shortfall"]


def test_report_lists_tracks_with_shortfall(conn) -> None:
    report = sr.rate_gap_report(conn, now=NOW)

    assert set(report["tracks"]) == {"crypto", "stock_kr", "stock_us", "poly"}
    assert "기준을 낮춰서가 아니라" in report["principle"]


# ── 3. 결정 대기 ────────────────────────────────────────────────────────


def test_unsigned_gate_is_a_blocking_decision() -> None:
    items = pd.pending_decisions(gate_approved=False, sleep_guard={"available": True, "guarded": True})

    ids = {item["id"] for item in items}
    assert "live_trading_gate_signature" in ids
    assert any(item["severity"] == pd.BLOCKING for item in items)


def test_signed_gate_closes_that_item() -> None:
    """코드로 확인 가능한 항목은 자동으로 닫힌다."""
    items = pd.pending_decisions(gate_approved=True, sleep_guard={"available": True, "guarded": True})

    assert "live_trading_gate_signature" not in {item["id"] for item in items}


def test_guarded_host_closes_the_persistence_item() -> None:
    items = pd.pending_decisions(gate_approved=True, sleep_guard={"available": True, "guarded": True})

    assert "host_persistence_choice" not in {item["id"] for item in items}


def test_unguarded_host_reopens_the_persistence_item() -> None:
    items = pd.pending_decisions(gate_approved=True, sleep_guard={"available": True, "guarded": False})

    assert "host_persistence_choice" in {item["id"] for item in items}


def test_statistical_method_stays_pending_until_a_human_picks() -> None:
    """방법 채택은 검증 기준 변경이다 — 코드가 스스로 닫지 않는다."""
    items = pd.pending_decisions(gate_approved=True, sleep_guard={"available": True, "guarded": True})

    method = next(item for item in items if item["id"] == "statistical_method_adoption")
    assert method["severity"] == pd.BLOCKING
    assert "283" in method["detail"]


def test_summary_counts_blocking_separately() -> None:
    summary = pd.pending_summary(pd.pending_decisions(gate_approved=False, sleep_guard={"available": True, "guarded": False}))

    # 게이트 서명 + 통계 방법 + DIRECTIONAL-INTEGRITY 3건 = 차단 5건, 호스트 지속성은 영향 항목.
    assert summary["count"] == 6
    assert summary["blocking_count"] == 5
    assert "진행 차단" in summary["line"]


def test_directional_integrity_decisions_block_until_a_human_picks() -> None:
    """WO-FCE-DIRECTIONAL-INTEGRITY-01 §0 — 이 3건 전에는 Phase 0 외 착수하지 않는다."""
    items = pd.pending_decisions(gate_approved=True, sleep_guard={"available": True, "guarded": True})

    required = {"validation_window_contamination", "wyckoff_role", "directional_coverage_thresholds"}
    by_id = {item["id"]: item for item in items}

    assert required <= set(by_id)
    for item_id in required:
        assert by_id[item_id]["severity"] == pd.BLOCKING, f"{item_id} 는 진행 차단 항목이어야 한다"


def test_weekly_lines_name_each_pending_item() -> None:
    """건수만 세면 무엇을 기다리는지 알 수 없다."""
    lines = pd.format_pending_lines(pd.pending_decisions(gate_approved=False, sleep_guard={"available": True, "guarded": True}))

    joined = "\n".join(lines)
    assert "결정 대기" in joined
    assert "LIVE_TRADING_GATE.md" in joined
    assert "코드로 풀 수 없습니다" in joined


def test_no_pending_items_still_reports_explicitly() -> None:
    assert "없음" in "\n".join(pd.format_pending_lines([]))
