from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from app.db.models import Direction, MarketCandle, MarketSnapshot, Position, PositionSnapshot
from app.positions.chart_analysis import PositionContext, build_chart_analysis
from app.review.engine import build_judgment_entries
from app.structure.liquidity.engine import analyze_liquidity_structure, attach_liquidity_crosscheck_to_wyckoff
from app.structure.liquidity.pools import LiquidityPool, detect_liquidity_pools
from app.structure.liquidity.structure import detect_structure_shift
from app.structure.liquidity.sweeps import detect_htf_range_sweeps, detect_liquidity_sweeps


BASE_TIME = datetime(2026, 7, 6, tzinfo=timezone.utc)


def test_liquidity_pool_detection_finds_equal_and_old_extremes_with_type_cap() -> None:
    pools = detect_liquidity_pools(_pool_fixture())
    kinds = {pool.kind for pool in pools}

    assert {"eqh", "eql", "old_high", "old_low"}.issubset(kinds)
    eqh = next(pool for pool in pools if pool.kind == "eqh" and pool.touch_count >= 2 and abs(pool.price - 110.02) < 0.05)
    eql = next(pool for pool in pools if pool.kind == "eql" and pool.touch_count >= 2 and abs(pool.price - 94.02) < 0.05)
    assert abs(eqh.price - 110.02) < 0.05
    assert abs(eql.price - 94.02) < 0.05

    by_kind = Counter(pool.kind for pool in pools)
    assert all(count <= 6 for count in by_kind.values())


def test_sweep_grades_are_depth_based_and_volume_confirmation_is_mandatory() -> None:
    pool = _sell_side_pool()

    weak = detect_liquidity_sweeps(_sweep_fixture(99.75, volume=2200), [pool])["sweeps"][0]
    mid = detect_liquidity_sweeps(_sweep_fixture(99.1, volume=2200), [pool])["sweeps"][0]
    strong = detect_liquidity_sweeps(_sweep_fixture(97.8, volume=2200), [pool])["sweeps"][0]
    unconfirmed = detect_liquidity_sweeps(_sweep_fixture(99.1, volume=1200), [pool])

    assert weak["grade"] == "Weak"
    assert mid["grade"] == "Mid"
    assert strong["grade"] == "Strong"
    assert weak["components"]["depth_significance"] < mid["components"]["depth_significance"] < strong["components"]["depth_significance"]
    assert all(item["volume_confirmed"] for item in (weak, mid, strong))
    assert unconfirmed["sweeps"] == []
    assert unconfirmed["rejected_sweeps"][0]["status"] == "unconfirmed"
    assert unconfirmed["rejected_sweeps"][0]["components"]["volume_confirmation"] == 0


def test_htf_range_sweep_uses_previous_daily_range() -> None:
    events = detect_htf_range_sweeps(_htf_sweep_fixture())

    assert events
    event = events[0]
    assert event["type"] == "htf_range_sweep"
    assert event["side"] == "buy_side"
    assert event["expected_move"] == "down"
    assert event["confirmed"] is True


def test_structure_shift_uses_body_close_for_bos_and_choch() -> None:
    bos = detect_structure_shift(_structure_fixture(final_close=107.5))
    choch = detect_structure_shift(_structure_fixture(final_close=97.0))

    assert bos["event"] == "BOS"
    assert bos["direction"] == "up"
    assert bos["trend_before"] == "bullish"
    assert choch["event"] == "CHoCH"
    assert choch["direction"] == "down"
    assert choch["trend_before"] == "bullish"


def test_wyckoff_crosscheck_adds_liquidity_confirmation_component() -> None:
    wyckoff = {
        "spring_candidate": True,
        "events": [{"type": "spring_candidate", "label": "스프링", "confidence": 61, "components": {"depth_significance": 16}}],
    }
    liquidity = {
        "wyckoff_crosscheck": {
            "confirmed": True,
            "confirmations": [{"wyckoff_event": "spring_candidate", "sweep_grade": "Strong", "confidence": 82}],
        }
    }

    boosted = attach_liquidity_crosscheck_to_wyckoff(wyckoff, liquidity)
    event = boosted["events"][0]

    assert event["confidence"] == 76
    assert event["components"]["liquidity_confirmation"] == 15
    assert "스윕 확인" in event["display_label"]


def test_liquidity_engine_detects_volume_confirmed_sell_side_sweep_and_wyckoff_crosscheck() -> None:
    candles = _analysis_fixture()

    result = analyze_liquidity_structure(
        candles,
        mark_price=102.2,
        levels=_levels(),
        wyckoff={"spring_candidate": True, "events": [{"type": "spring_candidate"}]},
    )

    sweep = next(item for item in result["sweeps"] if item["side"] == "sell_side")
    assert result["method"] == "deterministic_ohlcv_liquidity_v2"
    assert sweep["confirmed"] is True
    assert sweep["wyckoff_equivalent"] == "spring_candidate"
    assert sweep["expected_move"] == "up"
    assert sweep["volume_ratio"] >= 1.5
    assert sweep["grade"] in {"Weak", "Mid", "Strong"}
    assert result["wyckoff_crosscheck"]["confirmed"] is True


