from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

from app.db.models import MarketCandle, MarketSnapshot, Position


MIN_CHART_CANDLES = 100


@dataclass(frozen=True)
class PriceLevel:
    price: float
    strength: str
    label: str


def build_chart_analysis(position: Position, snapshot: MarketSnapshot) -> dict:
    candles = sorted(snapshot.candles, key=lambda candle: candle.timestamp)
    if len(candles) < MIN_CHART_CANDLES:
        raise ValueError("차트 분석에 필요한 캔들 데이터가 부족합니다.")

    recent = candles[-MIN_CHART_CANDLES:]
    mark_price = position.mark_price or position.current_price or snapshot.price or recent[-1].close
    support = _support_levels(recent, mark_price)
    resistance = _resistance_levels(recent, mark_price)
    invalidation = _invalidation_levels(position, mark_price, support, resistance)
    profile = _volume_profile(recent)
    xray = _volume_xray(recent)
    wyckoff_markers = _wyckoff_markers(recent, support, resistance)

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
            "support": [level.__dict__ for level in support],
            "resistance": [level.__dict__ for level in resistance],
            "invalidation": invalidation,
        },
        "indicators": _indicators(recent),
        "volume_profile": profile,
        "volume_xray": xray,
        "wyckoff_markers": wyckoff_markers,
        "data_quality": {
            "candles": len(recent),
            "source": snapshot.provider,
            "estimated_volume_profile": True,
            "last_candle_at": recent[-1].timestamp,
        },
    }


