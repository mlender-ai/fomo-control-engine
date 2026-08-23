"""WO-FCE-EARNINGS-SUPPLY-01 4-3 (개정 §2) — 실적 게이트 3상태 회귀.

고정하는 명제:

1. **KR 선례를 재사용한다** — 어휘도 구조도 새로 만들지 않는다 (불변 규칙 2)
2. **판정을 재구현하지 않는다** — `_earnings_clear` 를 호출한다 (C1)
3. **3상태가 구분된다** — `not_evaluable` 과 `earnings_window` 는 다른 사유다
4. **`required=False` 가 기본이고, 크립토 진입 건수는 불변이다** (개정 §1-2)
5. **`required=True` 전환이 설정 한 값으로 된다** (옵트인)
6. `paper/policy.py` · `stock_paper/parameters.py` diff 0줄 (C3 · §1-5)
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.core.config import Settings
from app.paper.earnings_state import (
    CLEAR,
    EARNINGS_STATES,
    EARNINGS_WINDOW,
    NOT_EVALUABLE,
    coverage_summary,
    earnings_gate_passes,
    earnings_observation,
    earnings_state,
)
from app.paper.service import _earnings_clear

REPO_ROOT = Path(__file__).resolve().parents[2]

_IN_WINDOW = {"blocked": False, "days_to_event": 0}
_OUT_OF_WINDOW = {"blocked": False, "days_to_event": 30}


def _state(asset_class: str, earnings: dict | None = None) -> str:
    analysis = {"asset_class": asset_class}
    if earnings is not None:
        analysis["earnings"] = earnings
    return earnings_state(analysis, earnings_clear=_earnings_clear)


# ── 1. 3상태가 구분된다 ─────────────────────────────────────────────────


def test_missing_feed_is_not_evaluable_not_a_rejection() -> None:
    """**모르는 것과 아니라고 판정한 것을 같은 값으로 적으면 공급 결함이 위장된다.**"""
    assert _state("stock") == NOT_EVALUABLE
    assert _state("index") == NOT_EVALUABLE


def test_earnings_window_is_distinguished_from_missing_data() -> None:
    assert _state("stock", _IN_WINDOW) == EARNINGS_WINDOW
    assert _state("stock", _OUT_OF_WINDOW) == CLEAR


def test_crypto_is_not_subject_to_the_gate() -> None:
    assert _state("crypto") == CLEAR


def test_states_are_exactly_the_kr_vocabulary() -> None:
    """크립토 전용 상태명을 새로 만들면 두 트랙의 퍼널을 나란히 놓을 수 없다."""
    assert set(EARNINGS_STATES) == {"clear", "earnings_window", "not_evaluable"}

    kr_policy = (REPO_ROOT / "backend" / "app" / "stock_paper" / "policy.py").read_text(encoding="utf-8")
    assert '"not_evaluable"' in kr_policy, "KR 선례가 사라졌다면 이 이식의 근거가 사라진 것이다"


# ── 2. 판정을 재구현하지 않는다 (C1) ────────────────────────────────────


def test_module_does_not_redefine_the_threshold() -> None:
    """임계가 두 곳에 있으면 언젠가 갈린다. 문서에 인용하는 것과 코드로 계산하는 것은 다르다."""
    import ast

    path = REPO_ROOT / "backend" / "app" / "paper" / "earnings_state.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    # docstring 을 떼고 **실행 코드만** 본다 — 문서에 임계를 인용하는 것은 재구현이 아니다.
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                node.body = body[1:]
    code = ast.unparse(tree)

    # 임계는 딕셔너리 키(`"days_to_event"`)로만 등장해야 하고, 비교에 쓰이면 안 된다.
    assert "days_to_event" not in code.replace("'days_to_event'", ""), "임계를 코드로 재구현하고 있다"
    assert "{-1, 0, 1}" not in code, "실적 창 임계를 복제했다"
    assert "earnings_clear" in code, "판정을 호출하지 않고 자체 계산하고 있다"


def test_window_verdict_comes_from_the_live_function() -> None:
    """임계를 주입된 함수가 정한다는 확인 — 이 모듈은 창 안팎을 스스로 판단하지 않는다."""
    calls: list[dict] = []

    def spy(analysis: dict) -> bool:
        calls.append(analysis)
        return False

    assert earnings_state({"asset_class": "stock", "earnings": _OUT_OF_WINDOW}, earnings_clear=spy) == EARNINGS_WINDOW
    assert calls, "주입된 판정 함수가 호출되지 않았다"


# ── 3. required 플래그 (개정 §1-2·§1-3) ────────────────────────────────


def test_not_evaluable_passes_before_a_source_is_wired() -> None:
    """KR 트랙과 같다 — `required=False` 면 판정에서 제외한다."""
    assert earnings_gate_passes(NOT_EVALUABLE, required=False) is True


def test_not_evaluable_blocks_once_a_source_exists() -> None:
    """공급원이 있는데도 데이터가 없다면 그것은 진짜 신호다."""
    assert earnings_gate_passes(NOT_EVALUABLE, required=True) is False


def test_earnings_window_blocks_in_both_modes() -> None:
    """**이 WO 는 실적 구간 차단을 완화하지 않는다.**"""
    assert earnings_gate_passes(EARNINGS_WINDOW, required=False) is False
    assert earnings_gate_passes(EARNINGS_WINDOW, required=True) is False


def test_clear_passes_in_both_modes() -> None:
    assert earnings_gate_passes(CLEAR, required=False) is True
    assert earnings_gate_passes(CLEAR, required=True) is True


def test_required_defaults_to_false_and_is_one_setting() -> None:
    assert Settings().paper_earnings_gate_required is False
    assert Settings(paper_earnings_gate_required=True).paper_earnings_gate_required is True


# ── 4. 진입 건수 불변 — 크립토는 전후 동일 (개정 §2 수용 기준) ──────────


@pytest.mark.parametrize("required", [False, True])
def test_crypto_entry_decision_is_unchanged_in_both_modes(required: bool) -> None:
    """현행 라이브 유니버스는 사실상 전부 crypto 다(262종 오분류 포함).

    그래서 이 변경의 **진입 건수 영향은 crypto 에서 정확히 0**이다 — `required` 를 켜든 끄든
    같다. 이것이 개정 §2 가 "진입 건수 전후 동일"이라고 적은 근거다.
    """
    before = _earnings_clear({"asset_class": "crypto"})
    after = earnings_gate_passes(_state("crypto"), required=required)

    assert before is True
    assert after is True
    assert before == after


def test_stock_without_a_feed_stops_being_blocked_when_not_required() -> None:
    """**여기는 동작이 바뀐다. 숨기지 않는다.**

    공급원 없는 stock·index 는 지금까지 `_earnings_clear=False` 로 차단됐다. `required=False`
    에서는 통과한다 — KR 트랙과 같은 취급이며 개정 §1-2 가 의도한 변화다.

    실제 진입이 늘어나는지는 나머지 게이트에 달려 있다. 그 심볼들은 `stage2_template`
    (캔들 200봉)에서 이미 막혀 있어 표본이 0이다.
    """
    before = _earnings_clear({"asset_class": "stock"})
    after = earnings_gate_passes(_state("stock"), required=False)

    assert before is False, "현행은 차단이었다"
    assert after is True, "required=False 에서는 통과한다 — 의도된 변화다"


def test_required_true_restores_the_current_blocking_behaviour() -> None:
    """전환 후에는 현행과 같아진다 — 되돌릴 수 있다는 확인."""
    assert earnings_gate_passes(_state("stock"), required=True) == _earnings_clear({"asset_class": "stock"})


# ── 5. 관측치와 커버리지 (C9) ───────────────────────────────────────────


def test_observation_matches_the_kr_shape() -> None:
    observation = earnings_observation(NOT_EVALUABLE, required=False)

    assert observation["status"] == "not_evaluable"
    assert observation["threshold"] == "source_backlog"
    assert observation["required"] is False
    assert observation["passed"] is True


def test_coverage_summary_exposes_the_no_data_ratio() -> None:
    summary = coverage_summary([NOT_EVALUABLE, NOT_EVALUABLE, CLEAR, EARNINGS_WINDOW])

    assert summary["total"] == 4
    assert summary["counts"][NOT_EVALUABLE] == 2
    assert summary["not_evaluable_pct"] == 50.0
    assert summary["coverage_pct"] == 50.0
    assert "불통과가 아니다" in summary["note"]


def test_coverage_summary_is_honest_about_an_empty_sample() -> None:
    summary = coverage_summary([])

    assert summary["total"] == 0
    assert summary["not_evaluable_pct"] is None, "표본 0에서 비율을 만들어 내면 안 된다"


# ── 6. 제약 diff 0줄 (C3 · §1-5) ────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "backend/app/paper/policy.py",
        "backend/app/stock_paper/parameters.py",
        "backend/app/stock_paper/policy.py",
        "backend/app/marketdata/assets.py",
        "backend/app/analyst/",
        "backend/app/structure/",
    ],
)
def test_constrained_paths_have_zero_diff(path: str) -> None:
    """C3 진입 게이트 · §1-5 KR 불변식 · C5 자산군 분류 — 전부 불변이다."""
    diff = subprocess.run(
        ["git", "diff", "origin/main", "--stat", "--", path],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")

    assert diff.stdout.strip() == "", f"제약 경로가 변경됐다:\n{diff.stdout}"


def test_earnings_clear_itself_is_untouched() -> None:
    """C1 — 판정 로직은 그대로다. 이 WO 는 **공급과 표현**만 다룬다."""
    diff = subprocess.run(
        ["git", "diff", "origin/main", "--", "backend/app/paper/service.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")

    changed = [line for line in diff.stdout.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    assert not any("days_to_event" in line for line in changed), "실적 임계가 변경됐다"
    assert not any("def _earnings_clear" in line for line in changed), "_earnings_clear 정의가 변경됐다"
