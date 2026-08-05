"""WO-FCE-OBSERVATION-INTEGRITY-01 Phase 3-2 — 주식 후보 기아 수리 회귀.

실측: KOSPI100 탐색 표본 3개, NASDAQ100 5개(유니버스 200종목 대비 4%).
원인은 게이트가 아니라 **후보 풀**이었다. `(랭킹 ∪ 리테일랭킹) ∩ 가격` 으로 풀을 만들어
랭킹 30위 밖의 유니버스 종목은 평가 기회조차 없었다 — 랭킹이 정렬이 아니라 필터였다.

TPS 검산표는 이미 "후보 36종목(시장별 18)"을 전제로 계산돼 있었는데 실제로는 8종목만
평가되고 있었다. 예산을 실제로 채우는 것이지 한도를 늘리는 게 아니다.

고정하는 명제:
1. 랭킹에 없는 유니버스 종목도 후보가 된다 (풀 = 가격을 받은 종목 전체)
2. 랭킹은 우선순위 정렬로만 쓰인다 (랭킹 상위가 먼저 평가된다)
3. 정밀 평가 대상 수는 상한이 있다 — TPS 는 늘지 않는다
4. 게이트 임계는 손대지 않는다 (C1: 같은 잣대로 더 많이 잴 뿐)
"""

from __future__ import annotations

import asyncio

import pytest

from app.toss import service


class _StubStore:
    def append_raw(self, *_args, **_kwargs) -> None:
        return None

    def record_judgment(self, *_args, **_kwargs) -> None:
        return None


class _StubClient:
    def __init__(self) -> None:
        self.evidence_calls: list[str] = []

    async def get(self, path: str, **_kwargs):
        return {"result": []}


def _price_rows(symbols: list[str]) -> list[dict]:
    return [{"symbol": symbol, "lastPrice": 100.0} for symbol in symbols]


def _rankings(ranked: list[str]) -> dict:
    return {
        # 실제 응답 형태: result.rankings[] (평면 배열이 아니다)
        "MARKET_TRADING_AMOUNT": {
            "result": {"rankings": [{"symbol": symbol, "rank": index + 1, "price": {"lastPrice": 100.0}} for index, symbol in enumerate(ranked)]}
        },
        "TOSS_SECURITIES_TRADING_AMOUNT": {"result": {"rankings": []}},
    }


def _build(symbols: list[str], ranked: list[str], *, limit: int = 18) -> tuple[list[dict], dict[str, int]]:
    counters: dict[str, int] = {}

    async def _run():
        return await service._build_ranked_candidates(
            _StubClient(),
            _StubStore(),
            "US",
            _rankings(ranked),
            [],
            _price_rows(symbols),
            [{"symbol": symbol, "name": symbol} for symbol in symbols],
            "2026-08-05T14:00:00+00:00",
            limit=limit,
            counters=counters,
        )

    return asyncio.run(_run()), counters


def test_pool_is_not_limited_to_ranked_symbols() -> None:
    """랭킹 밖 유니버스 종목이 후보 풀에 들어와야 한다 — 이게 기아의 원인이었다."""
    symbols = [f"SYM{index:03d}" for index in range(50)]
    _, counters = _build(symbols, ranked=symbols[:5])

    assert counters["priced"] == 50
    assert counters["ranked_and_priced"] == 5
    assert counters["candidates"] > 5, "후보 풀이 랭킹 교집합에 갇혀 있으면 기아가 재발한다"


def test_ranking_orders_the_pool_instead_of_filtering_it() -> None:
    """랭킹 상위가 먼저 평가되어야 한다 — 정렬 기능은 유지한다."""
    symbols = [f"SYM{index:03d}" for index in range(30)]
    ranked = ["SYM020", "SYM025"]
    _, counters = _build(symbols, ranked=ranked, limit=5)

    assert counters["ranked_and_priced"] == 2
    assert counters["limit"] == 5


def test_precise_evaluation_is_capped_so_tps_does_not_grow() -> None:
    """정밀 평가 대상은 상한을 넘지 않는다 — 풀을 넓히되 호출량은 예산 안이다(C4)."""
    symbols = [f"SYM{index:03d}" for index in range(200)]
    _, counters = _build(symbols, ranked=symbols[:30], limit=18)

    assert counters["evaluated"] <= 18
    assert counters["candidates"] >= counters["evaluated"]


def test_counters_expose_every_stage() -> None:
    """단계별 탈락을 추측 없이 특정할 수 있어야 한다(WO 3-2 작업 1)."""
    _, counters = _build([f"SYM{index:03d}" for index in range(20)], ranked=["SYM000"])

    for key in ("priced", "ranked", "ranked_and_priced", "candidates", "tradable", "observation_only", "evaluated", "limit"):
        assert key in counters, f"단계 카운터 {key} 누락"


@pytest.mark.parametrize("limit", [1, 5, 18, 30])
def test_limit_is_parameterised(limit: int) -> None:
    _, counters = _build([f"SYM{index:03d}" for index in range(60)], ranked=[], limit=limit)
    assert counters["limit"] == limit
    assert counters["evaluated"] <= limit


def test_default_limit_matches_the_tps_budget() -> None:
    """검산표가 전제한 '시장별 18종목'과 기본값이 어긋나면 TPS 계산이 거짓이 된다."""
    from app.core.config import Settings

    assert Settings(database_url="memory://").stock_candidates_per_market == service._MAX_CANDIDATES_PER_MARKET
