from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.backtest.signatures import signatures_from_analysis
from app.db.models import DerivativeMetric, MarketCandle
from app.exchange.bitget.provider import BitgetMarketDataProvider
from app.marketdata.coinglass import _aggregate_money_flow, _options_summary
from app.marketdata.money_flow import classify_money_flow, flow_observation
from app.marketdata.signals import build_derivative_signals
from app.services.runtime import _confirmed_money_flow_context


def _history(now: datetime, *, count: int = 20) -> list[dict]:
    return [
        {
            "as_of": (now - timedelta(hours=index * 4)).isoformat(),
            "price_change_pct": 1.0 + index / 100,
            "spot_cvd_delta_ratio": 0.10 + index / 1000,
            "futures_cvd_delta_ratio": 0.12 + index / 1000,
            "oi_change_pct": 1.5 + index / 100,
        }
        for index in range(count)
    ]


def _current(now: datetime, *, price: float, spot: float, futures: float, oi: float) -> dict:
    return {
        "as_of": now.isoformat(),
        "price_change_pct": price,
        "spot_cvd_delta_ratio": spot,
        "futures_cvd_delta_ratio": futures,
        "oi_change_pct": oi,
        "source": "bitget_spot",
        "coverage": {"spot_available": True, "futures_available": True},
        "spot_cvd": [],
        "futures_cvd": [],
    }


def test_money_flow_four_deterministic_states_use_distribution_thresholds() -> None:
    now = datetime.now(timezone.utc)
    history = _history(now)
    assert classify_money_flow(_current(now, price=3, spot=0.4, futures=0.1, oi=2), history)["state"] == "spot_led"
    assert classify_money_flow(_current(now, price=3, spot=-0.3, futures=0.4, oi=3), history)["state"] == "futures_led"
    assert classify_money_flow(_current(now, price=-3, spot=0.4, futures=-0.1, oi=2), history)["state"] == "spot_absorb"
    assert classify_money_flow(_current(now, price=-3, spot=-0.3, futures=-0.2, oi=-3), history)["state"] == "delever"
    result = classify_money_flow(_current(now, price=3, spot=-0.3, futures=0.4, oi=3), history)
    assert 0 < result["confidence"] <= 100


def test_money_flow_waits_for_30_day_distribution_sample() -> None:
    now = datetime.now(timezone.utc)
    result = classify_money_flow(_current(now, price=3, spot=-0.3, futures=0.4, oi=3), _history(now, count=4))
    assert result["state"] == "mixed"
    assert result["provisional"] is True
    assert result["sample_size"] == 4


def test_money_flow_waits_when_confirmed_fill_window_is_empty() -> None:
    now = datetime.now(timezone.utc)
    current = _current(now, price=3, spot=-0.3, futures=0.4, oi=3)
    current["spot_cvd_delta_ratio"] = None

    result = classify_money_flow(current, _history(now))

    assert result["state"] == "mixed"
    assert result["provisional"] is True
    assert "체결 표본" in result["reason"]


def test_money_flow_exposes_spot_mapping_failure() -> None:
    result = classify_money_flow(
        {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "coverage": {"spot_available": False, "futures_available": True},
            "notes": ["SOXLUSDT 현물 마켓이 없습니다."],
        },
        [],
    )
    assert result["available"] is False
    assert "현물 마켓" in result["reason"]


def test_money_flow_rejects_provisional_candle() -> None:
    now = datetime.now(timezone.utc)
    current = _current(now, price=3, spot=-0.3, futures=0.4, oi=3)
    current["confirmed"] = False

    result = classify_money_flow(current, _history(now))

    assert result["state"] == "mixed"
    assert result["provisional"] is True
    assert "마감" in result["reason"]


def test_confirmed_money_flow_context_excludes_open_bar() -> None:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    candles = [
        SimpleNamespace(timestamp=now - timedelta(hours=8), close=100),
        SimpleNamespace(timestamp=now - timedelta(hours=4), close=104),
        SimpleNamespace(timestamp=now, close=110),
    ]

    confirmed, as_of, _ = _confirmed_money_flow_context(candles, "4h")

    assert [item.close for item in confirmed] == [100, 104]
    assert as_of == now


def test_confirmed_money_flow_context_uses_24_candle_price_window() -> None:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    candles = [SimpleNamespace(timestamp=now - timedelta(hours=(25 - index) * 4), close=100 + index) for index in range(26)]

    confirmed, _, change = _confirmed_money_flow_context(candles, "4h")

    expected = ((confirmed[-1].close / confirmed[-24].close) - 1) * 100
    assert change == pytest.approx(expected)


def test_flow_observation_uses_real_buy_sell_bucket_delta() -> None:
    flow = {
        "data_available": True,
        "source": "bitget_spot",
        "buckets": [{"buy_volume": 8, "sell_volume": 2, "delta": 6}],
        "cvd": [{"time": 1, "value": 6}],
    }
    observation = flow_observation(
        price_change_pct=2,
        spot_flow=flow,
        futures_flow={**flow, "source": "bitget_futures"},
        oi_change_pct=3,
    )
    assert observation["spot_cvd_delta_ratio"] == 0.6
    assert observation["futures_cvd_delta_ratio"] == 0.6
    assert observation["source"] == "bitget_spot"


