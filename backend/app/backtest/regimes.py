"""레짐 라벨링 (WO-FCE-36 §5) — 결정론.

200MA 기울기 + ATR 백분위로 4분류:
상승추세 / 하락추세 / 저변동 횡보 / 고변동 횡보.
임계값은 config 소유 — 하드코딩 변경은 제안-승인 경유(WO-22).
"""

from __future__ import annotations

from typing import Any

from app.db.models import MarketCandle

REGIME_LABELS: dict[str, str] = {
    "uptrend": "상승추세",
    "downtrend": "하락추세",
    "quiet_range": "저변동 횡보",
    "volatile_range": "고변동 횡보",
    "unknown": "레짐 미판정",
}

MIN_MA_PERIOD = 40


def label_regime(
    candles: list[MarketCandle],
    *,
    ma_period: int = 200,
    slope_window: int = 20,
    slope_threshold_pct: float = 1.0,
    atr_lookback: int = 120,
    atr_high_percentile: float = 70.0,
) -> dict[str, Any]:
    """현 시점 레짐 판정. 데이터가 짧으면 MA 기간을 줄여 판정하고 사용 기간을 기록한다."""
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    closes = [candle.close for candle in ordered]
    n = len(closes)
    if n < MIN_MA_PERIOD + slope_window:
        return {"regime": "unknown", "regime_label": REGIME_LABELS["unknown"], "reason": "insufficient_candles", "candles": n}

    used_period = min(ma_period, n - slope_window)
    used_period = max(MIN_MA_PERIOD, used_period)
    ma_series = _sma_series(closes, used_period)
    if len(ma_series) < slope_window:
        return {"regime": "unknown", "regime_label": REGIME_LABELS["unknown"], "reason": "insufficient_ma_points", "candles": n}

    ma_now = ma_series[-1]
    ma_then = ma_series[-slope_window]
    slope_pct = round((ma_now - ma_then) / ma_then * 100, 3) if ma_then else 0.0

    atr_percentile = _atr_percentile(ordered, lookback=atr_lookback)

    if slope_pct > slope_threshold_pct:
        regime = "uptrend"
    elif slope_pct < -slope_threshold_pct:
        regime = "downtrend"
    elif atr_percentile is not None and atr_percentile >= atr_high_percentile:
        regime = "volatile_range"
    else:
        regime = "quiet_range"

    return {
        "regime": regime,
        "regime_label": REGIME_LABELS[regime],
        "ma_period_used": used_period,
        "ma_slope_pct": slope_pct,
        "atr_percentile": atr_percentile,
        "thresholds": {"slope_pct": slope_threshold_pct, "atr_high_percentile": atr_high_percentile},
    }


def _sma_series(closes: list[float], period: int) -> list[float]:
    if len(closes) < period:
        return []
    series: list[float] = []
    window_sum = sum(closes[:period])
    series.append(window_sum / period)
    for index in range(period, len(closes)):
        window_sum += closes[index] - closes[index - period]
        series.append(window_sum / period)
    return series


def _atr_percentile(candles: list[MarketCandle], *, lookback: int, period: int = 14) -> float | None:
    """현재 ATR(14)이 최근 lookback 구간 ATR 분포에서 차지하는 백분위."""
    if len(candles) < period + 5:
        return None
    true_ranges: list[float] = []
    previous_close = candles[0].close
    for candle in candles[1:]:
        true_ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )
        previous_close = candle.close
    atr_series: list[float] = []
    for index in range(period, len(true_ranges) + 1):
        window = true_ranges[index - period : index]
        atr_series.append(sum(window) / period)
    if len(atr_series) < 5:
        return None
    history = atr_series[-lookback:]
    current = history[-1]
    below = sum(1 for value in history if value <= current)
    return round(below / len(history) * 100, 1)
