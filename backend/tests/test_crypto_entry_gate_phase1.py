"""WO-FCE-CORE-DEFECTS-01 Phase 1 — 크립토 진입 데드락 해제 회귀.

핵심 증명: **품질 임계는 diff 0이다.** confirmed_flip·validated_signature 두 게이트의
평가 방식만 바꿨고 evidence·checklist·invalidation_hygiene·risk_reward·
liquidation_safety·earnings_clear·data_fresh 는 동일 입력에 동일 결과여야 한다.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.db.models import Direction
from app.paper.policy import PaperPolicy, evaluate_entry

RETAINED_GATES = (
    "evidence",
    "checklist",
    "invalidation_hygiene",
    "risk_reward",
    "liquidation_safety",
    "earnings_clear",
    "data_fresh",
)

V1 = PaperPolicy(version="crypto-v1", stance_gate_mode="confirmed_flip", signature_gate_mode="required")
V2 = PaperPolicy(version="crypto-v2", stance_gate_mode="stable_direction", signature_gate_mode="record_only")


def _decide(policy: PaperPolicy, **overrides):
    kwargs = {
        "stance_state": {"stance": "long_leaning", "flipped": False, "transitioning": False},
        "direction": Direction.long,
        "evidence_count": 4,
        "checklist_passed": 5,
        "checklist_total": 5,
        "rr_ratio": 1.5,
        "invalidation_hygiene": True,
        "survives_to_invalidation": True,
        "validated_signature": False,
        "signature_ci_low_pct": None,
        "earnings_clear": True,
        "data_fresh": True,
        "confirmed_bar": True,
        "policy": policy,
    }
    kwargs.update(overrides)
    return evaluate_entry(**kwargs)


def test_v2_params_file_exists_and_declares_modes() -> None:
    path = Path("app/paper/params/crypto-v2.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == "crypto-v2"
    assert payload["stance_gate_mode"] == "stable_direction"
    assert payload["signature_gate_mode"] == "record_only"


def test_v1_blocks_stable_stance_without_flip() -> None:
    """수리 전: flip 펄스가 지난 안정 구간은 진입할 수 없었다 — 진입 창이 1봉뿐."""
    decision = _decide(V1)
    assert decision.gates["confirmed_flip"] is False
    assert decision.enter is False


def test_v2_allows_stable_stance_in_matching_direction() -> None:
    decision = _decide(V2)
    assert decision.gates["confirmed_flip"] is True
    assert decision.enter is True


def test_v2_still_requires_direction_match() -> None:
    """완화가 아니다 — 방향이 어긋나면 여전히 막는다."""
    decision = _decide(V2, stance_state={"stance": "short_leaning", "transitioning": False})
    assert decision.gates["confirmed_flip"] is False


def test_v2_still_requires_stability() -> None:
    """전환 관찰 중(transitioning)에는 여전히 진입하지 않는다."""
    decision = _decide(V2, stance_state={"stance": "long_leaning", "transitioning": True})
    assert decision.gates["confirmed_flip"] is False


def test_v2_still_requires_confirmed_bar() -> None:
    """룩어헤드 금지 — 확정 캔들 요구는 존치한다(도메인 불변)."""
    decision = _decide(V2, confirmed_bar=False)
    assert decision.gates["confirmed_flip"] is False


def test_v2_short_direction_is_symmetric() -> None:
    decision = _decide(V2, stance_state={"stance": "short_leaning", "transitioning": False}, direction=Direction.short)
    assert decision.gates["confirmed_flip"] is True


def test_signature_circularity_released() -> None:
    """검증 대상을 검증 조건으로 삼을 수 없다 — 미검증 시그니처도 진입 가능해야 표본이 생긴다."""
    assert _decide(V1, validated_signature=False).gates["validated_signature"] is False
    assert _decide(V2, validated_signature=False).gates["validated_signature"] is True


def test_retained_gates_have_zero_diff() -> None:
    """C1 증명 — 품질 임계는 동일 입력에 동일 결과다."""
    cases = [
        {},
        {"evidence_count": 3},
        {"checklist_passed": 4},
        {"checklist_total": 4},
        {"rr_ratio": 1.49},
        {"rr_ratio": None},
        {"invalidation_hygiene": False},
        {"survives_to_invalidation": False},
        {"earnings_clear": False},
        {"data_fresh": False},
    ]
    for case in cases:
        v1 = _decide(V1, **case)
        v2 = _decide(V2, **case)
        for gate in RETAINED_GATES:
            assert v1.gates[gate] == v2.gates[gate], f"{gate} diff at {case}"


def test_quality_gates_still_block_in_v2() -> None:
    """v2 에서도 품질 미달은 진입 불가 — 데드락만 풀고 기준은 그대로."""
    for case in ({"evidence_count": 3}, {"checklist_passed": 4}, {"rr_ratio": 1.49}, {"survives_to_invalidation": False}):
        assert _decide(V2, **case).enter is False, f"v2 가 {case} 를 통과시켰다"


def test_flipped_is_recorded_not_required() -> None:
    """flipped 는 판정 대상이지 판정 조건이 아니다 — 원장에 남아야 사후 채점이 된다."""
    decision = _decide(V2, stance_state={"stance": "long_leaning", "flipped": True, "transitioning": False})
    assert decision.gates["confirmed_flip"] is True
    assert decision.observations["flipped"] is True
    assert decision.observations["stance_gate_mode"] == "stable_direction"
    assert decision.observations["policy_version"] == "crypto-v2"

    stable = _decide(V2)
    assert stable.observations["flipped"] is False
    # 미검증 시그니처로 진입했다는 사실도 관측치로 남는다.
    assert stable.observations["validated_signature_observed"] is False


def test_default_policy_preserves_v1_behaviour() -> None:
    """params 파일을 지우면 즉시 이전 정책으로 되돌아간다(옵트인)."""
    default = PaperPolicy()
    assert default.stance_gate_mode == "confirmed_flip"
    assert default.signature_gate_mode == "required"
    assert _decide(default).gates["confirmed_flip"] is False
