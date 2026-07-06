from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.db.models import (
    AlertRecord,
    AlertResponseRecord,
    Direction,
    MonitoringLog,
    Position,
    PositionSnapshot,
    PositionStatus,
    Trade,
    utc_now,
)

RESPONSE_SAMPLE_FLOOR = 5
PRICE_TOLERANCE_PCT = 0.5
QUANTITY_TOLERANCE = 1e-9


def detect_alert_response(
    alert: AlertRecord,
    position: Position | None,
    trades: list[Trade],
    *,
    as_of: datetime | None = None,
    window_hours: float = 6.0,
) -> AlertResponseRecord | None:
    if alert.position_id is None:
        return None
    now = _aware(as_of or utc_now())
    fired_at = _aware(alert.fired_at)
    window_end = fired_at + timedelta(hours=window_hours)
    if now < fired_at:
        return None

    baseline_qty = _float(alert.payload.get("quantity_at_alert") or alert.payload.get("position_quantity"))
    baseline_stop = _float(alert.payload.get("planned_stop_at_alert") or alert.payload.get("planned_stop_price"))
    baseline_price = _float(alert.payload.get("current_price") or alert.payload.get("mark_price") or alert.payload.get("trigger_price"))
    trade = _first_trade_after(trades, fired_at, window_end)
    if trade is not None:
        return _response(
            alert,
            "closed_full",
            trade.created_at,
            trade.exit_price,
            baseline_qty,
            0.0,
            baseline_stop,
            None,
        )

    if position is None:
        if now >= window_end:
            return _response(
                alert,
                "held",
                window_end,
                baseline_price,
                baseline_qty,
                baseline_qty,
                baseline_stop,
                baseline_stop,
            )
        return None

    detected_at = min(now, window_end)
    current_qty = _float(position.quantity)
    current_price = _float(position.mark_price or position.current_price or baseline_price)
    current_stop = _float(position.planned_stop_price)

    if position.status == PositionStatus.closed or (current_qty is not None and current_qty <= QUANTITY_TOLERANCE):
        return _response(
            alert,
            "closed_full",
            _aware(position.closed_at or detected_at),
            current_price,
            baseline_qty,
            0.0,
            baseline_stop,
            current_stop,
        )
    if baseline_qty is not None and current_qty is not None:
        if current_qty < baseline_qty - QUANTITY_TOLERANCE:
            return _response(
                alert,
                "reduced",
                detected_at,
                current_price,
                baseline_qty,
                current_qty,
                baseline_stop,
                current_stop,
            )
        if current_qty > baseline_qty + QUANTITY_TOLERANCE:
            return _response(
                alert,
                "added",
                detected_at,
                current_price,
                baseline_qty,
                current_qty,
                baseline_stop,
                current_stop,
            )
    if _stop_changed(baseline_stop, current_stop):
        return _response(
            alert,
            "stop_moved",
            detected_at,
            current_price,
            baseline_qty,
            current_qty,
            baseline_stop,
            current_stop,
        )
    if now >= window_end:
        return _response(
            alert,
            "held",
            window_end,
            current_price,
            baseline_qty,
            current_qty,
            baseline_stop,
            current_stop,
        )
    return None


