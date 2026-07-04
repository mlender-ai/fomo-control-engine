from __future__ import annotations

from datetime import datetime, timezone
from statistics import mean
from typing import Any

from app.core.config import get_settings
from app.db.models import MarketCandle, MarketSnapshot, Position
from app.structure.harmonic.engine import detect_harmonic_patterns
from app.structure.levels.engine import StructureLevel, detect_structure_levels
from app.structure.wyckoff.engine import analyze_wyckoff


MIN_CHART_CANDLES = 100
_HARMONIC_CACHE: dict[tuple[str, str, int, int, float], dict[str, Any]] = {}


def build_chart_analysis(position: Position, snapshot: MarketSnapshot, trade_flow: dict | None = None) -> dict:
    candles = sorted(snapshot.candles, key=lambda candle: candle.timestamp)
    if len(candles) < MIN_CHART_CANDLES:
        raise ValueError("차트 분석에 필요한 캔들 데이터가 부족합니다.")

    recent = candles[-200:]
    mark_price = position.mark_price or position.current_price or snapshot.price or recent[-1].close
    profile = _volume_profile(recent, trade_flow)
    levels = detect_structure_levels(recent, mark_price, profile)
    support = levels["support"]
    resistance = levels["resistance"]
    invalidation = _invalidation_levels(position, support, resistance)
    xray = _volume_xray(recent, trade_flow)
    wyckoff = analyze_wyckoff(recent, levels=levels, trade_flow=trade_flow, timeframe=snapshot.timeframe)
    wyckoff_events = split_wyckoff_events(wyckoff, get_settings().wyckoff_event_min_confidence)
    harmonic = _harmonic_analysis(position.symbol, snapshot.timeframe, recent, levels, profile)

    return {
        "position_id": str(position.id),
        "symbol": position.symbol,
        "timeframe": snapshot.timeframe,
        "direction": position.direction.value,
        "entry_price": position.entry_price,
        "mark_price": mark_price,
        "liquidation_price": position.liquidation_price,
        "candles": [_candle_payload(candle) for candle in recent],
        "price_levels": {
            "entry": position.entry_price,
            "mark": mark_price,
            "liquidation": position.liquidation_price,
            "support": [level.model_dump() for level in support],
            "resistance": [level.model_dump() for level in resistance],
            "invalidation": invalidation,
        },
        "indicators": _indicators(recent),
        "volume_profile": profile,
        "volume_xray": xray,
        "trade_flow": _trade_flow_payload(trade_flow),
        "wyckoff": wyckoff,
        "wyckoff_range": wyckoff.get("range"),
        "wyckoff_phase": {
            "phase": wyckoff.get("phase", "undetermined"),
            "side": wyckoff.get("side", "neutral"),
            "evidence_event_ids": wyckoff.get("evidence_event_ids", []),
            "phase_evidence": wyckoff_events["phase_evidence"],
        },
        "wyckoff_mtf": wyckoff.get("mtf", {"htf_phase": None, "htf_trend": None, "alignment": "neutral"}),
        "wyckoff_markers": wyckoff_events["events"],
        "wyckoff_markers_low_confidence": wyckoff_events["events_low_confidence"],
        "harmonic": harmonic,
        "harmonic_patterns": harmonic.get("patterns", []),
        "harmonic_prz": _harmonic_prz(harmonic.get("patterns", [])),
        "data_quality": {
            "candles": len(recent),
            "source": snapshot.provider,
            "estimated_volume_profile": profile["method"] != "trade_fills",
            "volume_profile_method": profile["method"],
            "last_candle_at": recent[-1].timestamp,
        },
    }


def split_wyckoff_events(wyckoff: dict[str, Any], min_confidence: int, display_limit: int = 4) -> dict[str, Any]:
    """표시용 이벤트(임계값 이상, 최근 N개)와 저신뢰 이벤트를 분리하고 phase 판정 근거를 명시한다.

    저신뢰 이벤트는 삭제하지 않는다 — 복기/캘리브레이션에 원본이 필요하다.
    """
    events = [event for event in wyckoff.get("events", []) if isinstance(event, dict)]
    high = [event for event in events if int(event.get("confidence", 0)) >= min_confidence]
    low = [event for event in events if int(event.get("confidence", 0)) < min_confidence]
    evidence_ids = set(wyckoff.get("evidence_event_ids", []))
    return {
        "events": high[-display_limit:],
        "events_low_confidence": low,
        "phase_evidence": [event for event in events if event.get("id") in evidence_ids],
    }


