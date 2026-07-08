from __future__ import annotations

from statistics import median
from typing import Any

from app.db.models import MarketCandle


def score_event_outcome(
    future_candles: list[MarketCandle],
    *,
    direction: str,
    entry_price: float,
    invalidation_price: float | None = None,
    atr_value: float | None = None,
    max_bars: int = 48,
    cost_pct: float = 0.0,
) -> dict[str, Any]:
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    ordered = sorted(future_candles, key=lambda candle: candle.timestamp)[:max_bars]
    if not ordered:
        return _empty(entry_price, invalidation_price, atr_value, cost_pct, direction=direction)

    risk = _risk_distance(entry_price, invalidation_price, atr_value)
    fallback = invalidation_price is None or invalidation_price <= 0
    # 비용(수수료 왕복 + 슬리피지)을 가격으로 환산 — 목표는 비용을 덮고 나서야 net 달성.
    cost_price = entry_price * max(0.0, cost_pct) / 100.0
    cost_r = round(cost_price / risk, 4) if risk else 0.0
    stop = entry_price - risk if direction == "long" else entry_price + risk
    target_1r = entry_price + (risk + cost_price) if direction == "long" else entry_price - (risk + cost_price)
    target_2r = entry_price + (risk * 2 + cost_price) if direction == "long" else entry_price - (risk * 2 + cost_price)
    gross_target_1r = entry_price + risk if direction == "long" else entry_price - risk
    gross_target_2r = entry_price + risk * 2 if direction == "long" else entry_price - risk * 2

    mfe = 0.0
    mae = 0.0
    win_1r: bool | None = None
    win_2r: bool | None = None
    gross_win_1r = False
    gross_win_2r = False
    resolved_bars: int | None = None
    realized_rr: float | None = None

    for offset, candle in enumerate(ordered, start=1):
        favorable = (candle.high - entry_price) / risk if direction == "long" else (entry_price - candle.low) / risk
        adverse = (entry_price - candle.low) / risk if direction == "long" else (candle.high - entry_price) / risk
        mfe = max(mfe, favorable)
        mae = max(mae, adverse)

        stop_hit = candle.low <= stop if direction == "long" else candle.high >= stop
        one_hit = candle.high >= target_1r if direction == "long" else candle.low <= target_1r
        two_hit = candle.high >= target_2r if direction == "long" else candle.low <= target_2r
        if win_1r is None and not gross_win_1r:
            gross_win_1r = candle.high >= gross_target_1r if direction == "long" else candle.low <= gross_target_1r
        if win_2r is None and not gross_win_2r:
            gross_win_2r = candle.high >= gross_target_2r if direction == "long" else candle.low <= gross_target_2r

        # Same-candle stop/target is intentionally conservative: loss first.
        if stop_hit:
            if win_1r:
                # 1R은 이미 확보 — rr은 1R 익절 기준을 유지해야 win_1r 집계와 정합.
                win_2r = False
                break
            win_1r = False
            win_2r = False
            resolved_bars = offset
            realized_rr = round(-1.0 - cost_r, 3)
            break
        if one_hit and win_1r is None:
            win_1r = True
            resolved_bars = offset if resolved_bars is None else resolved_bars
            realized_rr = max(realized_rr or 0.0, 1.0)
        if two_hit and win_2r is None:
            win_2r = True
            resolved_bars = offset
            realized_rr = 2.0
            break

    if realized_rr is None and ordered:
        # 목표/스탑 미도달 타임아웃 — 최종 종가 마크(net). 피크(MFE) 기준은 낙관 편향.
        final_close = ordered[-1].close
        mark = (final_close - entry_price) / risk if direction == "long" else (entry_price - final_close) / risk
        realized_rr = round(mark - cost_r, 3)

    return {
        "entry_price": round(entry_price, 8),
        "invalidation_price": round(stop, 8),
        "risk_distance": round(risk, 8),
        "risk_fallback": fallback,
        "cost_pct": round(max(0.0, cost_pct), 4),
        "cost_r": cost_r,
        "win_1r": bool(win_1r) if win_1r is not None else False,
        "win_2r": bool(win_2r) if win_2r is not None else False,
        "mfe_r": round(mfe, 3),
        "mae_r": round(mae, 3),
        "resolved_bars": resolved_bars or len(ordered),
        "realized_rr": round(realized_rr if realized_rr is not None else 0.0, 3),
        "bars_evaluated": len(ordered),
        # gross는 내부 디버그 전용 — 집계·발행 금지.
        "gross_debug": {"win_1r": gross_win_1r or bool(win_1r), "win_2r": gross_win_2r or bool(win_2r)},
        "policy": (
            "가상 진입은 확정 캔들 종가, 같은 캔들 목표/무효화 동시 도달은 보수적으로 실패 처리, "
            "1R/2R 판정은 비용(수수료·슬리피지) 차감 후 net 기준. "
            "realized_rr: 도달 목표 기준(1R/2R), 스탑 -1R-비용, 미해결 타임아웃은 최종 종가 마크"
        ),
    }


