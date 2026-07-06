from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.db.models import DerivativeDataSnapshot, utc_now
from app.exchange.bitget.provider import BitgetMarketDataProvider


def collect_bitget_derivative_snapshot(provider: object, symbol: str, settings: Settings) -> DerivativeDataSnapshot:
    if not isinstance(provider, BitgetMarketDataProvider):
        return DerivativeDataSnapshot(
            symbol=symbol.upper(),
            provider="bitget",
            tier="bitget_public",
            source_status="locked",
            data_quality={"source": "bitget_public", "enabled": False},
            notes=["Bitget provider is not active."],
            as_of=utc_now(),
        )
    payload = provider.get_derivative_snapshot(symbol, settings.derivative_ratio_period)
    return _snapshot_from_payload(payload)


def coinglass_status_snapshot(symbol: str, settings: Settings) -> DerivativeDataSnapshot:
    configured = bool(settings.coinglass_api_key.strip())
    status = "partial" if configured else "locked"
    note = (
        "Coinglass key is configured; V4 ingestion can be enabled without schema changes."
        if configured
        else "Coinglass V4 key is not configured. Tier 2 liquidation clusters and exchange-aggregated OI are locked."
    )
    return DerivativeDataSnapshot(
        symbol=symbol.upper(),
        provider="coinglass",
        tier="coinglass",
        source_status=status,
        as_of=utc_now(),
        data_quality={"source": "coinglass_v4", "configured": configured},
        notes=[note],
    )


def flow_summary(snapshot: DerivativeDataSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {
            "status": "missing",
            "headline": "파생 데이터가 아직 없습니다.",
            "funding_state": "unknown",
            "oi_state": "unknown",
            "long_short_state": "unknown",
        }
    funding = snapshot.funding_rate
    oi_change = snapshot.open_interest_change_pct
    ratio = snapshot.long_short_ratio
    funding_state = "neutral"
    if funding is not None and abs(funding) >= 0.01:
        funding_state = "long_overheated" if funding > 0 else "short_overheated"
    oi_state = "unknown" if oi_change is None else "rising" if oi_change > 0 else "falling" if oi_change < 0 else "flat"
    long_short_state = "unknown"
    if ratio is not None:
        long_short_state = "long_heavy" if ratio > 1.15 else "short_heavy" if ratio < 0.85 else "balanced"
    return {
        "status": snapshot.source_status,
        "headline": _headline(funding_state, oi_state, long_short_state),
        "funding_state": funding_state,
        "oi_state": oi_state,
        "long_short_state": long_short_state,
        "sources": {
            "provider": snapshot.provider,
            "tier": snapshot.tier,
            "as_of": snapshot.as_of.isoformat(),
            "source_status": snapshot.source_status,
        },
    }


def _snapshot_from_payload(payload: dict[str, Any]) -> DerivativeDataSnapshot:
    return DerivativeDataSnapshot(
        symbol=str(payload.get("symbol", "")).upper(),
        provider=str(payload.get("provider", "bitget")),
        tier="bitget_public",
        as_of=payload.get("as_of") or utc_now(),
        open_interest=_optional_float(payload.get("open_interest")),
        open_interest_value=_optional_float(payload.get("open_interest_value")),
        open_interest_change_pct=_optional_float(payload.get("open_interest_change_pct")),
        funding_rate=_optional_float(payload.get("funding_rate")),
        funding_rate_interval_hours=_optional_int(payload.get("funding_rate_interval_hours")),
        next_funding_time=payload.get("next_funding_time"),
        long_short_ratio=_optional_float(payload.get("long_short_ratio")),
        long_account_ratio=_optional_float(payload.get("long_account_ratio")),
        short_account_ratio=_optional_float(payload.get("short_account_ratio")),
        taker_buy_sell_ratio=_optional_float(payload.get("taker_buy_sell_ratio")),
        data_quality=payload.get("data_quality") if isinstance(payload.get("data_quality"), dict) else {},
        source_status=payload.get("source_status") if payload.get("source_status") in {"ok", "partial", "locked", "error"} else "partial",
        notes=[str(note) for note in payload.get("notes", []) if note],
        raw_json=payload.get("raw_json") if isinstance(payload.get("raw_json"), dict) else {},
    )


def _headline(funding_state: str, oi_state: str, long_short_state: str) -> str:
    parts = []
    if funding_state == "long_overheated":
        parts.append("펀딩 과열: 롱 비용 부담")
    elif funding_state == "short_overheated":
        parts.append("펀딩 음수 과열: 숏 비용 부담")
    else:
        parts.append("펀딩 중립")
    if oi_state == "rising":
        parts.append("OI 증가")
    elif oi_state == "falling":
        parts.append("OI 감소")
    if long_short_state == "long_heavy":
        parts.append("롱 비중 우세")
    elif long_short_state == "short_heavy":
        parts.append("숏 비중 우세")
    return " · ".join(parts)


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
