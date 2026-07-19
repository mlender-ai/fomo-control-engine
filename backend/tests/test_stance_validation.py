from __future__ import annotations

from datetime import datetime, timedelta, timezone

import app.backtest.stance_validation as subject
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.db.models import BacktestStat, MarketCandle
from app.db.repository import MemoryRepository


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_stance_backtest_api_is_honest_when_real_provider_is_unavailable(client: TestClient) -> None:
    dashboard = client.get("/api/backtest/stance")
    refresh = client.post("/api/backtest/stance/refresh")

    assert dashboard.status_code == 200
    assert dashboard.json()["status"] == "pending"
    assert dashboard.json()["synthetic_result_combined"] is False
    assert refresh.status_code == 409
    assert "실제 히스토리" in refresh.json()["detail"]


def test_real_history_scoring_uses_non_overlapping_net_outcomes(monkeypatch) -> None:
    candles = _candles(350)
    monkeypatch.setattr(
        subject,
        "replay_confirmed_stance_points",
        lambda **kwargs: [
            {
                "time": int(candle.timestamp.timestamp()),
                "stance": "long_leaning",
                "transitioning": False,
            }
            for candle in kwargs["candles"][99:]
        ],
    )

    result = subject.evaluate_stance_history(
        symbol="BTCUSDT",
        timeframe="4h",
        candles=candles,
        settings=Settings(backtest_taker_fee_pct=0.06, backtest_slippage_crypto_pct=0.03),
        horizon_bars=6,
        generated_at=BASE + timedelta(days=90),
    )

    cases = result["cases"]
    assert result["real_history"] is True
    assert result["cost_pct"] == 0.15
    assert result["sample_size"] >= 30
    assert result["sample_sufficient"] is True
    assert result["directional_hit_pct"] == 100.0
    assert result["directional_hit_ci"] == [100.0, 100.0]
    assert all(case["net_directional_return_pct"] < case["gross_directional_return_pct"] for case in cases)
    assert all(datetime.fromisoformat(right["as_of"]) - datetime.fromisoformat(left["as_of"]) == timedelta(hours=24) for left, right in zip(cases, cases[1:]))


def test_small_real_history_sample_is_visible_but_conclusion_withheld(monkeypatch) -> None:
    candles = _candles(130)
    monkeypatch.setattr(
        subject,
        "replay_confirmed_stance_points",
        lambda **kwargs: [{"time": int(candle.timestamp.timestamp()), "stance": "short_leaning", "transitioning": False} for candle in kwargs["candles"][99:]],
    )

    result = subject.evaluate_stance_history(
        symbol="ETHUSDT",
        timeframe="4h",
        candles=candles,
        settings=Settings(stance_backtest_sample_floor=30),
        horizon_bars=6,
    )

    assert result["sample_size"] < 30
    assert result["publishable"] is False
    assert result["decision"] == "withheld"
    assert "표본 부족" in result["statement"]
    assert result["directional_hit_ci"] is not None


def test_dashboard_keeps_real_history_separate_from_synthetic_stats() -> None:
    repo = MemoryRepository()
    repo.upsert_backtest_stat(
        BacktestStat(
            signature_key=subject.SIGNATURE_KEY,
            symbol="BTCUSDT",
            engine="directional_v2",
            event_type="forward_close_24h",
            strength_class="real_history",
            direction="neutral",
            sample_size=31,
            payload={
                "signature_key": subject.SIGNATURE_KEY,
                "symbol": "BTCUSDT",
                "generated_at": BASE.isoformat(),
                "real_history": True,
                "sample_size": 31,
                "candle_sha256": "audit-only",
                "cases": [{"win": True}],
            },
        )
    )
    repo.upsert_backtest_stat(
        BacktestStat(
            signature_key="directional_v2_synthetic",
            symbol="BTCUSDT",
            engine="directional_v2",
            event_type="synthetic",
            strength_class="synthetic",
            direction="neutral",
            sample_size=999,
            payload={"symbol": "BTCUSDT", "generated_at": BASE.isoformat(), "real_history": False},
        )
    )

    payload = subject.stance_backtest_dashboard(repo)

    assert payload["synthetic_result_combined"] is False
    assert payload["items"][0]["sample_size"] == 31
    assert "candle_sha256" not in payload["items"][0]
    assert "cases" not in payload["items"][0]
    assert payload["items"][1]["decision"] == "pending"


def _candles(count: int) -> list[MarketCandle]:
    return [
        MarketCandle(
            timestamp=BASE + timedelta(hours=4 * index),
            open=100 + index,
            high=102 + index,
            low=99 + index,
            close=101 + index,
            volume=1_000 + index,
        )
        for index in range(count)
    ]
