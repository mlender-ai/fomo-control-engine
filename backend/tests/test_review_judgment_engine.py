from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.db.models import (
    Direction,
    JudgmentLedgerEntry,
    JudgmentScore,
    Position,
    PositionSnapshot,
    Trade,
)
from app.review.engine import (
    build_calibration_summary,
    build_review_v2,
    build_weekly_calibration_report,
    generate_calibration_suggestions,
    generate_review_text,
    score_interim_judgments,
    score_judgments,
)


def _trade(created_at: datetime, exit_price: float = 90) -> Trade:
    return Trade(
        id=uuid4(),
        position_id=uuid4(),
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100,
        exit_price=exit_price,
        quantity=1,
        pnl_percent=-10,
        pnl_amount=-10,
        entry_score=70,
        exit_score=60,
        holding_minutes=120,
        exit_reason="test exit",
        review_text="",
        created_at=created_at,
    )


def _snapshot(position_id, at: datetime, price: float) -> PositionSnapshot:
    return PositionSnapshot(
        position_id=position_id,
        symbol="BTCUSDT",
        as_of=at,
        mark_price=price,
        pnl_percent=0,
        pnl_source="computed",
        liquidation_price=None,
        liquidation_distance_pct=None,
        health_score=50,
        status_label="관찰 필요",
        risk_score=50,
        score_json={},
        analysis_json={},
        created_at=at,
    )


def _judgment(
    position_id,
    as_of: datetime,
    judgment_type: str,
    claim: dict,
    confidence: int | None = None,
) -> JudgmentLedgerEntry:
    return JudgmentLedgerEntry(
        judgment_id=f"test:{judgment_type}:{claim}",
        position_id=position_id,
        source_type="test",
        as_of=as_of,
        type=judgment_type,
        claim=claim,
        confidence=confidence,
    )


def test_invalidation_score_uses_only_prices_after_judgment_as_of() -> None:
    as_of = datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc)
    trade = _trade(as_of + timedelta(hours=2), exit_price=100)
    judgment = _judgment(
        trade.position_id,
        as_of,
        "invalidation",
        {"price": 95, "condition": "break_below", "level_score": 50},
    )
    snapshots = [
        _snapshot(trade.position_id, as_of - timedelta(minutes=30), 90),
        _snapshot(trade.position_id, as_of + timedelta(minutes=30), 100),
    ]

    scores = score_judgments(trade, [judgment], snapshots, [])

    assert scores[0].outcome == "untested"
    assert scores[0].metrics["path_points"] == 2


def test_planned_invalidation_uses_only_prices_after_judgment_as_of() -> None:
    as_of = datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc)
    trade = _trade(as_of + timedelta(hours=2), exit_price=100)
    judgment = _judgment(
        trade.position_id,
        as_of,
        "planned_invalidation",
        {
            "price": 95,
            "level_score": 50,
            "param_version": {"min_invalidation_level_score": 40},
        },
    )
    snapshots = [
        _snapshot(trade.position_id, as_of - timedelta(minutes=30), 90),
        _snapshot(trade.position_id, as_of + timedelta(minutes=30), 100),
    ]

    scores = score_judgments(trade, [judgment], snapshots, [])

    assert scores[0].outcome == "untested"
    assert scores[0].metrics["path_points"] == 2


def test_invalidation_correct_and_whipsaw_are_distinguished() -> None:
    as_of = datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc)
    trade = _trade(as_of + timedelta(hours=2), exit_price=90)
    judgment = _judgment(
        trade.position_id,
        as_of,
        "invalidation",
        {"price": 95, "condition": "break_below", "level_score": 72},
    )

    correct = score_judgments(
        trade,
        [judgment],
        [_snapshot(trade.position_id, as_of + timedelta(minutes=30), 94)],
        [],
    )[0]
    whipsaw_trade = _trade(as_of + timedelta(hours=2), exit_price=98)
    whipsaw_trade.position_id = trade.position_id
    whipsaw = score_judgments(
        whipsaw_trade,
        [judgment],
        [_snapshot(trade.position_id, as_of + timedelta(minutes=30), 94)],
        [],
    )[0]

    assert correct.outcome == "correct"
    assert whipsaw.outcome == "whipsaw"


def test_take_profit_conservative_target_is_marked_wrong() -> None:
    as_of = datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc)
    trade = _trade(as_of + timedelta(hours=2), exit_price=110)
    judgment = _judgment(
        trade.position_id,
        as_of,
        "take_profit",
        {"price": 105, "condition": "touch_or_break_above"},
    )

    score = score_judgments(
        trade,
        [judgment],
        [_snapshot(trade.position_id, as_of + timedelta(minutes=30), 106)],
        [],
    )[0]

    assert score.outcome == "wrong"


def test_calibration_suggestion_requires_sample_and_low_accuracy() -> None:
    scores = []
    for index in range(16):
        scores.append(
            JudgmentScore(
                judgment_id=f"judgment-{index}",
                position_id=uuid4(),
                trade_id=uuid4(),
                judgment_type="invalidation",
                claim={"level_score": 50},
                confidence=None,
                outcome="correct" if index < 3 else "wrong",
                detail="fixture",
            )
        )

    suggestions = generate_calibration_suggestions(scores)
    summary = build_calibration_summary(scores, suggestions)

    assert suggestions
    assert suggestions[0].proposed_change["to"] == 55
    assert summary["invalidation"]["sample_state"] == "ok"

    # WO-36 §4: 제안에 OOS 검증(학습/검증 분할 + 검증기간 성립 여부)이 첨부된다.
    oos = suggestions[0].oos_validation
    assert oos["sample_state"] == "ok"
    assert oos["train"]["sample_size"] >= 5
    assert oos["validation"]["sample_size"] >= 5
    assert oos["holds_in_validation"] is True  # 낮은 적중률이 검증기간에도 지속


