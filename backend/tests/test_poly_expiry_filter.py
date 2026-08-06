"""WO-FCE-SAMPLE-VIABILITY-01 PHASE 2 — 폴리 만기 필터 회귀.

배경: 보유 9건 중 8건이 2027-01-01 만기였다(실측 2026-08-05). 검증 종료는 08-19 다.
유니버스에는 검증 종료 이전 만기 시장이 4,847개 있었다 — **데이터 문제가 아니라 선정
기준에 만기 조건이 없다**는 문제였다. v2 까지 만기 조건은 아래쪽(min_days_to_resolution)
에만 있었다.

고정하는 명제:
1. 채점 창 밖 만기는 선정되지 않는다 (신규 진입부터)
2. 창 안 만기는 계속 선정된다 (필터가 전부를 막으면 그건 다른 결함이다)
3. 채점 창 밖 제외는 "거부"가 아니라 별도 분류다 — 지표 분모를 오염시키지 않는다
4. **기존 임계값은 하나도 바뀌지 않았다** (C1: 임계값·게이트 파라미터 diff 0)
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.poly_paper.models import Category, PolyMarket
from app.poly_paper.parameters import DEFAULT_PATH, load_poly_parameters
from app.poly_paper.service import (
    RESOLUTION_BEYOND_SCORING_WINDOW,
    _apply_market_gates,
    classify_exclusion,
    expiry_histogram,
    scoring_cutoff,
)

NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)
DEADLINE = datetime(2026, 8, 19, tzinfo=timezone.utc)


def _market(end_at: datetime | None, *, market_id: str = "m1") -> PolyMarket:
    return PolyMarket(
        id=market_id,
        slug=market_id,
        question=market_id,
        category=Category.CRYPTO,
        observed_at=NOW,
        end_at=end_at,
        active=True,
        closed=False,
        liquidity=50_000.0,
        resolution_source="https://example.test/official",
        description="Resolves Yes when the official reference is above the threshold.",
        yes_token_id="yes",
        no_token_id="no",
        yes_price=0.52,
        no_price=0.48,
        trade_eligible=True,
        exclusion_reason=None,
    )


# ── 1. 필터 동작 ────────────────────────────────────────────────────────


def test_market_expiring_after_the_scoring_deadline_is_excluded() -> None:
    """실측 재현: 2027-01-01 만기는 진입해도 검증 안에 정산되지 않는다."""
    cutoff = scoring_cutoff(load_poly_parameters(), now=NOW, deadline=DEADLINE)

    gated = _apply_market_gates(_market(datetime(2027, 1, 1, tzinfo=timezone.utc)), now=NOW, scoring_deadline=cutoff)

    assert gated.trade_eligible is False
    assert gated.exclusion_reason == RESOLUTION_BEYOND_SCORING_WINDOW


def test_market_inside_the_window_still_passes() -> None:
    """필터가 후보를 전부 막으면 그건 필터가 아니라 결함이다."""
    cutoff = scoring_cutoff(load_poly_parameters(), now=NOW, deadline=DEADLINE)

    gated = _apply_market_gates(_market(datetime(2026, 8, 12, tzinfo=timezone.utc)), now=NOW, scoring_deadline=cutoff)

    assert gated.trade_eligible is True
    assert gated.exclusion_reason is None


def test_no_deadline_means_the_hard_cap_still_applies() -> None:
    """검증 종료일을 모르면 파라미터 상한(max_days_to_resolution)만으로 자른다."""
    parameters = load_poly_parameters()
    cutoff = scoring_cutoff(parameters, now=NOW, deadline=None)

    assert cutoff == NOW + timedelta(days=parameters.max_days_to_resolution)


def test_filter_is_inert_when_the_parameter_set_has_no_cap() -> None:
    """v1·v2 하위호환 — 상한이 없으면 필터는 동작하지 않는다(과거 재현 가능성 유지)."""
    legacy = load_poly_parameters(DEFAULT_PATH.with_name("poly-v2.json"))

    assert legacy.max_days_to_resolution is None
    assert scoring_cutoff(legacy, now=NOW, deadline=None) is None


def test_settlement_buffer_pulls_the_cutoff_before_the_deadline() -> None:
    """만기 당일에 정산이 확정되지 않는다 — 안전 여유만큼 앞당긴다."""
    parameters = load_poly_parameters()
    cutoff = scoring_cutoff(parameters, now=NOW, deadline=DEADLINE)

    assert cutoff == DEADLINE - timedelta(days=parameters.settlement_buffer_days)


# ── 2. 지표 분류 ────────────────────────────────────────────────────────


def test_window_filtered_is_not_counted_as_a_rejection() -> None:
    """ "거부"는 판정했으나 기준 미달인 경우다. 창 밖 만기는 표본이 될 수 없는 시장이다."""
    assert classify_exclusion(RESOLUTION_BEYOND_SCORING_WINDOW) == "window_filtered"
    assert classify_exclusion("liquidity_below_minimum") == "rejected"


def test_expiry_histogram_contrasts_before_and_after(_=None) -> None:
    """수정 전후 대조 (규칙 5) — 무엇이 걸렸는지 숫자로 남아야 한다."""
    markets = [
        _market(NOW + timedelta(days=3), market_id="near"),
        _market(NOW + timedelta(days=20), market_id="mid"),
        _market(datetime(2027, 1, 1, tzinfo=timezone.utc), market_id="far"),
        _market(None, market_id="unknown"),
    ]

    histogram = expiry_histogram(markets, now=NOW, scoring_deadline=scoring_cutoff(load_poly_parameters(), now=NOW, deadline=DEADLINE))

    assert histogram["buckets"] == {"<=7d": 1, "8-28d": 1, "29-90d": 0, ">90d": 1, "unknown": 1}
    assert histogram["within_scoring_deadline"] == 1
    assert histogram["beyond_scoring_deadline"] == 2


# ── 3. 임계값 불변 (C1) ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field",
    [
        "min_edge",
        "min_liquidity",
        "min_days_to_resolution",
        "min_estimate_quality",
        "max_position_fraction",
        "max_open_markets",
        "max_observed_ask_fraction",
        "min_resolution_clarity",
        "estimate_min_interval_minutes",
        "coverage_entry_enabled",
        "coverage_position_fraction",
        "coverage_target_open_markets",
    ],
)
def test_existing_thresholds_are_unchanged_in_v3(field: str) -> None:
    """이 WO 는 게이트를 **추가**했을 뿐 완화하지 않았다. 하나라도 달라지면 실패해야 한다."""
    v2 = load_poly_parameters(DEFAULT_PATH.with_name("poly-v2.json"))
    v3 = load_poly_parameters(DEFAULT_PATH.with_name("poly-v3.json"))

    assert getattr(v3, field) == getattr(v2, field), f"{field} 가 바뀌었다 — 임계값 diff 0 위반"


def test_existing_positions_are_not_liquidated_by_the_filter() -> None:
    """기존 보유 9건은 청산하지 않는다. 필터는 **신규 진입 선정**에만 관여한다."""
    held = replace(_market(datetime(2027, 1, 1, tzinfo=timezone.utc)), trade_eligible=True)

    gated = _apply_market_gates(held, now=NOW, scoring_deadline=scoring_cutoff(load_poly_parameters(), now=NOW, deadline=DEADLINE))

    # 게이트는 진입 자격만 끈다 — 포지션 자체를 건드리는 경로는 이 함수에 없다.
    assert gated.id == held.id
    assert gated.exclusion_reason == RESOLUTION_BEYOND_SCORING_WINDOW
