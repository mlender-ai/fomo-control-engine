from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.db.models import Direction, JudgmentLedgerEntry, Trade
from app.db.repository import SQLiteRepository
from app.review.engine import score_judgments
from app.shadow.fomo import FOMO_INDEX_THRESHOLD, build_entry_fomo_snapshot, monthly_fomo_attribution


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


def test_entry_snapshot_is_deterministic_and_decomposed() -> None:
    snapshot = build_entry_fomo_snapshot(
        direction=Direction.long,
        entry_price=103,
        plan_price=100,
        report_created_at=NOW - timedelta(minutes=2),
        entered_at=NOW,
        scout_originated=False,
        held_stance="short_leaning",
        entry_state_label="관찰 가치 있음",
    )

    assert snapshot["complete"] is True
    assert snapshot["component_coverage_pct"] == 100.0
    assert snapshot["fomo_index"] == 99.3
    assert snapshot["components"]["chase"]["contribution"] == 40.0
    assert snapshot["components"]["stance"]["contribution"] == 25.0
    assert snapshot["policy"] == "entry_time_only_no_backfill"


def test_monthly_attribution_excludes_legacy_and_keeps_sample_guard() -> None:
    complete = {"complete": True, "components": {"chase": {"contribution": 20}}}
    trades = [
        _trade(fomo_index=80, fomo_components=complete, pnl_amount=-25, created_at=NOW),
        _trade(fomo_index=30, fomo_components=complete, pnl_amount=-75, created_at=NOW),
        _trade(entry_score=20, pnl_amount=-200, created_at=NOW),
    ]

    guarded = monthly_fomo_attribution(trades, now=NOW, min_trades=3)
    published = monthly_fomo_attribution(trades, now=NOW, min_trades=2)

    assert guarded["fomo_cost_usdt"] is None
    assert "표본 부족" in guarded["statement"]
    assert guarded["excluded_legacy_trades"] == 1
    assert published["fomo_cost_usdt"] == 25
    assert published["loss_share_pct"] == 25.0
    assert published["legacy_proxy_comparison"]["count"] == 1


def test_fomo_judgment_is_scored_on_exit() -> None:
    trade = _trade(fomo_index=90, fomo_components={"complete": True}, pnl_amount=-10, created_at=NOW)
    judgment = JudgmentLedgerEntry(
        judgment_id="fomo:entry:1",
        position_id=trade.position_id,
        source_type="entry_fomo_snapshot",
        as_of=NOW - timedelta(hours=1),
        type="fomo_entry",
        claim={"fomo_index": 90, "threshold": FOMO_INDEX_THRESHOLD, "complete": True},
    )

    score = score_judgments(trade, [judgment], [], [])[0]

    assert score.outcome == "correct"
    assert score.metrics["predicted_fomo_loss"] is True
    assert score.metrics["realized_loss"] is True


def test_sqlite_fomo_fields_are_queryable_and_survive_reopen(tmp_path) -> None:
    path = tmp_path / "fomo.db"
    trade = _trade(
        plan_price=100,
        chase_pct=2,
        report_to_entry_minutes=4,
        scout_originated=False,
        stance_alignment="against",
        entry_state_label="관찰",
        fomo_index=82,
        fomo_components={"complete": True},
        created_at=NOW,
    )
    SQLiteRepository(str(path)).add_trade(trade)

    restored = SQLiteRepository(str(path)).get_trade(trade.id)

    assert restored is not None
    assert restored.fomo_index == 82
    assert restored.stance_alignment == "against"


def _trade(**updates) -> Trade:
    payload = {
        "position_id": uuid4(),
        "symbol": "BTCUSDT",
        "direction": Direction.long,
        "entry_price": 100,
        "exit_price": 99,
        "quantity": 1,
        "pnl_percent": -1,
        "pnl_amount": -1,
        "entry_score": 70,
        "exit_score": 65,
        "holding_minutes": 60,
        "exit_reason": "test",
        "review_text": "",
    }
    payload.update(updates)
    return Trade(**payload)
