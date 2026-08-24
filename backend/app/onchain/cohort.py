"""WO-FCE-WHALE-FOLLOW-01 5-1 — 추적 코호트 고정.

## 왜 이 모듈이 생겼나

`SMART-MONEY-01` 4-1 이 승격자 0명의 원인을 확정했다. 축적 속도(7.6건/일)는 충분했고,
**표본이 쌓인 지갑이 추적군에서 빠지는 것**이 원인이었다. 기전은 한 줄이다 —
`leaderboard.py` 의 발견 실행이 매번 새로 선발하고, 새 선발에 없는 discovery 지갑을
`active=False` 로 내린다. 그리고 `collector.py:12` 는 `active=True` 지갑만 수집한다.
비활성 지갑은 관측이 **멈춘다**. 표본은 남지만 자라지 않는다.

2026-08-25 실측이 그 결과를 보여준다:

| 지갑 | 채점 N | 활성 |
| --- | --- | --- |
| `0x1ee7a7…6bedf5` | 39 | 아니오 |
| `0x10f1d8…43202f` | 37 | 아니오 |
| `0x212abc…13a8c6` | 34 | 아니오 |
| 현재 활성 20지갑 최대 | 11 | 예 |

N>=30 인 지갑 3개가 **전부** 추적군 밖이다. 선발 기준이 월간 PnL·ROI 기반이므로
리더보드 순위가 흔들릴 때마다 코호트가 회전한다. 표본은 완주하지 못한다.

## 무엇을 바꾸는가

1. **유지가 선발보다 앞선다.** 표본을 완주하지 못한 지갑은 유지한다.
2. **해제 사유는 성과가 아니다.** `RELEASE_REASONS` 만 허용한다 — 활동 중단, 완주,
   계정 소멸, 사용자 지정. 손익·ROI·승률은 해제 사유가 아니다(C4).
3. **선발 점수에서 성과 항을 뺀다.** 계좌 규모·활동량·포지션 규모만 쓴다.
   `assert_no_performance_inputs()` 가 이것을 실행 시점에 증명한다.
4. **표본 보유 지갑을 복귀시킨다.** 이미 N 을 쌓아둔 지갑이 우선 편성된다.

## 무엇을 바꾸지 않는가

승격 기준(28일·N>=30·CI 하한 55%)은 건드리지 않는다(C5). 이 모듈은 표본이 **완주할 수
있게** 만들 뿐, 통과 여부를 바꾸지 않는다. 실제로 2026-08-25 기준 복귀 대상 3지갑의
CI 하한은 35.9 · 48.6 · 32.4 로 전부 미달이다 — 고정해도 지금은 승격자가 0명이다.
그 사실이 이 모듈의 목적을 부정하지 않는다. 회전이 계속되면 **왜 0명인지조차** 알 수 없다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

# 표본 완주 기준. `sample_viability.TARGET_SAMPLES` · 승격 기준 N>=30 과 같은 수다.
COHORT_SAMPLE_TARGET = 30
# 새로 편성된 지갑에게 주는 최소 축적 기간. 이것이 없으면 N=0 지갑이 다음 실행에 밀려
# 나가고 회전이 그대로 재현된다.
COHORT_MIN_TENURE_DAYS = 7
# 이 기간 체결이 없으면 '활동 중단'으로 본다. 성과가 아니라 활동의 부재다.
COHORT_DORMANT_DAYS = 10

# 해제 사유 화이트리스트. 여기 없는 사유로는 코호트에서 내리지 않는다(C4).
# `slot_pressure` 는 계좌 한도(폴링 예산) 때문이며 성과 사유가 아니다.
RELEASE_REASONS: tuple[str, ...] = ("dormant", "sample_complete", "vanished", "manual", "slot_pressure")

# 자리가 모자랄 때의 우선순위 계층. 위가 앞선다.
PRIORITY_TIERS: tuple[str, ...] = ("manual_source", "sample_incomplete(표본 보유)", "tenure_floor(표본 0)")
# 유지 사유. 조회 가능해야 한다(C10).
RETAIN_REASONS: tuple[str, ...] = ("tenure_floor", "sample_incomplete", "manual_source")

# 선발·유지 입력에 들어오면 C4 위반인 키. 성과 지표를 다른 성과 지표로 갈아끼우는 것을
# 막기 위해 이름을 박아둔다.
FORBIDDEN_PERFORMANCE_INPUTS = frozenset(
    {
        "month_pnl_usd",
        "month_roi",
        "week_pnl_usd",
        "week_roi",
        "all_time_pnl_usd",
        "all_time_roi",
        "quality_score",
        "win_rate_pct",
        "win_1r_pct",
        "closed_pnl_usd",
        "cumulative_return_r",
        "average_return_r",
        "profit_factor_r",
    }
)

# 비성과 선발 입력. 규모(계좌·포지션)와 활동량(거래대금)이다.
NON_PERFORMANCE_INPUTS = ("account_value_usd", "month_volume_usd", "focus_notional_usd")


@dataclass(frozen=True)
class RetentionDecision:
    """지갑 한 개의 유지·해제 판정. 사유가 항상 붙는다 — 침묵 금지(C10)."""

    address: str
    keep: bool
    reason: str
    detail: str
    sample_size: int
    tenure_days: int
    idle_days: int | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "keep": self.keep,
            "reason": self.reason,
            "detail": self.detail,
            "sample_size": self.sample_size,
            "tenure_days": self.tenure_days,
            "idle_days": self.idle_days,
            "basis": "비성과 사유만 사용 — 손익·ROI·승률은 해제 근거가 아니다(C4)",
        }


def _days_between(later: datetime, earlier: datetime | None) -> int | None:
    if earlier is None:
        return None
    return max(0, int((later - earlier).total_seconds() // 86400))


def retention_decision(
    *,
    address: str,
    source: str,
    sample_size: int,
    added_at: datetime | None,
    last_fill_at: datetime | None,
    now: datetime,
    on_leaderboard: bool = True,
    sample_target: int = COHORT_SAMPLE_TARGET,
    min_tenure_days: int = COHORT_MIN_TENURE_DAYS,
    dormant_days: int = COHORT_DORMANT_DAYS,
) -> RetentionDecision:
    """한 지갑을 코호트에 유지할지 판정한다. 성과는 입력이 아니다.

    판정 순서가 의미를 갖는다. 수동 지정은 발견 로직이 건드리지 않고, 계정 소멸과 활동
    중단이 그다음이며, 최소 기간은 신규 지갑을 보호한다. 표본 완주는 **마지막**이다 —
    완주하지 못한 지갑을 내리지 않는 것이 이 WO 의 목적이기 때문이다.
    """
    tenure_days = _days_between(now, added_at) or 0
    idle_days = _days_between(now, last_fill_at)

    if source != "discovery":
        return RetentionDecision(address, True, "manual_source", "사용자 지정 지갑 — 발견 로직이 해제하지 않는다", sample_size, tenure_days, idle_days)
    if not on_leaderboard and sample_size == 0 and (idle_days is None or idle_days >= dormant_days):
        return RetentionDecision(address, False, "vanished", "리더보드에서 사라지고 체결도 없다 — 계정 소멸로 본다", sample_size, tenure_days, idle_days)
    if idle_days is not None and idle_days >= dormant_days:
        return RetentionDecision(address, False, "dormant", f"{idle_days}일간 체결 없음 — 활동 중단", sample_size, tenure_days, idle_days)
    if tenure_days < min_tenure_days:
        return RetentionDecision(
            address, True, "tenure_floor", f"편성 {tenure_days}일 — 최소 {min_tenure_days}일까지 축적 기회를 준다", sample_size, tenure_days, idle_days
        )
    if sample_size < sample_target:
        return RetentionDecision(address, True, "sample_incomplete", f"표본 {sample_size}/{sample_target} — 완주까지 유지", sample_size, tenure_days, idle_days)
    return RetentionDecision(
        address, False, "sample_complete", f"표본 {sample_size}/{sample_target} 완주 — 심사 대상으로 확정됐다", sample_size, tenure_days, idle_days
    )


def _priority_tier(decision: RetentionDecision) -> int:
    """자리 경합 순위. 사용자 지정 > 표본 보유 > 표본 0 신규."""
    if decision.reason == "manual_source":
        return 0
    if decision.sample_size > 0:
        return 1
    return 2


def focus_notional(candidate: dict[str, Any]) -> float:
    return sum(abs(float(position.get("size_usd") or 0.0)) for position in candidate.get("focus_positions") or [])


def non_performance_score(candidate: dict[str, Any]) -> float:
    """규모·활동량만으로 만든 선발 점수. PnL·ROI 항이 없다(C4).

    현행 `quality_score` 는 `log10(pnl)*30 + min(roi,1)*100 + log10(account)*8` 로 두 항이
    성과다. 그것을 빼면 남는 것은 '큰 계좌가 크게 자주 움직이는가'다. 그것은 성적이 아니라
    관측 가치의 대리다 — 작게 움직이는 지갑은 표본을 만들지 못한다.
    """
    account = math.log10(max(1.0, abs(float(candidate.get("account_value_usd") or 0.0)))) * 10.0
    volume = math.log10(max(1.0, abs(float(candidate.get("month_volume_usd") or 0.0)))) * 10.0
    notional = math.log10(max(1.0, focus_notional(candidate))) * 5.0
    return round(account + volume + notional, 4)


def assert_no_performance_inputs(inputs: Any) -> None:
    """C4 증명. 선발·유지 입력에 성과 키가 섞이면 실행 시점에 터진다."""
    if isinstance(inputs, dict):
        keys = set(inputs)
    else:
        keys = set(inputs or ())
    offending = sorted(keys & FORBIDDEN_PERFORMANCE_INPUTS)
    if offending:
        raise ValueError(f"코호트 선발·유지 입력에 성과 지표가 있다(C4 위반): {', '.join(offending)}")


def non_performance_criteria(settings: Any) -> dict[str, float | None]:
    """비성과 자격 요건. PnL·ROI 임계를 `None`(무임계)으로 둔다.

    계좌 규모·거래대금·회전율은 남긴다. 규모는 성과가 아니고, 거래대금과 회전율은 활동
    빈도의 지표다 — 5-1 항목 4가 허용한 축이다.
    """
    criteria: dict[str, float | None] = {
        "min_account_usd": float(settings.hyperliquid_whale_discovery_min_account_usd),
        "min_month_pnl_usd": None,
        "min_month_roi": None,
        "min_month_volume_usd": float(settings.hyperliquid_whale_discovery_min_month_volume_usd),
        "max_turnover": float(settings.hyperliquid_whale_discovery_max_turnover),
    }
    return criteria


def reinstatement_plan(
    sample_sizes: dict[str, int],
    *,
    active_addresses: set[str],
    slots: int,
    sample_target: int = COHORT_SAMPLE_TARGET,
) -> list[dict[str, Any]]:
    """표본을 보유했는데 추적군에서 빠진 지갑의 복귀 계획. N 큰 순이다.

    복귀 순서를 N 으로 정하는 것은 성과가 아니다 — 완주에 가까운 순서다. 어느 지갑이
    이겼는지는 보지 않는다.
    """
    candidates = [
        {"address": address, "sample_size": int(size), "remaining": max(0, sample_target - int(size))}
        for address, size in sample_sizes.items()
        if int(size) > 0 and address.lower() not in {item.lower() for item in active_addresses}
    ]
    candidates.sort(key=lambda item: (-int(item["sample_size"]), str(item["address"])))
    plan = candidates[: max(0, slots)]
    for rank, item in enumerate(plan, start=1):
        item["reinstatement_rank"] = rank
        item["reason"] = "existing_sample"
        item["basis"] = f"표본 {item['sample_size']}건 보유 — 회전으로 이탈했다"
    return plan


def cohort_plan(
    wallets: list[Any],
    *,
    sample_sizes: dict[str, int],
    leaderboard_addresses: set[str],
    now: datetime,
    max_wallets: int,
    sample_target: int = COHORT_SAMPLE_TARGET,
    min_tenure_days: int = COHORT_MIN_TENURE_DAYS,
    dormant_days: int = COHORT_DORMANT_DAYS,
) -> dict[str, Any]:
    """추적군 편성 계획. 우선순위: 수동 > 유지 > 복귀 > 신규 선발.

    돌려주는 것은 계획이고 쓰기는 호출부가 한다 — 테스트가 DB 없이 판정을 검사할 수 있게.
    """
    sizes = {str(address).lower(): int(size) for address, size in sample_sizes.items()}
    decisions: list[RetentionDecision] = []
    for wallet in wallets:
        address = str(getattr(wallet, "address", "")).lower()
        if not address or not bool(getattr(wallet, "active", False)):
            continue
        decisions.append(
            retention_decision(
                address=address,
                source=str(getattr(wallet, "source", "discovery") or "discovery"),
                sample_size=sizes.get(address, 0),
                added_at=getattr(wallet, "added_at", None),
                last_fill_at=getattr(wallet, "last_fill_at", None),
                now=now,
                on_leaderboard=address in {item.lower() for item in leaderboard_addresses},
                sample_target=sample_target,
                min_tenure_days=min_tenure_days,
                dormant_days=dormant_days,
            )
        )
    keepers = [decision for decision in decisions if decision.keep]
    released = [decision for decision in decisions if not decision.keep]

    # 자리가 모자랄 때의 순위. 표본을 가진 지갑이 표본 0 신규보다 앞선다.
    #
    # 실측 2026-08-25 가 이 순위를 요구했다. 유지 규칙만으로는 현재 활성 19지갑 중 13개가
    # `tenure_floor`(편성 7일 미만·N=0)로 자리를 차지해 복귀 슬롯이 1개만 남았다. N=39·37·34
    # 세 지갑 중 하나만 돌아온다. 그러면 코호트를 고정해도 표본은 완주하지 못한다.
    #
    # 순위 축은 성과가 아니다 — **완주까지의 거리**다. 누가 이겼는지는 보지 않는다(C4).
    # 계좌 한도(20)를 올려 해결할 수는 없다: 폴링 가중치가 이미 880/1200 이다(C9).
    keepers.sort(key=lambda decision: (_priority_tier(decision), -decision.sample_size, decision.tenure_days * -1))
    retained = keepers[: max(0, int(max_wallets))]
    crowded_out = keepers[max(0, int(max_wallets)) :]
    for decision in crowded_out:
        released.append(
            RetentionDecision(
                decision.address,
                False,
                "slot_pressure",
                f"자리 부족 — 표본 {decision.sample_size}건으로 상위 {max_wallets}위 밖. 성과가 아니라 완주 거리 순위다",
                decision.sample_size,
                decision.tenure_days,
                decision.idle_days,
            )
        )

    retained_addresses = {decision.address for decision in retained}
    free_slots = max(0, int(max_wallets) - len(retained))
    reinstated = reinstatement_plan(sizes, active_addresses=retained_addresses, slots=free_slots, sample_target=sample_target)

    # 복귀 대상이 남았는데 자리가 없으면, `tenure_floor`(표본 0) 유지분과 맞바꾼다.
    pending = reinstatement_plan(sizes, active_addresses=retained_addresses, slots=len(sizes), sample_target=sample_target)
    pending = [item for item in pending if item["address"] not in {row["address"] for row in reinstated}]
    swappable = [decision for decision in retained if decision.reason == "tenure_floor" and decision.sample_size == 0]
    swappable.sort(key=lambda decision: decision.tenure_days)
    for item in pending:
        if not swappable:
            break
        victim = swappable.pop(0)
        retained = [decision for decision in retained if decision.address != victim.address]
        released.append(
            RetentionDecision(
                victim.address,
                False,
                "slot_pressure",
                f"표본 0 · 편성 {victim.tenure_days}일 — 표본 {item['sample_size']}건 보유 지갑에 자리를 넘긴다",
                0,
                victim.tenure_days,
                victim.idle_days,
            )
        )
        reinstated.append(item)

    remaining_slots = max(0, int(max_wallets) - len(retained) - len(reinstated))
    return {
        "retained": [decision.as_payload() for decision in retained],
        "released": [decision.as_payload() for decision in released],
        "reinstated": reinstated,
        "retained_count": len(retained),
        "released_count": len(released),
        "reinstated_count": len(reinstated),
        "discovery_slots": remaining_slots,
        "sample_target": sample_target,
        "min_tenure_days": min_tenure_days,
        "dormant_days": dormant_days,
        "release_reasons": list(RELEASE_REASONS),
        "priority": list(PRIORITY_TIERS),
        "policy": "유지가 선발보다 앞선다 · 순위는 완주 거리(비성과) · 해제 사유는 비성과만(C4) · 승격 기준 무변경(C5)",
    }


def scored_by(score: Callable[[dict[str, Any]], float] | None) -> Callable[[dict[str, Any]], float]:
    """정렬 키 주입점. 기본은 현행 `quality_score` 유지 — 옵트인 전 동작이 바뀌지 않는다."""
    if score is not None:
        return score
    return lambda candidate: float(candidate.get("quality_score") or 0.0)