def test_chart_analysis_registers_liquidity_sweep_judgment() -> None:
    candles = _analysis_fixture(120)
    snapshot = MarketSnapshot(
        symbol="TESTUSDT",
        timeframe="4h",
        price=102.2,
        change_24h=0.4,
        funding_rate=0,
        open_interest_change=0,
        candles=candles,
    )
    position = Position(
        symbol="TESTUSDT",
        direction=Direction.long,
        entry_price=101.0,
        quantity=1.0,
        leverage=5,
        mark_price=102.2,
    )
    position_snapshot = PositionSnapshot(
        position_id=position.id,
        symbol=position.symbol,
        mark_price=102.2,
        health_score=72,
        status_label="관찰 필요",
        risk_score=28,
        score_json={},
        analysis_json={},
    )

    analysis = build_chart_analysis(snapshot, PositionContext.from_position(position))
    entries = build_judgment_entries(
        position,
        position_snapshot,
        {"invalidation": {}, "take_profit": []},
        analysis,
        source_type="test",
        source_id="liquidity",
    )

    liquidity_entries = [entry for entry in entries if entry.type == "liquidity_sweep"]
    assert liquidity_entries
    assert liquidity_entries[0].claim["expected_move"] == "up"
    assert liquidity_entries[0].confidence is not None


def _levels() -> dict:
    return {
        "support": [
            {
                "price": 100.0,
                "score": 84,
                "touches": 5,
                "kind": "support",
                "sources": ["swing"],
                "last_touch_at": BASE_TIME.isoformat(),
            }
        ],
        "resistance": [
            {
                "price": 110.0,
                "score": 78,
                "touches": 4,
                "kind": "resistance",
                "sources": ["swing"],
                "last_touch_at": BASE_TIME.isoformat(),
            }
        ],
    }


def _pool_fixture() -> list[MarketCandle]:
    candles: list[MarketCandle] = []
    for index in range(70):
        close = 100.0 + (index % 5 - 2) * 0.4
        low = close - 1.0
        high = close + 1.0
        if index == 10:
            high = 110.0
        if index == 22:
            high = 110.04
        if index == 14:
            low = 94.0
        if index == 28:
            low = 94.03
        if index == 45:
            high = 114.0
        if index == 48:
            low = 90.0
        candles.append(
            _candle(
                index,
                open_price=close - 0.3,
                high=high,
                low=low,
                close=close,
                volume=1000.0 + (index % 3) * 50,
            )
        )
    return candles


def _analysis_fixture(count: int = 120) -> list[MarketCandle]:
    candles: list[MarketCandle] = []
    for index in range(count):
        close = 103.0 + (index % 6 - 2) * 0.6
        low = 100.4 if index % 11 == 0 else min(close - 1.0, 102.5)
        high = 108.8 if index % 13 == 0 else max(close + 1.3, 105.8)
        candles.append(
            _candle(
                index,
                open_price=close - 0.2,
                high=high,
                low=low,
                close=close,
                volume=950.0 + (index % 3) * 70,
            )
        )
    candles.append(_candle(count, open_price=103.0, high=105.0, low=97.8, close=102.2, volume=3200.0))
    return candles


def _sweep_fixture(sweep_low: float, *, volume: float) -> list[MarketCandle]:
    candles = [_candle(index, open_price=101.0, high=102.0, low=100.5, close=101.0, volume=1000.0) for index in range(40)]
    candles.append(_candle(40, open_price=100.5, high=101.0, low=sweep_low, close=100.2, volume=volume))
    return candles


def _sell_side_pool() -> LiquidityPool:
    return LiquidityPool(
        id="test:sell-side",
        price=100.0,
        kind="eql",
        touch_count=3,
        first_seen=BASE_TIME,
        last_touch_at=BASE_TIME + timedelta(hours=39 * 4),
        swept=False,
        swept_at=None,
        score=80,
        side="sell_side",
        grade="Strong",
        label="동일 저점 유동성",
    )


def _htf_sweep_fixture() -> list[MarketCandle]:
    candles: list[MarketCandle] = []
    for index in range(6):
        high = 110.0 if index == 3 else 105.0
        candles.append(_candle(index, open_price=100.0, high=high, low=95.0, close=100.0, volume=1000.0))
    for index in range(6, 12):
        high = 111.0 if index == 8 else 104.0
        close = 109.0 if index == 8 else 101.0
        volume = 2200.0 if index == 8 else 1000.0
        candles.append(_candle(index, open_price=101.0, high=high, low=97.0, close=close, volume=volume))
    return candles


def _structure_fixture(*, final_close: float) -> list[MarketCandle]:
    candles: list[MarketCandle] = []
    for index in range(20):
        close = 100.0 + index * 0.1
        high = close + 1.0
        low = close - 1.0
        if index == 4:
            high = 104.0
            close = 103.0
        if index == 8:
            low = 98.0
            close = 99.0
        if index == 12:
            high = 106.0
            close = 105.0
        if index == 15:
            low = 100.0
            close = 101.0
        if index == 19:
            close = final_close
            high = final_close + 1.0
            low = final_close - 1.0
        candles.append(_candle(index, open_price=close, high=high, low=low, close=close, volume=1000.0))
    return candles


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
