from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.backtest import replay as replay_module
from app.backtest.replay import replay_candles
from app.backtest.signatures import signature_from_setup_candidate, signatures_from_analysis
from app.db.models import MarketCandle
from app.structure.levels.engine import StructureLevel


def test_active_signal_signature_conversion_is_shared_with_setup_candidates() -> None:
    analysis = {
        "timeframe": "4h",
        "asset_class": "crypto",
        "mark_price": 100,
        "liquidity": {
            "sweeps": [
                {
                    "confirmed": True,
                    "side": "sell_side",
                    "grade": "Strong",
                    "confidence": 82,
                }
            ],
            "htf_range_sweeps": [],
        },
        "price_levels": {"support": [], "resistance": []},
        "wyckoff_markers": [],
        "harmonic_patterns": [],
    }

    live_signature = signatures_from_analysis(analysis)[0]
    candidate_signature = signature_from_setup_candidate(
        {"setup_type": "liquidity_pool", "direction": "long", "confidence": 82},
        asset_class="crypto",
        timeframe="4h",
    )

    assert live_signature["engine"] == candidate_signature["engine"]
    assert live_signature["direction"] == candidate_signature["direction"]


def test_full_alignment_is_registered_as_candidate_signature() -> None:
    signatures = signatures_from_analysis(
        {
            "timeframe": "4h",
            "asset_class": "stock",
            "mark_price": 100,
            "liquidity": {},
            "price_levels": {},
            "wyckoff_markers": [],
            "harmonic_patterns": [],
            "full_alignment": {"unanimous": True, "direction": "long", "agreeing": 5},
        }
    )

    alignment = next(item for item in signatures if item["engine"] == "full_alignment")
    assert alignment["event_type"] == "unanimous"
    assert alignment["strength_class"] == "5_modules"
    assert alignment["direction"] == "long"


def test_replay_uses_only_candles_up_to_confirmation_and_incremental_matches(monkeypatch) -> None:
    candles = [_candle(index, close=100 + index * 0.2) for index in range(36)]
    observed_lengths: list[int] = []

    def fake_levels(past, mark_price=None, volume_profile=None):
        observed_lengths.append(len(past))
        return {
            "support": [
                StructureLevel(
                    price=past[-1].close,
                    score=82,
                    touches=5,
                    last_touch_at=past[-1].timestamp,
                    kind="support",
                    sources=["test"],
                )
            ],
            "resistance": [],
        }

    monkeypatch.setattr(replay_module, "detect_structure_levels", fake_levels)
    monkeypatch.setattr(
        replay_module,
        "analyze_liquidity_structure",
        lambda *args, **kwargs: {"sweeps": [], "htf_range_sweeps": []},
    )
    monkeypatch.setattr(
        replay_module,
        "detect_harmonic_patterns",
        lambda *args, **kwargs: {"patterns": []},
    )

    full = replay_candles("TESTUSDT", "4h", candles, min_window=10, lookahead_bars=4)
    partial = replay_candles("TESTUSDT", "4h", candles, min_window=10, lookahead_bars=4, start_index=20)
    expected = [case for case in full if case["confirmation_index"] >= 20]

    assert full
    assert partial == expected
    assert max(observed_lengths) <= len(candles) - 1
    assert all(case["entry_price"] == candles[case["confirmation_index"]].close for case in full)


def _candle(index: int, *, close: float) -> MarketCandle:
    return MarketCandle(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index),
        open=close - 0.05,
        high=close + 0.3,
        low=close - 0.3,
        close=close,
        volume=1000 + index,
    )
