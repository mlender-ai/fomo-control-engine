"""캔들 데이터 무결성 검사 (WO-FCE-36 §1).

리플레이 전에 갭·중복·OHLC 논리 위반·볼륨 이상·극단 아웃라이어를 걸러내고
심볼별 data_quality_score를 남긴다. 품질 하한 미달이면 통계를 발행하지 않는다.
"""

from __future__ import annotations

from typing import Any

from app.db.models import MarketCandle

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "6h": 21600,
    "12h": 43200,
    "1d": 86400,
}

OUTLIER_MOVE_PCT = 40.0  # 전 캔들 대비 ±40% — 상장 초기·오류 티크


def assess_candles(candles: list[MarketCandle], timeframe: str) -> dict[str, Any]:
    """캔들 무결성 평가. 위반 캔들은 valid_candles에서 제외한다."""
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    total = len(ordered)
    if total == 0:
        return {
            "score": 0,
            "checked": 0,
            "violations": [{"code": "empty", "count": 1, "detail": "캔들이 없습니다."}],
            "valid_candles": [],
        }

    step = TIMEFRAME_SECONDS.get(timeframe.lower())
    violations: dict[str, int] = {}
    valid: list[MarketCandle] = []
    seen_stamps: set[float] = set()
    gaps = 0
    previous: MarketCandle | None = None

    for candle in ordered:
        stamp = candle.timestamp.timestamp()
        if stamp in seen_stamps:
            violations["duplicate_timestamp"] = violations.get("duplicate_timestamp", 0) + 1
            continue
        seen_stamps.add(stamp)

        ohlc_broken = (
            candle.high < candle.low
            or candle.high < candle.open
            or candle.high < candle.close
            or candle.low > candle.open
            or candle.low > candle.close
            or min(candle.open, candle.high, candle.low, candle.close) <= 0
        )
        if ohlc_broken:
            violations["ohlc_violation"] = violations.get("ohlc_violation", 0) + 1
            continue
        if candle.volume < 0:
            violations["negative_volume"] = violations.get("negative_volume", 0) + 1
            continue
        if previous is not None and previous.close > 0:
            move_pct = abs(candle.close / previous.close - 1) * 100
            if move_pct > OUTLIER_MOVE_PCT:
                violations["extreme_outlier"] = violations.get("extreme_outlier", 0) + 1
                # 아웃라이어는 리플레이에서 제외하되 다음 비교 기준은 갱신한다 (연쇄 오탐 방지)
                previous = candle
                continue
            if step is not None:
                delta = stamp - previous.timestamp.timestamp()
                if delta > step * 1.5:
                    gaps += max(1, int(round(delta / step)) - 1)
        previous = candle
        valid.append(candle)

    if gaps:
        violations["missing_candles"] = gaps
    zero_volume = sum(1 for candle in valid if candle.volume == 0)
    if zero_volume:
        violations["zero_volume"] = zero_volume

    # 점수: 위반 종류별 감점 (하드 위반 > 소프트 위반)
    penalty = 0.0
    penalty += violations.get("duplicate_timestamp", 0) * 2.0
    penalty += violations.get("ohlc_violation", 0) * 3.0
    penalty += violations.get("negative_volume", 0) * 2.0
    penalty += violations.get("extreme_outlier", 0) * 3.0
    penalty += violations.get("missing_candles", 0) * 1.0
    penalty += min(10.0, violations.get("zero_volume", 0) * 0.2)
    score = max(0, round(100 - penalty / max(1, total) * 100))

    return {
        "score": score,
        "checked": total,
        "violations": [{"code": code, "count": count, "detail": _violation_detail(code)} for code, count in sorted(violations.items())],
        "valid_candles": valid,
    }


def _violation_detail(code: str) -> str:
    return {
        "duplicate_timestamp": "중복 타임스탬프",
        "ohlc_violation": "OHLC 논리 위반 (고가<저가 등)",
        "negative_volume": "음수 볼륨",
        "extreme_outlier": f"전 캔들 대비 ±{OUTLIER_MOVE_PCT:.0f}% 초과 이동",
        "missing_candles": "누락 캔들(갭)",
        "zero_volume": "0 볼륨 캔들",
        "empty": "캔들 없음",
    }.get(code, code)