def _harmonic_analysis(symbol: str, timeframe: str, candles: list[MarketCandle], levels: dict[str, list[StructureLevel]], volume_profile: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    key = (symbol, timeframe, int(candles[-1].timestamp.timestamp()), len(candles), candles[-1].close)
    cached = _HARMONIC_CACHE.get(key)
    if cached is not None:
        return cached
    result = detect_harmonic_patterns(
        candles,
        levels=levels,
        volume_profile=volume_profile,
        atr_multiplier=settings.harmonic_zigzag_atr_multiplier,
        min_confidence=settings.harmonic_min_confidence,
    )
    if len(_HARMONIC_CACHE) > 64:
        _HARMONIC_CACHE.clear()
    _HARMONIC_CACHE[key] = result
    return result


def _harmonic_prz(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    zones = []
    for pattern in patterns:
        prz = pattern.get("prz")
        if not isinstance(prz, dict):
            continue
        zones.append(
            {
                "pattern_id": pattern.get("id"),
                "pattern": pattern.get("label"),
                "direction": pattern.get("direction"),
                "status": pattern.get("status"),
                "confidence": pattern.get("confidence"),
                "low": prz.get("low"),
                "high": prz.get("high"),
                "mid": prz.get("mid"),
                "basis": pattern.get("basis"),
            }
        )
    return zones


def _candle_payload(candle: MarketCandle) -> dict:
    return {
        "time": int(candle.timestamp.timestamp()),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


def _invalidation_levels(position: Position, support: list[StructureLevel], resistance: list[StructureLevel]) -> list[dict]:
    if position.planned_stop_price:
        return [{"price": position.planned_stop_price, "label": "계획 손절/무효화", "source": "user"}]
    candidates = support if position.direction.value == "long" else resistance
    strong_candidates = [level for level in candidates if level.score >= 40]
    if strong_candidates:
        level = strong_candidates[0]
        action = "이탈 시 진입 논리 약화" if position.direction.value == "long" else "돌파 시 진입 논리 약화"
        return [{**level.model_dump(), "label": action, "source": "structure_level"}]
    return [{"price": None, "label": "구조 레벨 부족, 사용자 손절 기준 필요", "source": "insufficient_structure"}]


def _volume_profile(candles: list[MarketCandle], trade_flow: dict | None = None, bin_count: int = 24) -> dict:
    price_low = min(candle.low for candle in candles)
    price_high = max(candle.high for candle in candles)
    if price_high <= price_low:
        price_high = price_low * 1.01
    step = (price_high - price_low) / bin_count
    bins = [
        {
            "price_low": price_low + step * index,
            "price_high": price_low + step * (index + 1),
            "volume": 0.0,
            "_buy_volume": 0.0,
            "_sell_volume": 0.0,
            "_methods": set(),
        }
        for index in range(bin_count)
    ]
    coverage = _trade_coverage(trade_flow)

    for candle in candles:
        if coverage and coverage["from"] <= candle.timestamp <= coverage["to"]:
            continue
        low = min(candle.low, candle.high)
        high = max(candle.low, candle.high)
        span = max(high - low, step)
        for bucket in bins:
            overlap = max(0.0, min(high, bucket["price_high"]) - max(low, bucket["price_low"]))
            if overlap <= 0:
                continue
            allocated = candle.volume * (overlap / span)
            bucket["volume"] += allocated
            bucket["_methods"].add("ohlcv_estimated")

    for fill in _trade_fills(trade_flow):
        price = fill.get("price")
        size = fill.get("size")
        side = str(fill.get("side", "")).lower()
        if not isinstance(price, (int, float)) or not isinstance(size, (int, float)) or size <= 0:
            continue
        index = min(max(int((price - price_low) / step), 0), bin_count - 1)
        bucket = bins[index]
        bucket["volume"] += size
        bucket["_methods"].add("trade_fills")
        if side == "buy":
            bucket["_buy_volume"] += size
        elif side == "sell":
            bucket["_sell_volume"] += size

    for bucket in bins:
        methods = bucket.pop("_methods")
        buy_volume = float(bucket.pop("_buy_volume"))
        sell_volume = float(bucket.pop("_sell_volume"))
        bucket["price_low"] = round(bucket["price_low"], 8)
        bucket["price_high"] = round(bucket["price_high"], 8)
        bucket["volume"] = round(bucket["volume"], 2)
        if "trade_fills" in methods:
            bucket["buy_volume"] = round(buy_volume, 8)
            bucket["sell_volume"] = round(sell_volume, 8)
            bucket["delta"] = round(buy_volume - sell_volume, 8)
        if "trade_fills" in methods and "ohlcv_estimated" in methods:
            bucket["method"] = "mixed"
        elif "trade_fills" in methods:
            bucket["method"] = "trade_fills"
        else:
            bucket["method"] = "ohlcv_estimated"

    poc = max(bins, key=lambda bucket: bucket["volume"])
    value_area = _value_area(bins)
    source_methods = sorted({bucket["method"] for bucket in bins if bucket["volume"] > 0}) or ["ohlcv_estimated"]
    method = source_methods[0] if len(source_methods) == 1 else "mixed"
    has_trade_fills = any(method in {"trade_fills", "mixed"} for method in source_methods)
    return {
        "bins": bins,
        "poc_price": round((poc["price_low"] + poc["price_high"]) / 2, 8),
        "value_area_high": value_area["high"],
        "value_area_low": value_area["low"],
        "method": method,
        "source_methods": source_methods,
        "has_trade_fills": has_trade_fills,
        "coverage": trade_flow.get("coverage") if isinstance(trade_flow, dict) else None,
    }


def _value_area(bins: list[dict]) -> dict:
    total = sum(bucket["volume"] for bucket in bins)
    if total <= 0:
        return {"high": bins[-1]["price_high"], "low": bins[0]["price_low"]}
    selected: list[dict] = []
    running = 0.0
    for bucket in sorted(bins, key=lambda item: item["volume"], reverse=True):
        selected.append(bucket)
        running += bucket["volume"]
        if running / total >= 0.7:
            break
    return {
        "high": round(max(bucket["price_high"] for bucket in selected), 8),
        "low": round(min(bucket["price_low"] for bucket in selected), 8),
    }


def _volume_xray(candles: list[MarketCandle], trade_flow: dict | None = None) -> dict:
    last = candles[-1]
    recent_volume = mean([candle.volume for candle in candles[-5:]])
    baseline = mean([candle.volume for candle in candles[-30:-5]]) if len(candles) >= 35 else mean([candle.volume for candle in candles[:-5]])
    relative_volume = recent_volume / baseline if baseline else 1.0
    buckets = _trade_buckets(trade_flow)
    if not buckets:
        return {
            "relative_volume": round(relative_volume, 2),
            "relative_volume_method": "ohlcv",
            "volume_state": "data_unavailable",
            "method": "data_unavailable",
            "data_available": False,
            "spike_detected": False,
            "climax_candidate": False,
            "absorption_candidate": False,
            "rebound_with_volume": False,
            "delta_ratio": None,
            "cvd_change": None,
            "notes": ["실체결 데이터가 없어 CVD, 흡수, 클라이맥스 판정을 보류합니다."],
        }
    previous_close = candles[-6].close if len(candles) >= 6 else candles[0].close
    push = (last.close - previous_close) / previous_close if previous_close else 0.0
    recent_buckets = buckets[-5:]
    previous_buckets = buckets[:-5]
    recent_trade_volume = mean([_bucket_total(bucket) for bucket in recent_buckets]) if recent_buckets else 0.0
    baseline_trade_volume = mean([_bucket_total(bucket) for bucket in previous_buckets]) if previous_buckets else recent_trade_volume
    relative_trade_volume = recent_trade_volume / baseline_trade_volume if baseline_trade_volume else 1.0
    recent_delta = sum(float(bucket.get("delta", 0.0)) for bucket in recent_buckets)
    recent_total = sum(_bucket_total(bucket) for bucket in recent_buckets)
    delta_ratio = recent_delta / recent_total if recent_total else 0.0
    abs_delta_baseline = mean([abs(float(bucket.get("delta", 0.0))) for bucket in buckets[:-1]]) if len(buckets) > 1 else abs(recent_delta)
    spike_detected = relative_trade_volume >= 1.8
    body = abs(last.close - last.open)
    full_range = max(last.high - last.low, last.close * 0.001)
    body_direction = 1 if last.close > last.open else -1 if last.close < last.open else 0
    delta_direction = 1 if delta_ratio > 0.15 else -1 if delta_ratio < -0.15 else 0
    absorption_candidate = abs(delta_ratio) >= 0.35 and body / full_range < 0.35 and delta_direction != 0 and delta_direction != body_direction
    climax_candidate = abs(recent_delta) >= abs_delta_baseline * 2 and abs(push) > 0.025
    state = _trade_volume_state(relative_trade_volume, delta_ratio, spike_detected, absorption_candidate, climax_candidate)
    return {
        "relative_volume": round(relative_trade_volume, 2),
        "relative_volume_method": "trade_fills",
        "volume_state": state,
        "method": "trade_fills",
        "data_available": True,
        "spike_detected": spike_detected,
        "climax_candidate": climax_candidate,
        "absorption_candidate": absorption_candidate,
        "rebound_with_volume": push > 0 and relative_volume >= 1.2,
        "delta_ratio": round(delta_ratio, 4),
        "cvd_change": round(recent_delta, 8),
        "notes": _volume_notes(state, absorption_candidate, climax_candidate),
    }


def _volume_state(relative_volume: float, push: float, spike_detected: bool, climax_candidate: bool) -> str:
    if climax_candidate:
        return "climax_candidate"
    if spike_detected:
        return "volume_expanding"
    if relative_volume < 0.7:
        return "drying_up"
    if push > 0.01 and relative_volume >= 1.2:
        return "rebound_with_volume"
    if abs(push) > 0.01 and relative_volume < 1:
        return "declining_after_push"
    return "weak_rebound"


def _trade_volume_state(relative_volume: float, delta_ratio: float, spike_detected: bool, absorption_candidate: bool, climax_candidate: bool) -> str:
    if climax_candidate:
        return "climax_candidate"
    if absorption_candidate:
        return "absorption_candidate"
    if spike_detected:
        return "volume_expanding"
    if abs(delta_ratio) >= 0.25:
        return "delta_imbalanced"
    if relative_volume < 0.7:
        return "drying_up"
    return "balanced_flow"


def _volume_notes(state: str, absorption: bool, climax: bool) -> list[str]:
    notes = {
        "volume_expanding": "최근 거래량이 기준 대비 증가했습니다. 방향성 캔들의 지속 여부를 확인해야 합니다.",
        "declining_after_push": "가격 이동 이후 거래량이 둔화되고 있습니다. 추격보다 반응 확인이 우선입니다.",
        "climax_candidate": "거래량 급증과 큰 가격 이동이 같이 나타나 클라이맥스 후보로 봅니다.",
        "drying_up": "거래량이 말라가는 구간입니다. 돌파/이탈 신뢰도는 낮게 봐야 합니다.",
        "rebound_with_volume": "반등에 거래량이 동반됐습니다. 포지션 방향과 반대라면 리스크 상승 신호입니다.",
        "weak_rebound": "거래량 확장 없이 약한 반응이 이어지고 있습니다.",
        "absorption_candidate": "실체결 델타와 가격 반응이 엇갈려 흡수 후보로 봅니다.",
        "delta_imbalanced": "최근 체결 델타가 한쪽으로 기울었습니다. 포지션 방향과 정렬되는지 확인해야 합니다.",
        "balanced_flow": "최근 체결 델타는 한쪽으로 크게 치우치지 않았습니다.",
    }
    result = [notes.get(state, "거래량 상태를 추가 확인해야 합니다.")]
    if absorption:
        result.append("큰 거래량 대비 캔들 몸통이 작아 흡수 후보로 표시합니다.")
    if climax:
        result.append("클라이맥스 후보는 확정 신호가 아니라 다음 캔들의 반응 확인이 필요합니다.")
    return result


def _trade_flow_payload(trade_flow: dict | None) -> dict:
    if not isinstance(trade_flow, dict):
        return {
            "method": "data_unavailable",
            "source": "none",
            "data_available": False,
            "coverage": None,
            "buckets": [],
            "cvd": [],
            "notes": ["실체결 데이터 제공자가 연결되지 않았습니다."],
        }
    return {
        "method": trade_flow.get("method", "data_unavailable"),
        "source": trade_flow.get("source"),
        "data_available": bool(trade_flow.get("data_available")),
        "coverage": trade_flow.get("coverage"),
        "buckets": _trade_buckets(trade_flow),
        "cvd": trade_flow.get("cvd", []),
        "notes": trade_flow.get("notes", []),
    }


def _trade_fills(trade_flow: dict | None) -> list[dict[str, Any]]:
    if not isinstance(trade_flow, dict) or not trade_flow.get("data_available"):
        return []
    fills = trade_flow.get("fills")
    if not isinstance(fills, list):
        return []
    return [fill for fill in fills if isinstance(fill, dict)]


def _trade_buckets(trade_flow: dict | None) -> list[dict[str, Any]]:
    if not isinstance(trade_flow, dict) or not trade_flow.get("data_available"):
        return []
    buckets = trade_flow.get("buckets")
    if not isinstance(buckets, list):
        return []
    return [bucket for bucket in buckets if isinstance(bucket, dict)]


def _trade_coverage(trade_flow: dict | None) -> dict[str, datetime] | None:
    if not isinstance(trade_flow, dict) or not trade_flow.get("data_available"):
        return None
    coverage = trade_flow.get("coverage")
    if not isinstance(coverage, dict):
        return None
    start = _parse_datetime(coverage.get("from"))
    end = _parse_datetime(coverage.get("to"))
    if start is None or end is None:
        return None
    return {"from": start, "to": end}


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _bucket_total(bucket: dict[str, Any]) -> float:
    return float(bucket.get("buy_volume", 0.0)) + float(bucket.get("sell_volume", 0.0))


def _indicators(candles: list[MarketCandle]) -> dict:
    closes = [candle.close for candle in candles]
    return {
        "rsi": _rsi_series(candles, closes),
        "macd": _macd_series(candles, closes),
        "bollinger": _bollinger_series(candles, closes),
    }


def _rsi_series(candles: list[MarketCandle], closes: list[float], period: int = 14) -> list[dict]:
    values: list[dict] = []
    for index in range(period, len(closes)):
        window = closes[index - period : index + 1]
        gains = [max(0.0, window[i] - window[i - 1]) for i in range(1, len(window))]
        losses = [max(0.0, window[i - 1] - window[i]) for i in range(1, len(window))]
        avg_gain = mean(gains) if gains else 0.0
        avg_loss = mean(losses) if losses else 0.0
        rsi = 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
        values.append({"time": int(candles[index].timestamp.timestamp()), "value": round(rsi, 2)})
    return values


def _macd_series(candles: list[MarketCandle], closes: list[float]) -> list[dict]:
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd = [fast - slow for fast, slow in zip(ema12, ema26)]
    signal = _ema(macd, 9)
    return [
        {
            "time": int(candles[index].timestamp.timestamp()),
            "macd": round(macd[index], 8),
            "signal": round(signal[index], 8),
            "histogram": round(macd[index] - signal[index], 8),
        }
        for index in range(len(candles))
    ]


def _bollinger_series(candles: list[MarketCandle], closes: list[float], period: int = 20) -> dict:
    upper: list[dict] = []
    middle: list[dict] = []
    lower: list[dict] = []
    for index in range(period - 1, len(closes)):
        window = closes[index - period + 1 : index + 1]
        avg = mean(window)
        variance = mean([(value - avg) ** 2 for value in window])
        band = variance**0.5 * 2
        time = int(candles[index].timestamp.timestamp())
        upper.append({"time": time, "value": round(avg + band, 8)})
        middle.append({"time": time, "value": round(avg, 8)})
        lower.append({"time": time, "value": round(avg - band, 8)})
    return {"upper": upper, "middle": middle, "lower": lower}


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result
