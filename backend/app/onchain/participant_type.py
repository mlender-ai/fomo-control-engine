"""WO-FCE-WHALE-FOLLOW-01 5-2 — 참여자 유형 추정.

## 왜 필요한가

마켓메이커를 추종하면 안 된다. MM 의 체결은 방향 베팅이 아니라 재고 관리다. 그런데 현행
선발 기준(월간 PnL·ROI)은 MM 을 걸러내지 못한다 — MM 은 스프레드로 안정적으로 벌기 때문에
오히려 상위에 오른다. 실측이 그것을 보여준다:

| 지갑 | maker% | 방향 편중 | 일 이벤트 | open/close |
| --- | --- | --- | --- | --- |
| `0x4e2328…20c3` | 77.4 | **0.024** | 195.8 | 15 / 17 |
| `0x77375a…bf66` | **100.0** | 0.119 | 31.4 | 0 / 0 |
| `0x42bc06…3946` | 98.8 | **0.999** | 26.6 | 0 / 2 |
| `0x9546b9…181c` | **0.1** | 0.954 | 178.3 | 31 / 33 |

앞의 셋은 방향 베팅으로 보기 어렵다. 넷째만 방향성이다.

## 무엇으로 판정하는가 — `crossed`

하이퍼리퀴드 체결 원본이 `crossed` 를 준다. `false` 면 지정가가 채워진 것(maker),
`true` 면 스프레드를 넘어간 것(taker)이다. **이것이 이 판정의 핵심 축이다.** 보유 시간이나
회전율 같은 대리 지표보다 직접적이다 — 방향에 확신이 있는 참여자는 스프레드를 지불한다.

2026-08-25 실측(N>=30 지갑 81개)의 maker% 분포가 이봉이다:

```
최소 0.0 · p10 1.3 · p25 14.6 · 중위 61.8 · p75 85.9 · p90 95.3 · 최대 100.0
```

한 모집단이 아니다. 임계는 이 분포의 골에서 잡았다 — taker 우세 40% 이하, maker 우세 70% 이상.

## 이것은 신규 감지기가 아니다

`AGENTS.md` 의 신규 감지기 모라토리엄은 시장 구조 후보 시그니처(엘리엇·추가 하모닉)를
겨냥한다. 이 모듈은 시장을 보지 않는다. **이미 수집된 체결로 지갑을 분류해 추종 대상에서
제외**하며, 방향 판단(`analyst/`·`structure/`)에 입력되지 않는다. 감지기를 늘리는 것이 아니라
관측 대상을 줄인다.

## 추정이다

`estimate=True` 가 모든 반환에 붙는다. 스팟 다리를 볼 수 없으므로 베이시스·캐리는 특히
약한 추정이다. 확정으로 표시하면 안 된다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

TYPE_DIRECTIONAL = "directional"
TYPE_MARKET_MAKER = "market_maker"
TYPE_BASIS_CARRY = "basis_carry"
TYPE_UNCLASSIFIED = "unclassified"

# 추종 허용 유형. 미분류는 포함하지 않는다 — 근거 없이 추종하지 않는다(5-2 항목 3).
FOLLOW_ELIGIBLE_TYPES = frozenset({TYPE_DIRECTIONAL})

# 이 미만이면 판정하지 않는다. 유형을 표본 부족 상태에서 발표하지 않는다.
MIN_EVENTS = 30

# maker 우세 임계. 실측 maker% 분포의 p75(85.9)와 중위(61.8) 사이 골에서 잡았다.
MAKER_DOMINANT_PCT = 70.0
# taker 우세 임계. p25(14.6)와 중위 사이. 이 아래면 스프레드를 지불하고 있다.
TAKER_DOMINANT_PCT = 40.0
# MM 의 재고 양방향성. 실측 최소 편중이 0.015 이고 중위가 0.445 다.
MM_MAX_SKEW = 0.35
# MM 은 빈번하다. 실측 일 이벤트 중위 14.7 · p90 87.6.
MM_MIN_EVENTS_PER_DAY = 20.0
# 캐리는 한 방향을 계속 들고 있다.
CARRY_MIN_SKEW = 0.90
CARRY_MAX_COINS = 2
# 캐리는 청산을 거의 하지 않는다 — 자금률을 수취하며 유지한다.
CARRY_MAX_CLOSE_RATIO = 0.02

OPENING_EVENTS = ("open", "increase", "flip")
CLOSING_EVENTS = ("close", "reduce")


@dataclass(frozen=True)
class ParticipantEstimate:
    """지갑 한 개의 유형 추정. 확정이 아니다."""

    address: str
    participant_type: str
    confidence: float
    follow_eligible: bool
    reason: str
    indicators: dict[str, Any]

    def as_payload(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "participant_type": self.participant_type,
            "confidence": self.confidence,
            "follow_eligible": self.follow_eligible,
            "reason": self.reason,
            "indicators": self.indicators,
            "estimate": True,
            "basis": "체결 crossed(maker/taker)·방향 편중·빈도·청산비 — 행동 기반 추정이며 신원·의도 확정이 아님",
        }


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _get(event: Any, *names: str) -> Any:
    for name in names:
        value = event.get(name) if isinstance(event, dict) else getattr(event, name, None)
        if value is not None:
            return value
    return None


def _crossed(event: Any) -> bool | None:
    """체결이 스프레드를 넘어갔는가. 저장 구조가 2단 중첩이라 양쪽을 본다."""
    payload = _get(event, "payload")
    if not isinstance(payload, dict):
        return None
    for source in (payload.get("payload") if isinstance(payload.get("payload"), dict) else None, payload):
        if not isinstance(source, dict):
            continue
        raw = source.get("raw")
        if isinstance(raw, dict) and isinstance(raw.get("crossed"), bool):
            return bool(raw["crossed"])
        if isinstance(source.get("crossed"), bool):
            return bool(source["crossed"])
    return None


def wallet_indicators(events: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """지갑별 행동 지표. 손익은 세지 않는다 — 유형은 성적이 아니다."""
    tally: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"events": 0, "maker": 0, "taker": 0, "long_usd": 0.0, "short_usd": 0.0, "coins": set(), "opens": 0, "closes": 0, "first": None, "last": None}
    )
    for event in events:
        address = str(_get(event, "wallet_address") or "").lower()
        if not address:
            continue
        bucket = tally[address]
        bucket["events"] += 1
        crossed = _crossed(event)
        if crossed is True:
            bucket["taker"] += 1
        elif crossed is False:
            bucket["maker"] += 1
        size_usd = abs(float(_get(event, "size_usd") or 0.0))
        side = str(_get(event, "side") or "").lower()
        if side in {"long", "short"}:
            bucket[f"{side}_usd"] += size_usd
        symbol = str(_get(event, "symbol") or _get(event, "coin") or "").upper()
        if symbol:
            bucket["coins"].add(symbol)
        kind = str(_get(event, "event", "event_type") or "").lower()
        if kind in OPENING_EVENTS:
            bucket["opens"] += 1
        elif kind in CLOSING_EVENTS:
            bucket["closes"] += 1
        stamp = _parse(_get(event, "event_at"))
        if stamp is not None:
            bucket["first"] = min(bucket["first"] or stamp, stamp)
            bucket["last"] = max(bucket["last"] or stamp, stamp)

    result: dict[str, dict[str, Any]] = {}
    for address, bucket in tally.items():
        classified = int(bucket["maker"]) + int(bucket["taker"])
        directional_usd = float(bucket["long_usd"]) + float(bucket["short_usd"])
        span_days = 0.0
        if bucket["first"] and bucket["last"]:
            span_days = max(1.0 / 24.0, (bucket["last"] - bucket["first"]).total_seconds() / 86400.0)
        result[address] = {
            "events": int(bucket["events"]),
            "maker_pct": round(int(bucket["maker"]) / classified * 100, 1) if classified else None,
            "classified_pct": round(classified / int(bucket["events"]) * 100, 1) if bucket["events"] else 0.0,
            "direction_skew": round(abs(float(bucket["long_usd"]) - float(bucket["short_usd"])) / directional_usd, 3) if directional_usd > 0 else None,
            "events_per_day": round(int(bucket["events"]) / span_days, 1) if span_days else None,
            "distinct_coins": len(bucket["coins"]),
            "close_ratio": round(int(bucket["closes"]) / int(bucket["events"]), 3) if bucket["events"] else 0.0,
            "opens": int(bucket["opens"]),
            "closes": int(bucket["closes"]),
            "observed_days": round(span_days, 1),
        }
    return result


def _confidence(margins: list[float], events: int) -> float:
    """임계에서 얼마나 떨어져 있는지 × 표본 충분성. 경계에 붙으면 낮게 낸다."""
    if not margins:
        return 0.0
    margin = min(max(0.0, value) for value in margins)
    sample_factor = min(1.0, events / (MIN_EVENTS * 3))
    return round(min(0.95, margin * sample_factor), 2)


def estimate_participant_type(address: str, indicators: dict[str, Any], *, min_events: int = MIN_EVENTS) -> ParticipantEstimate:
    """행동 지표에서 유형을 추정한다. 규칙이 맞지 않으면 미분류로 둔다."""
    events = int(indicators.get("events") or 0)
    maker_pct = indicators.get("maker_pct")
    skew = indicators.get("direction_skew")
    per_day = indicators.get("events_per_day")
    coins = int(indicators.get("distinct_coins") or 0)
    close_ratio = float(indicators.get("close_ratio") or 0.0)

    if events < min_events:
        return ParticipantEstimate(address, TYPE_UNCLASSIFIED, 0.0, False, f"이벤트 {events}건 — 판정 최소 {min_events}건 미달", indicators)
    if maker_pct is None or skew is None or per_day is None:
        return ParticipantEstimate(address, TYPE_UNCLASSIFIED, 0.0, False, "maker/taker 또는 방향 지표를 산출할 수 없다", indicators)

    # 캐리를 MM 보다 먼저 본다. 한쪽으로 완전히 쏠린 패시브 재고는 MM 이 아니다.
    if skew >= CARRY_MIN_SKEW and coins <= CARRY_MAX_COINS and close_ratio <= CARRY_MAX_CLOSE_RATIO and maker_pct >= 50.0:
        margins = [(skew - CARRY_MIN_SKEW) / (1.0 - CARRY_MIN_SKEW), (CARRY_MAX_CLOSE_RATIO - close_ratio) / CARRY_MAX_CLOSE_RATIO]
        return ParticipantEstimate(
            address,
            TYPE_BASIS_CARRY,
            _confidence(margins, events),
            False,
            f"편중 {skew} · 청산비 {close_ratio} · 종목 {coins}개 · maker {maker_pct}% — 자금률 수취 재고로 추정(스팟 다리 관측 불가로 약한 추정)",
            indicators,
        )
    if maker_pct >= MAKER_DOMINANT_PCT and skew <= MM_MAX_SKEW and per_day >= MM_MIN_EVENTS_PER_DAY:
        margins = [
            (maker_pct - MAKER_DOMINANT_PCT) / (100.0 - MAKER_DOMINANT_PCT),
            (MM_MAX_SKEW - skew) / MM_MAX_SKEW,
            min(1.0, (per_day - MM_MIN_EVENTS_PER_DAY) / MM_MIN_EVENTS_PER_DAY),
        ]
        return ParticipantEstimate(
            address,
            TYPE_MARKET_MAKER,
            _confidence(margins, events),
            False,
            f"maker {maker_pct}% · 편중 {skew} · {per_day}건/일 — 양방향 패시브 재고 관리로 추정",
            indicators,
        )
    if maker_pct <= TAKER_DOMINANT_PCT and skew > MM_MAX_SKEW:
        margins = [(TAKER_DOMINANT_PCT - maker_pct) / TAKER_DOMINANT_PCT, min(1.0, (skew - MM_MAX_SKEW) / MM_MAX_SKEW)]
        return ParticipantEstimate(
            address,
            TYPE_DIRECTIONAL,
            _confidence(margins, events),
            True,
            f"maker {maker_pct}% (스프레드 지불) · 편중 {skew} — 방향 베팅으로 추정",
            indicators,
        )
    if maker_pct <= TAKER_DOMINANT_PCT:
        # taker 우세이므로 MM 은 아니다. 그러나 순포지션이 거의 남지 않는 양방향 고빈도는
        # 방향 베팅으로 볼 수 없다 — 스캘핑·차익이면 엔진 진입 지연 안에서 신호가 소멸한다.
        # 아니라고 말할 근거는 있고 그렇다고 말할 근거는 없다. 그래서 미분류다(5-2 항목 3).
        return ParticipantEstimate(
            address,
            TYPE_UNCLASSIFIED,
            0.0,
            False,
            f"maker {maker_pct}% 로 MM 은 아니나 방향 편중 {skew} (임계 {MM_MAX_SKEW}) · {per_day}건/일 — 양방향 고빈도로 추정되어 추종 지연에 취약하다",
            indicators,
        )
    return ParticipantEstimate(
        address,
        TYPE_UNCLASSIFIED,
        0.0,
        False,
        f"maker {maker_pct}% · 편중 {skew} · {per_day}건/일 — 어느 유형 규칙도 만족하지 않는다. 근거 없이 추종하지 않는다",
        indicators,
    )


def classify_wallets(events: Iterable[Any], *, min_events: int = MIN_EVENTS) -> dict[str, dict[str, Any]]:
    indicators = wallet_indicators(events)
    return {address: estimate_participant_type(address, values, min_events=min_events).as_payload() for address, values in indicators.items()}


def follow_eligible_addresses(estimates: dict[str, dict[str, Any]]) -> set[str]:
    return {address for address, payload in estimates.items() if bool(payload.get("follow_eligible"))}


def type_distribution(estimates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for payload in estimates.values():
        counts[str(payload.get("participant_type") or TYPE_UNCLASSIFIED)] += 1
    return {
        "counts": dict(sorted(counts.items(), key=lambda item: (-item[1], item[0]))),
        "wallets": len(estimates),
        "follow_eligible": len(follow_eligible_addresses(estimates)),
        "eligible_types": sorted(FOLLOW_ELIGIBLE_TYPES),
        "excluded_types": sorted({TYPE_MARKET_MAKER, TYPE_BASIS_CARRY, TYPE_UNCLASSIFIED}),
        "estimate": True,
        "note": "미분류는 추종하지 않는다 — 관측·추적은 계속한다",
    }
