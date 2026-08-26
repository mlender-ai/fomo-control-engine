"""WO-FCE-STOCK-STATUS-01 — 주식 트랙 정지·거부 카운터·유실일 계약.

이 WO 는 진단이 본체다. 아래 테스트는 진단으로 확정한 **기전**을 고정한다 — 같은 형태가
다시 생기면 여기서 걸린다.
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from app.db import maintenance
from app.stock_paper.store import REJECTION_COUNTER_NOTE, REJECTION_COUNTER_WINDOW, RESUME_PROCEDURE, _halt_block
from app.validation.pending_decisions import BLOCKING, IMPACTING, effective_day_ceiling, pending_decisions

REPO_ROOT = Path(__file__).resolve().parents[2]

# C3 — 진입 게이트·판정 로직은 이 WO 가 건드리지 않는다.
UNTOUCHABLE = ("backend/app/analyst", "backend/app/structure", "backend/app/stock_paper/policy.py")


def _events_db() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE stock_paper_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL, symbol TEXT, order_id TEXT,
            event_type TEXT NOT NULL, reason TEXT, observed_at TEXT NOT NULL, payload TEXT NOT NULL)"""
    )
    return connection


# ── D1 · 리텐션이 사건 증거를 지웠다 ────────────────────────────────────


def test_retention_never_deletes_halt_evidence() -> None:
    """**이 WO 의 D1 원인.** 스팸을 지우면서 사건 증거가 함께 사라졌다.

    `unfilled` 2,087만 행 대 `track_stopped`·`invariant_failure` 각 1행. id 오름차순으로
    지우면 희소한 증거가 먼저 사라지고, 실제로 US 트랙이 왜·언제 멈췄는지 조회 불가가 됐다.
    """
    connection = _events_db()
    connection.execute(
        "INSERT INTO stock_paper_events (market, event_type, reason, observed_at, payload) VALUES ('US','invariant_failure','fill_price_outside_observed_range','2026-08-20T00:00:00Z','{}')"
    )
    connection.execute(
        "INSERT INTO stock_paper_events (market, event_type, reason, observed_at, payload) VALUES ('US','track_stopped','fill_price_outside_observed_range','2026-08-20T00:00:00Z','{}')"
    )
    connection.executemany(
        "INSERT INTO stock_paper_events (market, event_type, reason, observed_at, payload) VALUES ('KR','unfilled','session_closed','2026-08-20T00:00:00Z','{}')",
        [() for _ in range(5_000)],
    )

    result = maintenance._trim_stock_paper_events(connection, keep_rows=100, delete_budget=10_000)

    assert int(result["stock_paper_events_deleted"]) > 0, "스팸을 지우지 않았다"
    survivors = {row["event_type"] for row in connection.execute("SELECT DISTINCT event_type FROM stock_paper_events")}
    assert "invariant_failure" in survivors, "정지 유발 증거가 지워졌다"
    assert "track_stopped" in survivors, "정지 기록이 지워졌다"


def test_only_registered_types_are_trimmable() -> None:
    """목록에 적는 행위가 검토 지점이다. 새 종류를 조용히 지우지 않는다."""
    assert maintenance.TRIMMABLE_STOCK_EVENT_TYPES == ("unfilled",)
    for preserved in ("track_stopped", "invariant_failure", "fill", "partial_fill", "exit_signal"):
        assert preserved not in maintenance.TRIMMABLE_STOCK_EVENT_TYPES


def test_trim_counts_only_trimmable_rows_toward_the_cap() -> None:
    """상한은 스팸에 대한 것이다. 보존 종류가 상한을 잡아먹으면 스팸이 남는다."""
    connection = _events_db()
    connection.executemany(
        "INSERT INTO stock_paper_events (market, event_type, reason, observed_at, payload) VALUES ('KR','exit_signal','time_stop','2026-08-20T00:00:00Z','{}')",
        [() for _ in range(500)],
    )
    connection.executemany(
        "INSERT INTO stock_paper_events (market, event_type, reason, observed_at, payload) VALUES ('KR','unfilled','session_closed','2026-08-20T00:00:00Z','{}')",
        [() for _ in range(1_000)],
    )
    result = maintenance._trim_stock_paper_events(connection, keep_rows=100, delete_budget=10_000)
    remaining_spam = connection.execute("SELECT COUNT(*) FROM stock_paper_events WHERE event_type='unfilled'").fetchone()[0]
    assert int(result["stock_paper_events_deleted"]) > 0
    assert remaining_spam <= 100, f"보존 종류가 상한을 먹어 스팸이 {remaining_spam}행 남았다"
    assert connection.execute("SELECT COUNT(*) FROM stock_paper_events WHERE event_type='exit_signal'").fetchone()[0] == 500