def score_alert_response(
    response: AlertResponseRecord,
    alert: AlertRecord,
    position: Position | None,
    snapshots: list[PositionSnapshot],
    monitoring_logs: list[MonitoringLog],
    *,
    trades: list[Trade] | None = None,
    outcome_hours: float = 24.0,
) -> AlertResponseRecord:
    direction = _direction(position, alert)
    if direction not in {"long", "short"}:
        return response.model_copy(
            update={
                "outcome": "inconclusive",
                "result_detail": "포지션 방향을 확인할 수 없어 대응 결과를 보류했습니다.",
            }
        )
    base = _float(response.price_at_response)
    if base is None or base <= 0:
        return response.model_copy(
            update={
                "outcome": "inconclusive",
                "result_detail": "대응 시점 가격이 없어 결과 비교를 보류했습니다.",
            }
        )
    path = _price_path_after(response.detected_at, snapshots, monitoring_logs, trades or [], outcome_hours)
    if not path:
        return response.model_copy(
            update={
                "outcome": "inconclusive",
                "result_detail": "대응 이후 24시간 가격 경로가 부족합니다.",
            }
        )
    final = path[-1]["price"]
    adverse_pct = _adverse_pct(direction, base, final)
    cost_usdt = _cost_usdt(
        base,
        final,
        response.quantity_at_response if response.quantity_at_response is not None else response.quantity_at_alert,
    )
    if response.response in {"closed_full", "reduced"}:
        if adverse_pct > PRICE_TOLERANCE_PCT:
            outcome = "response_good"
            detail = "결과론적 비교: 대응 이후 위험 방향 움직임이 이어져 손실 확대를 일부 피했습니다."
        elif adverse_pct < -PRICE_TOLERANCE_PCT:
            outcome = "response_costly"
            detail = "결과론적 비교: 대응 이후 가격이 회복되어 청산/축소가 보수적이었을 수 있습니다."
        else:
            outcome = "inconclusive"
            detail = "결과론적 비교: 대응 이후 가격 변화가 판정 임계값보다 작습니다."
    else:
        if adverse_pct > PRICE_TOLERANCE_PCT:
            outcome = "response_costly"
            detail = "결과론적 비교: 유지/증액 이후 위험 방향 움직임이 이어졌습니다."
        elif adverse_pct < -PRICE_TOLERANCE_PCT:
            outcome = "response_good"
            detail = "결과론적 비교: 유지/조정 이후 가격이 회복되었습니다."
        else:
            outcome = "inconclusive"
            detail = "결과론적 비교: 대응 이후 가격 변화가 판정 임계값보다 작습니다."
    metrics = {
        **response.metrics,
        "response_as_of": _aware(response.detected_at).isoformat(),
        "outcome_window_hours": outcome_hours,
        "path_points": len(path),
        "final_price": round(final, 8),
        "adverse_pct": round(adverse_pct, 4),
        "result_pct": round(
            -adverse_pct if response.response in {"closed_full", "reduced"} else adverse_pct,
            4,
        ),
        "estimated_cost_usdt": round(cost_usdt, 4) if cost_usdt is not None else None,
        "comparison_note": "결과론적 비교입니다. 다음 알림 임계값에 자동 반영하지 않습니다.",
    }
    return response.model_copy(update={"outcome": outcome, "result_detail": detail, "metrics": metrics})


def build_alert_response_summary(
    responses: list[AlertResponseRecord],
) -> dict[str, Any]:
    by_rule: dict[str, dict[str, Any]] = {}
    for rule_id, items in _group_by_rule(responses).items():
        by_rule[rule_id] = _response_bucket(items)
    total = _response_bucket(responses)
    return {
        "sample_floor": RESPONSE_SAMPLE_FLOOR,
        "total": total,
        "by_rule": by_rule,
        "behavior_summary": _behavior_summary(responses),
        "sample_warning": "대응 표본 N < 5 구간은 이력 문장을 표시하지 않습니다.",
    }


def alert_history_line(responses: list[AlertResponseRecord], rule_id: str) -> str | None:
    similar = [response for response in responses if response.rule_id == rule_id]
    if len(similar) < RESPONSE_SAMPLE_FLOOR:
        return None
    held = len([response for response in similar if response.response == "held"])
    costly_held = len([response for response in similar if response.response == "held" and response.outcome == "response_costly"])
    if held == 0:
        return f"참고: 최근 유사 알림 {len(similar)}건 중 기록된 유지 대응은 0건입니다."
    if costly_held == 0:
        return f"참고: 최근 유사 알림 {len(similar)}건 중 유지 {held}건, 추가 손실로 채점된 사례는 0건입니다."
    return f"참고: 최근 유사 알림 {len(similar)}건 중 유지 {held}건, 유지 후 추가 손실 {costly_held}건입니다."


def _response_bucket(items: list[AlertResponseRecord]) -> dict[str, Any]:
    counts = Counter(item.response for item in items)
    outcomes = Counter(item.outcome for item in items)
    tested = len([item for item in items if item.outcome != "inconclusive"])
    good = outcomes["response_good"]
    costly = outcomes["response_costly"]
    costs = [_float(item.metrics.get("estimated_cost_usdt")) for item in items if _float(item.metrics.get("estimated_cost_usdt")) is not None]
    pct_values = [_float(item.metrics.get("adverse_pct")) for item in items if _float(item.metrics.get("adverse_pct")) is not None]
    return {
        "total": len(items),
        "tested": tested,
        "sample_state": "ok" if len(items) >= RESPONSE_SAMPLE_FLOOR else "insufficient_sample",
        "response_good": good,
        "response_costly": costly,
        "inconclusive": outcomes["inconclusive"],
        "good_rate_pct": round(good / tested * 100, 1) if tested else None,
        "costly_rate_pct": round(costly / tested * 100, 1) if tested else None,
        "avg_estimated_cost_usdt": round(sum(costs) / len(costs), 4) if costs else None,
        "avg_adverse_pct": round(sum(pct_values) / len(pct_values), 4) if pct_values else None,
        "responses": dict(counts),
        "outcomes": dict(outcomes),
    }


