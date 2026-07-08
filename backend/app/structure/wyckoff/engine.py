from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from app.db.models import MarketCandle, MarketSnapshot
from app.structure.levels.engine import StructureLevel, detect_structure_levels


RANGE_LOOKBACK = 60
MIN_RANGE_CANDLES = 20


@dataclass(frozen=True)
class WyckoffLevel:
    price: float
    score: int
    touches: int
    kind: str
    sources: list[str]


@dataclass(frozen=True)
class TradingRange:
    support: WyckoffLevel
    resistance: WyckoffLevel
    start_time: int
    end_time: int
    candles_inside: int
    width_pct: float
    atr: float


def analyze_structure(snapshot: MarketSnapshot, indicators: dict) -> dict:
    candles = sorted(snapshot.candles, key=lambda candle: candle.timestamp)
    wyckoff = analyze_wyckoff(candles, timeframe=snapshot.timeframe)
    trend = _trend_payload(candles, indicators)
    trend_direction = trend["direction"]
    structure_score = _structure_score(trend, wyckoff, indicators)
    wyckoff = {**wyckoff, "structure_comment": _structure_comment(wyckoff)}

    return {
        "structure_score": structure_score,
        "wyckoff": wyckoff,
        "trend": {
            **trend,
            "direction": trend_direction,
            "support_broken": bool(wyckoff.get("sow_confirmed", False)),
            "resistance_broken": bool(wyckoff.get("sos_confirmed", False) or wyckoff.get("utad_candidate", False)),
        },
    }


def analyze_wyckoff(
    candles: list[MarketCandle],
    *,
    levels: dict[str, list[Any]] | None = None,
    trade_flow: dict | None = None,
    timeframe: str = "4h",
    include_mtf: bool = True,
) -> dict:
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    if len(ordered) < 30:
        return _empty_result(
            "undetermined",
            "데이터가 부족해 와이코프 국면을 억지로 판정하지 않았습니다.",
            timeframe=timeframe,
        )

    atr = _atr(ordered)
    mark_price = ordered[-1].close
    normalized_levels = _normalize_levels(levels if levels is not None else detect_structure_levels(ordered, mark_price))
    trading_range = _detect_trading_range(ordered, normalized_levels, atr)
    trend = _trend_payload(
        ordered,
        {
            "twenty_close": _rolling_mean([candle.close for candle in ordered], 20),
            "relative_volume": _relative_volume(ordered),
        },
    )

    if trading_range is None:
        result = _empty_result(
            "trending",
            "명확한 거래 박스가 없어 Spring/UTAD 이벤트 감지를 보류했습니다.",
            timeframe=timeframe,
        )
        result["trend"] = trend
        if include_mtf:
            result["mtf"] = _mtf_payload(ordered, None, result)
        return result

    events = _detect_events(ordered, trading_range, trade_flow)
    phase = _phase_from_events(events)
    events = _contextualize_event_labels(events, phase["side"])
    accumulation_score, distribution_score = _side_scores(events)
    conflict_note = _conflict_note(events, phase["side"])
    result = {
        "timeframe": timeframe,
        "phase": phase["phase"],
        "phase_hint": phase["phase"],
        "side": phase["side"],
        "evidence_event_ids": phase["evidence_event_ids"],
        "range": _range_payload(trading_range),
        "events": events,
        "markers": events,
        "accumulation_score": accumulation_score,
        "distribution_score": distribution_score,
        "spring_candidate": _has_event(events, "spring_candidate"),
        "test_candidate": _has_event(events, "test_candidate"),
        "sos_confirmed": _has_event(events, "sos_confirmed"),
        "lps_candidate": _has_event(events, "lps_candidate"),
        "utad_candidate": _has_event(events, "utad_candidate"),
        "sow_confirmed": _has_event(events, "sow_confirmed"),
        "lpsy_candidate": _has_event(events, "lpsy_candidate"),
        "trend": trend,
        "structure_comment": _structure_comment({"phase": phase["phase"], "side": phase["side"], "events": events}),
        "conflict_note": conflict_note,
        "mtf": {"htf_phase": None, "htf_trend": None, "alignment": "neutral"},
    }
    if include_mtf:
        result["mtf"] = _mtf_payload(ordered, normalized_levels, result)
    return result


