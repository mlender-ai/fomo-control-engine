"""2026-09-08 차트 500 회귀 — 부트스트랩 CI 캐시.

## 무엇이 있었나

화면에 "차트 데이터를 불러올 수 없습니다 · API request failed: 500" 이 떴다. 그런데
백엔드는 **500 을 반환하지 않았다** — 200 을 15.8초에 반환했고, 프론트가 타임아웃하고
그것을 500 으로 바꿨다. 원인 후보로 적힌 "Bitget 시세 오류 · 캔들 데이터 부족 ·
심볼 매핑 오류" 는 셋 다 아니었다.

프로파일: 28.4초 중 **25.6초가 `bootstrap_ci_from_counts`**, `random.randrange` 4,623만 회.
`chart_onchain_context` 가 지갑 리뷰 528건을 계산하며 각각 CI 를 새로 돌렸다.
실측 551회 호출 중 서로 다른 인자는 **79개**(중복 85.7%) — `(30, 47)` 221회,
`(105, 226)` 125회.

## 캐시가 정당한 이유

`bootstrap_ci_from_counts` 는 `(correct, tested, iterations, confidence)` 만의 순수
결정론 함수다. 승패 벡터를 항상 같은 순서로 만들고 시드를 그 시퀀스에서 파생한다.
**문턱을 낮추거나 리샘플을 줄인 것이 아니다** — `iterations` 는 그대로 1000 이다.
"""

from __future__ import annotations

import random

import pytest

from app.backtest.statistics import (
    _bootstrap_ci_cached,
    bootstrap_ci_from_counts,
    bootstrap_win_ci,
)


def _reference(correct: int, tested: int, *, iterations: int = 1000, confidence: float = 0.95):
    """캐시 도입 전 구현. 값이 바뀌지 않았음을 이것으로 대조한다."""
    if tested <= 0:
        return None
    correct = max(0, min(tested, correct))
    wins = [True] * correct + [False] * (tested - correct)
    return bootstrap_win_ci(wins, iterations=iterations, confidence=confidence)


@pytest.mark.parametrize(
    "correct,tested",
    [(0, 0), (0, 1), (1, 1), (5, 7), (30, 47), (105, 226), (9, 13), (17, 32), (1, 3), (2, 4)],
)
def test_cache_returns_the_same_value_as_before(correct: int, tested: int) -> None:
    """**캐시는 판정값을 바꾸지 않는다.** 이것이 이 수리의 전제다."""
    assert bootstrap_ci_from_counts(correct, tested) == _reference(correct, tested)


def test_cache_matches_reference_across_random_inputs() -> None:
    """손으로 고른 케이스만 맞추는 캐시는 증명이 아니다."""
    rng = random.Random(1)
    for _ in range(120):
        correct, tested = rng.randint(0, 60), rng.randint(1, 60)
        assert bootstrap_ci_from_counts(correct, tested) == _reference(correct, tested)


@pytest.mark.parametrize("correct,tested", [(-3, 10), (99, 10)])
def test_out_of_range_counts_still_clamp(correct: int, tested: int) -> None:
    """클램프는 캐시 **앞**에서 일어나야 한다 — 뒤면 같은 값이 여러 키로 들어간다."""
    assert bootstrap_ci_from_counts(correct, tested) == _reference(correct, tested)


def test_zero_tested_is_none_and_is_not_cached_as_a_value() -> None:
    assert bootstrap_ci_from_counts(0, 0) is None
    assert bootstrap_ci_from_counts(5, 0) is None
    assert bootstrap_ci_from_counts(5, -1) is None


def test_repeated_calls_hit_the_cache() -> None:
    """중복 호출이 재계산되면 이 수리가 사라진 것이다 — 화면이 다시 타임아웃한다."""
    bootstrap_ci_from_counts(30, 47)
    before = _bootstrap_ci_cached.cache_info()
    for _ in range(50):
        bootstrap_ci_from_counts(30, 47)
    after = _bootstrap_ci_cached.cache_info()
    assert after.hits - before.hits == 50
    assert after.misses == before.misses


def test_iterations_stayed_at_the_original_thousand() -> None:
    """**리샘플 수를 줄여 빠르게 만든 것이 아니다.** 줄이면 CI 가 넓어지고 게이트가 흔들린다."""
    import inspect

    sig = inspect.signature(bootstrap_ci_from_counts)
    assert sig.parameters["iterations"].default == 1000
    assert inspect.signature(bootstrap_win_ci).parameters["iterations"].default == 1000


def test_different_iterations_do_not_share_a_cache_entry() -> None:
    """`iterations` 가 키에 없으면 다른 리샘플 수가 같은 값을 받는다."""
    a = bootstrap_ci_from_counts(30, 47, iterations=1000)
    b = bootstrap_ci_from_counts(30, 47, iterations=200)
    assert a == _reference(30, 47, iterations=1000)
    assert b == _reference(30, 47, iterations=200)


def test_different_confidence_do_not_share_a_cache_entry() -> None:
    narrow = bootstrap_ci_from_counts(30, 47, confidence=0.50)
    wide = bootstrap_ci_from_counts(30, 47, confidence=0.95)
    assert narrow == _reference(30, 47, confidence=0.50)
    assert wide == _reference(30, 47, confidence=0.95)
    assert narrow != wide