def aggregate_outcomes(cases: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = [case.get("outcome") for case in cases if isinstance(case.get("outcome"), dict)]
    n = len(outcomes)
    if n == 0:
        return {
            "sample_size": 0,
            "win_1r_pct": None,
            "win_2r_pct": None,
            "median_rr": None,
            "avg_mfe_r": None,
            "avg_mae_r": None,
            "avg_resolution_bars": None,
        }
    return {
        "sample_size": n,
        "win_1r_pct": round(sum(1 for item in outcomes if item.get("win_1r")) / n * 100, 1),
        "win_2r_pct": round(sum(1 for item in outcomes if item.get("win_2r")) / n * 100, 1),
        "median_rr": round(median([float(item.get("realized_rr") or 0.0) for item in outcomes]), 2),
        "avg_mfe_r": round(sum(float(item.get("mfe_r") or 0.0) for item in outcomes) / n, 2),
        "avg_mae_r": round(sum(float(item.get("mae_r") or 0.0) for item in outcomes) / n, 2),
        "avg_resolution_bars": round(sum(int(item.get("resolved_bars") or 0) for item in outcomes) / n, 1),
    }


def atr(candles: list[MarketCandle], period: int = 14) -> float:
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    if len(ordered) < 2:
        return ordered[-1].close * 0.015 if ordered else 1.0
    ranges = []
    previous_close = ordered[0].close
    for candle in ordered[1:]:
        ranges.append(
            max(
                candle.high - candle.low,
                abs(candle.high - previous_close),
                abs(candle.low - previous_close),
            )
        )
        previous_close = candle.close
    window = ranges[-period:] if len(ranges) >= period else ranges
    return max(sum(window) / len(window), ordered[-1].close * 0.0005) if window else ordered[-1].close * 0.015


def _risk_distance(entry_price: float, invalidation_price: float | None, atr_value: float | None) -> float:
    if invalidation_price is not None and invalidation_price > 0 and abs(entry_price - invalidation_price) > entry_price * 0.0005:
        return abs(entry_price - invalidation_price)
    fallback = atr_value if atr_value is not None and atr_value > 0 else entry_price * 0.015
    return max(fallback * 1.5, entry_price * 0.003)


def _empty(
    entry_price: float,
    invalidation_price: float | None,
    atr_value: float | None,
    cost_pct: float = 0.0,
    *,
    direction: str = "long",
) -> dict[str, Any]:
    risk = _risk_distance(entry_price, invalidation_price, atr_value)
    fallback_stop = entry_price - risk if direction == "long" else entry_price + risk
    return {
        "entry_price": round(entry_price, 8),
        "invalidation_price": round(invalidation_price or fallback_stop, 8),
        "risk_distance": round(risk, 8),
        "risk_fallback": invalidation_price is None,
        "cost_pct": round(max(0.0, cost_pct), 4),
        "cost_r": round(entry_price * max(0.0, cost_pct) / 100.0 / risk, 4) if risk else 0.0,
        "win_1r": False,
        "win_2r": False,
        "mfe_r": 0.0,
        "mae_r": 0.0,
        "resolved_bars": 0,
        "realized_rr": 0.0,
        "bars_evaluated": 0,
        "gross_debug": {"win_1r": False, "win_2r": False},
        "policy": "평가 가능한 미래 캔들이 없습니다.",
    }

