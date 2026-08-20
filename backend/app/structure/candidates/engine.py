from __future__ import annotations

from statistics import mean
from typing import Any

from app.db.models import MarketCandle
from app.structure.liquidity.common import fractal_swings, relative_volume
from app.structure.liquidity.structure import detect_structure_shift

CANDIDATE_ENGINES = frozenset({"fvg", "order_block", "vcp", "stage2_template", "full_alignment", "money_flow"})


def detect_candidate_signatures(candles: list[MarketCandle]) -> dict[str, Any]:
    """Detect candidate events using only the supplied, closed prefix."""
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    if not ordered:
        return {"events": [], "active_fvgs": [], "stage2_template": _stage2_payload(False, "표본 없음")}
    events: list[dict[str, Any]] = []
    events.extend(_fvg_event(ordered))
    events.extend(_order_block_retest(ordered))
    events.extend(_vcp_event(ordered))
    stage2 = detect_stage2_template(ordered)
    if stage2["active"] and not detect_stage2_template(ordered[:-1])["active"]:
        events.append(
            _event(
                "stage2_template",
                "stage2_active",
                "long",
                ordered[-1],
                ordered[-1].close,
                "2단계 상승 조건 진입",
                {"ma150": stage2["ma150"], "ma200": stage2["ma200"], "high_distance_pct": stage2["high_distance_pct"]},
            )
        )
    return {"events": events, "active_fvgs": _active_fvgs(ordered), "stage2_template": stage2}


def detect_stage2_template(candles: list[MarketCandle]) -> dict[str, Any]:
    """2단계 상승 조건.

    ## 조건별 판정을 함께 낸다 (WO-FCE-STOCK-UNBLOCK-01 3-1)

    지금까지 `active` 불리언 하나만 나와서 **왜 떨어졌는지 알 수 없었다.** 캔들이 200개
    미만이면 나머지 세 조건은 **평가조차 되지 않는데**, 밖에서 보면 그냥 "미충족"이라
    데이터 결함과 조건 부적합을 구분할 수 없었다.

    그래서 `checks` 에 조건별 결과를 낸다. 평가 불가한 조건은 `False` 가 아니라 **`None`**
    이다 — "틀렸다"와 "재보지 못했다"는 다른 상태다.

    ⚠️ **`active` 판정 로직은 한 줄도 바꾸지 않았다**(C4). 이미 계산하던 중간값을 내보내기만 한다.
    """
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    candle_count = len(ordered)
    if candle_count < 200:
        payload = _stage2_payload(False, f"캔들 {candle_count}개 — MA200 표본 부족")
        payload["candle_count"] = candle_count
        payload["checks"] = {
            "candle_count_ok": False,
            # 아래 셋은 평가 자체가 불가능하다. False 로 적으면 "조건이 틀렸다"로 읽힌다.
            "alignment_ok": None,
            "slope_ok": None,
            "high_distance_ok": None,
        }
        return payload
    closes = [candle.close for candle in ordered]
    ma150 = mean(closes[-150:])
    ma200 = mean(closes[-200:])
    previous_ma200 = mean(closes[-220:-20]) if len(closes) >= 220 else mean(closes[:200])
    slope = ma200 - previous_ma200
    high = max(candle.high for candle in ordered[-min(260, len(ordered)) :])
    close = closes[-1]
    high_distance_pct = (close / high - 1.0) * 100.0 if high > 0 else None
    active = close > ma150 > ma200 and slope > 0 and high_distance_pct is not None and high_distance_pct >= -25.0
    return {
        "active": active,
        "label": "2단계 상승 조건" if active else "2단계 상승 조건 미충족",
        "ma150": round(ma150, 8),
        "ma200": round(ma200, 8),
        "ma200_slope_20": round(slope, 8),
        "high_distance_pct": round(high_distance_pct, 3) if high_distance_pct is not None else None,
        "as_of": ordered[-1].timestamp.isoformat(),
        "candle_count": candle_count,
        # 관측 전용 — `active` 는 위에서 이미 정해졌다.
        "checks": {
            "candle_count_ok": True,
            "alignment_ok": bool(close > ma150 > ma200),
            "slope_ok": bool(slope > 0),
            "high_distance_ok": bool(high_distance_pct is not None and high_distance_pct >= -25.0),
        },
    }


def _fvg_event(candles: list[MarketCandle]) -> list[dict[str, Any]]:
    if len(candles) < 3:
        return []
    left, current = candles[-3], candles[-1]
    if left.high < current.low:
        low, high, direction = left.high, current.low, "long"
    elif left.low > current.high:
        low, high, direction = current.high, left.low, "short"
    else:
        return []
    midpoint = (low + high) / 2.0
    return [_event("fvg", "gap_formed", direction, current, midpoint, "미충전 갭 형성", {"zone_low": low, "zone_high": high, "midpoint": midpoint})]


def _active_fvgs(candles: list[MarketCandle]) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    for index in range(2, len(candles)):
        left, current = candles[index - 2], candles[index]
        if left.high < current.low:
            low, high, direction = left.high, current.low, "long"
        elif left.low > current.high:
            low, high, direction = current.high, left.low, "short"
        else:
            continue
        midpoint = (low + high) / 2.0
        future = candles[index + 1 :]
        filled = any(candle.low <= midpoint if direction == "long" else candle.high >= midpoint for candle in future)
        if not filled:
            active.append(
                {
                    "formed_at": current.timestamp.isoformat(),
                    "direction": direction,
                    "low": round(low, 8),
                    "high": round(high, 8),
                    "midpoint": round(midpoint, 8),
                }
            )
    return active[-8:]


