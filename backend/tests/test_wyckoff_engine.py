from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import Direction, MarketCandle
from app.positions.engine import direction_aware_score
from app.structure.wyckoff.engine import analyze_wyckoff


BASE_TIME = datetime(2026, 7, 4, tzinfo=timezone.utc)


def test_wyckoff_v2_detects_spring_with_component_confidence() -> None:
    candles = _range_candles(72)
    candles.append(_candle(72, open_price=104.0, high=105.2, low=98.0, close=102.3, volume=2600.0))
    levels = _levels()

    result = analyze_wyckoff(candles, levels=levels, include_mtf=False)
    spring = next(event for event in result["events"] if event["type"] == "spring_candidate")

    assert result["phase"] == "accumulation_phase_c"
    assert result["side"] == "accumulation"
    assert spring["confidence"] == sum(spring["components"].values())
    assert set(spring["components"]) == {
        "depth_significance",
        "return_speed",
        "volume_confirmation",
        "level_strength",
    }
    assert spring["confidence"] != 62
    assert result["spring_candidate"] is True


def test_wyckoff_v2_detects_utad_with_component_confidence() -> None:
    candles = _range_candles(72)
    candles.append(_candle(72, open_price=106.0, high=112.3, low=104.5, close=108.1, volume=2700.0))
    levels = _levels()

    result = analyze_wyckoff(candles, levels=levels, include_mtf=False)
    utad = next(event for event in result["events"] if event["type"] == "utad_candidate")

    assert result["phase"] == "distribution_phase_c"
    assert result["side"] == "distribution"
    assert utad["confidence"] == sum(utad["components"].values())
    assert set(utad["components"]) == {
        "depth_significance",
        "return_speed",
        "volume_confirmation",
        "level_strength",
    }
    assert utad["confidence"] != 58
    assert result["utad_candidate"] is True


def test_wyckoff_v2_skips_events_when_no_range_exists() -> None:
    candles = [
        _candle(
            index,
            open_price=100 + index * 1.2,
            high=102 + index * 1.2,
            low=99 + index * 1.2,
            close=101 + index * 1.2,
            volume=900.0,
        )
        for index in range(80)
    ]

    result = analyze_wyckoff(candles, levels={"support": [], "resistance": []}, include_mtf=False)

    assert result["phase"] == "trending"
    assert result["events"] == []
    assert result["spring_candidate"] is False
    assert result["utad_candidate"] is False


def test_short_position_gets_distribution_thesis_hook() -> None:
    structure = {
        "trend": {
            "direction": "bearish_to_neutral",
            "higher_low": False,
            "lower_high": True,
            "break_of_structure": False,
            "breakdown_structure": True,
        },
        "wyckoff": {
            "accumulation_score": 28,
            "distribution_score": 82,
            "phase": "distribution_phase_c",
            "phase_hint": "distribution_phase_c",
            "side": "distribution",
            "spring_candidate": False,
            "sos_confirmed": False,
            "lps_candidate": False,
            "utad_candidate": True,
            "sow_confirmed": False,
            "lpsy_candidate": False,
            "mtf": {
                "htf_phase": "distribution_phase_c",
                "htf_trend": "bearish",
                "alignment": "aligned",
            },
        },
    }
    indicators = {
        "last_close": 108.0,
        "bollinger_upper": 114.0,
        "bollinger_lower": 98.0,
    }

    short_score = direction_aware_score(Direction.short, structure, indicators)
    long_score = direction_aware_score(Direction.long, structure, indicators)

    assert short_score >= 75
    assert short_score > long_score + 30


def _range_candles(count: int) -> list[MarketCandle]:
    candles: list[MarketCandle] = []
    for index in range(count):
        close = 104.0 + (index % 6 - 2) * 0.8
        low = 100.8 if index % 11 == 0 else min(close - 1.6, 103.0)
        high = 109.2 if index % 13 == 0 else max(close + 1.8, 106.5)
        candles.append(
            _candle(
                index,
                open_price=close - 0.4,
                high=high,
                low=low,
                close=close,
                volume=900.0 + (index % 4) * 70,
            )
        )
    return candles


def _levels() -> dict:
    return {
        "support": [
            {
                "price": 100.0,
                "score": 82,
                "touches": 5,
                "kind": "support",
                "sources": ["swing"],
            }
        ],
        "resistance": [
            {
                "price": 110.0,
                "score": 78,
                "touches": 4,
                "kind": "resistance",
                "sources": ["swing"],
            }
        ],
    }


def _candle(
    index: int,
    *,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float,
) -> MarketCandle:
    return MarketCandle(
        timestamp=BASE_TIME + timedelta(hours=index * 4),
        open=open_price,
        high=max(high, open_price, close),
        low=min(low, open_price, close),
        close=close,
        volume=volume,
    )