def _contextualize_event_labels(events: list[dict], side: str) -> list[dict]:
    """이벤트 라벨을 판정된 국면 맥락에 맞춘다 (WO-43 Part B).

    UTAD(UpThrust After Distribution)는 분산 국면을 전제하는 명칭이다. 매집 우세로
    판정된 레인지에서 저항 상단 스윕은 교과서적으로 UT(업스러스트)이므로 재명명한다
    — "매집 Phase A + UTAD" 같은 자기모순 출력을 구조적으로 제거.
    type은 유지(신호 계보·시그니처 안정성), 표시 라벨만 맥락화한다.
    """
    if side != "accumulation":
        return events
    relabeled = []
    for event in events:
        if event.get("type") == "utad_candidate":
            relabeled.append({**event, "label": "UT", "context_note": "매집 레인지 상단 업스러스트 — UTAD 아님"})
        else:
            relabeled.append(event)
    return relabeled


def _conflict_note(events: list[dict], side: str) -> str | None:
    """판정 side와 반대되는 고신뢰 이벤트가 공존하면 혼합 신호를 명시한다 (은폐 금지)."""
    if side not in {"accumulation", "distribution"}:
        return None
    opposing_side = "distribution" if side == "accumulation" else "accumulation"
    opposing = [event for event in events if event.get("side") == opposing_side and int(event.get("confidence", 0)) >= 65]
    if not opposing:
        return None
    best = max(opposing, key=lambda event: int(event.get("confidence", 0)))
    side_label = "매집" if side == "accumulation" else "분산"
    return f"{side_label} 우세 판정이나 반대측 {best.get('label')} {best.get('confidence')}점 공존 — 혼합 신호"


def _detect_trading_range(candles: list[MarketCandle], levels: dict[str, list[WyckoffLevel]], atr: float) -> TradingRange | None:
    if not levels["support"] or not levels["resistance"]:
        return None
    recent = candles[-RANGE_LOOKBACK:]
    close = candles[-1].close
    supports = [level for level in levels["support"] if level.price < close * 1.03]
    resistances = [level for level in levels["resistance"] if level.price > close * 0.97]
    if not supports or not resistances:
        supports = levels["support"]
        resistances = levels["resistance"]

    candidates: list[tuple[int, float, WyckoffLevel, WyckoffLevel, int]] = []
    for support in supports[:5]:
        for resistance in resistances[:5]:
            if resistance.price <= support.price:
                continue
            width = resistance.price - support.price
            width_pct = width / max(close, 1e-9) * 100
            if width < atr * 1.2 or width > atr * 18 or width_pct > 35:
                continue
            inside = sum(1 for candle in recent if support.price <= candle.close <= resistance.price)
            min_inside = min(MIN_RANGE_CANDLES, max(10, int(len(recent) * 0.45)))
            if inside < min_inside:
                continue
            score = support.score + resistance.score + inside
            candidates.append((score, width_pct, support, resistance, inside))
    if not candidates:
        return None
    _, width_pct, support, resistance, inside = sorted(candidates, key=lambda item: (-item[0], item[1]))[0]
    return TradingRange(
        support=support,
        resistance=resistance,
        start_time=_timestamp(recent[0]),
        end_time=_timestamp(recent[-1]),
        candles_inside=inside,
        width_pct=round(width_pct, 2),
        atr=round(atr, 8),
    )


