"""WO-FCE-WHALE-FOLLOW-02 7-2 — 고래 추종 페이퍼 트랙 (이벤트 구동 진입).

## 규칙 한 줄

> **승률 좋은 고래가 들어간 가격 근처에서 같이 들어간다.**

자격은 `onchain/follow_eligibility.py` 가 정한다(N>=30 · 승률>=55% · MM 아님). 이 모듈이
정하는 것은 **언제·얼마에 들어가는가**다.

## Phase 6 은 4시간 늦게 들어갔다 (문제 1)

```python
# 이전
bar = _confirmed_bar(...)          # 마지막 **확정** 4시간봉
open_trade(bar=bar)                # 진입가 = 그 봉의 종가
```

확정봉 종가는 최대 4시간 묵은 가격이고 고래 체결 시각과 아무 관계가 없다. 실측 지연이
**4.0시간**이었다. 그건 추종이 아니다 — 고래가 들어간 자리가 아니라 **그 후 4시간 동안
무슨 일이 있었든 상관없는 자리**에 들어간다.

그리고 `latency_seconds` 를 **기록만 하고 거부하지 않았다.** 상한이 없는 관측치는
관측치가 아니라 변명이다.

## 무엇을 바꿨나

| | Phase 6 | 지금 |
| --- | --- | --- |
| 진입 시각 | 봉 마감 대기 | **고래 체결 감지 즉시** |
| 진입 가격 | 마지막 확정봉 종가 | **현재가**(진행 중 봉의 종가 = 최종 체결가) |
| 지연 | 기록만 | **상한 초과면 거부** |
| 가격 이탈 | 없음 | **무효화 거리의 N% 초과면 거부** |

`FOLLOW_TIMEFRAME` 은 이제 **분석·무효화선 산출용으로만** 쓴다. 진입 시각과 가격은
거기서 오지 않는다.

## 왜 이탈 상한이 지연 상한보다 본질적인가

**5분이어도 3% 갔으면 다른 거래다.** 지연은 이탈의 대리 지표일 뿐이고, 우리가 실제로
신경 쓰는 것은 "고래가 잡은 가격을 우리도 잡았는가"다. 그래서 두 상한을 함께 걸되
이탈을 **무효화 거리 대비**로 잰다 — 절대 %로 재면 변동성이 다른 심볼에서 같은 값이
다른 것을 뜻하게 된다.

이탈은 **불리한 방향만** 잰다. 롱인데 값이 내렸으면 고래보다 싸게 잡은 것이고, 그것은
막을 이유가 없다. (스톱이 가까워지는 부작용은 `invalidation_hygiene` 이 잡는다.)

## 사이징·잠금·출구는 손대지 않는다 (C4)

`paper/policy.py` diff 0줄이다. 현재가는 **`plan_position_size` 의 입력**으로 들어갈 뿐,
사이징 식은 그대로다. 재진입 잠금의 기준 봉(`entry_bar_at`)도 확정봉 그대로 둔다 —
그것을 현재 시각으로 바꾸면 잠금 의미가 달라진다.

## 무효화선이 없으면 진입하지 않는다 (C6)

고래 체결에는 스톱이 없다. 구조 레벨에서 산출하되 **산출 불가면 진입하지 않는다** —
사이징이 스톱 거리를 요구하므로 무효화선 없는 진입은 사이징 불가와 같다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.db.models import Direction, MarketCandle, PaperTrade, utc_now
from app.onchain.follow_eligibility import QUALIFICATION_FOLLOW
from app.paper import policy as paper_policy
from app.paper import service as paper_service

# 진입 트리거가 되는 체결. 감액·청산은 진입 신호가 아니다 — 별도 판단이다.
ENTRY_EVENTS = frozenset({"open", "increase", "flip"})

# ── 지연·이탈 상한 (7-2 항목 3·4 · 권고 시작값) ─────────────────────────

# 고래 체결 → 진입까지 이 시간을 넘기면 **거부한다**. 기록만 하지 않는다.
DEFAULT_MAX_LATENCY_MINUTES = 30
# 고래 체결가 대비 불리한 이탈 상한. **무효화 거리 대비 비율**이다 —
# 스톱이 3% 면 0.75% 이탈까지 허용한다. 절대 %로 재면 심볼마다 뜻이 달라진다.
DEFAULT_MAX_DRIFT_PCT_OF_STOP = 25.0
# 신호 목록을 만들 때의 조회 지평. 상한의 배수로 둔다 — 상한을 조정하면 함께 움직인다.
# 이보다 오래된 체결은 거부 사유를 남길 가치도 없다(이미 죽은 신호다).
SIGNAL_SCAN_MULTIPLIER = 4

# 한 실행에서 여는 최대 건수. 크립토 트랙 실행을 밀어내지 않는다(C9).
MAX_ENTRIES_PER_RUN = 2
# 한 실행에서 **분석을 조회하는** 최대 건수. 진입 상한과 별개다.
#
# 진입 상한만 두면 신호가 전부 거부될 때 분석 조회가 무제한이 된다. 심볼당 ~30초이므로
# 그것이 곧 잡 타임아웃이고, 실제로 DISCOVERY-UNBLOCK-01 이 같은 기전으로 라이브 장애를
# 냈다(유니버스 3→15 확대 × 30초). 조회 자체를 세서 막는다.
MAX_EVALUATIONS_PER_RUN = 3
# 출구 판정도 같은 이유로 상한을 둔다. 열린 포지션 수에 비례해 커지면 안 된다.
MAX_EXIT_EVALUATIONS_PER_RUN = 6
FOLLOW_TIMEFRAME = "4h"

# ── 거부 사유 코드 (7-2 항목 5 · 7-4 항목 5) ────────────────────────────
#
# 사유를 문자열로만 남기면 분포를 세지 못한다. 코드로 남겨야 "무엇이 걸렀는가"가 나온다.
REASON_LATENCY = "latency_exceeded"
REASON_DRIFT = "price_drift_exceeded"
REASON_WHALE_PRICE_UNKNOWN = "whale_price_unknown"
REASON_NO_INVALIDATION = "no_invalidation"
REASON_NO_PRICE = "no_price"
REASON_SAFETY_GATE = "safety_gate"
REASON_REENTRY_LOCK = "reentry_lock"
REASON_ALREADY_OPEN = "already_open"
REASON_ENTRY_CAP = "entry_cap"
REASON_EVALUATION_CAP = "evaluation_cap"
REASON_ERROR = "error"

REASON_CODES = (
    REASON_LATENCY,
    REASON_DRIFT,
    REASON_WHALE_PRICE_UNKNOWN,
    REASON_NO_INVALIDATION,
    REASON_NO_PRICE,
    REASON_SAFETY_GATE,
    REASON_REENTRY_LOCK,
    REASON_ALREADY_OPEN,
    REASON_ENTRY_CAP,
    REASON_EVALUATION_CAP,
    REASON_ERROR,
)


def _direction(side: str) -> Direction | None:
    value = str(side or "").lower()
    if value == "long":
        return Direction.long
    if value == "short":
        return Direction.short
    return None


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # NaN 제외


def entry_signals(
    events: list[Any],
    *,
    eligible: dict[str, str],
    now: datetime,
    scan_horizon_minutes: int,
) -> list[dict[str, Any]]:
    """추종 대상 지갑의 증액 체결만 남긴다. 지갑당 심볼당 최신 1건.

    **여기서는 지연 상한을 걸지 않는다.** `scan_horizon_minutes` 는 목록을 유한하게 만드는
    조회 지평일 뿐이고, 지연 상한은 `run_entries` 가 **거부로** 적용한다 — 그래야 "몇 건이
    지연으로 잘렸는가"가 세어진다. 조용히 거르면 그 수가 사라진다(C10).
    """
    cutoff = now - timedelta(minutes=max(1, int(scan_horizon_minutes)))
    keyed: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        address = str(getattr(event, "wallet_address", "") or "").lower()
        qualification = eligible.get(address)
        if qualification is None:
            continue
        if str(getattr(event, "event", "") or "").lower() not in ENTRY_EVENTS:
            continue
        direction = _direction(str(getattr(event, "side", "") or ""))
        event_at = getattr(event, "event_at", None)
        symbol = str(getattr(event, "symbol", "") or "").upper()
        if direction is None or event_at is None or not symbol or event_at < cutoff:
            continue
        key = (address, symbol)
        existing = keyed.get(key)
        if existing is None or event_at > existing["event_at"]:
            keyed[key] = {
                "address": address,
                "qualification": qualification,
                "symbol": symbol,
                "direction": direction,
                "event_at": event_at,
                "event": str(getattr(event, "event", "")),
                "size_usd": float(getattr(event, "size_usd", 0.0) or 0.0),
                "wallet_label": str(getattr(event, "wallet_label", "") or ""),
                # 고래가 **실제로 체결한 가격**. 이탈 상한의 기준선이다(7-2 항목 4).
                # `event_from_fill` 이 체결 `px` 를 그대로 넣는다.
                "whale_price": _float(getattr(event, "entry_px", None)),
                "signal_age_seconds": max(0.0, (now - event_at).total_seconds()),
            }
    # 최신 신호 우선. 지연이 곧 이 트랙의 성패이므로 큰 금액보다 **덜 늙은 것**을 먼저 본다.
    return sorted(keyed.values(), key=lambda item: item["event_at"], reverse=True)


# 현재가로 인정하는 필드. 앞에서부터 찾는다. **캔들은 여기 없다** — 아래 이유 참조.
LIVE_PRICE_FIELDS = ("mark_price", "reference_price", "mark")


def live_price(analysis: dict[str, Any], *, as_of: Any = None) -> tuple[float | None, datetime | None]:
    """**현재가.** 제공자 마크가로 읽는다.

    ## 캔들 마지막 봉을 쓰면 안 된다 — 실측이 그것을 잡았다

    처음 구현은 `analysis["candles"][-1]["close"]` 를 "진행 중 봉의 종가 = 마지막 체결가"로
    보고 썼다. **이 저장소에서는 그것이 성립하지 않는다.** `MTF-PATTERN-01` 이 미확정 진행
    봉을 분석 입력에서 의도적으로 제거했으므로 `candles` 는 **확정봉만** 담는다.

    2026-08-25T14:06Z 실측 (BTCUSDT 4h):

    | 값 | 가격 | 나이 |
    | --- | --- | --- |
    | `candles[-1].close` (= `_confirmed_bar`) | 79,090.8 | **6시간** |
    | `mark_price` | 78,817.4 | 2초 (`payload.as_of`) |
    | 고래 체결가 (14:02) | 78,736.7 | — |

    캔들 경로는 확정봉 종가와 **같은 값**을 냈다. 즉 7-2 의 "현재가 진입"이 실데이터에서는
    작동하지 않았고, 고래 체결가 대비 괴리가 354 → 102 로 줄어드는 몫을 잃고 있었다.
    합성 입력에는 진행 봉을 넣어줬기 때문에 테스트가 통과했다.

    ## 캔들로 폴백하지 않는다

    마크가가 없으면 `None` 을 돌려 진입을 포기한다. 캔들로 되돌아가면 결함이 조용히
    되살아나고, 호출부의 "봉 마감 종가로 대신하지 않는다"가 거짓이 된다.

    함께 돌려주는 시각은 분석 payload 의 `as_of` 다 — 가격이 얼마나 낡았는지를 재는 값이다.
    """
    price: float | None = None
    for field in LIVE_PRICE_FIELDS:
        price = _float(analysis.get(field))
        if price and price > 0:
            break
        price = None
    if price is None:
        levels = analysis.get("price_levels")
        if isinstance(levels, dict):
            price = _float(levels.get("mark"))
        if not price or price <= 0:
            liquidity = analysis.get("liquidity")
            price = _float(liquidity.get("reference_price")) if isinstance(liquidity, dict) else None
    if not price or price <= 0:
        return None, None
    return price, paper_service._timestamp(as_of)


def price_drift(*, whale_price: float, entry_price: float, direction: Direction, stop_distance: float) -> dict[str, Any]:
    """고래 체결가 대비 **불리한** 이탈 (7-2 항목 4).

    롱은 값이 올랐을 때, 숏은 내렸을 때가 불리하다 — 고래보다 나쁜 값에 잡는 것이다.
    유리한 이탈은 0 으로 본다: 고래보다 싸게 잡은 것을 막을 이유가 없다.

    비율은 **무효화 거리 대비**다. 절대 %로 재면 변동성이 다른 심볼에서 같은 숫자가 다른
    것을 뜻한다 — 스톱 1% 짜리의 0.5% 이탈과 스톱 8% 짜리의 0.5% 이탈은 다른 사건이다.
    """
    raw = entry_price - whale_price if direction == Direction.long else whale_price - entry_price
    adverse = max(0.0, raw)
    return {
        "whale_price": whale_price,
        "entry_price": entry_price,
        "adverse_abs": round(adverse, 10),
        "adverse_pct": round(adverse / whale_price * 100, 4) if whale_price else None,
        "stop_distance": round(stop_distance, 10),
        "pct_of_stop": round(adverse / stop_distance * 100, 2) if stop_distance > 0 else None,
        "favorable": raw < 0,
    }


def _safety_gates(
    *,
    bar: MarketCandle,
    timeframe: str,
    now: datetime,
    invalidation: float | None,
    take_profit: float | None,
    simulation: dict[str, Any],
    target_plan: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, bool]:
    """안전 게이트만. 방향 판단 게이트는 여기 없다 — 그것이 이 트랙의 설계다."""
    return {
        "freshness": paper_service._data_fresh(bar, timeframe, now),
        "action_levels": invalidation is not None and take_profit is not None,
        "invalidation_hygiene": simulation.get("invalidation_too_close") is not True and target_plan.get("execution_invalidation_too_close") is not True,
        "liquidation_safety": simulation.get("survives_to_invalidation") is True,
        "event_window": paper_service._earnings_clear(analysis),
    }


def evaluate_signal(
    repo: Any,
    settings: Any,
    signal: dict[str, Any],
    *,
    analysis_loader: paper_service.AnalysisLoader,
    simulation_loader: paper_service.SimulationLoader,
    now: datetime,
    timeframe: str = FOLLOW_TIMEFRAME,
    max_drift_pct_of_stop: float = DEFAULT_MAX_DRIFT_PCT_OF_STOP,
) -> dict[str, Any]:
    """한 신호를 진입 후보로 판정한다. 거부도 사유 **코드**를 남긴다(C10)."""
    symbol = str(signal["symbol"])
    direction: Direction = signal["direction"]
    rejected = {"signal": signal, "opened": False}

    payload = analysis_loader(symbol, timeframe)
    analysis = paper_service._dict(payload.get("analysis"))
    gauges = paper_service._dict(payload.get("gauges"))
    # 확정봉은 **분석·무효화선 산출**에만 쓴다. 진입 가격은 여기서 오지 않는다(7-2 항목 1).
    bar = paper_service._confirmed_bar(analysis, gauges)
    if bar is None:
        return {**rejected, "reason_code": REASON_NO_PRICE, "reason": "확정 봉 없음 — 구조 분석 근거를 만들 수 없다"}

    entry_price, price_at = live_price(analysis, as_of=payload.get("as_of"))
    if entry_price is None:
        return {**rejected, "reason_code": REASON_NO_PRICE, "reason": "현재가를 읽을 수 없다 — 봉 마감 종가로 대신하지 않는다"}

    asset_class = str(analysis.get("asset_class") or "crypto")
    policy = paper_service.policy_from_settings(settings, asset_class)

    # 고래 **방향으로** 시뮬레이션을 조회한다. 엔진 스탠스가 반대여도 그것을 묻지 않는다.
    # 진입가가 현재가이므로 시뮬레이션도 현재가 기준으로 건다.
    simulation = simulation_loader(symbol, timeframe, direction.value, entry_price)
    action_plan = paper_service._dict(simulation.get("action_plan"))
    invalidation = paper_service._price_from(action_plan.get("invalidation") or action_plan.get("engine_invalidation"))
    target_plan = paper_service._paper_target_plan(
        analysis,
        gauges,
        bar=bar,
        direction=direction,
        invalidation_price=invalidation,
        action_plan=action_plan,
        policy=policy,
    )
    invalidation = paper_service._float(target_plan.get("execution_invalidation"))
    take_profit = paper_service._float(target_plan.get("take_profit_1"))
    simulation = paper_service._paper_simulation_contract(simulation, target_plan)

    if invalidation is None:
        # C6 — 무효화선 없으면 진입하지 않는다. 사이징이 스톱 거리를 요구한다.
        return {**rejected, "reason_code": REASON_NO_INVALIDATION, "reason": "무효화선 산출 불가 — 사이징이 스톱 거리를 요구하므로 진입하지 않는다"}

    # ── 가격 이탈 상한 (7-2 항목 4) ────────────────────────────────────
    whale_price = _float(signal.get("whale_price"))
    if whale_price is None or whale_price <= 0:
        # 상한을 걸 수 없으면 진입하지 않는다. "상한 없이 진입"이 지금 상태이고 그것이 결함이다.
        return {**rejected, "reason_code": REASON_WHALE_PRICE_UNKNOWN, "reason": "고래 체결가 미상 — 이탈 상한을 걸 수 없으므로 진입하지 않는다"}
    stop_distance = abs(entry_price - invalidation)
    if stop_distance <= 0:
        return {**rejected, "reason_code": REASON_NO_INVALIDATION, "reason": "무효화선이 진입가와 같다 — 스톱 거리가 0 이다"}
    drift = price_drift(whale_price=whale_price, entry_price=entry_price, direction=direction, stop_distance=stop_distance)
    cap = max(0.0, float(max_drift_pct_of_stop))
    if (drift["pct_of_stop"] or 0.0) > cap:
        return {
            **rejected,
            "reason_code": REASON_DRIFT,
            "reason": f"가격 이탈 {drift['pct_of_stop']}% (무효화 거리 대비) — 상한 {cap}% 초과. 고래가 잡은 자리가 아니다",
            "drift": drift,
        }

    gates = _safety_gates(
        bar=bar,
        timeframe=timeframe,
        now=now,
        invalidation=invalidation,
        take_profit=take_profit,
        simulation=simulation,
        target_plan=target_plan,
        analysis=analysis,
    )
    failed = [name for name, passed in gates.items() if not passed]
    if failed:
        return {**rejected, "reason_code": REASON_SAFETY_GATE, "reason": f"안전 게이트 미통과: {', '.join(failed)}", "gates": gates, "drift": drift}

    lock = paper_service._reentry_block_reason(repo, symbol=symbol, timeframe=timeframe, bar=bar, direction=direction, policy=policy)
    if lock is not None:
        return {**rejected, "reason_code": REASON_REENTRY_LOCK, "reason": f"재진입 잠금: {lock}", "gates": gates, "drift": drift}

    if any(trade.symbol == symbol and trade.timeframe == timeframe for trade in repo.list_whale_follow_trades(status="open", limit=200)):
        return {**rejected, "reason_code": REASON_ALREADY_OPEN, "reason": "이 심볼에 추종 트랙 포지션이 이미 열려 있다", "gates": gates, "drift": drift}

    return {
        "signal": signal,
        "opened": True,
        "reason": "지연·이탈 상한 통과 · 안전 게이트 통과 · 무효화선 확보",
        "gates": gates,
        "drift": drift,
        "bar": bar,
        "entry_price": entry_price,
        "price_at": price_at,
        "analysis": analysis,
        "asset_class": asset_class,
        "policy": policy,
        "invalidation": invalidation,
        "take_profit": take_profit,
        "take_profit_2": paper_service._float(target_plan.get("take_profit_2")),
        "target_plan": target_plan,
        "simulation": simulation,
        "entry_atr": paper_service._float(target_plan.get("atr")),
    }


def open_follow_trade(candidate: dict[str, Any], *, now: datetime) -> PaperTrade:
    """`policy.open_trade` 를 그대로 호출한다 — 신규 사이징 구현 0건(C4).

    ## 진입가를 현재가로 넣는 방법

    `policy.open_trade` 는 `bar.close` 를 진입가로 쓰고 `bar.timestamp` 를 진입 봉으로 쓴다.
    그래서 **종가만 현재가로 바꾼 봉**을 만들어 넘긴다:

    - `entry_price` = 현재가 → 사이징도 현재가 기준으로 계산된다
    - `entry_bar_at` = 확정봉 시각 그대로 → 재진입 잠금·출구 판정 의미가 바뀌지 않는다(C4)
    - `entry_at` = **지금** → 실제 진입 시각. 알림 상한과 지연 집계가 이 값을 읽는다

    `paper/policy.py` 는 한 줄도 바뀌지 않는다. 입력만 바꾼다.
    """
    signal = candidate["signal"]
    bar: MarketCandle = candidate["bar"]
    entry_price = float(candidate["entry_price"])
    # 종가만 현재가로 교체한다. 고가·저가는 현재가를 포함하도록 넓혀 봉이 자기모순이 되지 않게 한다.
    entry_bar = bar.model_copy(update={"close": entry_price, "high": max(bar.high, entry_price), "low": min(bar.low, entry_price)})

    # 체결→진입 지연. **엔진이 판단한 벽시계 기준**이다.
    #
    # 봉 타임스탬프로 재면 안 된다 — 확정봉의 timestamp 는 봉이 *열린* 시각이고 고래 체결보다
    # 앞설 수 있어 음수가 나온다. 그것을 0 으로 누르면 "지연 없음"이라는 거짓이 기록된다
    # (Phase 6 실측에서 실제로 0.0초가 찍혔다).
    latency_seconds = (now - signal["event_at"]).total_seconds()
    # 진입가의 실제 나이다. 기준 시각이 분석 payload 의 `as_of`(조회 시각)이므로
    # 마크가가 얼마나 묵었는지를 그대로 나타낸다 — 실측 2~3초.
    #
    # 이전 구현은 이 값이 **봉이 열린 시각** 기준이라 4시간봉에서 최대 4시간까지 찍혔고,
    # 이름도 `price_bar_age_seconds` 였다. 그때는 "가격의 나이가 아니다"라는 주석으로
    # 오해를 막았는데, 지금은 정말 가격의 나이라서 이름을 뜻에 맞춘다. 7-4 가 상한
    # 적정성을 판정할 때 읽는 값이므로 뜻이 흔들리면 판정이 흔들린다.
    price_at = candidate.get("price_at")
    price_age = (now - price_at).total_seconds() if isinstance(price_at, datetime) else None
    drift = candidate.get("drift") or {}
    trade = paper_policy.open_trade(
        trade_id=uuid5(NAMESPACE_URL, f"fce:whale-follow:{signal['address']}:{signal['symbol']}:{bar.timestamp.isoformat()}:{signal['event_at'].isoformat()}"),
        symbol=str(signal["symbol"]),
        timeframe=str(candidate.get("timeframe") or FOLLOW_TIMEFRAME),
        asset_class=str(candidate["asset_class"]),
        direction=signal["direction"],
        bar=entry_bar,
        invalidation_price=float(candidate["invalidation"]),
        take_profit_price=float(candidate["take_profit"]),
        take_profit_2_price=candidate.get("take_profit_2"),
        entry_atr=candidate.get("entry_atr"),
        target_plan=candidate.get("target_plan"),
        policy=candidate["policy"],
        evidence={
            "entry_mode": "whale_follow",
            "track": "whale_follow",
            "qualification": QUALIFICATION_FOLLOW,
            # C8 — 미검증임을 원장에 명시한다. 화면·알림이 이 값을 그대로 읽는다.
            "unverified": True,
            "label": "미검증 추종 자격 진입",
            "whale_address": signal["address"],
            "whale_label": signal.get("wallet_label"),
            "whale_event": signal.get("event"),
            "whale_size_usd": signal.get("size_usd"),
            "whale_event_at": signal["event_at"].isoformat(),
            "whale_price": drift.get("whale_price"),
            "entry_bar_at": bar.timestamp.isoformat(),
            # 7-2 항목 3·4 — 상한을 통과했다는 사실과 그 값이 함께 남는다.
            "signal_to_entry_seconds": latency_seconds,
            "price_drift_pct_of_stop": drift.get("pct_of_stop"),
            "price_drift_pct": drift.get("adverse_pct"),
            "price_drift_favorable": drift.get("favorable"),
            # 진행 중 봉이 열린 지 얼마나 됐는가. **가격의 나이가 아니다**(위 주석).
            "price_age_seconds": price_age,
            "price_as_of": price_at.isoformat() if isinstance(price_at, datetime) else None,
            # 출처를 정확히 적는다. 처음엔 "live_last_trade" 였는데 실제 출처는 제공자
            # 마크가다 — 캔들 마지막 봉을 읽던 구현이 확정봉을 읽고 있었다(실측 확인).
            "entry_price_source": "provider_mark_price",
            "entry_price_note": "진행 중 봉의 종가 = 조회 시점 마지막 체결가. 확정봉 종가가 아니다",
            "win_pct": signal.get("win_pct"),
            "participant_type": signal.get("participant_type"),
            "participant_confidence": signal.get("participant_confidence"),
            "unclassified_flag": signal.get("unclassified_flag"),
            "sample_size": signal.get("sample_size"),
            "ci_low": signal.get("ci_low"),
            "gates": candidate.get("gates"),
            "gate_scope": "안전 게이트만 적용 · 방향 판단 게이트 제외(고래 신호가 방향 판단을 대체한다는 가설)",
            "not_promotion": "추종 자격은 승격이 아니다. 이 트랙 성과를 승격 근거로 쓰지 않는다.",
            "note": "실주문이 아닌 엔진 가상 거래 기록",
            "opened_at": now.isoformat(),
        },
        checklist={
            "entry_mode": "whale_follow",
            "items": candidate.get("simulation", {}).get("checklist") or [],
            "passed": candidate.get("simulation", {}).get("checklist_passed"),
            "total": candidate.get("simulation", {}).get("checklist_total"),
            "note": "체크리스트는 기록만 한다 — 이 트랙의 진입 조건이 아니다",
        },
        stance_snapshot={"source": "whale_follow", "note": "스탠스는 진입 조건이 아니다 — 방향은 고래 체결에서 온다"},
        signature_snapshot={"source": "whale_follow", "signature_gate": "제외(설계)"},
    )
    # 진입 시각은 봉이 아니라 **지금**이다. `entry_bar_at` 은 확정봉 그대로 남는다(C4).
    return trade.model_copy(update={"entry_at": now})


def rejection_summary(rejected: list[dict[str, Any]]) -> dict[str, Any]:
    """거부 사유 **종류별** 분포 (7-2 항목 5 · 7-4 항목 5).

    사유 문자열만 남기면 "무엇이 걸렀는가"를 셀 수 없다. 코드별로 센다.
    """
    counts = {code: 0 for code in REASON_CODES}
    unknown = 0
    for item in rejected:
        code = str(item.get("reason_code") or "")
        if code in counts:
            counts[code] += 1
        else:
            unknown += 1
    return {
        "total": len(rejected),
        "by_reason": {code: value for code, value in counts.items() if value},
        "zero_counts": [code for code, value in counts.items() if not value],
        "uncoded": unknown,
    }


def run_entries(
    repo: Any,
    settings: Any,
    *,
    eligible: dict[str, str],
    analysis_loader: paper_service.AnalysisLoader,
    simulation_loader: paper_service.SimulationLoader,
    signal_context: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
    max_entries: int = MAX_ENTRIES_PER_RUN,
    max_evaluations: int = MAX_EVALUATIONS_PER_RUN,
    max_latency_minutes: int = DEFAULT_MAX_LATENCY_MINUTES,
    max_drift_pct_of_stop: float = DEFAULT_MAX_DRIFT_PCT_OF_STOP,
    event_limit: int = 200,
) -> dict[str, Any]:
    """추종 진입 1회 실행. 거부 사유를 **코드와 함께** 전부 돌려준다(C10).

    판정 순서는 **싼 것부터**다 — 지연 상한은 분석 조회 없이 판정되므로 먼저 건다.
    늙은 신호 때문에 30초짜리 분석 조회를 태우면 그것이 곧 예산 초과다(C9).
    """
    moment = now or utc_now()
    latency_cap = max(1, int(max_latency_minutes))
    if not eligible:
        return {
            "entries": [],
            "opened": 0,
            "rejected": [],
            "rejection_summary": rejection_summary([]),
            "signals": 0,
            "reason": "추종 자격 지갑이 없다 — 기준을 낮추지 않는다(7-1 항목 4)",
            "caps": {"max_latency_minutes": latency_cap, "max_drift_pct_of_stop": float(max_drift_pct_of_stop)},
        }

    events: list[Any] = []
    for address in eligible:
        events.extend(repo.list_whale_events(wallet_address=address, limit=event_limit))
    signals = entry_signals(events, eligible=eligible, now=moment, scan_horizon_minutes=latency_cap * SIGNAL_SCAN_MULTIPLIER)
    for signal in signals:
        signal.update(signal_context.get(str(signal["address"]), {}) if signal_context else {})

    opened: list[PaperTrade] = []
    rejected: list[dict[str, Any]] = []
    latencies: list[float] = []
    evaluations = 0
    for signal in signals:
        base = {"address": signal["address"], "symbol": signal["symbol"], "latency_seconds": round(float(signal["signal_age_seconds"]), 1)}
        # ── 지연 상한 — 분석 조회 **전에** 건다 (7-2 항목 3) ─────────────
        if float(signal["signal_age_seconds"]) > latency_cap * 60:
            rejected.append(
                {
                    **base,
                    "reason_code": REASON_LATENCY,
                    "reason": f"체결→진입 {signal['signal_age_seconds'] / 60:.1f}분 — 상한 {latency_cap}분 초과. 추종이 아니다",
                }
            )
            continue
        if len(opened) >= max(0, int(max_entries)):
            rejected.append({**base, "reason_code": REASON_ENTRY_CAP, "reason": f"진입 상한 {max_entries}건 도달 — 다음 실행으로 넘긴다"})
            continue
        if evaluations >= max(0, int(max_evaluations)):
            # 침묵하지 않는다 — 잘렸다는 사실을 남긴다(C10). 이것이 없으면 "신호가 없었다"로 읽힌다.
            rejected.append(
                {**base, "reason_code": REASON_EVALUATION_CAP, "reason": f"분석 조회 상한 {max_evaluations}건 도달 — 평가하지 않고 다음 실행으로 넘긴다(C9)"}
            )
            continue
        evaluations += 1
        try:
            candidate = evaluate_signal(
                repo,
                settings,
                signal,
                analysis_loader=analysis_loader,
                simulation_loader=simulation_loader,
                now=moment,
                max_drift_pct_of_stop=max_drift_pct_of_stop,
            )
        except Exception as exc:  # 분석 조회 실패가 트랙 전체를 멈추면 안 된다
            rejected.append({**base, "reason_code": REASON_ERROR, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        if not candidate.get("opened"):
            rejected.append(
                {
                    **base,
                    "reason_code": candidate.get("reason_code"),
                    "reason": candidate.get("reason"),
                    "gates": candidate.get("gates"),
                    "drift": candidate.get("drift"),
                }
            )
            continue
        trade = open_follow_trade({**candidate, "timeframe": FOLLOW_TIMEFRAME}, now=moment)
        repo.upsert_whale_follow_trade(trade)
        opened.append(trade)
        latencies.append(float(signal["signal_age_seconds"]))

    return {
        "entries": [
            {
                "id": str(trade.id),
                "symbol": trade.symbol,
                "direction": trade.direction.value,
                "entry_price": trade.entry_price,
                "latency_seconds": (trade.entry_evidence or {}).get("signal_to_entry_seconds"),
                "drift_pct_of_stop": (trade.entry_evidence or {}).get("price_drift_pct_of_stop"),
            }
            for trade in opened
        ],
        "opened": len(opened),
        "rejected": rejected,
        "rejection_summary": rejection_summary(rejected),
        "signals": len(signals),
        "evaluated": evaluations,
        "evaluation_cap": int(max_evaluations),
        "caps": {"max_latency_minutes": latency_cap, "max_drift_pct_of_stop": float(max_drift_pct_of_stop)},
        "entry_latency_seconds": sorted(round(value, 1) for value in latencies),
        "eligible_wallets": len(eligible),
        "track": "whale_follow",
        "ledger": "whale_follow_trades (paper_trades 와 분리 · C2)",
    }


def run_exits(
    repo: Any,
    settings: Any,
    *,
    analysis_loader: paper_service.AnalysisLoader,
    now: datetime | None = None,
    timeframe: str = FOLLOW_TIMEFRAME,
) -> dict[str, Any]:
    """열린 추종 포지션의 출구를 판정한다. `policy.evaluate_exit` 를 그대로 쓴다(C4).

    진입만 되고 청산이 없으면 표본은 0이다. 그래서 출구는 진입과 같은 잡에서 돈다 —
    한쪽만 도는 상태가 생기지 않게.

    **반대 스탠스 청산은 이 트랙에서 작동하지 않는다.** 스탠스를 진입 조건에서 뺐으므로
    청산 조건에도 넣지 않는다 — 그러면 방향 판단이 뒷문으로 들어온다. 빈 스탠스를 넘겨
    `_opposite_confirmed_flip` 가 발화하지 않게 한다. 손절·익절·시간 만료는 그대로다.
    """
    moment = now or utc_now()
    open_trades = repo.list_whale_follow_trades(status="open", limit=200)
    closed: list[dict[str, Any]] = []
    held = 0
    deferred = 0
    errors: list[dict[str, Any]] = []
    for index, trade in enumerate(open_trades):
        if index >= MAX_EXIT_EVALUATIONS_PER_RUN:
            deferred += 1
            continue
        try:
            payload = analysis_loader(trade.symbol, timeframe)
            analysis = paper_service._dict(payload.get("analysis"))
            gauges = paper_service._dict(payload.get("gauges"))
            bar = paper_service._confirmed_bar(analysis, gauges)
            if bar is None or bar.timestamp <= trade.entry_bar_at:
                held += 1
                continue
            policy = paper_service.policy_from_settings(settings, str(analysis.get("asset_class") or "crypto"))
            decision = paper_policy.evaluate_exit(
                trade,
                bar=bar,
                stance_state={},
                take_profit_pressure=None,
                prior_high_pressure_streak=0,
                policy=policy,
            )
            updated = paper_policy.apply_exit_decision(trade, decision=decision, bar=bar, policy=policy)
            repo.upsert_whale_follow_trade(updated)
            if decision.action == "hold":
                held += 1
            else:
                closed.append(
                    {
                        "id": str(updated.id),
                        "symbol": updated.symbol,
                        "action": decision.action,
                        "reason": decision.reason,
                        "status": updated.status,
                        "net_pnl_usdt": updated.net_pnl_usdt,
                        "qualification": (updated.entry_evidence or {}).get("qualification"),
                    }
                )
        except Exception as exc:
            errors.append({"id": str(trade.id), "symbol": trade.symbol, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "open": len(open_trades),
        "held": held,
        # 상한에 걸려 이번 실행에서 판정하지 않은 건수. 0 이 아니면 다음 실행에서 처리된다.
        "deferred": deferred,
        "evaluation_cap": MAX_EXIT_EVALUATIONS_PER_RUN,
        "closed": closed,
        "closed_count": len(closed),
        "errors": errors,
        "as_of": moment.isoformat(),
    }


def _distribution(values: list[float], *, unit: str, empty_note: str) -> dict[str, Any]:
    """분포. **표본 0에서 숫자를 만들어 내지 않는다**(C10)."""
    if not values:
        return {"count": 0, "note": empty_note}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "unit": unit,
        "min": round(ordered[0], 2),
        "median": round(ordered[len(ordered) // 2], 2),
        "p90": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))], 2),
        "max": round(ordered[-1], 2),
    }


def latency_distribution(trades: list[PaperTrade]) -> dict[str, Any]:
    """체결→진입 지연 분포 (7-4 항목 2). 상한이 실제로 무엇을 걸렀는지 읽는 쪽 절반이다."""
    values = [
        float((trade.entry_evidence or {}).get("signal_to_entry_seconds"))
        for trade in trades
        if isinstance((trade.entry_evidence or {}).get("signal_to_entry_seconds"), (int, float))
    ]
    return _distribution([value / 60 for value in values], unit="minutes", empty_note="진입 0건 — 지연을 측정할 대상이 없다")


def drift_distribution(trades: list[PaperTrade]) -> dict[str, Any]:
    """가격 이탈 분포 (7-4 항목 3). 상한 값이 적정한지 판정하는 근거다.

    분포가 상한에 몰려 있으면 상한이 너무 조인 것이고, 상한 근처가 비어 있으면 느슨한 것이다.
    """
    values = [
        float((trade.entry_evidence or {}).get("price_drift_pct_of_stop"))
        for trade in trades
        if isinstance((trade.entry_evidence or {}).get("price_drift_pct_of_stop"), (int, float))
    ]
    return _distribution(values, unit="pct_of_stop", empty_note="진입 0건 — 이탈을 측정할 대상이 없다")


def performance_by_qualification(trades: list[PaperTrade]) -> dict[str, Any]:
    """자격별 집계. 자격이 하나로 줄었어도 **과거 행의 자격은 그대로 읽는다**.

    Phase 6 의 `observation`/`promotion` 행을 새 `follow` 행과 섞으면 문턱이 다른 표본이
    한 통계에 들어간다. 자격이 단순해진 것은 지금부터이지 소급이 아니다.

    R 은 계획 리스크 기준이다 — `planned_risk_usdt` 가 있으면 그것으로 나눈다. 없으면
    R 을 만들지 않는다(추정치를 적지 않는다).
    """
    buckets: dict[str, dict[str, Any]] = {}
    for trade in trades:
        evidence = trade.entry_evidence or {}
        key = str(evidence.get("qualification") or "unknown")
        bucket = buckets.setdefault(
            key,
            {
                "qualification": key,
                "entries": 0,
                "closed": 0,
                "wins": 0,
                "net_usdt": 0.0,
                "net_r": 0.0,
                "r_samples": 0,
                "gross_win_usdt": 0.0,
                "gross_loss_usdt": 0.0,
                "_trades": [],
            },
        )
        bucket["entries"] += 1
        bucket["_trades"].append(trade)
        if trade.status != "closed":
            continue
        net = float(trade.net_pnl_usdt or 0.0)
        bucket["closed"] += 1
        bucket["net_usdt"] += net
        if net > 0:
            bucket["wins"] += 1
            bucket["gross_win_usdt"] += net
        else:
            bucket["gross_loss_usdt"] += abs(net)
        planned_risk = float(((trade.target_plan or {}).get("sizing") or {}).get("planned_risk_usdt") or 0.0)
        if planned_risk > 0:
            bucket["net_r"] += net / planned_risk
            bucket["r_samples"] += 1

    for bucket in buckets.values():
        rows = bucket.pop("_trades")
        bucket["latency"] = latency_distribution(rows)
        bucket["drift"] = drift_distribution(rows)
        bucket["net_usdt"] = round(bucket["net_usdt"], 4)
        bucket["net_r"] = round(bucket["net_r"], 4) if bucket["r_samples"] else None
        bucket["win_pct"] = round(bucket["wins"] / bucket["closed"] * 100, 1) if bucket["closed"] else None
        losses = bucket.pop("gross_loss_usdt")
        winnings = bucket.pop("gross_win_usdt")
        # PF 는 손실이 있어야 정의된다. 손실 0에서 무한대를 적지 않는다.
        bucket["profit_factor"] = round(winnings / losses, 3) if losses > 0 else None
        bucket["not_promotion_evidence"] = "추종 트랙 성과는 승격(28일·N>=30·CI 하한 55%) 근거로 쓰지 않는다"
    return {
        "buckets": buckets,
        "note": "자격 종류가 다른 건은 분리 집계한다 — 문턱이 다르므로 섞으면 둘 다 해석 불가가 된다",
    }
