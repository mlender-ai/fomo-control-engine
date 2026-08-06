"""WO-FCE-SAMPLE-VIABILITY-01 PHASE 4 — checklist 병목 계측 회귀.

배경: 크립토 checklist 통과율 44.7%가 진입 병목이다. **이 페이즈는 고치는 게 아니라
보이게 하는 것이다.** checklist 는 품질 게이트이며 통과율이 낮다는 이유로 임계값을
건드리지 않는다 — 엄격한 게 맞을 수도 있다.

핵심은 **유일 탈락 사유 건수**다. 항목별 통과율만 보면 "여러 항목이 골고루 낮다"와
"한 항목이 전부를 막는다"가 구분되지 않는다.

고정하는 명제:
1. 항목별 평가·통과·통과율이 나온다
2. 이 항목 **하나만** 실패해 탈락한 건수가 따로 세어진다
3. checklist 가 유일한 미통과 게이트였던 건수가 나온다
4. 계측이 임계값을 바꾸지 않는다 (C1)
"""

from __future__ import annotations

from app.core.config import Settings
from app.paper import service as paper_service
from app.paper.service import _checklist_bottleneck, _checklist_pass_rates


def _row(items: list[tuple[str, str]], *, gates: dict[str, bool] | None = None, rejection_reasons: list[str] | None = None) -> dict:
    return {
        "checklist_items": [{"key": key, "label": key.upper(), "status": status} for key, status in items],
        "gates": gates if gates is not None else {"checklist": all(status == "pass" for _, status in items)},
        "rejection_reasons": rejection_reasons if rejection_reasons is not None else ([] if all(status == "pass" for _, status in items) else ["checklist"]),
    }


def test_pass_rates_are_reported_per_item() -> None:
    rows = [_row([("rr", "fail"), ("htf", "pass")]), _row([("rr", "pass"), ("htf", "pass")])]

    rates = {item["key"]: item for item in _checklist_pass_rates(rows)}

    assert rates["rr"]["evaluated"] == 2 and rates["rr"]["pass_rate_pct"] == 50.0
    assert rates["htf"]["pass_rate_pct"] == 100.0


def test_sole_block_count_separates_one_blocker_from_many() -> None:
    """한 항목이 전부를 막고 있으면 그 항목을 따로 봐야 한다 — 통과율만으로는 안 보인다."""
    rows = [
        _row([("rr", "fail"), ("htf", "pass")]),  # rr 단독 탈락
        _row([("rr", "fail"), ("htf", "pass")]),  # rr 단독 탈락
        _row([("rr", "fail"), ("htf", "fail")]),  # 둘 다 실패 — 단독 아님
    ]

    rates = {item["key"]: item for item in _checklist_pass_rates(rows)}

    assert rates["rr"]["sole_block_count"] == 2
    assert rates["htf"]["sole_block_count"] == 0, "동시 실패는 어느 항목의 단독 탈락도 아니다"


def test_all_pass_rows_produce_no_sole_blocker() -> None:
    rates = {item["key"]: item for item in _checklist_pass_rates([_row([("rr", "pass"), ("htf", "pass")])])}

    assert rates["rr"]["sole_block_count"] == 0


def test_bottleneck_counts_rows_blocked_by_checklist_alone() -> None:
    """checklist 만 막은 건수 = 이 게이트만 풀렸다면 진입했을 건수다."""
    rows = [
        _row([("rr", "fail"), ("htf", "pass")], gates={"checklist": False}, rejection_reasons=["checklist"]),
        _row([("rr", "fail"), ("htf", "pass")], gates={"checklist": False}, rejection_reasons=["checklist", "signature_gate"]),
        _row([("rr", "pass"), ("htf", "pass")], gates={"checklist": True}, rejection_reasons=[]),
    ]

    bottleneck = _checklist_bottleneck(rows, _checklist_pass_rates(rows))

    assert bottleneck["evaluated"] == 3
    assert bottleneck["checklist_gate_failed"] == 2
    assert bottleneck["checklist_only_blocked"] == 1, "다른 게이트도 막은 건은 checklist 단독이 아니다"
    assert bottleneck["top_sole_blocker"]["key"] == "rr"


def test_bottleneck_states_the_no_relaxation_policy() -> None:
    """통과율이 낮다고 자동으로 '너무 엄격하다'고 결론내지 않는다 — 판단은 사람이 한다."""
    bottleneck = _checklist_bottleneck([], [])

    assert "완화하지 않는다" in bottleneck["policy"]
    assert bottleneck["top_sole_blocker"] is None


def test_instrumentation_does_not_touch_the_gate_threshold() -> None:
    """계측 WO 가 게이트 파라미터를 건드리면 그건 계측이 아니라 완화다 (C1).

    임계값 변경이 필요해 보이면 **적용하지 말고 근거와 함께 질문한다**(PHASE 4-3).
    """
    assert Settings().paper_min_checklist_passed == 5, "checklist 임계값이 바뀌었다 — 이 WO 의 범위 밖이다"
    assert paper_service.VALIDATION_BOOTSTRAP_MIN_CHECKLIST_PASSED == 3, "부트스트랩 임계값이 바뀌었다"
