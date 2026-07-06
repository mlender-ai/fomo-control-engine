from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.db.models import (
    AlertRecord,
    AlertResponseRecord,
    Direction,
    Position,
    PositionSnapshot,
    Trade,
)
from app.review.alert_responses import (
    alert_history_line,
    detect_alert_response,
    score_alert_response,
)


def _alert(position_id, at: datetime, rule_id: str = "invalidation_breach") -> AlertRecord:
    return AlertRecord(
        id=uuid4(),
        rule_id=rule_id,
        position_id=position_id,
        symbol="BTCUSDT",
        severity="critical",
        fired_at=at,
        payload={
            "current_price": 95,
            "trigger_price": 96,
            "quantity_at_alert": 1.0,
            "planned_stop_at_alert": 96,
            "position_direction": "long",
        },
        delivered=True,
    )


def _position(position_id, *, quantity: float = 1.0, price: float = 95, status="open") -> Position:
    return Position(
        id=position_id,
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100,
        quantity=quantity,
        leverage=10,
        status=status,
        mark_price=price,
        planned_stop_price=96,
        opened_at=datetime(2026, 7, 4, tzinfo=timezone.utc),
    )


def _trade(position_id, at: datetime, exit_price: float = 94) -> Trade:
    return Trade(
        id=uuid4(),
        position_id=position_id,
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100,
        exit_price=exit_price,
        quantity=1,
        pnl_percent=-60,
        pnl_amount=-6,
        entry_score=70,
        exit_score=30,
        holding_minutes=120,
        exit_reason="test close",
        review_text="",
        created_at=at,
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


def test_alert_response_detects_reduced_held_and_closed() -> None:
    fired_at = datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc)
    position_id = uuid4()
    alert = _alert(position_id, fired_at)

    reduced = detect_alert_response(
        alert,
        _position(position_id, quantity=0.4, price=93),
        [],
        as_of=fired_at + timedelta(hours=1),
    )
    held = detect_alert_response(
        alert,
        _position(position_id, quantity=1.0, price=94),
        [],
        as_of=fired_at + timedelta(hours=7),
    )
    closed = detect_alert_response(
        alert,
        _position(position_id, quantity=1.0, price=94),
        [_trade(position_id, fired_at + timedelta(hours=2))],
        as_of=fired_at + timedelta(hours=3),
    )

    assert reduced is not None and reduced.response == "reduced"
    assert held is not None and held.response == "held"
    assert closed is not None and closed.response == "closed_full"


def test_alert_response_scoring_uses_only_prices_after_response() -> None:
    fired_at = datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc)
    position_id = uuid4()
    alert = _alert(position_id, fired_at)
    response = AlertResponseRecord(
        alert_id=alert.id,
        position_id=position_id,
        rule_id=alert.rule_id,
        symbol="BTCUSDT",
        response="held",
        detected_at=fired_at + timedelta(hours=6),
        price_at_response=95,
        quantity_at_alert=1,
        quantity_at_response=1,
    )
    snapshots = [
        _snapshot(position_id, fired_at + timedelta(hours=1), 80),
        _snapshot(position_id, fired_at + timedelta(hours=7), 95.1),
    ]

    scored = score_alert_response(response, alert, _position(position_id), snapshots, [], outcome_hours=24)

    assert scored.outcome == "inconclusive"
    assert scored.metrics["path_points"] == 1


def test_held_after_critical_alert_costly_and_closed_good() -> None:
    fired_at = datetime(2026, 7, 4, 0, 0, tzinfo=timezone.utc)
    position_id = uuid4()
    alert = _alert(position_id, fired_at)
    held = AlertResponseRecord(
        alert_id=alert.id,
        position_id=position_id,
        rule_id=alert.rule_id,
        symbol="BTCUSDT",
        response="held",
        detected_at=fired_at + timedelta(hours=6),
        price_at_response=95,
        quantity_at_response=1,
    )
    closed = held.model_copy(update={"id": uuid4(), "response": "closed_full"})
    path = [_snapshot(position_id, fired_at + timedelta(hours=8), 90)]

    held_score = score_alert_response(held, alert, _position(position_id), path, [])
    closed_score = score_alert_response(closed, alert, _position(position_id), path, [])

    assert held_score.outcome == "response_costly"
    assert closed_score.outcome == "response_good"


def test_alert_history_line_requires_five_samples() -> None:
    position_id = uuid4()
    responses = [
        AlertResponseRecord(
            alert_id=uuid4(),
            position_id=position_id,
            rule_id="invalidation_breach",
            symbol="BTCUSDT",
            response="held",
            outcome="response_costly",
        )
        for _ in range(4)
    ]
    assert alert_history_line(responses, "invalidation_breach") is None
    responses.append(
        AlertResponseRecord(
            alert_id=uuid4(),
            position_id=position_id,
            rule_id="invalidation_breach",
            symbol="BTCUSDT",
            response="closed_full",
            outcome="response_good",
        )
    )
    assert "최근 유사 알림 5건" in (alert_history_line(responses, "invalidation_breach") or "")
