"""일일 요약 내용물 + 생존 신호 푸시 강등 회귀.

사용자 지시(2026-08-16):

    "하트비트 죽는거 그만 보고해. 살아있다는것도 보고하지마. 그게 아무런 도움이 안 돼.
     그냥 포지션을 들어갔냐, 폴리마켓은, 리더보드는 잘 검증되고 있는지만 요약해서 보내라고."

고정하는 명제:
1. 생존·사망·복구 신호는 텔레그램으로 나가지 않는다 (앱 경로 + 외부 감시자 양쪽)
2. 포지션 위험 경보는 계속 나간다 — 생존 강등이 critical 경보를 함께 끄면 새 사고다
3. 요약에는 진입·폴리·리더보드가 들어간다. 0건도 적는다
4. 표본 부족은 숨기지 않는다
5. 감시 자체는 그대로 돈다 — 강등이지 삭제가 아니다
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.db.migrations import run_migrations
from app.notify import content_summary, delivery_gate

NOW = datetime(2026, 8, 16, 0, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[2]


# ── 1. 생존 신호는 푸시되지 않는다 ──────────────────────────────────


def test_liveness_rules_are_not_pushed() -> None:
    for rule in ("engine_liveness", "process_restarted", "data_stall", "infra_capacity", "job_backoff_stuck"):
        decision = delivery_gate.evaluate_rule(rule)
        assert decision.allowed is False, f"{rule} 이 아직 푸시된다"
        assert "생존" in decision.reason


def test_position_alerts_still_push() -> None:
    """생존 강등이 critical 포지션 경보를 함께 끄면 그건 수리가 아니라 새 사고다."""
    for rule in ("invalidation_breach", "liq_proximity", "mdd_limit_critical", "position_closed", "take_profit_hit"):
        assert delivery_gate.evaluate_rule(rule).allowed is True, f"{rule} 이 막혔다"


def test_deadman_does_not_push_by_default() -> None:
    """외부 감시자도 발송하지 않는다 — 앱만 막으면 절반만 막은 것이다."""
    source = (REPO_ROOT / "scripts" / "local" / "deadman.sh").read_text(encoding="utf-8")
    assert 'DEADMAN_PUSH="${FCE_DEADMAN_PUSH:-0}"' in source, "기본값이 발송이면 지시가 지켜지지 않는다"
    assert "생존 알림 미발송(푸시 강등)" in source


def test_watchdog_still_runs() -> None:
    """강등이지 삭제가 아니다 — 판정·재시작·기록은 그대로여야 한다."""
    source = (REPO_ROOT / "scripts" / "local" / "supervisor.sh").read_text(encoding="utf-8")
    assert "heartbeat stale" in source and "kill -9" in source, "감시·복구가 사라졌다"
    assert "capture-hang.sh" in source, "증거 포착이 사라졌다"


def test_registry_snapshot_exposes_the_demotion() -> None:
    """무엇이 왜 안 오는지 조회 가능해야 한다 — 조용한 차단은 금지다."""
    snapshot = delivery_gate.registry_snapshot()
    assert "engine_liveness" in snapshot["liveness_demoted_rules"]
    assert snapshot["liveness_demotion_reason"]


# ── 2. 요약 내용물 ──────────────────────────────────────────────────


def _db(tmp_path) -> str:
    path = tmp_path / "summary.db"
    connection = sqlite3.connect(path)
    run_migrations(connection)
    connection.close()
    return f"sqlite:///{path}"


def test_summary_has_all_three_sections(tmp_path) -> None:
    lines = content_summary.content_summary_lines(_db(tmp_path), now=NOW)
    body = "\n".join(lines)
    assert "진입·청산" in body
    assert "폴리마켓" in body
    assert "리더보드" in body


def test_zero_entries_are_still_reported(tmp_path) -> None:
    """줄이 없으면 '알림이 안 온 건가'와 구분되지 않는다."""
    body = "\n".join(content_summary.content_summary_lines(_db(tmp_path), now=NOW))
    assert "진입 0건" in body
    assert "주식: 체결 0건" in body


def test_poly_zero_settlement_is_stated_every_time(tmp_path) -> None:
    """검증 창 내 정산 0건은 매번 적어야 한다 — 조용히 두면 '아직 안 나왔나 보다'로 읽힌다."""
    url = _db(tmp_path)
    path = url.removeprefix("sqlite:///")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO poly_markets (market_id, slug, question, category, observed_at, end_at, active, closed, payload) "
            "VALUES ('1','s','q','crypto',?,?,1,0,'{}')",
            (NOW.isoformat(), "2027-01-01T05:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO poly_positions (market_id, estimate_id, direction, shares, average_price, cost, opened_at, status, payload, entry_mode) "
            "VALUES ('1','e','YES',10,0.5,5,?,'open','{}','strict_edge')",
            (NOW.isoformat(),),
        )
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        line = content_summary.poly_line(connection, now=NOW)

    assert "0건" in line and "표본이 생기지 않습니다" in line
    assert "2027-01-01" in line


def test_whale_line_states_the_selection_basis(tmp_path) -> None:
    """승률을 앞에 세우면 '승률로 뽑았다'로 오해된다 — 선정 기준을 매번 적는다."""
    path = _db(tmp_path).removeprefix("sqlite:///")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        line = content_summary.whale_line(connection)
    assert "리더보드" in line
    assert "청산 표본이 없습니다" in line or "quality_score" in line


def test_query_failure_degrades_one_line_only(tmp_path) -> None:
    """조회 실패가 요약 전체를 막으면 아무것도 못 받는다."""
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    lines = content_summary.content_summary_lines(f"sqlite:///{empty}", now=NOW)
    body = "\n".join(lines)
    assert "미산출" in body, "실패를 사유와 함께 적어야 한다"
    assert "폴리마켓" in body and "리더보드" in body, "한 줄 실패가 나머지를 지웠다"


def test_no_liveness_wording_in_summary(tmp_path) -> None:
    """요약에 생존 문구가 다시 스며들면 지시가 무력화된다."""
    body = "\n".join(content_summary.content_summary_lines(_db(tmp_path), now=NOW))
    for banned in ("하트비트", "생존", "사망", "heartbeat"):
        assert banned not in body, f"요약에 '{banned}' 가 들어 있다"