def test_flow_observation_prefers_event_cvd_for_coarse_timeframes() -> None:
    flow = {
        "data_available": True,
        "source": "bitget_futures",
        "buckets": [{"buy_volume": 8, "sell_volume": 2, "delta": 6}],
        "cvd": [{"time": 1, "value": 6}],
        "event_cvd": [
            {"time": 1, "value": 2, "method": "event_time_fills"},
            {"time": 2, "value": 1, "method": "event_time_fills"},
            {"time": 3, "value": 6, "method": "event_time_fills"},
        ],
    }

    observation = flow_observation(
        price_change_pct=2,
        spot_flow=None,
        futures_flow=flow,
        oi_change_pct=3,
    )

    assert observation["futures_cvd"] == [
        {"time": 1, "value": 2},
        {"time": 2, "value": 1},
        {"time": 3, "value": 6},
    ]
    assert observation["coverage"]["futures_cvd_method"] == "event_time_fills"


def test_coinglass_aggregate_replaces_cvd_but_keeps_bitget_market_context() -> None:
    now = datetime.now(timezone.utc)
    metrics: list[DerivativeMetric] = []
    for index, observation in enumerate(_history(now)):
        metrics.append(
            DerivativeMetric(
                symbol="BTCUSDT",
                source="bitget",
                tier="bitget_public",
                as_of=now - timedelta(hours=index * 4),
                raw_json={"money_flow_observation": observation},
            )
        )
        metrics.append(
            DerivativeMetric(
                symbol="BTCUSDT",
                source="coinglass",
                tier="coinglass",
                as_of=now - timedelta(hours=index * 4),
                raw_json={
                    "money_flow_aggregate": {
                        **observation,
                        "source": "coinglass_agg",
                        "coverage": {"spot_available": True, "futures_available": True},
                    }
                },
            )
        )
    metrics[0] = metrics[0].model_copy(
        update={
            "oi_change_pct": 3,
            "raw_json": {
                "money_flow_observation": _current(now, price=3, spot=0.3, futures=0.2, oi=3),
            },
        }
    )
    metrics.insert(
        0,
        DerivativeMetric(
            symbol="BTCUSDT",
            source="coinglass",
            tier="coinglass",
            as_of=now,
            raw_json={
                "money_flow_aggregate": {
                    "as_of": now.isoformat(),
                    "source": "coinglass_agg",
                    "spot_cvd_delta_ratio": -0.4,
                    "futures_cvd_delta_ratio": 0.5,
                    "price_change_pct": None,
                    "oi_change_pct": None,
                    "spot_cvd": [],
                    "futures_cvd": [],
                    "coverage": {"spot_available": True, "futures_available": True},
                }
            },
        ),
    )

    flow = build_derivative_signals(metrics)["money_flow"]

    assert flow["state"] == "futures_led"
    assert flow["source"] == "coinglass_agg"
    assert flow["source_label"] == "Coinglass 전거래소 집계"


def test_coinglass_money_flow_and_btc_options_parsers() -> None:
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    payload = {
        "code": "0",
        "data": [{"time": now_ms, "buy_volume": 8, "sell_volume": 2}],
    }
    aggregate = _aggregate_money_flow(
        {"status": "ok", "payload": payload},
        {"status": "ok", "payload": payload},
        {},
    )
    options = _options_summary(
        {"status": "ok", "payload": {"data": [{"time": now_ms, "put_call_ratio": 0.8}]}},
        {"status": "ok", "payload": {"data": [{"time": now_ms, "open_interest": 1200}]}},
        "BTC",
    )

    assert aggregate is not None
    assert aggregate["source"] == "coinglass_agg"
    assert aggregate["spot_cvd_delta_ratio"] == pytest.approx(0.6)
    assert aggregate["futures_cvd_delta_ratio"] == pytest.approx(0.6)
    assert options["available"] is True
    assert options["put_call_ratio"] == pytest.approx(0.8)
    assert options["options_open_interest"] == pytest.approx(1200)


@pytest.mark.asyncio
async def test_spot_flow_ends_at_last_confirmed_candle_boundary() -> None:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    candles = [
        MarketCandle(timestamp=now - timedelta(hours=8), open=100, high=102, low=99, close=101, volume=10),
        MarketCandle(timestamp=now - timedelta(hours=4), open=101, high=103, low=100, close=102, volume=12),
    ]
    provider = BitgetMarketDataProvider(SimpleNamespace(), trade_cache=None)
    captured: dict[str, datetime] = {}

    async def fake_spot_fills(symbol: str, start_time: datetime, end_time: datetime, **_: object) -> list:
        captured.update({"start": start_time, "end": end_time})
        return []

    provider.get_spot_trade_fills = fake_spot_fills  # type: ignore[method-assign]
    await provider.get_spot_trade_flow_async("BTCUSDT", "4h", candles)

    assert captured["end"] == candles[-1].timestamp + timedelta(hours=4)


def test_futures_led_registers_candidate_signature() -> None:
    analysis = {
        "timeframe": "4h",
        "asset_class": "crypto",
        "derivatives": {
            "signals": {
                "money_flow": {
                    "state": "futures_led",
                    "available": True,
                    "provisional": False,
                }
            }
        },
    }

    signatures = signatures_from_analysis(analysis)

    assert len(signatures) == 1
    signature = signatures[0]
    assert signature["engine"] == "money_flow"
    assert signature["event_type"] == "futures_led_rally"
    assert signature["strength_class"] == "candidate"
    assert signature["direction"] == "short"
    assert signature["asset_class"] == "crypto"
    assert signature["timeframe"] == "4h"
