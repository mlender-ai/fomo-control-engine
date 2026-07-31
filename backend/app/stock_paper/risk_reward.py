"""분할 청산 가중 RR 산출 (WO-FCE-PNF-TARGET-01 작업 1).

기존 결함: RR의 보상 분자가 ``take_profit[0]`` — 가장 가까운 목표 하나뿐이었다. 실제 운용은
TP1/TP2/TP3 분할 청산인데 RR은 TP1만으로 계산되므로 **체계적으로 과소평가**된다. 구조 레벨이
촘촘한 종목에서는 첫 목표가 손절폭보다 가까워 RR<1이 항상 발생하고, 그 결과 RR 게이트가
종목을 거르는 게 아니라 목표 산출 방식이 스스로를 거르는 기아 상태가 된다.

이 모듈은 **분자를 정합화**할 뿐 임계(``min_rr``)를 건드리지 않는다(C1). 두 정의를 모두
산출해 병기하므로 어느 정의로 게이트를 통과했는지 항상 추적할 수 있다.

크립토 트랙은 이 모듈을 쓰지 않는다(C6) — 표본이 있고 성과가 양호한 경로는 건드리지 않는다.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

# 분할 청산 기본 배분 — TP1에 가장 큰 비중. 파라미터로 덮어쓸 수 있으며 hard bound는
# app/analyst/param_registry.py 의 stock_tp_weight_* 정의가 강제한다.
TP_WEIGHTS_DEFAULT: tuple[float, ...] = (0.40, 0.35, 0.25)


def normalize_weights(weights: Sequence[float] | None, count: int) -> list[float]:
    """사용 가능한 목표 수에 맞춰 배분 비율을 정규화한다.

    목표가 2개뿐인데 3개 비율을 그대로 쓰면 보상이 과소 집계된다(합이 1 미만). 실제
    시나리오 빌더는 목표를 최대 2개까지만 만들므로 정규화가 정상 경로다.
    """
    if count <= 0:
        return []
    source = list(weights) if weights else list(TP_WEIGHTS_DEFAULT)
    usable = [float(item) for item in source[:count] if _finite(item) and float(item) > 0]
    if not usable:
        return [1.0 / count] * count
    if len(usable) < count:
        usable.extend([usable[-1]] * (count - len(usable)))
    total = sum(usable)
    if total <= 0:
        return [1.0 / count] * count
    return [item / total for item in usable]


def weighted_reward(targets: Sequence[Any], weights: Sequence[float] | None = None) -> float | None:
    """분할 청산 배분으로 가중 평균 보상(%)을 산출한다."""
    distances = [value for value in (_distance(target) for target in targets or []) if value is not None and value > 0]
    if not distances:
        return None
    ratios = normalize_weights(weights, len(distances))
    return sum(distance * ratio for distance, ratio in zip(distances, ratios))


def risk_reward(
    targets: Sequence[Any],
    risk_pct: float | None,
    *,
    weights: Sequence[float] | None = None,
) -> dict[str, Any]:
    """두 RR 정의를 함께 산출한다.

    반환:
      - ``rr_weighted``: 분할 청산 가중 기대값 기반 (게이트가 사용하는 정본)
      - ``rr_first_target``: 기존 TP1 단독 기준 (병기·추적용, 회귀 비교의 기준선)
      - ``target_count`` / ``weights_used``: 어떻게 계산됐는지의 감사 흔적
    """
    risk = abs(float(risk_pct)) if _finite(risk_pct) else None
    distances = [value for value in (_distance(target) for target in targets or []) if value is not None and value > 0]
    first = distances[0] if distances else None
    reward = weighted_reward(targets, weights)
    ratios = normalize_weights(weights, len(distances))
    return {
        "rr_weighted": (reward / risk) if risk and reward is not None and risk > 0 else None,
        "rr_first_target": (first / risk) if risk and first is not None and risk > 0 else None,
        "risk_pct": risk,
        "reward_weighted_pct": reward,
        "reward_first_target_pct": first,
        "target_count": len(distances),
        "weights_used": [round(item, 6) for item in ratios],
        "definition": "weighted_partial_exit",
    }


def _distance(target: Any) -> float | None:
    if isinstance(target, dict):
        return _num(target.get("distance_pct"))
    return _num(target)


def _num(value: Any) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _finite(value: Any) -> bool:
    return _num(value) is not None
