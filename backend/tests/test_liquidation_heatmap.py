from datetime import datetime, timedelta, timezone

from app.db.models import LiquidationEvent, MarketCandle
from app.derivatives.liquidation_heatmap import build_realized_liquidation_heatmap, build_unified_liquidation_heatmap
from app.marketdata.bitget_liquidations import parse_bitget_liquidation


def test_bitget_liquidation_parser_keeps_observed_price_and_stable_id() -> None:
    row = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "price": "65000.5",
        "amount": "0.25",
        "ts": "1784123260221",
    }

    first = parse_bitget_liquidation(row, "BTCUSDT")
    second = parse_bitget_liquidation(row, "BTCUSDT")

    assert first is not None
    assert second is not None
    assert first.id == second.id
    assert first.source == "bitget"
    assert first.raw_json["price"] == 65000.5
    assert first.raw_json["position_side"] == "long"
    assert first.long_liquidation_usd == 16250.125
    assert first.short_liquidation_usd == 0
    assert first.data_quality["notional_estimated"] is True


def test_invalid_bitget_liquidation_is_rejected() -> None:
    assert parse_bitget_liquidation({"side": "buy", "price": "0", "amount": "1", "ts": "1"}, "BTCUSDT") is None
    assert parse_bitget_liquidation({"side": "other", "price": "1", "amount": "1", "ts": "1"}, "BTCUSDT") is None


def test_realized_heatmap_buckets_events_and_reports_sample_limitations() -> None:
    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    events = [
        _event(now - timedelta(hours=2), price=64_000, amount=1.0, side="long"),
        _event(now - timedelta(hours=1), price=65_000, amount=2.0, side="short"),
        _event(now - timedelta(hours=80), price=60_000, amount=10.0, side="long"),
    ]

    payload = build_realized_liquidation_heatmap(
        events,
        "BTCUSDT",
        current_price=64_500,
        window_hours=72,
        time_bins=24,
        price_bins=20,
        now=now,
    )

    assert payload["mode"] == "realized_liquidations"
    assert payload["source"] == "bitget_public_rest"
    assert payload["sample_size"] == 2
    assert len(payload["cells"]) == 2
    assert payload["summary"]["long_usd_estimated"] == 64_000
    assert payload["summary"]["short_usd_estimated"] == 130_000
    assert payload["top_zones"][0]["dominant_side"] == "short"
    assert payload["coverage"]["notional_estimated"] is True
    assert any("미래" in note for note in payload["notes"])


def test_realized_heatmap_returns_explicit_empty_state() -> None:
    now = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)

    payload = build_realized_liquidation_heatmap([], "ETHUSDT", now=now)

    assert payload["source_status"] == "empty"
    assert payload["sample_size"] == 0
    assert payload["cells"] == []


def test_unified_heatmap_persists_until_confirmed_price_revisit() -> None:
    now = datetime(2026, 7, 15, 16, tzinfo=timezone.utc)
    events = [
        _event(now - timedelta(hours=15), price=100, amount=10, side="long"),
        _event(now - timedelta(hours=11), price=110, amount=20, side="short"),
        _event(now - timedelta(hours=7), price=120, amount=30, side="long"),
        _event(now - timedelta(hours=3), price=130, amount=40, side="short"),
    ]
    candles = [
        _candle(now - timedelta(hours=16), 90, 95),
        _candle(now - timedelta(hours=12), 96, 99),
        _candle(now - timedelta(hours=8), 99, 105),
        _candle(now - timedelta(hours=4), 106, 112),
    ]

    payload = build_unified_liquidation_heatmap(
        events,
        candles,
        "BTCUSDT",
        timeframe_seconds=4 * 3600,
        range_key="24H",
        price_bins=32,
        mode="persist",
        now=now,
    )

    assert payload["source"] == "bitget_realized"
    assert payload["truth_label"] == "실제 청산 · 예상 아님"
    assert payload["n_events"] == 4
    assert payload["filters"]["leverage_available"] is False
    assert payload["filters"]["filter_basis"] == "size_quartile"
    first = payload["events"][0]
    assert first["persisted_until"] is not None
    first_bin = min(
        range(payload["price_bins"]["count"]),
        key=lambda index: abs(payload["price_bins"]["min"] + (index + 0.5) * payload["price_bins"]["step"] - first["price"]),
    )
    nonzero = [index for index, row in enumerate(payload["grid"]) if row[first_bin] > 0]
    assert len(nonzero) == 2