def _behavior_summary(responses: list[AlertResponseRecord]) -> str:
    critical = [item for item in responses if item.rule_id in {"invalidation_breach", "liq_proximity"}]
    if len(critical) < RESPONSE_SAMPLE_FLOOR:
        return "유사 critical 알림 표본이 아직 부족합니다."
    held = [item for item in critical if item.response == "held"]
    held_rate = len(held) / len(critical)
    costly_held = [item for item in held if item.outcome == "response_costly"]
    if held_rate >= 0.7 and len(costly_held) > len(held) / 2:
        return f"이탈/청산 접근 알림 {len(critical)}건 중 유지 {len(held)}건, 유지 후 추가 손실 {len(costly_held)}건입니다."
    return f"이탈/청산 접근 알림 {len(critical)}건 중 유지 {len(held)}건입니다."


def _group_by_rule(
    responses: list[AlertResponseRecord],
) -> dict[str, list[AlertResponseRecord]]:
    grouped: dict[str, list[AlertResponseRecord]] = defaultdict(list)
    for response in responses:
        grouped[response.rule_id].append(response)
    return grouped


def _response(
    alert: AlertRecord,
    response: str,
    detected_at: datetime,
    price: float | None,
    qty_at_alert: float | None,
    qty_at_response: float | None,
    stop_at_alert: float | None,
    stop_at_response: float | None,
) -> AlertResponseRecord:
    return AlertResponseRecord(
        id=uuid5(NAMESPACE_URL, f"fce:alert-response:{alert.id}:{response}"),
        alert_id=alert.id,
        position_id=alert.position_id,
        rule_id=alert.rule_id,
        symbol=alert.symbol,
        response=response,
        detected_at=_aware(detected_at),
        price_at_response=price,
        quantity_at_alert=qty_at_alert,
        quantity_at_response=qty_at_response,
        planned_stop_at_alert=stop_at_alert,
        planned_stop_at_response=stop_at_response,
        metrics={
            "alert_fired_at": _aware(alert.fired_at).isoformat(),
            "alert_title": alert.payload.get("title"),
        },
    )


def _first_trade_after(trades: list[Trade], start: datetime, end: datetime) -> Trade | None:
    candidates = [trade for trade in trades if start <= _aware(trade.created_at) <= end]
    return sorted(candidates, key=lambda item: item.created_at)[0] if candidates else None


def _price_path_after(
    start: datetime,
    snapshots: list[PositionSnapshot],
    logs: list[MonitoringLog],
    trades: list[Trade],
    hours: float,
) -> list[dict[str, Any]]:
    start = _aware(start)
    end = start + timedelta(hours=hours)
    points: list[dict[str, Any]] = []
    for snapshot in snapshots:
        at = _aware(snapshot.as_of)
        if start < at <= end and snapshot.mark_price is not None:
            points.append({"time": at, "price": float(snapshot.mark_price)})
    for log in logs:
        at = _aware(log.created_at)
        if start < at <= end:
            points.append({"time": at, "price": float(log.current_price)})
    for trade in trades:
        at = _aware(trade.created_at)
        if start < at <= end:
            points.append({"time": at, "price": float(trade.exit_price)})
    return sorted(points, key=lambda item: item["time"])


def _direction(position: Position | None, alert: AlertRecord) -> str | None:
    if position is not None:
        return position.direction.value if isinstance(position.direction, Direction) else str(position.direction)
    value = alert.payload.get("direction") or alert.payload.get("position_direction")
    return str(value).lower() if value else None


def _adverse_pct(direction: str, base: float, final: float) -> float:
    if direction == "short":
        return ((final - base) / base) * 100
    return ((base - final) / base) * 100


def _cost_usdt(base: float, final: float, quantity: float | None) -> float | None:
    if quantity is None:
        return None
    return abs(final - base) * quantity


def _stop_changed(before: float | None, after: float | None) -> bool:
    if before is None and after is None:
        return False
    if before is None or after is None:
        return True
    return abs(before - after) > max(abs(before) * 0.000001, 1e-12)


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
