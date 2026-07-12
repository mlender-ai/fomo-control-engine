from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


ChangeDirection = Literal["tighten", "loosen", "neutral"]


@dataclass(frozen=True)
class ParamDefinition:
    name: str
    tighten_when: Literal["increase", "decrease"]
    hard_min: float
    hard_max: float
    min_dwell_days: int = 14

    def classify(self, old_value: Any, new_value: Any) -> ChangeDirection:
        old_number = _to_number(old_value)
        new_number = _to_number(new_value)
        if old_number is None or new_number is None or old_number == new_number:
            return "neutral"
        increased = new_number > old_number
        if self.tighten_when == "increase":
            return "tighten" if increased else "loosen"
        return "tighten" if not increased else "loosen"

    def validate(self, value: Any) -> tuple[bool, str | None]:
        number = _to_number(value)
        if number is None:
            return False, "numeric_value_required"
        if number < self.hard_min:
            return False, f"below_hard_min:{self.hard_min:g}"
        if number > self.hard_max:
            return False, f"above_hard_max:{self.hard_max:g}"
        return True, None


PARAM_REGISTRY: dict[str, ParamDefinition] = {
    "min_invalidation_level_score": ParamDefinition(
        name="min_invalidation_level_score",
        tighten_when="increase",
        hard_min=30,
        hard_max=90,
    ),
    "wyckoff_event_min_confidence": ParamDefinition(
        name="wyckoff_event_min_confidence",
        tighten_when="increase",
        hard_min=50,
        hard_max=95,
    ),
    "harmonic_min_confidence": ParamDefinition(
        name="harmonic_min_confidence",
        tighten_when="increase",
        hard_min=50,
        hard_max=95,
    ),
    "alert_trigger_near_pct": ParamDefinition(
        name="alert_trigger_near_pct",
        tighten_when="decrease",
        hard_min=0.25,
        hard_max=5.0,
    ),
    "harmonic_ratio_tolerance_multiplier": ParamDefinition(
        name="harmonic_ratio_tolerance_multiplier",
        tighten_when="decrease",
        hard_min=0.5,
        hard_max=1.5,
    ),
    # WO-53: 방향 히스테리시스. flip 문턱↑·persist↑·ema_span↑ = 전환에 더 둔감(=tighten).
    # 상한은 "진동 흡수 범위를 넘어 추세를 막지 못하도록" 고정 (WO-53 금지: 둔감으로 추세 놓침).
    "directional_flip_margin": ParamDefinition(
        name="directional_flip_margin",
        tighten_when="increase",
        hard_min=0.25,
        hard_max=0.60,
    ),
    "directional_ema_span": ParamDefinition(
        name="directional_ema_span",
        tighten_when="increase",
        hard_min=1.0,
        hard_max=10.0,
    ),
    "directional_flip_persist": ParamDefinition(
        name="directional_flip_persist",
        tighten_when="increase",
        hard_min=1.0,
        hard_max=5.0,
    ),
    "paper_min_evidence": ParamDefinition(
        name="paper_min_evidence",
        tighten_when="increase",
        hard_min=3.0,
        hard_max=8.0,
    ),
    "paper_min_checklist_passed": ParamDefinition(
        name="paper_min_checklist_passed",
        tighten_when="increase",
        hard_min=4.0,
        hard_max=6.0,
    ),
    "paper_min_rr": ParamDefinition(
        name="paper_min_rr",
        tighten_when="increase",
        hard_min=1.0,
        hard_max=4.0,
    ),
}


def get_param_definition(name: str) -> ParamDefinition | None:
    return PARAM_REGISTRY.get(name)


def classify_param_change(parameter: str, old_value: Any, new_value: Any) -> ChangeDirection:
    definition = get_param_definition(parameter)
    if definition is None:
        return "neutral"
    return definition.classify(old_value, new_value)


def validate_param_change(parameter: str, new_value: Any) -> tuple[bool, str | None]:
    definition = get_param_definition(parameter)
    if definition is None:
        return False, "param_not_registered"
    return definition.validate(new_value)


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