def order_block_zones(candles: list[MarketCandle], *, limit: int = 4) -> list[dict[str, Any]]:
    """미시험 오더블록 존을 반환한다 (WO-FCE-STRUCTURE-CONTEXT-01).

    **신규 감지기가 아니다.** `_order_block_retest`가 이미 쓰던 존 도출 로직(구조 파괴 →
    직전 반대 캔들의 실체 구간)을 그대로 쓰되, "지금 이 캔들이 존을 터치했는가"라는 발화
    조건만 떼어냈다. 리테스트 이벤트는 발화 순간만 알려주므로 "내 포지션이 어느 존 안에
    있나"를 답할 수 없었다 — 그 빈자리를 메우기 위한 노출이다.

    확정 캔들만 사용한다(호출자가 확정분만 넘긴다 — C3).
    """
    if len(candles) < 12:
        return []
    current_index = len(candles) - 1
    zones: list[dict[str, Any]] = []
    seen: set[tuple[float, float]] = set()
    for break_index in range(current_index - 1, max(7, current_index - 35), -1):
        shift = detect_structure_shift(candles[: break_index + 1])
        if shift.get("state") != "structure_break" or not shift.get("direction"):
            continue
        direction = "long" if shift["direction"] == "up" else "short"
        opposite = (lambda candle: candle.close < candle.open) if direction == "long" else (lambda candle: candle.close > candle.open)
        anchor_index = next((index for index in range(break_index - 1, max(-1, break_index - 12), -1) if opposite(candles[index])), None)
        if anchor_index is None:
            continue
        anchor = candles[anchor_index]
        zone_low, zone_high = sorted((anchor.open, anchor.close))
        key = (round(zone_low, 8), round(zone_high, 8))
        if key in seen:
            continue
        seen.add(key)
        retests = sum(1 for candle in candles[break_index + 1 :] if _touches(candle, zone_low, zone_high))
        zones.append(
            {
                "kind": "demand" if direction == "long" else "supply",
                "direction": direction,
                "zone_low": round(zone_low, 8),
                "zone_high": round(zone_high, 8),
                "formed_at": anchor.timestamp.isoformat(),
                "break_event": shift.get("event"),
                "retests": retests,
                "untested": retests == 0,
            }
        )
        if len(zones) >= limit:
            break
    return zones


def _order_block_retest(candles: list[MarketCandle]) -> list[dict[str, Any]]:
    if len(candles) < 12:
        return []
    current_index = len(candles) - 1
    for break_index in range(current_index - 1, max(7, current_index - 35), -1):
        shift = detect_structure_shift(candles[: break_index + 1])
        if shift.get("state") != "structure_break" or not shift.get("direction"):
            continue
        direction = "long" if shift["direction"] == "up" else "short"
        opposite = (lambda candle: candle.close < candle.open) if direction == "long" else (lambda candle: candle.close > candle.open)
        anchor_index = next((index for index in range(break_index - 1, max(-1, break_index - 12), -1) if opposite(candles[index])), None)
        if anchor_index is None:
            continue
        anchor = candles[anchor_index]
        zone_low, zone_high = sorted((anchor.open, anchor.close))
        if any(_touches(candle, zone_low, zone_high) for candle in candles[break_index + 1 : current_index]):
            continue
        if not _touches(candles[-1], zone_low, zone_high):
            continue
        return [
            _event(
                "order_block",
                "retest",
                direction,
                candles[-1],
                (zone_low + zone_high) / 2.0,
                "매물 존 재시험",
                {"zone_low": zone_low, "zone_high": zone_high, "break_event": shift.get("event")},
            )
        ]
    return []


def _vcp_event(candles: list[MarketCandle]) -> list[dict[str, Any]]:
    if len(candles) < 30:
        return []
    swings = fractal_swings(candles)
    if len(swings) < 4:
        return []
    chain = swings[-4:]
    amplitudes = [abs(chain[index].price - chain[index - 1].price) for index in range(1, len(chain))]
    if amplitudes[0] <= 0 or not all(amplitudes[index] <= amplitudes[index - 1] * 0.75 for index in range(1, len(amplitudes))):
        return []
    rvol = relative_volume(candles, len(candles) - 1)
    if rvol >= 0.7:
        return []
    return [
        _event(
            "vcp",
            "contraction",
            "long",
            candles[-1],
            candles[-1].close,
            "변동성 수축 3파 확인",
            {"amplitudes": [round(value, 8) for value in amplitudes], "relative_volume": round(rvol, 3), "swing_confirmation_delay": 2},
        )
    ]


def _event(engine: str, event_type: str, direction: str, candle: MarketCandle, price: float, label: str, components: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"{engine}:{event_type}:{int(candle.timestamp.timestamp())}",
        "engine": engine,
        "event_type": event_type,
        "direction": direction,
        "time": int(candle.timestamp.timestamp()),
        "as_of": candle.timestamp.isoformat(),
        "price": round(price, 8),
        "label": label,
        "components": components,
        "lifecycle_state": "candidate",
        "confirmed": True,
    }


def _touches(candle: MarketCandle, low: float, high: float) -> bool:
    return candle.low <= high and candle.high >= low


def _stage2_payload(active: bool, label: str) -> dict[str, Any]:
    return {"active": active, "label": label, "ma150": None, "ma200": None, "ma200_slope_20": None, "high_distance_pct": None, "as_of": None}
