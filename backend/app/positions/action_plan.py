from __future__ import annotations

from typing import Any

from app.db.models import Position, PositionSnapshot


def build_action_plan(position: Position, snapshot: PositionSnapshot, chart_analysis: dict[str, Any]) -> dict[str, Any]:
    mark_price = snapshot.mark_price or chart_analysis.get("mark_price") or position.mark_price or position.current_price
    levels = chart_analysis.get("price_levels", {})
    support = _levels(levels.get("support"))
    resistance = _levels(levels.get("resistance"))
    invalidation_levels = _levels(levels.get("invalidation"))
    volume_profile = chart_analysis.get("volume_profile", {})
    volume_xray = chart_analysis.get("volume_xray", {})
    candles = chart_analysis.get("candles", [])
    direction = position.direction.value

    invalidation = _planned_invalidation(position, mark_price)
    engine_invalidation = _engine_invalidation(direction, invalidation_levels, support, resistance, mark_price)
    if invalidation is None:
        invalidation = engine_invalidation

    return {
        "as_of": snapshot.as_of.isoformat(),
        "mark_price": mark_price,
        "invalidation": invalidation,
        "engine_invalidation": engine_invalidation if invalidation != engine_invalidation else None,
        "take_profit": _take_profit_targets(position, mark_price, support, resistance, volume_profile, candles),
        "watch_triggers": _watch_triggers(position, mark_price, volume_profile, volume_xray),
        "liquidation": _liquidation(position),
    }


def _planned_invalidation(position: Position, mark_price: float | None) -> dict[str, Any] | None:
    if position.planned_stop_price is None:
        return None
    return _plan_item(
        position=position,
        price=position.planned_stop_price,
        mark_price=mark_price,
        basis="사용자 기록 손절/무효화 가격",
        action="이탈 시 손절 검토" if position.direction.value == "long" else "돌파 시 손절 검토",
    )


def _engine_invalidation(
    direction: str,
    invalidation_levels: list[dict[str, Any]],
    support: list[dict[str, Any]],
    resistance: list[dict[str, Any]],
    mark_price: float | None,
) -> dict[str, Any] | None:
    source = invalidation_levels[0] if invalidation_levels else None
    if source is None:
        source = support[0] if direction == "long" and support else resistance[0] if direction == "short" and resistance else None
    if not source or source.get("price") is None:
        return None
    action = "이탈 시 손절 검토" if direction == "long" else "돌파 시 손절 검토"
    return {
        "price": source["price"],
        "basis": _basis(source, "현행 차트 레벨 기반 무효화 가격"),
        "distance_pct": _distance_pct(source["price"], mark_price, direction),
        "action": action,
    }


def _take_profit_targets(
    position: Position,
    mark_price: float | None,
    support: list[dict[str, Any]],
    resistance: list[dict[str, Any]],
    volume_profile: dict[str, Any],
    candles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    direction = position.direction.value
    targets: list[dict[str, Any]] = []
    if position.planned_take_profit_price is not None:
        targets.append(
            _plan_item(
                position=position,
                price=position.planned_take_profit_price,
                mark_price=mark_price,
                basis="사용자 기록 익절 가격",
                action="부분 익절 검토",
            )
        )

    level_targets = resistance if direction == "long" else support
    target_label = "저항" if direction == "long" else "지지"
    for level in level_targets:
        price = level.get("price")
        if price is None or not _is_favorable_target(price, mark_price, direction):
            continue
        targets.append(
            _plan_item(
                position=position,
                price=price,
                mark_price=mark_price,
                basis=_basis(level, f"현행 차트 레벨 {target_label}"),
                action="부분 익절 검토",
            )
        )

    profile_key = "value_area_high" if direction == "long" else "value_area_low"
    profile_price = volume_profile.get(profile_key)
    if profile_price is not None and _is_favorable_target(profile_price, mark_price, direction):
        targets.append(
            _plan_item(
                position=position,
                price=profile_price,
                mark_price=mark_price,
                basis="추정 볼륨 프로파일 VAH" if direction == "long" else "추정 볼륨 프로파일 VAL",
                action="부분 익절 검토",
            )
        )

    previous_extreme = _previous_extreme(candles, direction)
    if previous_extreme is not None and _is_favorable_target(previous_extreme, mark_price, direction):
        targets.append(
            _plan_item(
                position=position,
                price=previous_extreme,
                mark_price=mark_price,
                basis="최근 100개 캔들 직전 고점" if direction == "long" else "최근 100개 캔들 직전 저점",
                action="추가 익절 검토",
            )
        )
    return _dedupe_price_items(targets)[:3]


def _watch_triggers(position: Position, mark_price: float | None, volume_profile: dict[str, Any], volume_xray: dict[str, Any]) -> list[dict[str, str]]:
    triggers: list[dict[str, str]] = []
    poc = volume_profile.get("poc_price")
    if poc is not None and mark_price is not None:
        if position.direction.value == "long":
            condition = f"POC {_format_price(poc)} 상향 회복" if mark_price < poc else f"POC {_format_price(poc)} 지지 유지"
            meaning = "롱 유지 시나리오 강화"
        else:
            condition = f"POC {_format_price(poc)} 하향 이탈" if mark_price > poc else f"POC {_format_price(poc)} 저항 유지"
            meaning = "숏 유지 시나리오 강화"
        triggers.append({"condition": condition, "meaning": meaning})
    volume_state = volume_xray.get("volume_state")
    if volume_xray.get("spike_detected"):
        triggers.append({"condition": "거래량 급증 캔들 출현", "meaning": "포지션 방향과 캔들 방향이 같은지 확인"})
    if volume_state:
        triggers.append({"condition": f"거래량 상태 {volume_state}", "meaning": "거래량 변화가 포지션 방향과 정렬되는지 확인"})
    return triggers[:3]


def _liquidation(position: Position) -> dict[str, Any]:
    if position.liquidation_price is None:
        return {"price": None, "warning": "거래소 청산가 미수신"}
    return {"price": position.liquidation_price, "warning": None}


def _plan_item(position: Position, price: float, mark_price: float | None, basis: str, action: str) -> dict[str, Any]:
    return {
        "price": price,
        "basis": basis,
        "distance_pct": _distance_pct(price, mark_price, position.direction.value),
        "action": action,
    }


def _distance_pct(price: float | None, mark_price: float | None, direction: str) -> float | None:
    if price is None or mark_price is None or mark_price <= 0:
        return None
    side = 1 if direction == "long" else -1
    return round(((price - mark_price) / mark_price) * 100 * side, 2)


def _is_favorable_target(price: float, mark_price: float | None, direction: str) -> bool:
    if mark_price is None:
        return True
    return price > mark_price if direction == "long" else price < mark_price


def _previous_extreme(candles: list[dict[str, Any]], direction: str) -> float | None:
    if not candles:
        return None
    recent = candles[-100:]
    if direction == "long":
        values = [item.get("high") for item in recent if item.get("high") is not None]
        return max(values) if values else None
    values = [item.get("low") for item in recent if item.get("low") is not None]
    return min(values) if values else None


def _levels(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict) and item.get("price") is not None]
    return []


def _basis(level: dict[str, Any], fallback: str) -> str:
    label = level.get("label") or fallback
    strength = level.get("strength")
    if strength:
        return f"{label} · strength {strength}"
    return str(label)


def _dedupe_price_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[float] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        price = item.get("price")
        key = round(float(price), 8) if price is not None else None
        if key is None or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _format_price(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.8f}".rstrip("0").rstrip(".")
    return str(value)