def _detect_events(candles: list[MarketCandle], trading_range: TradingRange, trade_flow: dict | None) -> list[dict]:
    recent = candles[-RANGE_LOOKBACK:]
    avg_volume = mean([candle.volume for candle in recent]) if recent else 1.0
    support = trading_range.support
    resistance = trading_range.resistance
    atr = trading_range.atr
    midpoint = support.price + (resistance.price - support.price) * 0.5
    threshold = max(atr * 0.15, candles[-1].close * 0.0015)
    events: list[dict] = []

    for index, candle in enumerate(recent):
        previous = recent[index - 1] if index > 0 else candle
        if _selling_climax(candle, support, avg_volume, atr):
            events.append(
                _event(
                    "selling_climax",
                    "SC",
                    "accumulation",
                    candle,
                    candle.low,
                    support,
                    atr,
                    avg_volume,
                    trade_flow,
                    "sell",
                    bars_to_return=4,
                )
            )
        if _buying_climax(candle, resistance, avg_volume, atr):
            events.append(
                _event(
                    "buying_climax",
                    "BC",
                    "distribution",
                    candle,
                    candle.high,
                    resistance,
                    atr,
                    avg_volume,
                    trade_flow,
                    "buy",
                    bars_to_return=4,
                )
            )
        if candle.low <= support.price + atr * 0.5 and candle.close >= midpoint and candle.volume >= avg_volume * 1.1:
            events.append(
                _event(
                    "automatic_rally",
                    "AR",
                    "accumulation",
                    candle,
                    candle.close,
                    support,
                    atr,
                    avg_volume,
                    trade_flow,
                    "buy",
                    bars_to_return=2,
                )
            )
        if candle.high >= resistance.price - atr * 0.5 and candle.close <= midpoint and candle.volume >= avg_volume * 1.1:
            events.append(
                _event(
                    "automatic_reaction",
                    "AR",
                    "distribution",
                    candle,
                    candle.close,
                    resistance,
                    atr,
                    avg_volume,
                    trade_flow,
                    "sell",
                    bars_to_return=2,
                )
            )
        if support.price <= candle.low <= support.price + atr * 0.45 and candle.volume <= avg_volume * 1.05:
            events.append(
                _event(
                    "secondary_test",
                    "ST",
                    "accumulation",
                    candle,
                    candle.low,
                    support,
                    atr,
                    avg_volume,
                    trade_flow,
                    "buy",
                    bars_to_return=3,
                )
            )
        if resistance.price - atr * 0.45 <= candle.high <= resistance.price and candle.volume <= avg_volume * 1.05:
            events.append(
                _event(
                    "secondary_test",
                    "ST",
                    "distribution",
                    candle,
                    candle.high,
                    resistance,
                    atr,
                    avg_volume,
                    trade_flow,
                    "sell",
                    bars_to_return=3,
                )
            )
        if candle.low < support.price - threshold and candle.close > support.price:
            events.append(
                _event(
                    "spring_candidate",
                    "Spring",
                    "accumulation",
                    candle,
                    candle.low,
                    support,
                    atr,
                    avg_volume,
                    trade_flow,
                    "buy",
                    bars_to_return=0,
                )
            )
        if candle.high > resistance.price + threshold and candle.close < resistance.price:
            events.append(
                _event(
                    "utad_candidate",
                    "UTAD",
                    "distribution",
                    candle,
                    candle.high,
                    resistance,
                    atr,
                    avg_volume,
                    trade_flow,
                    "sell",
                    bars_to_return=0,
                )
            )
        if candle.close > resistance.price + threshold and candle.volume >= avg_volume * 1.1:
            events.append(
                _event(
                    "sos_confirmed",
                    "SOS",
                    "accumulation",
                    candle,
                    candle.close,
                    resistance,
                    atr,
                    avg_volume,
                    trade_flow,
                    "buy",
                    bars_to_return=0,
                )
            )
        if candle.close < support.price - threshold and candle.volume >= avg_volume * 1.1:
            events.append(
                _event(
                    "sow_confirmed",
                    "SOW",
                    "distribution",
                    candle,
                    candle.close,
                    support,
                    atr,
                    avg_volume,
                    trade_flow,
                    "sell",
                    bars_to_return=0,
                )
            )
        if support.price <= candle.low <= support.price + threshold * 1.5 and candle.close > previous.close and candle.volume <= avg_volume * 0.95:
            events.append(
                _event(
                    "test_candidate",
                    "Test",
                    "accumulation",
                    candle,
                    candle.low,
                    support,
                    atr,
                    avg_volume,
                    trade_flow,
                    "buy",
                    bars_to_return=0,
                )
            )

    events = _dedupe_events(events)
    events.extend(_derived_retest_events(candles, events, trading_range, avg_volume, trade_flow))
    events = _dedupe_events(events)
    return sorted(events, key=lambda event: (event["time"], event["type"]))[-12:]


