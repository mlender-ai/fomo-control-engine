"""PNF 측정 목표를 기존 구조 레벨 목표에 조건부로 편입한다 (작업 3).

원칙: **PNF가 항상 이기게 하지 않는다.** PNF 목표가 유효하고 기존 목표보다 멀 때만 상위
TP로 추가하고, 그렇지 않으면 기존 목표를 그대로 유지한다. 어느 목표를 썼는지는
``target_source``로 항상 기록해 추적 가능하게 한다.

PNF가 채택되지 않은 경우에도 산출값 자체는 A/B 비교를 위해 보존한다(작업 4).
"""

from __future__ import annotations

from typing import Any

from app.positions.action_plan import _distance_pct

TARGET_SOURCE_STRUCTURE = "structure_levels"
TARGET_SOURCE_WITH_PNF = "structure_levels+pnf_measured_objective"


def merge_pnf_target(
    targets: list[dict[str, Any]],
    objective: Any,
    mark_price: float | None,
    *,
    direction: str = "long",
) -> tuple[list[dict[str, Any]], str, dict[str, Any] | None]:
    """구조 레벨 목표에 PNF 측정 목표를 조건부 편입한다.

    반환: ``(목표 리스트, target_source, pnf_payload)``

    편입 조건 — 전부 만족해야 채택:
      1. PNF 목표가 산출됨(축적 구간 명확·열 수 충족, C5)
      2. 현재가 기준 유리한 방향
      3. 기존 최원거리 목표보다 **멀다** (가까우면 기존이 이미 더 보수적이므로 유지)
    """
    existing = list(targets or [])
    payload = objective.payload() if objective is not None and hasattr(objective, "payload") else None
    if payload is None:
        return existing, TARGET_SOURCE_STRUCTURE, None

    price = payload.get("target_price")
    distance = _distance_pct(price, mark_price, direction)
    payload = {**payload, "distance_pct": distance}
    if distance is None or distance <= 0:
        payload["adopted"] = False
        payload["skip_reason"] = "not_favorable"
        return existing, TARGET_SOURCE_STRUCTURE, payload

    farthest = max(
        (value for value in (_num(item.get("distance_pct")) for item in existing) if value is not None),
        default=None,
    )
    if farthest is not None and distance <= farthest:
        # 기존 구조 목표가 이미 더 멀다 — PNF를 억지로 끼워 넣지 않는다.
        payload["adopted"] = False
        payload["skip_reason"] = "structure_target_farther"
        return existing, TARGET_SOURCE_STRUCTURE, payload

    payload["adopted"] = True
    merged = existing + [
        {
            "price": price,
            "basis": payload.get("basis") or "PNF 수평 카운트",
            "distance_pct": distance,
            "action": "부분 익절 검토",
            "source": "pnf_measured_objective",
            "confidence": payload.get("confidence"),
        }
    ]
    return merged, TARGET_SOURCE_WITH_PNF, payload


def _num(value: Any) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None
