"""2026-09-19 — 유령 포지션·DB 비대·청산가 미수신 소음.

사용자 보고: "지금 near는 포지션이 없는데 잡혀있어".

펄스가 이미 청산된 NEARUSDT 를 이렇게 보고했다:

    🔴 NEARUSDT 숏 10.0x · PnL -108.55% · 건강도 25/100
    → 긴급 확인: 2.25 지지 반응. 도달 시 부분 익절 검토.

**없는 포지션의 손익을 자신 있게 말하는 것이 침묵보다 나쁘다.** 분석은 원장 값으로
계산되므로 청산된 포지션도 그럴듯한 숫자를 만들어낸다.

원장 5건 vs 거래소 3건이었고, 거래소에서 사라진 시각(`last_seen_at` 09-18T05:40)과
종료 확정(09-19T03:21·05:37) 사이가 **24시간**이었다.
"""

from __future__ import annotations

import sqlite3

from app.core.config import Settings
from app.db import maintenance
from app.db.sqlite_utils import connect_sqlite

# ── 유령 포지션 ────────────────────────────────────────────────────────


def test_unconfirmed_positions_are_reported_out_of_sync() -> None:
    """목록을 밖으로 내보내지 않으면 아무도 유령을 가릴 수 없다."""
    import inspect

    from app.services import http_handlers

    src = inspect.getsource(http_handlers._sync_bitget_positions)
    assert "unconfirmed_position_ids" in src
    assert "unconfirmed_ids.append" in src


def test_unconfirmed_positions_do_not_become_analyzed_holdings() -> None:
    """**이것이 사용자가 본 거짓말이다** — 없는 포지션이 정상 보유로 실렸다."""
    import inspect

    from app.services import http_handlers

    src = inspect.getsource(http_handlers.sync_live_positions)
    assert "unconfirmed" in src
    body = src.split("for position in positions:")[1]
    guard_at = body.index("unconfirmed")
    analyze_at = body.index("_live_position_payload")
    assert guard_at < analyze_at, "분석 전에 걸러야 한다 — 분석하면 가짜 숫자가 만들어진다"


def test_a_failed_close_still_persists_the_miss_counter() -> None:
    """저장하지 않으면 다음 순회가 같은 값을 다시 읽어 **확정 문턱에 영원히 닿지 못한다.**

    실측: ETHUSDT·NEARUSDT 가 `miss=1` 에 24시간 머물렀다(확정 틱은 2).
    """
    import inspect

    from app.services import http_handlers

    src = inspect.getsource(http_handlers._sync_bitget_positions)
    branch = src.split("elif error:")[1]
    assert "repository.update_position(position)" in branch


def test_the_counter_is_saved_before_branching() -> None:
    """분기마다 따로 저장하면 하나를 빠뜨린다 — 실제로 그렇게 빠졌다."""
    import inspect

    from app.services import http_handlers

    src = inspect.getsource(http_handlers._sync_bitget_positions)
    loop = src.split("for position in existing:")[1]
    assign_at = loop.index("position.sync_miss_count = ")
    branch_at = loop.index("if position.sync_miss_count < confirm_ticks")
    assert assign_at < branch_at


# ── 빈 페이지 회수 ─────────────────────────────────────────────────────


def test_reclaim_is_chunked_and_budgeted() -> None:
    """인자 없는 `incremental_vacuum` 은 144만 페이지를 한 번에 회수하려 한다 —
    10.8GB 를 락 걸고 복사하던 백업과 같은 정체가 된다."""
    assert maintenance._VACUUM_PAGES_PER_STEP > 0
    assert maintenance._VACUUM_BUDGET_SECONDS > 0
    import inspect

    src = inspect.getsource(maintenance.reclaim_free_pages)
    assert "incremental_vacuum(" in src, "페이지 수를 주지 않으면 조각내는 의미가 없다"


def test_reclaim_actually_shrinks_the_freelist(tmp_path) -> None:
    path = tmp_path / "v.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
    connection.execute("VACUUM")
    connection.execute("CREATE TABLE t (a TEXT)")
    connection.executemany("INSERT INTO t (a) VALUES (?)", [("x" * 2000,) for _ in range(4000)])
    connection.commit()
    connection.execute("DELETE FROM t")
    connection.commit()
    before = connection.execute("PRAGMA freelist_count").fetchone()[0]
    assert before > 0, "이 테스트의 전제가 성립하지 않는다"
    result = maintenance.reclaim_free_pages(connection, pages_per_step=50, budget_seconds=20.0)
    assert result["pages_reclaimed"] > 0
    assert result["freelist_after"] < before
    connection.close()


def test_reclaim_respects_its_budget(tmp_path) -> None:
    """예산을 넘기면 회수가 다른 작업을 굶긴다 — 회수는 급하지 않다."""
    path = tmp_path / "b.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
    connection.execute("VACUUM")
    connection.execute("CREATE TABLE t (a TEXT)")
    connection.executemany("INSERT INTO t (a) VALUES (?)", [("x" * 2000,) for _ in range(3000)])
    connection.commit()
    connection.execute("DELETE FROM t")
    connection.commit()
    result = maintenance.reclaim_free_pages(connection, pages_per_step=1, budget_seconds=0.2)
    assert result["elapsed_seconds"] <= 5
    assert result["complete"] is False or result["freelist_after"] == 0
    connection.close()


def test_reclaim_is_harmless_on_a_plain_database(tmp_path) -> None:
    """`auto_vacuum` 이 아닌 DB 에서 터지면 리텐션 전체가 죽는다."""
    path = tmp_path / "p.db"
    connection = connect_sqlite(path)
    connection.execute("CREATE TABLE t (a INTEGER)")
    connection.commit()
    result = maintenance.reclaim_free_pages(connection, pages_per_step=10, budget_seconds=1.0)
    assert result["status"] == "ok"
    connection.close()


# ── 청산가 미수신 알림 ─────────────────────────────────────────────────


def test_liq_unknown_alert_is_off_by_default() -> None:
    """사용자 운용에서는 **증거금을 낮게 잡으면 청산가가 없는 것이 정상**이다.
    정상 상태를 경고로 계속 내보내면 알림 전체의 신뢰가 깎인다(사용자 지시 2026-09-19)."""
    assert "liq_unknown_high_lev" not in Settings().alert_enabled_rule_set


def test_real_liquidation_proximity_is_still_on() -> None:
    """이것은 진짜 위험 신호다 — 같이 끄면 안 된다."""
    assert "liq_proximity" in Settings().alert_enabled_rule_set


def test_the_rule_is_only_delisted_not_deleted() -> None:
    """수집·조회는 유지한다. 다시 켜고 싶을 때 코드를 되살릴 필요가 없어야 한다."""
    from app.notify import rules

    assert "liq_unknown_high_lev" in rules.RULE_LABELS
    assert hasattr(rules, "_liq_unknown_candidates")
