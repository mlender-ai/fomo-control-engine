"""WO-FCE-ASSET-CLASS-01 3-1 — 엔진 실행 예산 (심볼 수 대비 한계).

## 왜 이 모듈이 생겼나

`DISCOVERY-UNBLOCK-01` 이 유니버스를 3 → 15종으로 늘렸고, `paper_engine` 루프가 봉 변경
확인 **전에** 심볼당 약 30초짜리 분석을 무조건 호출했다. 15 × 30초가 90초 주기의 예산
450초를 넘겨 `sync_positions timeout after 450s` 로 죽었다.

**표본을 늘리려는 변경이 표본을 0으로 만들었다.**

`ASSET-CLASS-01` 3-2 는 심볼을 3 → 289 로 다시 20배 늘린다. 예산을 **먼저 수식으로**
세우지 않으면 같은 사고가 규모만 키워 재발한다. 그것이 C1 이고 이 모듈이 그 근거다.

## 두 개의 서로 다른 한계

한계가 하나라고 생각하면 틀린 곳을 조인다.

| 한계 | 무엇이 터지나 | 결정 변수 |
| --- | --- | --- |
| **실행당 예산** | 잡이 `timeout` 으로 죽는다 → 그 틱의 **모든** 심볼이 평가 안 됨 | 실행당 심볼 수 상한 |
| **순회 주기** | 죽지는 않지만 한 봉 안에 전 심볼을 못 돈다 → 일부 심볼이 봉을 건너뜀 | 유니버스 크기 |

`paper_engine_max_symbols_per_run` 이 둘을 **분리한다.** 상한이 있으면 유니버스가 커져도
실행당 비용은 고정이고, 커지는 것은 순회 주기다. 그래서 "심볼 289개는 감당 못 한다"는
결론은 상한이 없을 때만 참이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# 실측 근거. `universe_needing_evaluation` docstring 과 `WorkerHangEvidence §12` 의 사고 분석.
MEASURED_SECONDS_PER_SYMBOL = 30.0

# 4시간봉 한 봉의 길이. 순회 주기는 이 안에 들어와야 심볼이 봉을 건너뛰지 않는다.
BAR_SECONDS_4H = 14_400

# 예산을 얼마나 남겨 둘 것인가. 1.0 이면 여유 0 — 지연 한 번에 터진다.
DEFAULT_SAFETY_MARGIN = 0.6


@dataclass(frozen=True)
class BudgetInputs:
    """예산 계산의 입력. 전부 설정 또는 실측에서 온다 — 여기서 새로 정하지 않는다."""

    interval_seconds: int
    timeout_multiplier: int
    timeout_floor_seconds: int
    timeout_ceiling_seconds: int
    max_symbols_per_run: int
    seconds_per_symbol: float = MEASURED_SECONDS_PER_SYMBOL
    safety_margin: float = DEFAULT_SAFETY_MARGIN

    @property
    def timeout_seconds(self) -> int:
        """`WorkerManager._job_timeout_seconds` 와 같은 식이다 — 재구현이 아니라 복제 검증용."""
        floor = max(10, self.timeout_floor_seconds)
        ceiling = max(floor, self.timeout_ceiling_seconds)
        multiplier = max(2, self.timeout_multiplier)
        return max(floor, min(ceiling, self.interval_seconds * multiplier or floor))


def from_settings(settings: Any, *, seconds_per_symbol: float = MEASURED_SECONDS_PER_SYMBOL) -> BudgetInputs:
    return BudgetInputs(
        interval_seconds=int(settings.worker_sync_positions_interval_seconds),
        timeout_multiplier=int(settings.worker_job_timeout_multiplier),
        timeout_floor_seconds=int(settings.worker_job_timeout_floor_seconds),
        timeout_ceiling_seconds=int(settings.worker_job_timeout_ceiling_seconds),
        max_symbols_per_run=int(getattr(settings, "paper_engine_max_symbols_per_run", 0) or 0),
        seconds_per_symbol=seconds_per_symbol,
    )


def per_run_limit(inputs: BudgetInputs) -> dict[str, Any]:
    """한 실행에서 몇 심볼까지 감당하는가 (3-1 작업 2).

    상한이 0(무제한)이면 유니버스 크기가 곧 실행당 비용이 되고, 그때 터지는 지점이
    `DISCOVERY-UNBLOCK-01` 사고의 재현이다.
    """
    budget = inputs.timeout_seconds
    usable = budget * inputs.safety_margin
    hard_cap = int(budget // inputs.seconds_per_symbol) if inputs.seconds_per_symbol > 0 else 0
    safe_cap = int(usable // inputs.seconds_per_symbol) if inputs.seconds_per_symbol > 0 else 0
    configured = inputs.max_symbols_per_run
    projected = configured * inputs.seconds_per_symbol if configured else None
    return {
        "timeout_seconds": budget,
        "seconds_per_symbol": inputs.seconds_per_symbol,
        "safety_margin": inputs.safety_margin,
        # 예산을 정확히 소진하는 지점. 여기에 맞추면 지연 한 번에 터진다.
        "hard_cap_symbols": hard_cap,
        # 여유를 남긴 권장 상한.
        "safe_cap_symbols": safe_cap,
        "configured_cap": configured or None,
        "projected_run_seconds": projected,
        "configured_within_safe_cap": bool(configured and configured <= safe_cap),
        "unbounded": configured == 0,
    }


def sweep_period(universe_size: int, inputs: BudgetInputs) -> dict[str, Any]:
    """전 유니버스를 한 바퀴 도는 데 걸리는 시간 (3-2 단계 확대의 실제 제약).

    상한이 있으면 유니버스가 커져도 잡은 죽지 않는다 — 대신 순회가 느려진다. 4시간봉에서
    한 봉 안에 못 돌면 그 심볼은 그 봉을 건너뛴다.
    """
    cap = inputs.max_symbols_per_run or max(1, universe_size)
    runs_needed = -(-max(0, universe_size) // max(1, cap))
    seconds = runs_needed * inputs.interval_seconds
    return {
        "universe_size": universe_size,
        "cap_per_run": cap,
        "runs_to_cover_universe": runs_needed,
        "sweep_seconds": seconds,
        "sweep_minutes": round(seconds / 60, 1),
        "bar_seconds": BAR_SECONDS_4H,
        "fits_in_one_bar": seconds <= BAR_SECONDS_4H,
    }


def max_universe_for_one_bar(inputs: BudgetInputs) -> int:
    """한 봉 안에 순회를 끝낼 수 있는 최대 유니버스 크기.

    이것이 3-2 단계 확대의 **상한**이다. 실행당 예산이 아니라 여기서 먼저 걸린다.
    """
    cap = inputs.max_symbols_per_run
    if cap <= 0 or inputs.interval_seconds <= 0:
        return 0
    runs_per_bar = BAR_SECONDS_4H // inputs.interval_seconds
    return int(cap * runs_per_bar)


def budget_report(universe_size: int, inputs: BudgetInputs) -> dict[str, Any]:
    """3-1 수용 기준의 "심볼 수 대비 예산 한계"를 한 페이로드로 낸다."""
    run = per_run_limit(inputs)
    sweep = sweep_period(universe_size, inputs)
    ceiling = max_universe_for_one_bar(inputs)
    return {
        "per_run": run,
        "sweep": sweep,
        "max_universe_for_one_bar": ceiling,
        "verdict": _verdict(universe_size, run, sweep, ceiling),
        "note": ("실행당 예산과 순회 주기는 다른 한계다. 상한이 있으면 유니버스 증가는 잡을 죽이지 않고 순회를 늦춘다 — 조여야 할 곳이 다르다."),
    }


def _verdict(universe_size: int, run: dict[str, Any], sweep: dict[str, Any], ceiling: int) -> str:
    if run["unbounded"]:
        return (
            f"실행당 상한이 없다. 유니버스 {universe_size}종이면 한 실행이 "
            f"{universe_size * run['seconds_per_symbol']:.0f}초를 시도해 예산 {run['timeout_seconds']}초를 "
            "넘긴다 — DISCOVERY-UNBLOCK-01 사고의 형태다."
        )
    if not run["configured_within_safe_cap"]:
        return (
            f"실행당 상한 {run['configured_cap']} 이 안전 상한 {run['safe_cap_symbols']} 을 넘는다. "
            f"예상 {run['projected_run_seconds']:.0f}초 / 예산 {run['timeout_seconds']}초."
        )
    if not sweep["fits_in_one_bar"]:
        return f"실행당 예산은 안전하나 순회가 한 봉을 넘는다 — {sweep['sweep_minutes']}분 > 240분. 이 상한에서 감당 가능한 최대 유니버스는 {ceiling}종이다."
    return (
        f"유니버스 {universe_size}종 감당 가능. 순회 {sweep['sweep_minutes']}분 "
        f"(한 봉 240분 이내) · 실행당 {run['projected_run_seconds']:.0f}초 / 예산 {run['timeout_seconds']}초. "
        f"이 상한의 유니버스 천장은 {ceiling}종."
    )