def _derived_retest_events(
    candles: list[MarketCandle],
    events: list[dict],
    trading_range: TradingRange,
    avg_volume: float,
    trade_flow: dict | None,
) -> list[dict]:
    derived: list[dict] = []
    by_time = {event["time"]: event for event in events}
    threshold = max(trading_range.atr * 0.35, candles[-1].close * 0.0025)
    recent_by_time = {_timestamp(candle): candle for candle in candles[-RANGE_LOOKBACK:]}
    for event in events:
        start_index = next(
            (index for index, candle in enumerate(candles) if _timestamp(candle) == event["time"]),
            None,
        )
        if start_index is None:
            continue
        followup = candles[start_index + 1 : start_index + 8]
        if event["type"] == "sos_confirmed":
            for candle in followup:
                if abs(candle.low - trading_range.resistance.price) <= threshold and candle.close >= trading_range.resistance.price:
                    derived.append(
                        _event(
                            "lps_candidate",
                            "LPS",
                            "accumulation",
                            candle,
                            candle.low,
                            trading_range.resistance,
                            trading_range.atr,
                            avg_volume,
                            trade_flow,
                            "buy",
                            bars_to_return=1,
                        )
                    )
                    break
        if event["type"] == "sow_confirmed":
            for candle in followup:
                if abs(candle.high - trading_range.support.price) <= threshold and candle.close <= trading_range.support.price:
                    derived.append(
                        _event(
                            "lpsy_candidate",
                            "LPSY",
                            "distribution",
                            candle,
                            candle.high,
                            trading_range.support,
                            trading_range.atr,
                            avg_volume,
                            trade_flow,
                            "sell",
                            bars_to_return=1,
                        )
                    )
                    break
        if event["type"] == "spring_candidate":
            for candle in followup:
                # Test는 지지선 "근접" 재시험이어야 한다. 근접 조건 없이는 스프링 뒤
                # 아무 양봉이나 Test가 되고, 레벨과의 거리가 depth 점수로 가산되어
                # 원본 스프링보다 높은 신뢰도의 허위 이벤트가 만들어진다 (매집 과대판정 원인).
                near_support = trading_range.support.price <= candle.low <= trading_range.support.price + threshold
                if near_support and candle.close > candle.open and candle.volume <= avg_volume * 1.1:
                    key = _timestamp(candle)
                    if key not in by_time and key in recent_by_time:
                        derived.append(
                            _event(
                                "test_candidate",
                                "Test",
                                "accumulation",
                                candle,
                                candle.low,
                                trading_range.support,
                                trading_range.atr,
                                avg_volume,
                                trade_flow,
                                "buy",
                                bars_to_return=1,
                            )
                        )
                    break
    return derived