def test_calibration_suggestion_waits_for_proposal_sample_floor() -> None:
    scores = [
        JudgmentScore(
            judgment_id=f"judgment-{index}",
            position_id=uuid4(),
            trade_id=uuid4(),
            judgment_type="invalidation",
            claim={"level_score": 50},
            confidence=None,
            outcome="wrong",
            detail="fixture",
        )
        for index in range(14)
    ]

    suggestions = generate_calibration_suggestions(scores)

    assert not suggestions


def test_confidence_curve_withholds_conclusion_until_sample_floor() -> None:
    scores = [
        JudgmentScore(
            judgment_id=f"wyckoff-{index}",
            position_id=uuid4(),
            trade_id=uuid4(),
            judgment_type="wyckoff_event",
            claim={},
            confidence=76,
            outcome="correct" if index < 4 else "wrong",
            detail="fixture",
        )
        for index in range(9)
    ]

    summary = build_calibration_summary(scores, [])

    bucket = summary["confidence_curve"][0]
    assert bucket["bucket"] == "70-79"
    assert bucket["tested"] == 9
    assert bucket["sample_state"] == "insufficient_sample"
    assert bucket["calibration_state"] == "insufficient_sample"
    assert bucket["conclusion"] == "표본 부족"


def test_overconfident_confidence_bucket_creates_pending_suggestion() -> None:
    scores = [
        JudgmentScore(
            judgment_id=f"wyckoff-{index}",
            position_id=uuid4(),
            trade_id=uuid4(),
            judgment_type="wyckoff_event",
            claim={},
            confidence=82,
            outcome="correct" if index < 4 else "wrong",
            detail="fixture",
        )
        for index in range(16)
    ]

    suggestions = generate_calibration_suggestions(scores)

    confidence_suggestion = next(item for item in suggestions if item.suggestion_type == "confidence_floor_review")
    assert confidence_suggestion.proposed_change["parameter"] == "wyckoff_event_min_confidence"
    assert confidence_suggestion.sample_size == 16


def test_interim_scoring_open_position_tags_context_and_param_version() -> None:
    opened_at = datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc)
    position = Position(
        id=uuid4(),
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100,
        quantity=1,
        leverage=10,
        current_price=92,
        mark_price=92,
        pnl_percent=-80,
        opened_at=opened_at,
    )
    judgment = _judgment(
        position.id,
        opened_at + timedelta(minutes=5),
        "planned_invalidation",
        {"price": 95, "condition": "break_below", "level_score": 55},
    )
    judgment.param_version = {
        "min_invalidation_level_score": {
            "value": 55,
            "approved_at": "2026-07-04T00:00:00Z",
        }
    }
    snapshots = [
        _snapshot(position.id, opened_at + timedelta(minutes=10), 94),
        _snapshot(position.id, opened_at + timedelta(hours=1), 92),
    ]

    scores = score_interim_judgments(position, [judgment], snapshots, [], as_of=opened_at + timedelta(hours=1))

    assert len(scores) == 1
    assert scores[0].trade_id is None
    assert scores[0].outcome == "correct"
    assert scores[0].metrics["score_context"] == "interim"
    assert scores[0].param_version["min_invalidation_level_score"]["value"] == 55


def test_weekly_calibration_report_uses_recent_scores_only() -> None:
    now = datetime(2026, 7, 12, 11, 0, tzinfo=timezone.utc)
    recent = JudgmentScore(
        judgment_id="recent",
        position_id=uuid4(),
        trade_id=uuid4(),
        judgment_type="invalidation",
        claim={},
        confidence=None,
        outcome="correct",
        detail="fixture",
        created_at=now - timedelta(days=1),
    )
    old = JudgmentScore(
        judgment_id="old",
        position_id=uuid4(),
        trade_id=uuid4(),
        judgment_type="invalidation",
        claim={},
        confidence=None,
        outcome="wrong",
        detail="fixture",
        created_at=now - timedelta(days=8),
    )

    report = build_weekly_calibration_report([recent, old], [], now=now)

    assert report["totals"]["total"] == 1
    assert report["totals"]["correct"] == 1
    assert report["totals"]["sample_state"] == "insufficient_sample"
    assert "N=1" in report["highlights"][0]


def test_review_llm_rejects_numbers_not_present_in_input() -> None:
    as_of = datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc)
    trade = _trade(as_of + timedelta(hours=2), exit_price=110)
    review_v2 = build_review_v2(trade, [], [])

    text, source, fallback_reason = generate_review_text(
        trade,
        review_v2,
        api_key="test-key",
        model="test-model",
        llm_client=lambda _prompt, _model: "새 목표가는 99999 입니다.",
    )

    assert source == "fallback_template"
    assert fallback_reason == "llm_number_validation_failed"
    assert "99999" not in text