def _candle_payload(candle: MarketCandle) -> dict:
    return {
        "time": int(candle.timestamp.timestamp()),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


def _support_levels(candles: list[MarketCandle], mark_price: float) -> list[PriceLevel]:
    lows = sorted({round(candle.low, 8) for candle in candles if candle.low <= mark_price})
    if not lows:
        lows = sorted({round(candle.low, 8) for candle in candles})
    candidates = sorted(lows, key=lambda price: abs(mark_price - price))[:2]
    return [PriceLevel(price=price, strength=_level_strength(price, candles), label="주요 지지") for price in candidates]


def _resistance_levels(candles: list[MarketCandle], mark_price: float) -> list[PriceLevel]:
    highs = sorted({round(candle.high, 8) for candle in candles if candle.high >= mark_price})
    if not highs:
        highs = sorted({round(candle.high, 8) for candle in candles})
    candidates = sorted(highs, key=lambda price: abs(mark_price - price))[:2]
    return [PriceLevel(price=price, strength=_level_strength(price, candles), label="주요 저항") for price in candidates]


def _invalidation_levels(position: Position, mark_price: float, support: list[PriceLevel], resistance: list[PriceLevel]) -> list[dict]:
    if position.planned_stop_price:
        return [{"price": position.planned_stop_price, "label": "계획 손절/무효화"}]
    if position.direction.value == "long" and support:
        return [{"price": support[0].price, "label": "이탈 시 진입 논리 약화"}]
    if position.direction.value == "short" and resistance:
        return [{"price": resistance[0].price, "label": "돌파 시 진입 논리 약화"}]
    fallback = mark_price * (0.98 if position.direction.value == "long" else 1.02)
    return [{"price": round(fallback, 8), "label": "임시 무효화 기준"}]


def _level_strength(price: float, candles: list[MarketCandle]) -> str:
    tolerance = price * 0.004
    touches = sum(1 for candle in candles if abs(candle.low - price) <= tolerance or abs(candle.high - price) <= tolerance)
    if touches >= 6:
        return "strong"
    if touches >= 3:
        return "medium"
    return "weak"


def _volume_profile(candles: list[MarketCandle], bin_count: int = 24) -> dict:
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
            "buy_volume_proxy": 0.0,
            "sell_volume_proxy": 0.0,
        }
        for index in range(bin_count)
    ]

    for candle in candles:
        low = min(candle.low, candle.high)
        high = max(candle.low, candle.high)
        span = max(high - low, step)
        buy_ratio = 0.62 if candle.close >= candle.open else 0.38
        for bucket in bins:
            overlap = max(0.0, min(high, bucket["price_high"]) - max(low, bucket["price_low"]))
            if overlap <= 0:
                continue
            allocated = candle.volume * (overlap / span)
            bucket["volume"] += allocated
            bucket["buy_volume_proxy"] += allocated * buy_ratio
            bucket["sell_volume_proxy"] += allocated * (1 - buy_ratio)

    for bucket in bins:
        bucket["price_low"] = round(bucket["price_low"], 8)
        bucket["price_high"] = round(bucket["price_high"], 8)
        bucket["volume"] = round(bucket["volume"], 2)
        bucket["buy_volume_proxy"] = round(bucket["buy_volume_proxy"], 2)
        bucket["sell_volume_proxy"] = round(bucket["sell_volume_proxy"], 2)

    poc = max(bins, key=lambda bucket: bucket["volume"])
    value_area = _value_area(bins)
    return {
        "bins": bins,
        "poc_price": round((poc["price_low"] + poc["price_high"]) / 2, 8),
        "value_area_high": value_area["high"],
        "value_area_low": value_area["low"],
        "method": "estimated_ohlcv_proxy",
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


def _volume_xray(candles: list[MarketCandle]) -> dict:
    last = candles[-1]
    recent_volume = mean([candle.volume for candle in candles[-5:]])
    baseline = mean([candle.volume for candle in candles[-30:-5]]) if len(candles) >= 35 else mean([candle.volume for candle in candles[:-5]])
    relative_volume = recent_volume / baseline if baseline else 1.0
    previous_close = candles[-6].close if len(candles) >= 6 else candles[0].close
    push = (last.close - previous_close) / previous_close if previous_close else 0.0
    top_volume = sorted((candle.volume for candle in candles), reverse=True)[:10]
    spike_threshold = top_volume[-1] if top_volume else recent_volume * 1.8
    spike_detected = last.volume >= spike_threshold or relative_volume >= 1.6
    body = abs(last.close - last.open)
    full_range = max(last.high - last.low, last.close * 0.001)
    absorption_candidate = spike_detected and body / full_range < 0.35
    climax_candidate = spike_detected and abs(push) > 0.025
    state = _volume_state(relative_volume, push, spike_detected, climax_candidate)
    return {
        "relative_volume": round(relative_volume, 2),
        "volume_state": state,
        "spike_detected": spike_detected,
        "climax_candidate": climax_candidate,
        "absorption_candidate": absorption_candidate,
        "rebound_with_volume": push > 0 and relative_volume >= 1.2,
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


def _volume_notes(state: str, absorption: bool, climax: bool) -> list[str]:
    notes = {
        "volume_expanding": "최근 거래량이 기준 대비 증가했습니다. 방향성 캔들의 지속 여부를 확인해야 합니다.",
        "declining_after_push": "가격 이동 이후 거래량이 둔화되고 있습니다. 추격보다 반응 확인이 우선입니다.",
        "climax_candidate": "거래량 급증과 큰 가격 이동이 같이 나타나 클라이맥스 후보로 봅니다.",
        "drying_up": "거래량이 말라가는 구간입니다. 돌파/이탈 신뢰도는 낮게 봐야 합니다.",
        "rebound_with_volume": "반등에 거래량이 동반됐습니다. 포지션 방향과 반대라면 리스크 상승 신호입니다.",
        "weak_rebound": "거래량 확장 없이 약한 반응이 이어지고 있습니다.",
    }
    result = [notes.get(state, "거래량 상태를 추가 확인해야 합니다.")]
    if absorption:
        result.append("큰 거래량 대비 캔들 몸통이 작아 흡수 후보로 표시합니다.")
    if climax:
        result.append("클라이맥스 후보는 확정 신호가 아니라 다음 캔들의 반응 확인이 필요합니다.")
    return result


def _wyckoff_markers(candles: list[MarketCandle], support: list[PriceLevel], resistance: list[PriceLevel]) -> list[dict]:
    markers: list[dict] = []
    if support:
        support_price = support[0].price
        for candle in candles[-40:]:
            if candle.low < support_price and candle.close > support_price:
                markers.append(
                    {
                        "time": int(candle.timestamp.timestamp()),
                        "price": candle.low,
                        "type": "spring_candidate",
                        "label": "Spring 후보",
                        "confidence": 62,
                    }
                )
                break
    if resistance:
        resistance_price = resistance[0].price
        for candle in candles[-40:]:
            if candle.high > resistance_price and candle.close < resistance_price:
                markers.append(
                    {
                        "time": int(candle.timestamp.timestamp()),
                        "price": candle.high,
                        "type": "distribution_warning",
                        "label": "Distribution 주의",
                        "confidence": 58,
                    }
                )
                break
    return markers[:3]


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