# ── 3-2 · 정지 상태 노출 ────────────────────────────────────────────────


def test_halt_block_reports_reason_and_resume_procedure() -> None:
    block = _halt_block({"status": "stopped", "stop_reason": "fill_price_outside_observed_range"}, [])
    assert block["stopped"] is True
    assert block["reason"] == "fill_price_outside_observed_range"
    assert block["resume_procedure"] == RESUME_PROCEDURE
    assert block["auto_resume"] is False, "C2 — 자동 재개는 금지다"


def test_halt_block_does_not_guess_the_stop_time() -> None:
    """`updated_at` 은 정지 시각이 아니다 — 트랙 행이 갱신될 때마다 바뀐다.

    실측에서 KR(정상)과 US(정지)의 `updated_at` 이 **2ms 차이**였다. 그것을 정지 시각으로
    표시하면 거짓이 된다. 이력이 없으면 없다고 적는다(C5).
    """
    block = _halt_block({"status": "stopped", "stop_reason": "x", "updated_at": "2026-08-25T23:42:44Z"}, [])
    assert block["stopped_at"] is None
    assert block["stopped_at_known"] is False
    assert "조회할 수 없다" in block["evidence_note"]


def test_halt_block_uses_the_event_time_when_present() -> None:
    history = [{"event_type": "track_stopped", "observed_at": "2026-08-24T01:02:03Z", "reason": "x", "symbol": None, "detail": None}]
    block = _halt_block({"status": "stopped", "stop_reason": "x"}, history)
    assert block["stopped_at"] == "2026-08-24T01:02:03Z"
    assert block["stopped_at_known"] is True
    assert block["evidence_note"] is None


def test_running_track_gets_no_halt_noise() -> None:
    block = _halt_block({"status": "running", "stop_reason": None}, [])
    assert block["stopped"] is False
    assert block["evidence_note"] is None
    assert block["resume_procedure"] is None


# ── 3-3 · 거부 카운터 정합 ─────────────────────────────────────────────


def test_rejection_counter_declares_its_window() -> None:
    """창 없는 수를 창 있는 수처럼 보여주면 안 된다 (`METRIC-TRUTH-01` 선례)."""
    assert "누적" in REJECTION_COUNTER_WINDOW
    assert "서로 다른 거부 건수가 아니라" in REJECTION_COUNTER_NOTE


# ── 3-5 · 유실일 → 유효일 상한 ─────────────────────────────────────────


def test_lost_days_cap_the_effective_days() -> None:
    """28일 창에서 19일을 잃으면 남은 날을 다 채워도 9일이다."""
    row = effective_day_ceiling(calendar_days=21, lost_days=19)
    assert row["effective_day_ceiling"] == 9
    assert row["reachable"] is False
    assert "유효일 최대 9일" in row["label"]


def test_no_loss_is_reachable() -> None:
    assert effective_day_ceiling(calendar_days=28, lost_days=0)["reachable"] is True


def test_unreachable_ceiling_escalates_host_persistence_to_blocking() -> None:
    """진행이 늦어지는 것과 성립하지 않는 것은 다르다. 실측이 등급을 올린다."""
    ceilings = {"US": effective_day_ceiling(calendar_days=21, lost_days=19)}
    item = next(row for row in pending_decisions(gate_approved=False, sleep_guard={}, lost_day_ceilings=ceilings) if row["id"] == "host_persistence_choice")
    assert item["severity"] == BLOCKING
    assert "성립하지 않는다" in item["detail"]
    assert "caffeinate" in item["remedy"]


def test_reachable_ceiling_stays_impacting() -> None:
    ceilings = {"US": effective_day_ceiling(calendar_days=28, lost_days=0)}
    item = next(row for row in pending_decisions(gate_approved=False, sleep_guard={}, lost_day_ceilings=ceilings) if row["id"] == "host_persistence_choice")
    assert item["severity"] == IMPACTING


# ── 제약 증명 ──────────────────────────────────────────────────────────


