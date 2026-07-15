from datetime import datetime, timedelta, timezone

from app.db.models import LiquidationEvent
from app.derivatives.liquidation_heatmap import build_realized_liquidation_heatmap
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


def _event(timestamp: datetime, *, price: float, amount: float, side: str) -> LiquidationEvent:
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
        },
    )
