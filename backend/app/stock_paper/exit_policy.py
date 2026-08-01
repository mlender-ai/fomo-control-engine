"""주식 페이퍼 청산 판단 (WO-FCE-STOCK-EXIT-01 작업 1·3).

**판단과 체결의 분리가 이 모듈의 핵심 계약이다.**

이 모듈은 "청산할 것인가 / 얼마나"만 결정하고 **체결 가격을 절대 정하지 않는다.** 가격은
전적으로 `execution.py`가 관측된 시장 데이터에서 파생한다. 그래서 무효화선 100에서 이탈해도
100에 체결되지 않고, 장외 이탈이면 다음 세션 시가(예: 92)에 체결된다 — 갭 손실은 갭 손실로
기록된다(C1). 이 분리가 없으면 손절이 실제보다 좋아 보이고, 그 성적으로 자동매매를 판단하면
실거래에서 그대로 손실이 된다.

크립토 `paper/policy.py::evaluate_exit`와 **판단 요소는 같지만**(무효화 이탈·TP·보유 상한)
체결 규칙은 공유하지 않는다. 주식은 세션·갭·상하한가·거래세·유동성이 근본적으로 다르다.
크립토 경로는 이 모듈을 임포트하지 않는다(C3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_EXIT_PARAMETERS_PATH = Path(__file__).with_name("params") / "stock-exit-v1.json"

# 우선순위: 위쪽이 이긴다. 강제 청산은 어떤 이익 목표보다 앞선다.
TRIGGER_PRIORITY = (
    "forced_liquidation",
    "invalidation_breach",
    "take_profit",
    "time_stop",
)


@dataclass(frozen=True)
class TakeProfitStep:
    level: int
    fraction: float


@dataclass(frozen=True)
class StockExitParameters:
    version: str
    invalidation_basis: str
    max_holding_days: int
    tp_ladder: tuple[TakeProfitStep, ...]
    breakeven_stop_after_tp1: bool
    forced_exit_warnings: tuple[str, ...]
    max_carryover_days: int


@dataclass(frozen=True)
class StockExitDecision:
    """청산 판단. ``fraction``은 잔여 수량 대비 비율이며 가격은 포함하지 않는다."""

    action: str  # "hold" | "exit"
    trigger: str = "none"
    fraction: float = 0.0
    level: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def should_exit(self) -> bool:
        return self.action == "exit"


def load_exit_parameters(path: Path = DEFAULT_EXIT_PARAMETERS_PATH) -> StockExitParameters:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ladder = tuple(TakeProfitStep(level=int(item["level"]), fraction=float(item["fraction"])) for item in payload.get("tp_ladder", []))
    parameters = StockExitParameters(
        version=str(payload["version"]),
        invalidation_basis=str(payload.get("invalidation_basis", "close")),
        max_holding_days=int(payload["max_holding_days"]),
        tp_ladder=ladder,
        breakeven_stop_after_tp1=bool(payload.get("breakeven_stop_after_tp1", True)),
        forced_exit_warnings=tuple(str(item) for item in payload.get("forced_exit_warnings", [])),
        max_carryover_days=int(payload.get("max_carryover_days", 3)),
    )
    if parameters.invalidation_basis != "close":
        # 종가 기준만 허용한다. 장중 터치로 판정하면 꼬리에 스쳐도 손절되어 실제보다 나빠지고,
        # 무엇보다 확정되지 않은 값으로 판단하게 된다.
        raise ValueError("stock exit invalidation basis must be close")
    if parameters.max_holding_days <= 0:
        raise ValueError("stock exit max holding days must be positive")
    if not parameters.tp_ladder:
        raise ValueError("stock exit take-profit ladder must not be empty")
    total = sum(step.fraction for step in parameters.tp_ladder)
    if not 0.99 <= total <= 1.01:
        raise ValueError("stock exit take-profit ladder fractions must sum to 1")
    return parameters


def evaluate_stock_exit(
    *,
    plan: dict[str, Any],
    close_price: float | None,
    average_price: float | None,
    holding_days: int,
    warnings: tuple[str, ...] = (),
    realized_levels: tuple[int, ...] = (),
    parameters: StockExitParameters,
) -> StockExitDecision:
    """보유 포지션의 청산 여부를 판정한다. 가격은 정하지 않는다(체결은 execution.py 소관).

    ``plan``은 진입 시점에 고정한 ``{invalidation_price, targets: [{level, price}]}``이다.
    진입 근거로 삼은 선을 그대로 청산 기준으로 쓰기 위해 진입 시 저장한 값을 사용한다.
    """
    forced = _forced_reason(warnings, parameters.forced_exit_warnings)
    if forced is not None:
        # 빠져나올 수 없어도 시도는 한다. 미체결이면 그 사유가 원장에 남는다(정직성).
        return StockExitDecision("exit", "forced_liquidation", 1.0, detail={"warning": forced})

    if close_price is None:
        return StockExitDecision("hold", "none", 0.0, detail={"reason": "mark_missing"})

    stop_price = _effective_stop(plan, average_price, realized_levels, parameters)
    if stop_price is not None and close_price <= stop_price:
        trigger_detail = {
            "stop_price": stop_price,
            "close_price": close_price,
            "basis": parameters.invalidation_basis,
            "breakeven": bool(realized_levels) and parameters.breakeven_stop_after_tp1,
        }
        return StockExitDecision("exit", "invalidation_breach", 1.0, detail=trigger_detail)

    step = _next_take_profit(plan, close_price, realized_levels, parameters)
    if step is not None:
        remaining_fraction = _remaining_fraction(realized_levels, parameters)
        fraction = 1.0 if _is_final_level(step.level, parameters) else min(1.0, step.fraction / remaining_fraction if remaining_fraction > 0 else 1.0)
        return StockExitDecision(
            "exit",
            "take_profit",
            fraction,
            level=step.level,
            detail={"target_price": _target_price(plan, step.level), "close_price": close_price},
        )

    if holding_days >= parameters.max_holding_days:
        # 8일 방치 재발을 막는 안전판. 정체 포지션이 자본을 묶는 것을 시간으로 끊는다.
        return StockExitDecision(
            "exit",
            "time_stop",
            1.0,
            detail={"holding_days": holding_days, "max_holding_days": parameters.max_holding_days},
        )

    return StockExitDecision("hold", "none", 0.0)


def _forced_reason(warnings: tuple[str, ...], forced: tuple[str, ...]) -> str | None:
    for warning in warnings:
        text = str(warning)
        for needle in forced:
            if needle and needle in text:
                return text
    return None


def _effective_stop(
    plan: dict[str, Any],
    average_price: float | None,
    realized_levels: tuple[int, ...],
    parameters: StockExitParameters,
) -> float | None:
    invalidation = _num(plan.get("invalidation_price"))
    if realized_levels and parameters.breakeven_stop_after_tp1 and average_price is not None:
        # TP1 실현 후 잔여분은 최소 본전을 지킨다. 무효화선이 이미 본전 위면 그대로 둔다.
        return max(invalidation, average_price) if invalidation is not None else average_price
    return invalidation


def _next_take_profit(
    plan: dict[str, Any],
    close_price: float,
    realized_levels: tuple[int, ...],
    parameters: StockExitParameters,
) -> TakeProfitStep | None:
    for step in parameters.tp_ladder:
        if step.level in realized_levels:
            continue
        target = _target_price(plan, step.level)
        if target is None:
            continue
        if close_price >= target:
            return step
        # 사다리는 순서대로 오른다 — 아래 단계가 미달이면 위 단계도 보지 않는다.
        break
    return None


def _target_price(plan: dict[str, Any], level: int) -> float | None:
    for item in plan.get("targets") or []:
        if isinstance(item, dict) and int(item.get("level") or 0) == level:
            return _num(item.get("price"))
    return None


def _remaining_fraction(realized_levels: tuple[int, ...], parameters: StockExitParameters) -> float:
    return sum(step.fraction for step in parameters.tp_ladder if step.level not in realized_levels)


def _is_final_level(level: int, parameters: StockExitParameters) -> bool:
    return level >= max(step.level for step in parameters.tp_ladder)


def _num(value: Any) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None
