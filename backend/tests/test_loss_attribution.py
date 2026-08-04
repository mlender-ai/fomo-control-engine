"""WO-FCE-ENTRY-THROUGHPUT-01 작업 3 — 손실 패인 분류 회귀.

주식 청산 엔진은 WO-FCE-STOCK-EXIT-01 이 정본이다(`stock_paper/exit_policy.py`).
이 파일은 패인 분류만 검증한다.
"""

from __future__ import annotations

from app.review.loss_attribution import (
    LOSS_REASON_LABELS,
    attribution_summary,
    classify_loss,
    format_attribution_lines,
)


def test_profitable_trade_is_not_classified() -> None:
    assert classify_loss({"net_pnl_usdt": 5.0}) is None


def test_gap_loss_takes_precedence() -> None:
    trade = {"net_pnl_usdt": -10.0, "exit_reason": "gap_loss", "holding_bars": 9}
    assert classify_loss(trade) == "gap_loss"


def test_time_stop_classified() -> None:
    for reason in ("time_stop", "time_decay"):
        trade = {"net_pnl_usdt": -1.0, "exit_reason": reason, "holding_bars": 30}
        assert classify_loss(trade) == "time_stop"


def test_immediate_adverse_move_is_entry_too_late() -> None:
    trade = {"net_pnl_usdt": -8.0, "exit_reason": "invalidation_breach", "holding_bars": 1}
    assert classify_loss(trade) == "entry_too_late"


def test_recovery_after_stop_is_invalidation_too_tight() -> None:
    """손절 뒤 원래 방향으로 되돌아왔으면 손절폭이 좁았던 것이다."""
    trade = {
        "net_pnl_usdt": -10.0,
        "exit_reason": "invalidation_breach",
        "holding_bars": 5,
        "entry_price": 100.0,
        "invalidation_price": 90.0,
        "direction": "long",
    }
    # 진입 100, 리스크 10. 청산 후 106까지 회복 → 0.6R 복귀.
    assert classify_loss(trade, post_exit_extreme=106.0) == "invalidation_too_tight"


def test_no_recovery_data_does_not_guess() -> None:
    """복귀 관측이 없으면 좁은 손절이라고 단정하지 않는다."""
    trade = {
        "net_pnl_usdt": -10.0,
        "exit_reason": "invalidation_breach",
        "holding_bars": 5,
        "entry_price": 100.0,
        "invalidation_price": 90.0,
    }
    assert classify_loss(trade) == "invalidation_correct"


def test_stance_flip_is_structure_misread() -> None:
    trade = {
        "net_pnl_usdt": -10.0,
        "exit_reason": "invalidation_breach",
        "holding_bars": 5,
        "entry_price": 100.0,
        "invalidation_price": 90.0,
        "stance_snapshot": {"state": "accumulation", "exit_state": "distribution"},
    }
    assert classify_loss(trade, post_exit_extreme=101.0) == "structure_misread"


def test_all_six_reasons_have_labels() -> None:
    expected = {
        "invalidation_correct",
        "invalidation_too_tight",
        "entry_too_late",
        "structure_misread",
        "gap_loss",
        "time_stop",
    }
    assert set(LOSS_REASON_LABELS) == expected


def test_attribution_summary_makes_no_number_on_empty() -> None:
    summary = attribution_summary([])
    assert summary["total"] == 0
    assert summary["reasons"] == []
    assert summary["top_reason"] is None
    assert "분류 대상 손실 없음" in "\n".join(format_attribution_lines(summary))


def test_attribution_summary_ranks_and_states_no_auto_apply() -> None:
    summary = attribution_summary(["invalidation_too_tight"] * 4 + ["time_stop", "gap_loss", None])
    text = "\n".join(format_attribution_lines(summary))

    assert summary["total"] == 6
    assert summary["top_reason"] == "invalidation_too_tight"
    assert "손절폭 재검토 <b>후보</b>" in text
    # 자동 적용 금지 원칙이 리포트 문장에 남아야 한다.
    assert "자동 변경 입력이 아닙니다" in text


def test_coverage_slot_count_excludes_exit_exempt_orphans(tmp_path) -> None:
    """WO-FCE-BREACH-ALERT-FIX-01: 청산 엔진이 관리할 수 없는 고아가 슬롯을 점유하면
    진입 0과 청산 0이 서로를 지탱하는 데드락이 된다(2026-08-04 실측 KR 3/US 3).
    """
    import sqlite3

    from app.db.migrations import run_migrations
    from app.stock_paper.models import Market
    from app.stock_paper.store import StockPaperStore

    db = tmp_path / "slots.db"
    with sqlite3.connect(db) as connection:
        run_migrations(connection)
    store = StockPaperStore(f"sqlite:///{db}")
    with store._connect() as connection:
        for symbol, excluded in (("AAA", 1), ("BBB", 1), ("CCC", 0)):
            connection.execute(
                """INSERT INTO stock_paper_mode_positions
                (market, symbol, entry_mode, quantity, average_price, currency, updated_at, excluded_from_stats)
                VALUES ('KR', ?, 'coverage', 1, 100.0, 'KRW', '2026-08-04T00:00:00+00:00', ?)""",
                (symbol, excluded),
            )

    # 관리 가능한 것만 슬롯을 소비한다 — 고아 2건은 별도로 노출된다.
    assert store.mode_position_count(Market.KR, "coverage") == 1
    assert store.exit_exempt_count(Market.KR, "coverage") == 2
