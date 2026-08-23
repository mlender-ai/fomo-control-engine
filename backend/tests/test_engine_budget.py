"""WO-FCE-ASSET-CLASS-01 3-1 — 엔진 실행 예산 회귀.

고정하는 명제:

1. **사고를 재현한다** — 상한 없이 15종이면 450초 예산을 정확히 넘긴다
2. **두 한계는 다르다** — 실행당 예산과 순회 주기. 상한이 둘을 분리한다
3. **289종은 감당 가능하다** — 예산은 3-2 의 병목이 아니다
4. 예산 식이 `WorkerManager._job_timeout_seconds` 와 일치한다
"""

from __future__ import annotations

from app.core.config import Settings
from app.validation import engine_budget as eb
from app.worker.manager import WorkerManager


def _inputs(**overrides) -> eb.BudgetInputs:
    return eb.from_settings(Settings(**overrides))


# ── 1. 사고 재현 (D1) ───────────────────────────────────────────────────


def test_the_incident_reproduces_exactly() -> None:
    """`DISCOVERY-UNBLOCK-01` 이 유니버스를 15종으로 늘렸을 때 무슨 일이 있었나.

    15 × 30초 = 450초 = 예산. **여유 0이 아니라 정확히 초과**다.
    """
    unbounded = _inputs(paper_engine_max_symbols_per_run=0)

    assert unbounded.timeout_seconds == 450, "90초 주기 × 배수 5 = 450초"
    report = eb.budget_report(15, unbounded)

    assert report["per_run"]["unbounded"] is True
    assert report["per_run"]["hard_cap_symbols"] == 15
    assert "DISCOVERY-UNBLOCK-01 사고의 형태" in report["verdict"]


def test_the_configured_cap_closes_the_incident() -> None:
    report = eb.budget_report(15, _inputs())

    assert report["per_run"]["configured_cap"] == 6
    assert report["per_run"]["projected_run_seconds"] == 180.0
    assert report["per_run"]["configured_within_safe_cap"] is True


def test_safe_cap_leaves_margin_below_the_hard_cap() -> None:
    """하드 상한에 맞추면 지연 한 번에 터진다 — 여유를 남긴 값이 권장치다."""
    run = eb.per_run_limit(_inputs())

    assert run["hard_cap_symbols"] == 15
    assert run["safe_cap_symbols"] == 9
    assert run["safe_cap_symbols"] < run["hard_cap_symbols"]


# ── 2. 두 한계의 분리 ───────────────────────────────────────────────────


def test_cap_decouples_universe_size_from_per_run_cost() -> None:
    """상한이 있으면 유니버스가 커져도 **실행당 비용은 고정**이다."""
    inputs = _inputs()

    small = eb.budget_report(15, inputs)["per_run"]["projected_run_seconds"]
    large = eb.budget_report(289, inputs)["per_run"]["projected_run_seconds"]

    assert small == large == 180.0, "실행당 비용은 유니버스 크기와 무관하다"


def test_universe_growth_slows_the_sweep_instead_of_killing_the_job() -> None:
    inputs = _inputs()

    assert eb.sweep_period(15, inputs)["sweep_minutes"] == 4.5
    assert eb.sweep_period(289, inputs)["sweep_minutes"] == 73.5


# ── 3. 289종 판정 — 3-2 단계 확대의 근거 ────────────────────────────────


def test_two_hundred_eighty_nine_symbols_fit_inside_one_bar() -> None:
    """**예산은 3-2 의 병목이 아니다.** 이것이 3-1 이 3-2 에 넘기는 숫자다."""
    report = eb.budget_report(289, _inputs())

    assert report["sweep"]["fits_in_one_bar"] is True
    assert report["sweep"]["runs_to_cover_universe"] == 49
    assert "감당 가능" in report["verdict"]


def test_ceiling_is_reported_so_the_next_expansion_knows_where_it_ends() -> None:
    inputs = _inputs()

    assert eb.max_universe_for_one_bar(inputs) == 960
    assert eb.sweep_period(960, inputs)["fits_in_one_bar"] is True
    assert eb.sweep_period(1_200, inputs)["fits_in_one_bar"] is False


def test_beyond_the_ceiling_the_verdict_names_the_sweep_not_the_budget() -> None:
    report = eb.budget_report(1_200, _inputs())

    assert "순회가 한 봉을 넘는다" in report["verdict"]
    assert report["per_run"]["configured_within_safe_cap"] is True, "예산은 여전히 안전하다 — 다른 한계다"


# ── 4. 예산 식이 워커와 일치하는가 ──────────────────────────────────────


def test_timeout_formula_matches_the_worker() -> None:
    """식을 복제했으므로 갈라지면 즉시 실패해야 한다."""
    settings = Settings()
    manager = WorkerManager(settings)

    assert eb.from_settings(settings).timeout_seconds == manager._job_timeout_seconds("sync_positions")


def test_seconds_per_symbol_is_the_measured_value_not_a_guess() -> None:
    assert eb.MEASURED_SECONDS_PER_SYMBOL == 30.0
    assert eb.BAR_SECONDS_4H == 14_400


def test_engine_budget_document_states_the_two_limits() -> None:
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[2] / "docs" / "validation" / "ENGINE_BUDGET.md").read_text(encoding="utf-8")

    assert "실행당" in doc and "순회" in doc, "두 한계가 정본에 구분돼 있지 않다"
    assert "960" in doc, "유니버스 천장이 정본에 없다"
    assert "289" in doc, "3-2 판정 숫자가 정본에 없다"
