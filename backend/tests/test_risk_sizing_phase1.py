"""WO-FCE-RISK-SIZING-01 Phase 1 — 리스크 기준 사이징.

핵심 불변식: **사이즈를 바꿔도 R 은 변하지 않는다.** 수량이 s 배가 되면 손익도
계획 리스크도 같이 s 배가 되므로 R = 손익 / 계획리스크 는 불변이다. 이것이 깨지면
사이즈 변경이 성과 회계에 새어 들어간 것이다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.paper.policy import PaperPolicy, plan_position_size

FIXED = PaperPolicy()
RISK = PaperPolicy(sizing_mode="risk_based", risk_budget_usdt=2.5, max_notional_usdt=600.0, min_notional_usdt=10.0)

# (진입가, 무효화가) — 실측 표본의 스톱 거리 범위(0.81%~5.0%)를 덮는다.
CASES = [
    (100.0, 99.19),  # 0.81%  가장 좁은 스톱
    (100.0, 98.45),  # 1.55%  중앙값
    (100.0, 95.00),  # 5.0%   가장 넓은 스톱
    (0.08185, 0.07886),  # 저가 알트 (BASEDUSDT 실측)
    (100.0, 101.55),  # 숏 (무효화가가 위)
]


def test_default_is_previous_behaviour() -> None:
    """기본값은 고정 명목이어야 한다 — 옵트인이 아니면 회귀다."""
    assert FIXED.sizing_mode == "fixed_notional"
    for entry, inval in CASES:
        sizing = plan_position_size(entry_price=entry, invalidation_price=inval, policy=FIXED)
        assert sizing["mode"] == "fixed_notional"
        assert sizing["notional_usdt"] == pytest.approx(FIXED.margin_usdt * FIXED.leverage)
        assert sizing["quantity"] == pytest.approx(FIXED.margin_usdt * FIXED.leverage / entry)


@pytest.mark.parametrize(("entry", "inval"), CASES)
def test_one_r_equals_the_budget(entry: float, inval: float) -> None:
    """1R 의 금액가치가 스톱 거리와 무관하게 리스크 예산 그 자체여야 한다."""
    sizing = plan_position_size(entry_price=entry, invalidation_price=inval, policy=RISK)
    assert sizing["constraint"] == "none"
    assert sizing["planned_risk_usdt"] == pytest.approx(RISK.risk_budget_usdt)


def test_one_r_is_constant_across_stop_distances() -> None:
    """고정 명목에서는 1R 금액이 스톱 거리에 비례한다. 리스크 기준에서는 상수다."""
    fixed_risks = [plan_position_size(entry_price=e, invalidation_price=i, policy=FIXED)["planned_risk_usdt"] for e, i in CASES]
    risk_risks = [plan_position_size(entry_price=e, invalidation_price=i, policy=RISK)["planned_risk_usdt"] for e, i in CASES]
    assert max(fixed_risks) / min(fixed_risks) > 5.0
    assert max(risk_risks) / min(risk_risks) == pytest.approx(1.0)


@pytest.mark.parametrize(("entry", "inval"), CASES)
def test_r_multiple_is_invariant_to_sizing(entry: float, inval: float) -> None:
    """수용 기준 1 — R 합계가 사이즈 변경 전후로 불변."""
    move = (inval - entry) * 0.7  # 계획 리스크의 70% 만큼 불리하게 움직인 가상 청산
    fixed = plan_position_size(entry_price=entry, invalidation_price=inval, policy=FIXED)
    risk = plan_position_size(entry_price=entry, invalidation_price=inval, policy=RISK)
    r_fixed = (move * fixed["quantity"]) / fixed["planned_risk_usdt"]
    r_risk = (move * risk["quantity"]) / risk["planned_risk_usdt"]
    assert r_risk == pytest.approx(r_fixed)


def test_max_notional_caps_runaway_quantity() -> None:
    """스톱 거리가 극히 작으면 수량이 발산한다. 상한이 그것을 막고 사유를 남긴다."""
    sizing = plan_position_size(entry_price=100.0, invalidation_price=99.99, policy=RISK)
    assert sizing["constraint"] == "max_notional"
    assert sizing["notional_usdt"] == pytest.approx(RISK.max_notional_usdt)
    # 상한에 걸리면 실제 리스크는 예산보다 **작다**. 예산이 아니라 실제를 적어야 한다.
    assert sizing["planned_risk_usdt"] < RISK.risk_budget_usdt


def test_min_notional_records_that_risk_exceeds_budget() -> None:
    """거래소 최소 주문 때문에 리스크가 예산을 넘으면 숨기지 않고 기록한다."""
    sizing = plan_position_size(entry_price=100.0, invalidation_price=60.0, policy=RISK)
    assert sizing["constraint"] == "min_notional"
    assert sizing["planned_risk_usdt"] > RISK.risk_budget_usdt


def test_no_stop_distance_falls_back_instead_of_dividing_by_zero() -> None:
    sizing = plan_position_size(entry_price=100.0, invalidation_price=100.0, policy=RISK)
    assert sizing["mode"] == "fixed_notional"
    assert sizing["constraint"] == "fallback:no_stop_distance"


def test_risk_budget_respects_c7_upper_bound() -> None:
    """C7 — 리스크 확대 금지. 예산은 현행 평균 1R 금액(실측 5.296) 이하여야 한다."""
    payload = json.loads(Path("app/paper/params/crypto-v2.json").read_text(encoding="utf-8"))
    assert payload["sizing_mode"] == "risk_based"
    assert payload["risk_budget_usdt"] <= 5.296


def test_entry_gate_params_are_untouched() -> None:
    """C1 — 사이즈 변경이 진입 게이트로 새어 들어가면 안 된다."""
    assert RISK.min_rr == FIXED.min_rr
    assert RISK.min_evidence == FIXED.min_evidence
    assert RISK.min_checklist_passed == FIXED.min_checklist_passed
    assert RISK.max_holding_bars == FIXED.max_holding_bars


def test_exit_ladder_params_are_untouched() -> None:
    """C2 — 출구 사다리는 Phase 1 실측이 지지했다. 건드리지 않는다."""
    assert RISK.take_profit_atr_k1 == FIXED.take_profit_atr_k1
    assert RISK.take_profit_atr_k2 == FIXED.take_profit_atr_k2
    assert RISK.take_profit_pressure_bars == FIXED.take_profit_pressure_bars