def _event(
    event_type: str,
    label: str,
    side: str,
    candle: MarketCandle,
    price: float,
    level: WyckoffLevel,
    atr: float,
    avg_volume: float,
    trade_flow: dict | None,
    expected_flow: str,
    *,
    bars_to_return: int,
) -> dict:
    components = {
        "depth_significance": _depth_component(abs(price - level.price), atr),
        "return_speed": _return_speed_component(bars_to_return),
        "volume_confirmation": _volume_component(candle, avg_volume, trade_flow, expected_flow),
        "level_strength": _level_component(level.score),
    }
    confidence = min(100, sum(components.values()))
    return {
        "id": f"{event_type}-{_timestamp(candle)}",
        "time": _timestamp(candle),
        "price": round(price, 8),
        "type": event_type,
        "label": label,
        "side": side,
        "confidence": int(confidence),
        "components": components,
        "level_price": round(level.price, 8),
        "level_kind": level.kind,
        "level_score": level.score,
    }


def _phase_from_events(events: list[dict]) -> dict:
    accumulation_events = [event for event in events if event["side"] == "accumulation"]
    distribution_events = [event for event in events if event["side"] == "distribution"]
    best_accumulation = max([event["confidence"] for event in accumulation_events], default=0)
    best_distribution = max([event["confidence"] for event in distribution_events], default=0)

    if not events:
        return {"phase": "undetermined", "side": "neutral", "evidence_event_ids": []}
    if best_distribution > best_accumulation + 8:
        return _distribution_phase(distribution_events)
    if best_accumulation > best_distribution + 8:
        return _accumulation_phase(accumulation_events)
    return {
        "phase": "undetermined",
        "side": "neutral",
        "evidence_event_ids": [event["id"] for event in events[-3:]],
    }


def _accumulation_phase(events: list[dict]) -> dict:
    event_types = {event["type"] for event in events}
    if "lps_candidate" in event_types:
        phase = "accumulation_phase_e"
    elif "sos_confirmed" in event_types:
        phase = "accumulation_phase_d"
    elif "spring_candidate" in event_types or "test_candidate" in event_types:
        phase = "accumulation_phase_c"
    elif {"selling_climax", "automatic_rally", "secondary_test"} & event_types:
        phase = "accumulation_phase_a"
    else:
        phase = "undetermined"
    return {
        "phase": phase,
        "side": "accumulation" if phase != "undetermined" else "neutral",
        "evidence_event_ids": [event["id"] for event in events[-4:]],
    }


def _distribution_phase(events: list[dict]) -> dict:
    event_types = {event["type"] for event in events}
    if "lpsy_candidate" in event_types:
        phase = "distribution_phase_e"
    elif "sow_confirmed" in event_types:
        phase = "distribution_phase_d"
    elif "utad_candidate" in event_types:
        phase = "distribution_phase_c"
    elif {"buying_climax", "automatic_reaction", "secondary_test"} & event_types:
        phase = "distribution_phase_a"
    else:
        phase = "undetermined"
    return {
        "phase": phase,
        "side": "distribution" if phase != "undetermined" else "neutral",
        "evidence_event_ids": [event["id"] for event in events[-4:]],
    }


def _side_scores(events: list[dict]) -> tuple[int, int]:
    accumulation = [event for event in events if event["side"] == "accumulation"]
    distribution = [event for event in events if event["side"] == "distribution"]
    accumulation_score = _score_side(accumulation, distribution)
    distribution_score = _score_side(distribution, accumulation)
    return accumulation_score, distribution_score


def _score_side(primary: list[dict], opposing: list[dict]) -> int:
    best = max([event["confidence"] for event in primary], default=0)
    opposing_best = max([event["confidence"] for event in opposing], default=0)
    count_boost = min(12, len(primary) * 3)
    return _clamp(35 + best * 0.55 + count_boost - opposing_best * 0.18)


def _mtf_payload(
    candles: list[MarketCandle],
    levels: dict[str, list[WyckoffLevel]] | None,
    lower_result: dict,
) -> dict:
    daily = _aggregate_daily(candles)
    if len(daily) < 30:
        return {
            "htf_phase": "undetermined",
            "htf_trend": _trend_payload(candles, {})["direction"],
            "alignment": "neutral",
        }
    htf = analyze_wyckoff(daily, levels=None, timeframe="1d", include_mtf=False)
    htf_phase = htf.get("phase", "undetermined")
    htf_trend = htf.get("trend", {}).get("direction", _trend_payload(daily, {})["direction"])
    alignment = _alignment(lower_result.get("side", "neutral"), htf.get("side", "neutral"), htf_trend)
    return {"htf_phase": htf_phase, "htf_trend": htf_trend, "alignment": alignment}


