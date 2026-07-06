from __future__ import annotations

from datetime import timedelta

from app.db.models import DerivativeDataSnapshot, DerivativeMetric, LiquidationEvent, utc_now
from app.marketdata.base import DerivativeCollection


class FakeDerivativesProvider:
    source = "demo"

    def collect(self, symbol: str) -> DerivativeCollection:
        normalized = symbol.upper()
        now = utc_now()
        funding = 0.012 if normalized == "ETHUSDT" else 0.0008 if normalized == "BTCUSDT" else -0.0003
        oi_change = 18.0 if normalized == "ETHUSDT" else 5.2 if normalized == "BASEDUSDT" else 2.8
        taker_ls = 1.72 if normalized != "ETHUSDT" else 0.62
        metric = DerivativeMetric(
            symbol=normalized,
            source="bitget",
            tier="bitget_public",
            as_of=now,
            open_interest=1_250_000.0 if normalized == "BASEDUSDT" else 42_000_000.0,
            open_interest_value=8_600_000.0 if normalized == "BASEDUSDT" else 160_000_000.0,
            oi_change_pct=oi_change,
            funding=funding,
            taker_ls=taker_ls,
            long_account_ratio=0.63 if taker_ls > 1 else 0.38,
            short_account_ratio=0.37 if taker_ls > 1 else 0.62,
            data_quality={"source": "demo_derivatives", "demo": True},
            coverage={"window": "24h", "method": "fixed_seed"},
            raw_json={"demo": True},
        )
        clusters = [
            {"price": _cluster_price(normalized, 1.018), "side": "short_liquidation", "magnitude": 82, "distance_pct": 1.8, "priority": "high", "source": "demo_liq_cluster"},
            {"price": _cluster_price(normalized, 0.972), "side": "long_liquidation", "magnitude": 64, "distance_pct": -2.8, "priority": "medium", "source": "demo_liq_cluster"},
        ]
        snapshot = DerivativeDataSnapshot(
            symbol=normalized,
            provider="bitget",
            tier="bitget_public",
            as_of=now,
            open_interest=metric.open_interest,
            open_interest_value=metric.open_interest_value,
            open_interest_change_pct=metric.oi_change_pct,
            funding_rate=metric.funding,
            long_short_ratio=metric.taker_ls,
            long_account_ratio=metric.long_account_ratio,
            short_account_ratio=metric.short_account_ratio,
            liquidation_clusters=clusters,
            data_quality={"source": "demo_derivatives", "demo": True},
            raw_json={"demo": True},
        )
        events = [
            LiquidationEvent(
                symbol=normalized,
                source="coinglass",
                interval="1h",
                bucket_start=now - timedelta(hours=index),
                long_liquidation_usd=25_000 * (index + 1),
                short_liquidation_usd=18_000 * (index + 1),
                source_status="ok",
                data_quality={"source": "demo_liquidations", "demo": True},
            )
            for index in range(4)
        ]
        return DerivativeCollection(provider="demo", symbol=normalized, metrics=[metric], liquidation_events=events, snapshot=snapshot)


def _cluster_price(symbol: str, multiplier: float) -> float:
    base = {"BTCUSDT": 110_400.0, "ETHUSDT": 3_706.0, "BASEDUSDT": 0.1007}.get(symbol, 100.0)
    return round(base * multiplier, 5 if symbol == "BASEDUSDT" else 2)
