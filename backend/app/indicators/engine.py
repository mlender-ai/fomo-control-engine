from statistics import mean

from app.db.models import MarketCandle, MarketSnapshot


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value - result[-1]) * multiplier + result[-1])
    return result


def _rsi(closes: list[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for previous, current in zip(closes[-period - 1 : -1], closes[-period:]):
        change = current - previous
        gains.append(max(change, 0))
        losses.append(abs(min(change, 0)))
    average_gain = mean(gains) if gains else 0
    average_loss = mean(losses) if losses else 0
    if average_loss == 0:
        return 100.0
    return 100 - (100 / (1 + average_gain / average_loss))


def _atr(candles: list[MarketCandle], period: int = 14) -> float:
    if len(candles) <= period:
        return 0.0
    ranges = []
    for previous, current in zip(candles[-period - 1 : -1], candles[-period:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return mean(ranges)


def calculate_indicators(snapshot: MarketSnapshot) -> dict:
    closes = [candle.close for candle in snapshot.candles]
    volumes = [candle.volume for candle in snapshot.candles]
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [fast - slow for fast, slow in zip(ema12[-len(ema26) :], ema26)]
    signal = _ema(macd_line, 9)
    macd_histogram = macd_line[-1] - signal[-1] if signal else 0.0
    recent_volume = volumes[-1]
    average_volume = mean(volumes[-21:-1]) if len(volumes) > 21 else mean(volumes)
    rolling_mean = mean(closes[-20:])
    variance = mean([(close - rolling_mean) ** 2 for close in closes[-20:]])
    deviation = variance**0.5

    return {
        "rsi": round(_rsi(closes), 2),
        "macd_histogram": round(macd_histogram, 4),
        "bollinger_upper": round(rolling_mean + deviation * 2, 4),
        "bollinger_mid": round(rolling_mean, 4),
        "bollinger_lower": round(rolling_mean - deviation * 2, 4),
        "atr": round(_atr(snapshot.candles), 4),
        "relative_volume": round(recent_volume / average_volume, 2) if average_volume else 1,
        "last_close": closes[-1],
        "previous_close": closes[-2],
        "twenty_close": closes[-20],
    }
