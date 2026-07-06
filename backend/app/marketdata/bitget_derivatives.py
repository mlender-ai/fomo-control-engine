from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.db.models import DerivativeDataSnapshot, DerivativeMetric, utc_now
from app.derivatives.engine import _snapshot_from_payload
from app.exchange.bitget.provider import BitgetMarketDataProvider
from app.marketdata.base import DerivativeCollection


class BitgetDerivProvider:
    source = "bitget"

    def __init__(self, market_provider: object, settings: Settings) -> None:
        self.market_provider = market_provider
        self.settings = settings

    def collect(self, symbol: str) -> DerivativeCollection:
        normalized = symbol.upper()
        if not isinstance(self.market_provider, BitgetMarketDataProvider):
            metric = DerivativeMetric(
                symbol=normalized,
                source="bitget",
                tier="bitget_public",
                source_status="locked",
                data_quality={"source": "bitget_public", "enabled": False},
                notes=["Bitget provider is not active."],
                as_of=utc_now(),
            )
            snapshot = DerivativeDataSnapshot(
                symbol=normalized,
                provider="bitget",
                tier="bitget_public",
                source_status="locked",
                data_quality=metric.data_quality,
                notes=metric.notes,
                as_of=metric.as_of,
            )
            return DerivativeCollection(
                provider="bitget",
                symbol=normalized,
                metrics=[metric],
                snapshot=snapshot,
            )
        payload = self.market_provider.get_derivative_snapshot(normalized, self.settings.derivative_ratio_period)
        snapshot = _snapshot_from_payload(payload)
        metric = metric_from_bitget_payload(payload)
        return DerivativeCollection(
            provider="bitget",
            symbol=normalized,
            metrics=[metric],
            snapshot=snapshot,
            requests_used=4,
        )


def metric_from_bitget_payload(payload: dict[str, Any]) -> DerivativeMetric:
    return DerivativeMetric(
        symbol=str(payload.get("symbol", "")).upper(),
        source="bitget",
        tier="bitget_public",
        as_of=payload.get("as_of") or utc_now(),
        open_interest=_optional_float(payload.get("open_interest")),
        open_interest_value=_optional_float(payload.get("open_interest_value")),
        oi_change_pct=_optional_float(payload.get("open_interest_change_pct")),
        funding=_optional_float(payload.get("funding_rate")),
        funding_interval_hours=_optional_int(payload.get("funding_rate_interval_hours")),
        funding_next=payload.get("next_funding_time"),
        taker_ls=_optional_float(payload.get("long_short_ratio")),
        long_account_ratio=_optional_float(payload.get("long_account_ratio")),
        short_account_ratio=_optional_float(payload.get("short_account_ratio")),
        source_status=payload.get("source_status") if payload.get("source_status") in {"ok", "partial", "locked", "error"} else "partial",
        data_quality=payload.get("data_quality") if isinstance(payload.get("data_quality"), dict) else {},
        coverage={
            "ratio_period": payload.get("raw_json", {}).get("long_short_ratio", {}).get("period") if isinstance(payload.get("raw_json"), dict) else None,
            "funding_interval_hours": _optional_int(payload.get("funding_rate_interval_hours")),
        },
        notes=[str(note) for note in payload.get("notes", []) if note],
        raw_json=payload.get("raw_json") if isinstance(payload.get("raw_json"), dict) else {},
    )


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