def _alignment(lower_side: str, htf_side: str, htf_trend: str) -> str:
    if lower_side == "neutral":
        return "neutral"
    if lower_side == htf_side and htf_side != "neutral":
        return "aligned"
    if lower_side == "accumulation" and (htf_side == "distribution" or htf_trend in {"bearish", "bearish_to_neutral"}):
        return "conflicting"
    if lower_side == "distribution" and (htf_side == "accumulation" or htf_trend in {"bullish", "neutral_to_bullish"}):
        return "conflicting"
    return "neutral"


def _trend_payload(candles: list[MarketCandle], indicators: dict) -> dict:
    if len(candles) < 28:
        return {
            "direction": "neutral",
            "higher_low": False,
            "lower_high": False,
            "break_of_structure": False,
            "breakdown_structure": False,
        }
    recent_lows = [candle.low for candle in candles[-12:]]
    prior_lows = [candle.low for candle in candles[-28:-12]]
    recent_highs = [candle.high for candle in candles[-12:]]
    prior_highs = [candle.high for candle in candles[-28:-12]]
    higher_low = min(recent_lows) > min(prior_lows)
    lower_high = max(recent_highs) < max(prior_highs)
    break_of_structure = max(recent_highs) > max(prior_highs)
    breakdown_structure = min(recent_lows) < min(prior_lows)
    twenty_close = _optional_float(indicators.get("twenty_close")) or _rolling_mean([candle.close for candle in candles], 20)
    close = candles[-1].close
    if break_of_structure and higher_low and close >= twenty_close:
        direction = "bullish"
    elif breakdown_structure and lower_high and close <= twenty_close:
        direction = "bearish"
    elif close >= twenty_close:
        direction = "neutral_to_bullish"
    elif close < twenty_close:
        direction = "bearish_to_neutral"
    else:
        direction = "neutral"
    return {
        "direction": direction,
        "higher_low": higher_low,
        "lower_high": lower_high,
        "break_of_structure": break_of_structure,
        "breakdown_structure": breakdown_structure,
    }


def _structure_score(trend: dict, wyckoff: dict, indicators: dict) -> int:
    score = 48
    if trend.get("higher_low"):
        score += 12
    if trend.get("break_of_structure"):
        score += 10
    if trend.get("lower_high"):
        score -= 5
    if trend.get("breakdown_structure"):
        score -= 8
    score += (wyckoff.get("accumulation_score", 35) - wyckoff.get("distribution_score", 35)) * 0.18
    relative_volume = _optional_float(indicators.get("relative_volume")) or 1.0
    if relative_volume > 1.4 and trend.get("break_of_structure"):
        score += 8
    return _clamp(score)


def _selling_climax(candle: MarketCandle, support: WyckoffLevel, avg_volume: float, atr: float) -> bool:
    return candle.low <= support.price + atr * 0.35 and candle.volume >= avg_volume * 1.6 and candle.close > candle.low + (candle.high - candle.low) * 0.35


def _buying_climax(candle: MarketCandle, resistance: WyckoffLevel, avg_volume: float, atr: float) -> bool:
    return candle.high >= resistance.price - atr * 0.35 and candle.volume >= avg_volume * 1.6 and candle.close < candle.high - (candle.high - candle.low) * 0.35


def _depth_component(distance: float, atr: float) -> int:
    if atr <= 0:
        return 0
    ratio = distance / atr
    if ratio >= 1.0:
        return 30
    if ratio >= 0.6:
        return 24
    if ratio >= 0.35:
        return 18
    if ratio >= 0.15:
        return 10
    return 4