def test_invariant_is_not_relaxed() -> None:
    """C1 — 체결가 범위 검사는 옳다. 정지 조건을 느슨하게 만들지 않는다."""
    source = (REPO_ROOT / "backend/app/stock_paper/execution.py").read_text(encoding="utf-8")
    assert "raise FillInvariantViolation" in source
    assert "observation.minute_low <= fill_price <= observation.minute_high" in source


def test_no_auto_resume_path_exists() -> None:
    """C2 — 원인 확인 없이 재개하면 같은 체결이 다시 들어간다.

    사람에게 보여주는 **절차 안내 문자열**과 실제 실행 경로를 구분해야 한다. 안내는 있어야
    하고(3-2), 코드가 스스로 실행하는 경로는 없어야 한다(C2). 그래서 `connection.execute`
    호출만 본다.
    """
    grep = subprocess.run(
        ["git", "grep", "-n", "-E", r"execute\(.*status='running'", "--", "backend/app"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert grep.stdout.strip() == "", f"코드가 스스로 트랙을 재개한다(C2 위반):\n{grep.stdout}"
    # 안내 문자열은 반대로 **있어야** 한다 — 지금까지 푸는 법이 어디에도 없었다.
    assert "수동 재개" in RESUME_PROCEDURE and "자동 재개는 금지" in RESUME_PROCEDURE


def test_entry_gates_and_direction_layers_are_untouched() -> None:
    """C3 — 진입 게이트·판정 로직 diff 0줄."""
    diff = subprocess.run(["git", "diff", "origin/main", "--stat", "--", *UNTOUCHABLE], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")
    assert diff.stdout.strip() == "", f"C3 위반:\n{diff.stdout}"


def test_host_persistence_severity_never_drops_below_impacting() -> None:
    """`pending_decisions` 에서 실제로 위험한 것은 **등급이 조용히 낮아지는 것**이다.

    파일 동결(`VERDICT_MODULES`) 대신 이 속성을 고정한다. 어떤 입력에도 이 항목이
    사라지거나 차단·영향 미만으로 내려가지 않는다 — 완화는 사람만 할 수 있다.
    """
    cases = [
        {},
        {"US": effective_day_ceiling(calendar_days=28, lost_days=0)},
        {"US": effective_day_ceiling(calendar_days=21, lost_days=19)},
        {"KR": effective_day_ceiling(calendar_days=1, lost_days=0), "US": effective_day_ceiling(calendar_days=21, lost_days=19)},
    ]
    for ceilings in cases:
        items = pending_decisions(gate_approved=False, sleep_guard={}, lost_day_ceilings=ceilings)
        item = next((row for row in items if row["id"] == "host_persistence_choice"), None)
        assert item is not None, f"항목이 사라졌다: {ceilings}"
        assert item["severity"] in {BLOCKING, IMPACTING}, f"등급이 낮아졌다: {item['severity']}"


def test_guarded_state_only_disappears_when_the_host_is_protected() -> None:
    """보호 중이면 결정 대기에서 빠진다 — 그것만이 항목이 사라지는 조건이다."""
    guarded = pending_decisions(gate_approved=False, sleep_guard={"available": True, "guarded": True}, lost_day_ceilings={})
    assert not any(row["id"] == "host_persistence_choice" for row in guarded)


# ── 3-4 · 자본·표본 표시 ───────────────────────────────────────────────


def test_zero_strategy_sample_is_stated_next_to_the_headline() -> None:
    """`+0.07%` 가 탐색 표본 5건의 값이라는 사실이 지금은 안 보인다 (WO 3-4 항목 3)."""
    from app.stock_paper.store import _sample_breakdown

    block = _sample_breakdown("KR", {("KR", "coverage"): 5, ("KR", "strict_signal"): 0})
    assert block["strategy_fills"] == 0
    assert block["exploration_fills"] == 5
    assert block["strategy_sample_zero"] is True
    assert "탐색 계정이 움직인 결과" in block["headline_note"]


def test_exploration_is_never_folded_into_strategy() -> None:
    """C4 — 탐색 표본을 전략 성적에 합산하지 않는다."""
    from app.stock_paper.store import STRATEGY_ENTRY_MODE, _sample_breakdown

    block = _sample_breakdown("US", {("US", "coverage"): 6, ("US", "strict_signal"): 3})
    assert block["strategy_fills"] == 3, "탐색 체결이 전략 표본에 섞였다"
    assert block["validation_eligible_mode"] == STRATEGY_ENTRY_MODE
    assert "합산하지 않는다" in block["exclusion_note"]