def test_unified_heatmap_size_quartile_and_estimated_adapter() -> None:
    now = datetime(2026, 7, 15, 16, tzinfo=timezone.utc)
    events = [_event(now - timedelta(hours=4 - index), price=100 + index, amount=amount, side="long") for index, amount in enumerate((1, 2, 3, 4))]
    candles = [_candle(now - timedelta(hours=4), 95, 110)]

    filtered = build_unified_liquidation_heatmap(
        events,
        candles,
        "BTCUSDT",
        timeframe_seconds=3600,
        range_key="12H",
        size_filter="q4",
        now=now,
    )
    estimated = build_unified_liquidation_heatmap(
        [],
        candles,
        "BTCUSDT",
        timeframe_seconds=3600,
        source="coinglass_est",
        now=now,
    )

    assert filtered["n_events"] == 1
    assert filtered["filters"]["quartile_thresholds_usd"]["q4"] > 0
    assert estimated["source_status"] == "locked"
    assert estimated["source"] == "coinglass_est"
    assert estimated["grid"]


def test_unified_heatmap_api_exposes_candle_aligned_adapter(client) -> None:
    response = client.get("/api/liq/heatmap?symbol=ETHUSDT&tf=4h&range=24H&side=all&size=q3_plus&mode=persist&price_bins=64")

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "ETHUSDT"
    assert payload["price_bins"]["count"] == 64
    assert payload["filters"]["size"] == "q3_plus"
    assert payload["filters"]["mode"] == "persist"
    assert len(payload["grid"]) == len(payload["time_buckets"])


def test_unified_heatmap_uses_observed_leverage_when_every_event_has_it() -> None:
    now = datetime(2026, 7, 15, 16, tzinfo=timezone.utc)
    events = [
        _event(now - timedelta(hours=2), price=100, amount=1, side="long", leverage=10),
        _event(now - timedelta(hours=1), price=101, amount=1, side="short", leverage=50),
    ]
    payload = build_unified_liquidation_heatmap(
        events,
        [_candle(now - timedelta(hours=2), 90, 110)],
        "BTCUSDT",
        timeframe_seconds=3600,
        size_filter="25x",
        now=now,
    )

    assert payload["n_events"] == 1
    assert payload["filters"]["filter_basis"] == "leverage"
    assert payload["filters"]["leverage_available"] is True
    assert payload["filters"]["leverage_minimum"] == 25
    assert payload["filters"]["available_thresholds"] == ["all", "10x", "25x", "50x", "100x"]


def _event(timestamp: datetime, *, price: float, amount: float, side: str, leverage: float | None = None) -> LiquidationEvent:
    notional = price * amount
    return LiquidationEvent(
        symbol="BTCUSDT",
        source="bitget",
        interval="event",
        bucket_start=timestamp,
        long_liquidation_usd=notional if side == "long" else 0,
        short_liquidation_usd=notional if side == "short" else 0,
        raw_json={
            "price": price,
            "amount": amount,
            "position_side": side,
            "notional_usd_estimated": notional,
            **({"leverage": leverage} if leverage is not None else {}),
        },
    )


def _candle(timestamp: datetime, low: float, high: float) -> MarketCandle:
    return MarketCandle(
        timestamp=timestamp,
        open=low + 1,
        high=high,
        low=low,
        close=high - 1,
        volume=1,
    )
