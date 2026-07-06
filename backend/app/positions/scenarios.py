from __future__ import annotations

from typing import Any

from app.positions.action_plan import (
    _basis,
    _distance_pct,
    _format_price,
    _is_favorable_target,
)


def build_direction_scenarios(
    support: list[dict[str, Any]],
    resistance: list[dict[str, Any]],
    volume_profile: dict[str, Any],
    volume_xray: dict[str, Any],
    mark_price: float | None,
) -> dict[str, Any]:
    """포지션이 없을 때 롱/숏 양방향의 무효화·익절 시나리오를 만든다.

    액션 플랜과 같은 의미론(레벨 기반 무효화, 유리한 방향의 레벨/매물대 익절)을 쓰되
    방향을 미리 정하지 않는다. 추천이 아니라 트리거 후보의 나열이다.
    """
    return {
        "long": _scenario("long", support, resistance, volume_profile, volume_xray, mark_price),
        "short": _scenario("short", support, resistance, volume_profile, volume_xray, mark_price),
    }


def _scenario(
    direction: str,
    support: list[dict[str, Any]],
    resistance: list[dict[str, Any]],
    volume_profile: dict[str, Any],
    volume_xray: dict[str, Any],
    mark_price: float | None,
) -> dict[str, Any]:
    invalidation_source = support[0] if direction == "long" and support else resistance[0] if direction == "short" and resistance else None
    invalidation = None
    if invalidation_source and invalidation_source.get("price") is not None:
        invalidation = {
            "price": invalidation_source["price"],
            "basis": _basis(invalidation_source, "현행 차트 레벨 기반 무효화 가격"),
            "distance_pct": _distance_pct(invalidation_source["price"], mark_price, direction),
            "action": "이탈 시 손절 검토" if direction == "long" else "돌파 시 손절 검토",
        }

    targets: list[dict[str, Any]] = []
    level_targets = resistance if direction == "long" else support
    target_label = "저항" if direction == "long" else "지지"
    for level in level_targets:
        price = level.get("price")
        if price is None or not _is_favorable_target(price, mark_price, direction):
            continue
        targets.append(
            {
                "price": price,
                "basis": _basis(level, f"현행 차트 레벨 {target_label}"),
                "distance_pct": _distance_pct(price, mark_price, direction),
                "action": "부분 익절 검토",
            }
        )
        if len(targets) >= 2:
            break
    profile_key = "value_area_high" if direction == "long" else "value_area_low"
    profile_price = volume_profile.get(profile_key)
    if len(targets) < 2 and profile_price is not None and _is_favorable_target(profile_price, mark_price, direction):
        targets.append(
            {
                "price": profile_price,
                "basis": "매물대 상단(VAH)" if direction == "long" else "매물대 하단(VAL)",
                "distance_pct": _distance_pct(profile_price, mark_price, direction),
                "action": "부분 익절 검토",
            }
        )

    watch_triggers: list[dict[str, str]] = []
    poc = volume_profile.get("poc_price")
    if poc is not None and mark_price is not None:
        if direction == "long":
            condition = f"최다 거래 가격(POC) {_format_price(poc)} 상향 회복" if mark_price < poc else f"최다 거래 가격(POC) {_format_price(poc)} 지지 유지"
            meaning = "롱 시나리오 강화"
        else:
            condition = f"최다 거래 가격(POC) {_format_price(poc)} 하향 이탈" if mark_price > poc else f"최다 거래 가격(POC) {_format_price(poc)} 저항 유지"
            meaning = "숏 시나리오 강화"
        watch_triggers.append({"condition": condition, "meaning": meaning})
    if volume_xray.get("spike_detected"):
        watch_triggers.append(
            {
                "condition": "거래량 급증 캔들 출현",
                "meaning": "캔들 방향과 시나리오 방향이 같은지 확인",
            }
        )

    return {
        "invalidation": invalidation,
        "take_profit": targets,
        "watch_triggers": watch_triggers[:3],
    }