def _return_speed_component(bars_to_return: int) -> int:
    if bars_to_return <= 0:
        return 25
    if bars_to_return == 1:
        return 20
    if bars_to_return <= 3:
        return 14
    if bars_to_return <= 5:
        return 8
    return 3


def _volume_component(candle: MarketCandle, avg_volume: float, trade_flow: dict | None, expected_flow: str) -> int:
    delta_score = _trade_delta_component(candle, trade_flow, expected_flow)
    if delta_score is not None:
        return delta_score
    relative = candle.volume / avg_volume if avg_volume else 1.0
    if relative >= 2.0:
        return 25
    if relative >= 1.5:
        return 20
    if relative >= 1.15:
        return 14
    if relative >= 0.85:
        return 8
    return 3


def _trade_delta_component(candle: MarketCandle, trade_flow: dict | None, expected_flow: str) -> int | None:
    if not isinstance(trade_flow, dict) or not trade_flow.get("data_available"):
        return None
    buckets = trade_flow.get("buckets")
    if not isinstance(buckets, list):
        return None
    candle_time = _timestamp(candle)
    bucket = next(
        (item for item in buckets if isinstance(item, dict) and int(item.get("time", -1)) == candle_time),
        None,
    )
    if bucket is None:
        return None
    buy = _optional_float(bucket.get("buy_volume")) or 0.0
    sell = _optional_float(bucket.get("sell_volume")) or 0.0
    total = buy + sell
    if total <= 0:
        return None
    delta_ratio = (buy - sell) / total
    expected_sign = 1 if expected_flow == "buy" else -1
    aligned = delta_ratio * expected_sign
    if aligned >= 0.45:
        return 25
    if aligned >= 0.25:
        return 20
    if aligned >= 0.10:
        return 14
    if aligned >= -0.10:
        return 8
    return 2


def _level_component(level_score: int) -> int:
    return max(0, min(20, int(round(level_score * 0.2))))


def _dedupe_events(events: list[dict]) -> list[dict]:
    best_by_key: dict[tuple[str, int], dict] = {}
    for event in events:
        key = (event["type"], event["time"])
        previous = best_by_key.get(key)
        if previous is None or event["confidence"] > previous["confidence"]:
            best_by_key[key] = event
    return list(best_by_key.values())


def _has_event(events: list[dict], event_type: str) -> bool:
    return any(event.get("type") == event_type for event in events)


def _range_payload(trading_range: TradingRange) -> dict:
    return {
        "support": {
            "price": round(trading_range.support.price, 8),
            "score": trading_range.support.score,
            "touches": trading_range.support.touches,
            "sources": trading_range.support.sources,
        },
        "resistance": {
            "price": round(trading_range.resistance.price, 8),
            "score": trading_range.resistance.score,
            "touches": trading_range.resistance.touches,
            "sources": trading_range.resistance.sources,
        },
        "start_time": trading_range.start_time,
        "end_time": trading_range.end_time,
        "candles_inside": trading_range.candles_inside,
        "width_pct": trading_range.width_pct,
        "atr": trading_range.atr,
    }


def _normalize_levels(levels: dict[str, list[Any]]) -> dict[str, list[WyckoffLevel]]:
    return {
        "support": _normalize_side(levels.get("support", []), "support"),
        "resistance": _normalize_side(levels.get("resistance", []), "resistance"),
    }


def _normalize_side(levels: list[Any], fallback_kind: str) -> list[WyckoffLevel]:
    normalized: list[WyckoffLevel] = []
    for item in levels:
        if isinstance(item, StructureLevel):
            normalized.append(WyckoffLevel(item.price, item.score, item.touches, item.kind, item.sources))
        elif isinstance(item, dict) and isinstance(item.get("price"), (int, float)):
            normalized.append(
                WyckoffLevel(
                    price=float(item["price"]),
                    score=int(item.get("score", 45)),
                    touches=int(item.get("touches", 1)),
                    kind=str(item.get("kind", fallback_kind)),
                    sources=[str(source) for source in item.get("sources", ["swing"])],
                )
            )
    return sorted(normalized, key=lambda level: (-level.score, level.price))


