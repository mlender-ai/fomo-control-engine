from __future__ import annotations

from typing import Any, Literal

AssetClass = Literal["crypto", "stock", "index", "unknown"]

INDEX_TICKERS = {
    "DIA",
    "IWM",
    "NDX",
    "QQQ",
    "SPY",
    "TQQQ",
    "SQQQ",
    "VIX",
}

STOCK_TICKERS = {
    "AAPL",
    "AMD",
    "AMZN",
    "ARM",
    "AVGO",
    "COIN",
    "CRCL",
    "GOOG",
    "GOOGL",
    "HOOD",
    "META",
    "MSFT",
    "MSTR",
    "NBIS",
    "NFLX",
    "NVDA",
    "NVDL",
    "NVDU",
    "PLTR",
    "POPMART",
    "SMCI",
    "SOXL",
    "SKHY",
    "TSLA",
    "TSLL",
    "TSLS",
    "XIAOMI",
}


def base_ticker(symbol: str, base_coin: str = "") -> str:
    base = (base_coin or "").strip().upper()
    if base:
        return base
    normalized = symbol.strip().upper().replace("/", "")
    for suffix in ("USDT", "USDC", "USD"):
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def classify_asset_class(symbol: str, base_coin: str = "", quote_coin: str = "", metadata: dict[str, Any] | None = None) -> AssetClass:
    """Classify a Bitget perp without inventing a class for unsupported symbols.

    Bitget RWA stock puffs are exposed on the same USDT-FUTURES productType and
    include isRwa=YES in the contracts payload. That field is the primary signal;
    ticker allowlists only split stock vs index inside the RWA universe.
    """

    meta = metadata or {}
    ticker = base_ticker(symbol, base_coin)
    category_text = " ".join(str(meta.get(key, "")) for key in ("category", "businessType", "symbolType", "symbolName", "productType")).lower()
    is_rwa = str(meta.get("isRwa", "")).upper() == "YES"

    if "index" in category_text or ticker in INDEX_TICKERS:
        return "index"
    if is_rwa:
        return "stock"
    if "stock" in category_text or "equity" in category_text:
        return "stock"
    if ticker in STOCK_TICKERS:
        return "stock"

    quote = quote_coin.strip().upper()
    if not quote:
        normalized = symbol.strip().upper().replace("/", "")
        quote = next((suffix for suffix in ("USDT", "USDC", "USD") if normalized.endswith(suffix)), "")
    if quote in {"USDT", "USDC", "USD"} and symbol.strip().upper().endswith(quote):
        return "crypto"
    return "unknown"


def source_category_from_contract(metadata: dict[str, Any] | None) -> str:
    meta = metadata or {}
    if str(meta.get("isRwa", "")).upper() == "YES":
        return "bitget_rwa"
    symbol_type = str(meta.get("symbolType", "")).strip()
    return symbol_type or "bitget_contract"


def funding_interval_from_contract(metadata: dict[str, Any] | None) -> int | None:
    meta = metadata or {}
    for key in ("fundInterval", "fundingRateInterval", "fundingInterval"):
        value = meta.get(key)
        if value in (None, ""):
            continue
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            continue
        return parsed if parsed > 0 else None
    return None
