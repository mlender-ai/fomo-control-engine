"""WO-FCE-PNF-TARGET-01 회귀 가드.

PNF는 진입 시그널이 아니라 목표 계산기다(C2). 이 테스트는 룩어헤드 금지(C4),
억지 목표 생성 금지(C5), PNF 무조건 우선 금지, 가중 RR 정합화, 임계 불변(C1)을 강제한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.stock_paper.risk_reward import TP_WEIGHTS_DEFAULT, normalize_weights, risk_reward, weighted_reward
from app.stock_paper.targets import TARGET_SOURCE_STRUCTURE, TARGET_SOURCE_WITH_PNF, merge_pnf_target
from app.structure.pnf import (
    PNF_MIN_COLUMNS,
    PNF_REVERSAL_DEFAULT,
    build_pnf_columns,
    measured_objective,
    resolve_box_size,
)


@dataclass
class Candle:
    high: float
    low: float
    close: float


def _oscillating(cycles: int, low: float = 100.0, high: float = 120.0) -> list[Candle]:
    """지지~저항을 왕복하는 축적 구간 — PNF 열이 여러 개 형성된다."""
    candles: list[Candle] = []
    for _ in range(cycles):
        for price in (low, low + 5, high, high - 5):
            candles.append(Candle(high=price + 1, low=price - 1, close=price))
    return candles


def _tr(low: float = 100.0, high: float = 120.0, atr: float = 4.0, inside: int = 30) -> dict:
    return {
        "support": {"price": low},
        "resistance": {"price": high},
        "atr": atr,
        "candles_inside": inside,
        "start_time": 1,
        "end_time": 2,
    }


# ── 박스 크기 산출 규칙 ────────────────────────────────────────


def test_box_size_is_atr_proportional_within_bounds() -> None:
    box = resolve_box_size(price=100.0, atr=4.0)
    assert box == 2.0  # ATR 4 × 0.5 = 2.0, 하한 0.5·상한 3.0 사이


def test_box_size_clamps_extreme_atr() -> None:
    assert resolve_box_size(price=100.0, atr=1000.0) == 3.0  # 상한 price×3%
    assert resolve_box_size(price=100.0, atr=0.01) == 0.5  # 하한 price×0.5%


def test_box_size_falls_back_without_atr() -> None:
    assert resolve_box_size(price=200.0, atr=None) == 2.0  # price×1%


def test_box_size_rejects_invalid_price() -> None:
    assert resolve_box_size(price=0.0, atr=1.0) is None
    assert resolve_box_size(price=-5.0, atr=1.0) is None


# ── 룩어헤드 금지 (C4) ────────────────────────────────────────


def test_columns_depend_only_on_past_candles() -> None:
    """접두사(prefix)로 만든 열은 전체로 만든 열의 접두사여야 한다.

    미래 봉이 과거 열 구성에 영향을 준다면 이 불변식이 깨진다 — 룩어헤드 감사.
    """
    candles = _oscillating(4)
    full = build_pnf_columns(candles, box_size=2.0)
    prefix = build_pnf_columns(candles[:-4], box_size=2.0)
    # 마지막(미완성) 열은 새 캔들로 확장될 수 있으므로 완결된 열만 비교한다.
    assert full[: len(prefix) - 1] == prefix[:-1]


def test_measured_objective_uses_only_supplied_candles() -> None:
    candles = _oscillating(4)
    first = measured_objective(candles, trading_range=_tr())
    again = measured_objective(list(candles), trading_range=_tr())
    assert first is not None and again is not None
    assert first.target_price == again.target_price  # 결정론적 — 숨은 상태 없음


# ── 억지 목표 생성 금지 (C5) ──────────────────────────────────


def test_returns_none_without_trading_range() -> None:
    assert measured_objective(_oscillating(4), trading_range=None) is None


def test_returns_none_when_columns_below_threshold() -> None:
    # 거의 움직이지 않는 캔들 → 열이 충분히 형성되지 않는다.
    flat = [Candle(high=100.5, low=99.5, close=100.0) for _ in range(40)]
    assert measured_objective(flat, trading_range=_tr()) is None


def test_returns_none_for_invalid_range() -> None:
    bad = {"support": {"price": 120.0}, "resistance": {"price": 100.0}, "atr": 4.0}
    assert measured_objective(_oscillating(4), trading_range=bad) is None


def test_returns_none_for_short_direction() -> None:
    # 주식 페이퍼는 long_only — 숏 목표는 검증 표본이 없으므로 만들지 않는다.
    assert measured_objective(_oscillating(4), trading_range=_tr(), direction="short") is None


def test_min_columns_threshold_is_enforced() -> None:
    candles = _oscillating(4)
    assert measured_objective(candles, trading_range=_tr(), min_columns=99) is None


# ── 측정 목표 산출 ────────────────────────────────────────────


def test_measured_objective_counts_columns_and_projects_target() -> None:
    objective = measured_objective(_oscillating(5), trading_range=_tr())
    assert objective is not None
    assert objective.count_columns >= PNF_MIN_COLUMNS
    assert objective.reversal == PNF_REVERSAL_DEFAULT
    # 목표 = 카운트라인 + 열수 × 박스 × 리버설
    expected = objective.count_line + objective.count_columns * objective.box_size * objective.reversal
    assert objective.target_price == round(expected, 8)
    assert objective.confidence <= 85  # 추정임을 잊지 않는 상한


def test_payload_exposes_audit_fields() -> None:
    objective = measured_objective(_oscillating(5), trading_range=_tr())
    assert objective is not None
    payload = objective.payload()
    for key in ("target_price", "count_columns", "box_size", "reversal", "tr_start", "tr_end", "confidence"):
        assert key in payload


# ── PNF가 항상 이기지 않는다 (작업 3) ─────────────────────────


class _Obj:
    def __init__(self, price: float, confidence: int = 60) -> None:
        self._price = price
        self.confidence = confidence

    def payload(self) -> dict:
        return {"target_price": self._price, "confidence": self.confidence, "basis": "PNF 수평 카운트", "direction": "long"}


def test_pnf_adopted_only_when_farther_than_structure_targets() -> None:
    targets = [{"price": 110.0, "distance_pct": 10.0}]
    merged, source, payload = merge_pnf_target(targets, _Obj(130.0), mark_price=100.0)
    assert source == TARGET_SOURCE_WITH_PNF
    assert len(merged) == 2
    assert payload["adopted"] is True


def test_pnf_skipped_when_structure_target_is_farther() -> None:
    targets = [{"price": 150.0, "distance_pct": 50.0}]
    merged, source, payload = merge_pnf_target(targets, _Obj(120.0), mark_price=100.0)
    assert source == TARGET_SOURCE_STRUCTURE
    assert merged == targets  # 기존 유지 — PNF를 억지로 끼워 넣지 않는다
    assert payload["adopted"] is False
    assert payload["skip_reason"] == "structure_target_farther"


def test_pnf_skipped_when_not_favorable() -> None:
    targets = [{"price": 110.0, "distance_pct": 10.0}]
    _, source, payload = merge_pnf_target(targets, _Obj(90.0), mark_price=100.0)
    assert source == TARGET_SOURCE_STRUCTURE
    assert payload["adopted"] is False
    assert payload["skip_reason"] == "not_favorable"


def test_no_objective_keeps_structure_source() -> None:
    targets = [{"price": 110.0, "distance_pct": 10.0}]
    merged, source, payload = merge_pnf_target(targets, None, mark_price=100.0)
    assert (merged, source, payload) == (targets, TARGET_SOURCE_STRUCTURE, None)


# ── 가중 RR 정합화 (작업 1) ───────────────────────────────────


def test_weights_normalize_over_available_targets() -> None:
    # 목표가 2개면 3개 비율을 그대로 쓰지 않고 정규화한다(보상 과소집계 방지).
    weights = normalize_weights(TP_WEIGHTS_DEFAULT, 2)
    assert len(weights) == 2
    assert abs(sum(weights) - 1.0) < 1e-9


def test_weighted_reward_exceeds_first_target_when_farther_targets_exist() -> None:
    targets = [{"distance_pct": 5.0}, {"distance_pct": 15.0}]
    reward = weighted_reward(targets)
    assert reward is not None
    assert reward > 5.0  # TP1 단독보다 크다 — 과소평가 해소


def test_risk_reward_reports_both_definitions() -> None:
    targets = [{"distance_pct": 5.0}, {"distance_pct": 15.0}]
    payload = risk_reward(targets, risk_pct=-6.0)
    assert payload["rr_first_target"] is not None
    assert payload["rr_weighted"] is not None
    # 병기: 두 정의가 모두 남아 어느 쪽으로 통과했는지 추적 가능
    assert payload["rr_weighted"] > payload["rr_first_target"]
    assert payload["definition"] == "weighted_partial_exit"
    assert payload["target_count"] == 2


def test_single_target_weighted_equals_first_target() -> None:
    payload = risk_reward([{"distance_pct": 9.0}], risk_pct=3.0)
    assert payload["rr_weighted"] == payload["rr_first_target"] == 3.0


def test_rr_is_none_without_risk_or_targets() -> None:
    assert risk_reward([], risk_pct=5.0)["rr_weighted"] is None
    assert risk_reward([{"distance_pct": 5.0}], risk_pct=None)["rr_weighted"] is None
    assert risk_reward([{"distance_pct": 5.0}], risk_pct=0.0)["rr_weighted"] is None


def test_negative_targets_are_ignored() -> None:
    # 불리한(음수) 목표는 보상으로 세지 않는다.
    payload = risk_reward([{"distance_pct": -3.0}, {"distance_pct": 10.0}], risk_pct=5.0)
    assert payload["target_count"] == 1
    assert payload["reward_weighted_pct"] == 10.0


# ── 절대 제약 가드 (C1 임계 불변 · C6 크립토 무변경) ─────────


def test_min_rr_threshold_unchanged() -> None:
    """C1: 이 WO는 분자를 정합화할 뿐 임계를 낮추지 않는다."""
    from app.stock_paper.parameters import load_stock_parameters

    assert load_stock_parameters().min_rr == 1.5


def test_entry_gate_thresholds_unchanged() -> None:
    """C1: 진입 게이트 임계 전반 diff 0."""
    from app.stock_paper.parameters import load_stock_parameters

    parameters = load_stock_parameters()
    assert parameters.min_evidence == 4
    assert parameters.min_checklist_passed == 5
    assert parameters.min_entry_score == 75


def test_crypto_paper_does_not_import_stock_rr_or_pnf_targets() -> None:
    """C6: 크립토 경로는 가중 RR·PNF 배선을 타지 않는다."""
    from pathlib import Path

    root = Path(__file__).parents[1] / "app" / "paper"
    source = "\n".join(path.read_text() for path in root.glob("*.py"))
    assert "stock_paper.risk_reward" not in source
    assert "stock_paper.targets" not in source
    assert "structure.pnf" not in source


def test_pnf_signature_registers_only_when_adopted() -> None:
    """C3: 채택된 PNF 목표만 candidate 시그니처가 된다(관측되지 않은 목표는 등록 금지)."""
    from app.backtest.signatures import signatures_from_analysis

    adopted = signatures_from_analysis(
        {"timeframe": "1d", "asset_class": "equity", "pnf_measured_objective": {"adopted": True, "confidence": 70, "direction": "long"}}
    )
    keys = [item["key"] for item in adopted]
    assert any("pnf_measured_objective" in key for key in keys)

    skipped = signatures_from_analysis(
        {"timeframe": "1d", "asset_class": "equity", "pnf_measured_objective": {"adopted": False, "confidence": 70, "direction": "long"}}
    )
    assert not any("pnf_measured_objective" in item["key"] for item in skipped)


def test_pnf_signature_is_candidate_strength_not_validated() -> None:
    """C3: 검증 없이 기본값 승격 금지 — strength_class는 신뢰도 버킷일 뿐 validated가 아니다."""
    from app.backtest.signatures import signatures_from_analysis

    signatures = signatures_from_analysis(
        {"timeframe": "1d", "asset_class": "equity", "pnf_measured_objective": {"adopted": True, "confidence": 70, "direction": "long"}}
    )
    pnf = [item for item in signatures if "pnf_measured_objective" in item["key"]]
    assert pnf and all(item["strength_class"] != "validated" for item in pnf)


def test_pnf_params_have_hard_bounds() -> None:
    """작업 1: 배분 비율·PNF 파라미터는 hard bound로 극단값이 막혀야 한다."""
    from app.analyst.param_registry import validate_param_change

    assert validate_param_change("stock_tp_weight_primary", 0.40)[0] is True
    assert validate_param_change("stock_tp_weight_primary", 0.95)[0] is False
    assert validate_param_change("pnf_reversal", 3)[0] is True
    assert validate_param_change("pnf_reversal", 99)[0] is False
    assert validate_param_change("pnf_min_columns", 4)[0] is True
    assert validate_param_change("pnf_min_columns", 1)[0] is False
