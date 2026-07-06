from __future__ import annotations

from typing import Any

from app.backtest.outcomes import aggregate_outcomes, atr, score_event_outcome
from app.backtest.signatures import SetupSignature, signature_key, signature_label
from app.db.models import MarketCandle
from app.marketdata.assets import classify_asset_class
from app.structure.harmonic.engine import detect_harmonic_patterns
from app.structure.levels.engine import StructureLevel, detect_structure_levels
from app.structure.liquidity.engine import analyze_liquidity_structure

MIN_REPLAY_CANDLES = 60
DEFAULT_LOOKAHEAD_BARS = 48


def replay_candles(
    symbol: str,
    timeframe: str,
    candles: list[MarketCandle],
    *,
    asset_class: str | None = None,
    min_window: int = MIN_REPLAY_CANDLES,
    lookahead_bars: int = DEFAULT_LOOKAHEAD_BARS,
    start_index: int | None = None,
) -> list[dict[str, Any]]:
    """Replay detectors over historical candles without future leakage.

    Each detector receives only ``candles[:index+1]``. The event entry price is
    the confirmation candle close, not the original anchor candle.
    """

    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    if len(ordered) < min_window + 5:
        return []
    resolved_asset_class = asset_class or classify_asset_class(symbol)
    first_index = max(min_window, start_index if start_index is not None else min_window)
    last_index = len(ordered) - 2
    cases: list[dict[str, Any]] = []
    for index in range(first_index, last_index + 1):
        past = ordered[: index + 1]
        future = ordered[index + 1 : index + 1 + lookahead_bars]
        if not future:
            continue
        levels = detect_structure_levels(past, past[-1].close)
        events = _events_at_confirmation(symbol, timeframe, resolved_asset_class, past, levels)
        if not events:
            continue
        candle_atr = atr(past)
        for event in events:
            invalidation = _invalidation_price(event["direction"], past[-1].close, levels)
            outcome = score_event_outcome(
                future,
                direction=event["direction"],
                entry_price=past[-1].close,
                invalidation_price=invalidation,
                atr_value=candle_atr,
                max_bars=lookahead_bars,
            )
            cases.append(
                {
                    "symbol": symbol.upper(),
                    "timeframe": timeframe,
                    "asset_class": resolved_asset_class,
                    "as_of": past[-1].timestamp.isoformat(),
                    "confirmation_index": index,
                    "entry_price": round(past[-1].close, 8),
                    "signature": event["signature"],
                    "signature_key": event["signature"]["key"],
                    "event": event["event"],
                    "outcome": outcome,
                    "price_path": _price_path(past[-12:], future[:18]),
                    "disclaimer": "과거 통계 · 미래 보장 아님 · 수수료/슬리피지 미반영",
                }
            )
    return cases


