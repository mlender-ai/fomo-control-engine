from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import app.analyst.stance_history as subject


BASE = datetime(2026, 7, 1, tzinfo=timezone.utc)


def test_replay_has_no_lookahead_matches_live_and_caches_incrementally(monkeypatch) -> None:
    subject.clear_stance_history_cache()
    monkeypatch.setattr(subject, "MIN_CHART_CANDLES", 3)
    observed_prefixes: list[list[int]] = []
    observed_priors: list[dict[str, Any] | None] = []

    def fake_analysis(snapshot, *_args):
        times = [int(candle.timestamp.timestamp()) for candle in snapshot.candles]
        observed_prefixes.append(times)
        return {"last_close": snapshot.candles[-1].close}

    def fake_confluence(*, analysis, prior_state=None, **_kwargs):
        observed_priors.append(prior_state)
        stance = "long_leaning" if analysis["last_close"] >= 103 else "short_leaning"
        state = {
            "stance": stance,
            "long_score_ema": 8.0 if stance == "long_leaning" else 2.0,
            "short_score_ema": 2.0 if stance == "long_leaning" else 8.0,
            "transitioning": False,
            "flipped": bool(prior_state and prior_state.get("stance") != stance),
        }
        return {
            "stance": stance,
            "stance_state": state,
            "long_evidence": [{"claim": "상방 근거"}] if stance == "long_leaning" else [],
            "short_evidence": [{"claim": "하방 근거"}] if stance == "short_leaning" else [],
        }

    monkeypatch.setattr(subject, "build_chart_analysis", fake_analysis)
    monkeypatch.setattr(subject, "build_confluence", fake_confluence)
    analysis = _analysis([100, 101, 102, 103, 104])
    live_state = {
        "stance": "short_leaning",
        "transitioning": False,
        "flipped": True,
        "long_score_ema": 1.0,
        "short_score_ema": 9.0,
        "live_marker": "persisted",
    }
    live = {
        "stance": "short_leaning",
        "stance_state": live_state,
        "long_evidence": [],
        "short_evidence": [{"claim": "라이브 하방 근거"}],
    }

    first = subject.replay_stance_history(
        analysis=analysis,
        current_confluence=live,
        bar_state={"provisional": False},
        timeframe="4h",
    )

    assert [len(prefix) for prefix in observed_prefixes] == [3, 4, 5]
    assert all(prefix[-1] == point["time"] for prefix, point in zip(observed_prefixes, first, strict=True))
    assert first[-1]["stance"] == live["stance"]
    assert first[-1]["reason"] == "라이브 하방 근거"

    calls_after_first = len(observed_prefixes)
    same = subject.replay_stance_history(
        analysis=analysis,
        current_confluence=live,
        bar_state={"provisional": False},
        timeframe="4h",
    )
    assert same == first
    assert len(observed_prefixes) == calls_after_first

    extended = _analysis([100, 101, 102, 103, 104, 105])
    subject.replay_stance_history(
        analysis=extended,
        current_confluence={**live, "stance": "long_leaning", "stance_state": {**live_state, "stance": "long_leaning"}},
        bar_state={"provisional": False},
        timeframe="4h",
    )
    assert len(observed_prefixes) == calls_after_first + 1
    assert observed_prefixes[-1] == [int((BASE + timedelta(hours=4 * index)).timestamp()) for index in range(6)]
    assert observed_priors[-1] == live_state


def test_provisional_candle_never_receives_confirmed_segment(monkeypatch) -> None:
    subject.clear_stance_history_cache()
    monkeypatch.setattr(subject, "MIN_CHART_CANDLES", 3)
    seen_last_times: list[int] = []

    def fake_analysis(snapshot, *_args):
        seen_last_times.append(int(snapshot.candles[-1].timestamp.timestamp()))
        return {}

    monkeypatch.setattr(subject, "build_chart_analysis", fake_analysis)
    monkeypatch.setattr(
        subject,
        "build_confluence",
        lambda **_kwargs: {
            "stance": "long_leaning",
            "stance_state": {"stance": "long_leaning", "long_score_ema": 8, "short_score_ema": 2},
            "long_evidence": [],
            "short_evidence": [],
        },
    )
    analysis = _analysis([100, 101, 102, 103])
    history = subject.replay_stance_history(
        analysis=analysis,
        current_confluence={"stance": "long_leaning", "stance_state": {}},
        bar_state={"provisional": True},
        timeframe="4h",
    )
    provisional_time = analysis["candles"][-1]["time"]
    assert all(point["time"] < provisional_time for point in history)
    assert max(seen_last_times) < provisional_time


def test_live_endpoint_separates_held_stance_from_raw_preview() -> None:
    subject.clear_stance_history_cache()
    analysis = _analysis([100])
    history = subject.replay_stance_history(
        analysis=analysis,
        current_confluence={
            "stance": "short_leaning",
            "stance_state": {
                "stance": "short_leaning",
                "transitioning": True,
                "preview": {"raw_stance": "long_leaning"},
            },
            "long_evidence": [{"claim": "순간 반등"}],
            "short_evidence": [{"claim": "유지 하방"}],
        },
        bar_state={"provisional": False},
        timeframe="4h",
    )

    assert history[-1]["stance"] == "short_leaning"
    assert history[-1]["preview_stance"] == "long_leaning"


def test_future_price_mutation_cannot_change_earlier_historical_stance(monkeypatch) -> None:
    monkeypatch.setattr(subject, "MIN_CHART_CANDLES", 3)

    def fake_analysis(snapshot, *_args):
        return {"last_close": snapshot.candles[-1].close}

    def fake_confluence(*, analysis, prior_state=None, **_kwargs):
        stance = "long_leaning" if analysis["last_close"] >= 103 else "short_leaning"
        return {
            "stance": stance,
            "stance_state": {
                "stance": stance,
                "long_score_ema": 8 if stance == "long_leaning" else 2,
                "short_score_ema": 2 if stance == "long_leaning" else 8,
                "transitioning": False,
                "previous": prior_state.get("stance") if prior_state else None,
            },
            "long_evidence": [],
            "short_evidence": [],
        }

    monkeypatch.setattr(subject, "build_chart_analysis", fake_analysis)
    monkeypatch.setattr(subject, "build_confluence", fake_confluence)
    original = subject._candles(_analysis([100, 101, 102, 103, 104]))
    future_changed = subject._candles(_analysis([100, 101, 102, 103, 1]))

    before = subject.replay_confirmed_stance_points(symbol="TESTUSDT", timeframe="4h", candles=original)
    after = subject.replay_confirmed_stance_points(symbol="TESTUSDT", timeframe="4h", candles=future_changed)

    assert before[:-1] == after[:-1]
    assert before[-1]["stance"] != after[-1]["stance"]


def _analysis(closes: list[float]) -> dict[str, Any]:
    candles = []
    for index, close in enumerate(closes):
        candles.append(
            {
                "time": int((BASE + timedelta(hours=4 * index)).timestamp()),
                "open": close - 0.5,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 1000 + index,
            }
        )
    return {"symbol": "TESTUSDT", "candles": candles}
