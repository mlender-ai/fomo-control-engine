"""WO-FCE-RISK-SIZING-01 Phase 4-1 — 슬리피지 **관측**.

## 왜

`policy.slippage_pct = 0.03` 은 **근거가 없다.** 어디서 온 값인지 문서에 없고 체결 기록과
대조된 적도 없다. 그런데 Phase 3-5 의 민감도표대로 이 값 하나가 엔진의 부호를 가른다:

    편도 0.090% (현행)  → netR −0.995  PF 0.9109
    편도 0.060% (슬립 0) → netR −0.013  PF 0.9988

**흑자 여부가 미검증 상수 하나에 달려 있다.** 낮추면 흑자가 나오는 상황이므로 관측 없이
값을 만지는 것은 조작이다(C7). 이 모듈은 **값을 바꾸지 않는다** — 측정 경로만 연다.

## 실주문 없이 측정하는 방법

봉인 때문에 실계좌 체결 기록은 없다(C12). 그러나 슬리피지는 체결 후에만 알 수 있는 것이
아니다 — **결정 시점의 호가 깊이에 명목가를 얹으면 예상 체결가가 나온다.** 시장가 주문은
호가를 위에서부터 먹으므로 가중평균 체결가(VWAP)와 중간가의 차이가 곧 예상 슬리피지다.

    예상 슬리피지% = |VWAP − 중간가| / 중간가 × 100

이것은 추정이지만 **자의적 가정이 아니라 관측된 호가창의 산술**이다. 0.03% 와 달리 출처가
있고 재검증이 가능하다.

## 결정 시점에 저장해야 한다

호가는 사후에 재구성할 수 없다. 크립토 봉조차 보존하지 않아 Phase 2 가 갭/종가규칙 분리를
포기했고(`STOP_EXECUTION.md` §3), 와이코프 국면 미저장으로 `POSITION-VIEW-01` 이 같은 벽에
막혔다. **같은 결함을 세 번째로 반복하지 않기 위해** 결정 시점 스냅샷을 남긴다.

## 대체 기준은 결과 전에 정한다

`ASSUMPTION_REPLACEMENT`(아래)가 그 기준이다. 표본이 차기 전에는 0.03% 를 유지한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence


# 관측이 가정을 대체할 수 있는 조건. **결과를 보기 전에 정한다** (4-1 작업 5).
#
# 왜 이 숫자들인가:
#   min_observations 30 — 이항/평균 추정에서 관례적 최소이며, `REQUIRED_SAMPLE.md` 의 승격
#     기준(N≥30)과 같은 값을 쓴다. 새 기준을 발명하지 않는다.
#   min_symbols 3 — 한 심볼의 유동성이 전체를 대표하지 않는다. 실측 표본은 BTC·ETH 같은
#     대형과 SPCX·BASED 같은 저유동성이 섞여 있고, 슬리피지는 후자에서 크다.
#   판정 방법 — 관측 분포의 **상위 백분위**(보수적 방향)를 쓴다. 평균을 쓰면 꼬리에서
#     체계적으로 과소평가된다. 갭 1건이 초과분의 56%를 만든 Phase 2 와 같은 교훈이다.
#   교체 방향 제약 — 관측 백분위가 0.03% 보다 **낮을 때만** 교체를 제안할 수 있고, 그
#     제안은 사용자 결정을 거친다. 자동 적용 경로를 만들지 않는다(도메인 불변: 자동 승격 금지).
ASSUMPTION_REPLACEMENT = {
    "min_observations": 30,
    "min_distinct_symbols": 3,
    "statistic": "p80",
    "rationale_doc": "docs/validation/EXECUTION_MODEL.md",
    "auto_apply": False,
}

# 이 명목가에서의 슬리피지를 낸다. Phase 1 사이징의 실측 명목 범위(50~308)를 덮고,
# 상한(600)까지 함께 재 저유동성 심볼에서 깊이가 마르는지 본다.
PROBE_NOTIONALS_USDT: tuple[float, ...] = (100.0, 300.0, 600.0)


@dataclass(frozen=True)
class BookWalk:
    """명목가를 호가창에 얹은 결과."""

    notional_usdt: float
    filled_notional_usdt: float
    vwap: float | None
    slippage_pct: float | None
    levels_consumed: int
    depth_exhausted: bool

    @property
    def fully_absorbed(self) -> bool:
        return not self.depth_exhausted and self.vwap is not None


def walk_book(levels: Sequence[Sequence[float]], *, notional_usdt: float, mid_price: float, side: str) -> BookWalk:
    """호가를 위에서부터 먹어 가중평균 체결가를 낸다.

    `levels` 는 `[[가격, 잔량], ...]` 이며 유리한 가격부터 정렬돼 있어야 한다(거래소 응답 순서).
    `side` 는 **주문 방향**이다 — `buy` 는 매도호가(asks)를, `sell` 은 매수호가(bids)를 먹는다.

    깊이가 모자라면 `depth_exhausted=True` 로 남기고 **채운 만큼의 VWAP 을 돌려준다.**
    모자란 사실을 숨기고 평균만 내면 저유동성 심볼의 슬리피지가 과소평가된다.
    """
    if notional_usdt <= 0 or mid_price <= 0:
        return BookWalk(notional_usdt, 0.0, None, None, 0, True)

    remaining = notional_usdt
    quantity = 0.0
    spent = 0.0
    consumed = 0
    for level in levels:
        if remaining <= 0:
            break
        price = _positive(level[0] if len(level) > 0 else None)
        size = _positive(level[1] if len(level) > 1 else None)
        if price is None or size is None:
            continue
        available = price * size
        take = min(available, remaining)
        quantity += take / price
        spent += take
        remaining -= take
        consumed += 1

    if quantity <= 0:
        return BookWalk(notional_usdt, 0.0, None, None, consumed, True)

    vwap = spent / quantity
    raw = (vwap - mid_price) if side == "buy" else (mid_price - vwap)
    return BookWalk(
        notional_usdt=notional_usdt,
        filled_notional_usdt=spent,
        vwap=vwap,
        # 불리한 방향을 양수로 낸다. 음수가 나오면(중간가보다 유리) 0 으로 깎지 않고 그대로 남긴다.
        slippage_pct=raw / mid_price * 100.0,
        levels_consumed=consumed,
        depth_exhausted=remaining > 1e-9,
    )


@dataclass(frozen=True)
class DepthObservation:
    """결정 시점 호가 깊이 한 건. 원장에 그대로 저장된다."""

    symbol: str
    timeframe: str
    decision: str
    observed_at: datetime
    direction: str | None
    trade_id: str | None
    best_bid: float | None
    best_ask: float | None
    best_bid_size: float | None
    best_ask_size: float | None
    mid_price: float | None
    spread_pct: float | None
    half_spread_pct: float | None
    walks: dict[str, BookWalk]
    assumed_slippage_pct: float
    unavailable_reason: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "decision": self.decision,
            "observed_at": self.observed_at.isoformat(),
            "direction": self.direction,
            "trade_id": self.trade_id,
            "best_bid": self.best_bid,
            "best_ask": self.best_ask,
            "best_bid_size": self.best_bid_size,
            "best_ask_size": self.best_ask_size,
            "mid_price": self.mid_price,
            "spread_pct": self.spread_pct,
            "half_spread_pct": self.half_spread_pct,
            # 관측치와 가정값을 **나란히** 둔다. 대체하지 않는다 (C7).
            "observed_slippage_pct": {key: (walk.slippage_pct if walk.vwap is not None else None) for key, walk in sorted(self.walks.items())},
            "depth_exhausted": {key: walk.depth_exhausted for key, walk in sorted(self.walks.items())},
            "levels_consumed": {key: walk.levels_consumed for key, walk in sorted(self.walks.items())},
            "assumed_slippage_pct": self.assumed_slippage_pct,
            "assumption_replacement_criteria": dict(ASSUMPTION_REPLACEMENT),
            "unavailable_reason": self.unavailable_reason,
        }


def observe_depth(
    *,
    symbol: str,
    timeframe: str,
    decision: str,
    observed_at: datetime,
    book: dict[str, Any] | None,
    assumed_slippage_pct: float,
    direction: str | None = None,
    trade_id: str | None = None,
    notionals: Iterable[float] = PROBE_NOTIONALS_USDT,
) -> DepthObservation:
    """호가 응답에서 관측 한 건을 만든다.

    `book` 이 없거나 비면 **사유를 담은 관측을 남긴다** — 조용히 건너뛰지 않는다(침묵 금지).
    깊이를 못 얻은 사실 자체가 나중에 표본 충분성 판정에 필요한 정보다.
    """
    empty = DepthObservation(
        symbol=symbol.upper(),
        timeframe=timeframe,
        decision=decision,
        observed_at=observed_at,
        direction=direction,
        trade_id=trade_id,
        best_bid=None,
        best_ask=None,
        best_bid_size=None,
        best_ask_size=None,
        mid_price=None,
        spread_pct=None,
        half_spread_pct=None,
        walks={},
        assumed_slippage_pct=assumed_slippage_pct,
    )
    if not book:
        return _with_reason(empty, "depth_unavailable")

    bids = _levels(book.get("bids"))
    asks = _levels(book.get("asks"))
    if not bids or not asks:
        return _with_reason(empty, "depth_side_missing")

    best_bid, best_bid_size = bids[0][0], bids[0][1]
    best_ask, best_ask_size = asks[0][0], asks[0][1]
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return _with_reason(empty, "depth_crossed_or_invalid")

    mid = (best_bid + best_ask) / 2.0
    spread_pct = (best_ask - best_bid) / mid * 100.0

    walks: dict[str, BookWalk] = {}
    for notional in notionals:
        walks[f"buy@{notional:g}"] = walk_book(asks, notional_usdt=notional, mid_price=mid, side="buy")
        walks[f"sell@{notional:g}"] = walk_book(bids, notional_usdt=notional, mid_price=mid, side="sell")

    return DepthObservation(
        symbol=symbol.upper(),
        timeframe=timeframe,
        decision=decision,
        observed_at=observed_at,
        direction=direction,
        trade_id=trade_id,
        best_bid=best_bid,
        best_ask=best_ask,
        best_bid_size=best_bid_size,
        best_ask_size=best_ask_size,
        mid_price=mid,
        spread_pct=spread_pct,
        half_spread_pct=spread_pct / 2.0,
        walks=walks,
        assumed_slippage_pct=assumed_slippage_pct,
    )


def summarize_observations(payloads: Sequence[dict[str, Any]], *, key: str, assumed_slippage_pct: float) -> dict[str, Any]:
    """관측 표본이 가정을 대체할 조건을 만족했는지 **판정만** 한다 (4-1 작업 5).

    교체를 실행하지 않는다 — `auto_apply=False` 이며 사용자 결정 경로를 거친다.
    """
    values = [
        value
        for payload in payloads
        if isinstance(value := (payload.get("observed_slippage_pct") or {}).get(key), (int, float)) and not isinstance(value, bool)
    ]
    symbols = {str(payload.get("symbol") or "") for payload in payloads if (payload.get("observed_slippage_pct") or {}).get(key) is not None}
    exhausted = sum(1 for payload in payloads if (payload.get("depth_exhausted") or {}).get(key) is True)

    statistic = _percentile(values, 80) if values else None
    enough = len(values) >= int(ASSUMPTION_REPLACEMENT["min_observations"]) and len(symbols) >= int(ASSUMPTION_REPLACEMENT["min_distinct_symbols"])
    return {
        "probe": key,
        "observations": len(values),
        "distinct_symbols": len(symbols),
        "depth_exhausted_count": exhausted,
        "mean_pct": (sum(values) / len(values)) if values else None,
        "median_pct": _percentile(values, 50) if values else None,
        "p80_pct": statistic,
        "max_pct": max(values) if values else None,
        "assumed_pct": assumed_slippage_pct,
        "sample_sufficient": enough,
        # 표본이 차기 전에는 방향을 말하지 않는다 (C10).
        "verdict": _verdict(enough, statistic, assumed_slippage_pct),
        "criteria": dict(ASSUMPTION_REPLACEMENT),
    }


def _verdict(enough: bool, statistic: float | None, assumed: float) -> str:
    if not enough or statistic is None:
        return "sample_insufficient"
    if statistic < assumed:
        return "observation_below_assumption:replacement_eligible_pending_user_decision"
    return "observation_at_or_above_assumption:keep_assumption"


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile / 100.0
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def _levels(raw: Any) -> list[tuple[float, float]]:
    if not isinstance(raw, (list, tuple)):
        return []
    parsed: list[tuple[float, float]] = []
    for level in raw:
        if not isinstance(level, (list, tuple)) or len(level) < 2:
            continue
        price = _positive(level[0])
        size = _positive(level[1])
        if price is None or size is None:
            continue
        parsed.append((price, size))
    return parsed


def _positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    return number if number > 0 else None


def _with_reason(observation: DepthObservation, reason: str) -> DepthObservation:
    return DepthObservation(
        symbol=observation.symbol,
        timeframe=observation.timeframe,
        decision=observation.decision,
        observed_at=observation.observed_at,
        direction=observation.direction,
        trade_id=observation.trade_id,
        best_bid=None,
        best_ask=None,
        best_bid_size=None,
        best_ask_size=None,
        mid_price=None,
        spread_pct=None,
        half_spread_pct=None,
        walks={},
        assumed_slippage_pct=observation.assumed_slippage_pct,
        unavailable_reason=reason,
    )