def _aggregate_daily(candles: list[MarketCandle]) -> list[MarketCandle]:
    by_day: dict[datetime, list[MarketCandle]] = {}
    for candle in candles:
        day = candle.timestamp.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        by_day.setdefault(day, []).append(candle)
    daily: list[MarketCandle] = []
    for day, items in sorted(by_day.items()):
        ordered = sorted(items, key=lambda candle: candle.timestamp)
        daily.append(
            MarketCandle(
                timestamp=day,
                open=ordered[0].open,
                high=max(candle.high for candle in ordered),
                low=min(candle.low for candle in ordered),
                close=ordered[-1].close,
                volume=sum(candle.volume for candle in ordered),
            )
        )
    return daily


def _empty_result(phase: str, comment: str, *, timeframe: str) -> dict:
    return {
        "timeframe": timeframe,
        "phase": phase,
        "phase_hint": phase,
        "side": "neutral",
        "evidence_event_ids": [],
        "range": None,
        "events": [],
        "markers": [],
        "accumulation_score": 35,
        "distribution_score": 35,
        "spring_candidate": False,
        "test_candidate": False,
        "sos_confirmed": False,
        "lps_candidate": False,
        "utad_candidate": False,
        "sow_confirmed": False,
        "lpsy_candidate": False,
        "structure_comment": comment,
        "conflict_note": None,
        "mtf": {"htf_phase": None, "htf_trend": None, "alignment": "neutral"},
        "trend": {
            "direction": "neutral",
            "higher_low": False,
            "lower_high": False,
            "break_of_structure": False,
            "breakdown_structure": False,
        },
    }


def _structure_comment(wyckoff: dict) -> str:
    phase = str(wyckoff.get("phase") or wyckoff.get("phase_hint") or "undetermined")
    side = str(wyckoff.get("side", "neutral"))
    events = wyckoff.get("events", [])
    if phase == "trending":
        return "명확한 거래 박스가 없어 와이코프 이벤트를 표시하지 않았습니다."
    if phase == "undetermined":
        return "이벤트 근거가 부족해 매집/분산 국면을 억지로 확정하지 않았습니다."
    best = max(events, key=lambda event: event.get("confidence", 0), default=None)
    best_text = f" 핵심 근거는 {best['label']} {best['confidence']}점입니다." if best else ""
    if side == "accumulation":
        return f"매집 시나리오 쪽 이벤트가 우세합니다.{best_text}"
    if side == "distribution":
        return f"분산 시나리오 쪽 이벤트가 우세합니다.{best_text}"
    return "와이코프 국면은 중립으로 봅니다."


def _atr(candles: list[MarketCandle], period: int = 14) -> float:
    if len(candles) < 2:
        return candles[-1].close * 0.01 if candles else 1.0
    ranges: list[float] = []
    previous_close = candles[0].close
    for candle in candles[1:]:
        ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )
        previous_close = candle.close
    window = ranges[-period:] if len(ranges) >= period else ranges
    return max(mean(window), candles[-1].close * 0.0001) if window else candles[-1].close * 0.01


def _relative_volume(candles: list[MarketCandle]) -> float:
    if len(candles) < 31:
        return 1.0
    baseline = mean([candle.volume for candle in candles[-31:-1]])
    return candles[-1].volume / baseline if baseline else 1.0


def _rolling_mean(values: list[float], period: int) -> float:
    window = values[-period:] if len(values) >= period else values
    return mean(window) if window else 0.0


def _timestamp(candle: MarketCandle) -> int:
    return int(candle.timestamp.timestamp())


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float) -> int:
    return max(0, min(100, int(round(value))))
