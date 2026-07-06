from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.backtest.outcomes import score_event_outcome
from app.db.models import MarketCandle


def test_score_event_outcome_uses_conservative_same_candle_policy() -> None:
    future = [
        _candle(1, open_=100, high=112, low=94, close=105),
    ]

    outcome = score_event_outcome(
        future,
        direction="long",
        entry_price=100,
        invalidation_price=95,
        max_bars=5,
    )

    assert outcome["win_1r"] is False
    assert outcome["win_2r"] is False
    assert outcome["realized_rr"] == -1.0


def test_score_event_outcome_uses_atr_fallback_when_no_invalidation() -> None:
    future = [
        _candle(1, open_=100, high=103.1, low=99.5, close=102.5),
    ]

    outcome = score_event_outcome(
        future,
        direction="long",
        entry_price=100,
        invalidation_price=None,
        atr_value=2,
        max_bars=5,
    )

    assert outcome["risk_fallback"] is True
    assert outcome["risk_distance"] == 3
    assert outcome["win_1r"] is True


def _candle(index: int, *, open_: float, high: float, low: float, close: float) -> MarketCandle:
    return MarketCandle(
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000,
    )

