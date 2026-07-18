from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal


UnderlyingKind = Literal["stock", "etf", "leveraged_etf"]


@dataclass(frozen=True)
class CanonicalUnderlying:
    ticker: str
    official_name: str
    exchange: str
    kind: UnderlyingKind
    leverage_note: str | None = None


# Bitget's contract catalog identifies these products as RWA but does not expose
# the underlying issuer name or listing exchange. This version-controlled registry
# supplies that missing identity evidence; it is never generated from ticker text.
CANONICAL_UNDERLYINGS: dict[str, CanonicalUnderlying] = {
    "MSTR": CanonicalUnderlying(
        ticker="MSTR",
        official_name="Strategy Inc",
        exchange="NASDAQ",
        kind="stock",
    ),
    "NBIS": CanonicalUnderlying(
        ticker="NBIS",
        official_name="NEBIUS GROUP N.V.",
        exchange="NASDAQ",
        kind="stock",
    ),
    "SOXL": CanonicalUnderlying(
        ticker="SOXL",
        official_name="DIREXION SHARES ETF TRUST DAILY SEMICONDUCTOR BULL 3X SHS",
        exchange="AMEX",
        kind="leveraged_etf",
        leverage_note="3x 레버리지 ETF · Bitget 퍼페추얼 결합 시 레버리지 중첩",
    ),
}


def canonical_underlying(ticker: str) -> CanonicalUnderlying | None:
    return CANONICAL_UNDERLYINGS.get(ticker.strip().upper())


def toss_underlying_kind(stock: dict[str, Any]) -> UnderlyingKind | None:
    security_type = str(stock.get("securityType") or "").upper()
    if security_type == "STOCK":
        return "stock"
    if security_type == "ETF":
        try:
            factor = abs(float(stock.get("leverageFactor") or 1))
        except (TypeError, ValueError):
            factor = 1
        return "leveraged_etf" if factor > 1 else "etf"
    return None


def compare_underlying_identity(
    canonical: CanonicalUnderlying,
    toss_stock: dict[str, Any],
) -> dict[str, bool]:
    return {
        "official_name": _identity_text(canonical.official_name) == _identity_text(str(toss_stock.get("englishName") or "")),
        "exchange": canonical.exchange.upper() == str(toss_stock.get("market") or "").upper(),
        "asset_type": canonical.kind == toss_underlying_kind(toss_stock),
    }


def _identity_text(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())