def aggregate_by_signature(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        grouped.setdefault(str(case.get("signature_key")), []).append(case)
    stats: list[dict[str, Any]] = []
    for key, items in grouped.items():
        signature = items[0]["signature"]
        aggregate = aggregate_outcomes(items)
        recent_cases = sorted(items, key=lambda item: str(item.get("as_of") or ""), reverse=True)[:3]
        stats.append(
            {
                "signature_key": key,
                "signature": signature,
                "label": signature_label(signature),
                "scope": "symbol",
                **aggregate,
                "cases": recent_cases,
                "sample_warning": "표본 부족 — 결론 유보" if aggregate["sample_size"] < 10 else None,
                "disclaimer": "과거 통계 · 미래 보장 아님 · 수수료/슬리피지 미반영",
            }
        )
    return sorted(stats, key=lambda item: (item["sample_size"], item.get("win_1r_pct") or 0), reverse=True)


def _events_at_confirmation(
    symbol: str,
    timeframe: str,
    asset_class: str,
    past: list[MarketCandle],
    levels: dict[str, list[StructureLevel]],
) -> list[dict[str, Any]]:
    current = past[-1]
    events: list[dict[str, Any]] = []
    events.extend(_liquidity_events(symbol, timeframe, asset_class, past, levels, current))
    events.extend(_level_events(timeframe, asset_class, current, levels))
    events.extend(_harmonic_events(timeframe, asset_class, past, levels, current))
    return _dedupe_events(events)


def _liquidity_events(
    symbol: str,
    timeframe: str,
    asset_class: str,
    past: list[MarketCandle],
    levels: dict[str, list[StructureLevel]],
    current: MarketCandle,
) -> list[dict[str, Any]]:
    liquidity = analyze_liquidity_structure(past, mark_price=current.close, levels=levels)
    result: list[dict[str, Any]] = []
    for sweep in _list(liquidity.get("sweeps")) + _list(liquidity.get("htf_range_sweeps")):
        if not sweep.get("confirmed"):
            continue
        if str(sweep.get("return_at"))[:19] != current.timestamp.isoformat()[:19]:
            continue
        direction = "long" if sweep.get("side") == "sell_side" else "short" if sweep.get("side") == "buy_side" else None
        if direction is None:
            continue
        prefix = "htf_sweep" if sweep.get("type") == "htf_range_sweep" else "sweep"
        suffix = "low" if direction == "long" else "high"
        signature = SetupSignature(
            engine="liquidity",
            event_type=f"{prefix}_{suffix}",
            strength_class=str(sweep.get("grade") or "unknown"),
            direction=direction,
            asset_class=asset_class,
            timeframe=timeframe,
        ).model_dump()
        signature["key"] = signature_key(signature)
        signature["label"] = signature_label(signature)
        result.append({"direction": direction, "signature": signature, "event": sweep})
    return result


def _level_events(
    timeframe: str,
    asset_class: str,
    current: MarketCandle,
    levels: dict[str, list[StructureLevel]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for direction, kind in (("long", "support"), ("short", "resistance")):
        for level in levels.get(kind, []):
            if level.score < 70:
                continue
            touched = current.low <= level.price <= current.high
            if not touched:
                continue
            signature = SetupSignature(
                engine="levels",
                event_type="level_touch",
                strength_class="score>=70" if level.score < 80 else "score>=80",
                direction=direction,
                asset_class=asset_class,
                timeframe=timeframe,
            ).model_dump()
            signature["key"] = signature_key(signature)
            signature["label"] = signature_label(signature)
            result.append({"direction": direction, "signature": signature, "event": level.model_dump()})
            break
    return result


def _harmonic_events(
    timeframe: str,
    asset_class: str,
    past: list[MarketCandle],
    levels: dict[str, list[StructureLevel]],
    current: MarketCandle,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    harmonic = detect_harmonic_patterns(past, levels=levels, min_confidence=70)
    for pattern in harmonic.get("patterns", []) if isinstance(harmonic.get("patterns"), list) else []:
        prz = pattern.get("prz") if isinstance(pattern.get("prz"), dict) else {}
        low = _float(prz.get("low"))
        high = _float(prz.get("high"))
        if low is None or high is None or not (low <= current.close <= high):
            continue
        direction = "long" if pattern.get("direction") == "bullish" else "short" if pattern.get("direction") == "bearish" else None
        if direction is None:
            continue
        confidence = int(_float(pattern.get("confidence")) or 0)
        strength = "conf>=80" if confidence >= 80 else "conf>=70"
        signature = SetupSignature(
            engine="harmonic",
            event_type="prz_touch",
            strength_class=strength,
            direction=direction,
            asset_class=asset_class,
            timeframe=timeframe,
        ).model_dump()
        signature["key"] = signature_key(signature)
        signature["label"] = signature_label(signature)
        result.append({"direction": direction, "signature": signature, "event": pattern})
    return result


def _invalidation_price(direction: str, entry: float, levels: dict[str, list[StructureLevel]]) -> float | None:
    if direction == "long":
        below = [level for level in levels.get("support", []) if level.price < entry]
        return below[0].price if below else None
    above = [level for level in levels.get("resistance", []) if level.price > entry]
    return above[0].price if above else None


def _price_path(past: list[MarketCandle], future: list[MarketCandle]) -> list[dict[str, Any]]:
    items = [*past, *future]
    return [
        {
            "time": candle.timestamp.isoformat(),
            "close": round(candle.close, 8),
            "high": round(candle.high, 8),
            "low": round(candle.low, 8),
        }
        for candle in items
    ]


def _dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for event in events:
        key = (event["signature"]["key"], str(event["event"].get("id") if isinstance(event.get("event"), dict) else event["signature"]["key"]))
        if key in seen:
            continue
        seen.add(key)
        result.append(event)
    return result


def _list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

