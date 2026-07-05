"""Pre-Entry Scout API (WO-FCE-12).

심볼 카탈로그 검색, 관심종목 CRUD, 포지션 무관 분석/일괄 스캔.
runtime 리포지토리/프로바이더는 routes 모듈 전역을 그대로 공유한다
(configure_runtime으로 교체되므로 호출 시점에 조회).
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api import routes as runtime
from app.db.models import CatalogSymbol, WatchlistItem, utc_now
from app.exchange.bitget.provider import BitgetMarketDataProvider
from app.indicators.engine import calculate_indicators
from app.positions.chart_analysis import build_chart_analysis
from app.positions.engine import direction_aware_score
from app.structure.wyckoff.engine import analyze_structure

router = APIRouter()

SCAN_CACHE_TTL_SECONDS = 300  # 심볼+타임프레임당 최소 재계산 간격 (Bitget 레이트리밋 방어)
CATALOG_REFRESH_SECONDS = 86400  # 카탈로그 일 1회 갱신

_ANALYSIS_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


class WatchlistRequest(BaseModel):
    symbol: str
    note: str = ""
    default_timeframe: str = "4h"


def _repo():
    return runtime.repository


def _provider():
    return runtime.market_provider


def reset_scout_cache() -> None:
    _ANALYSIS_CACHE.clear()


def _ensure_catalog() -> None:
    repo = _repo()
    updated_at = repo.symbol_catalog_updated_at()
    if updated_at is not None and (utc_now() - updated_at).total_seconds() < CATALOG_REFRESH_SECONDS:
        return
    lister = getattr(_provider(), "list_contracts", None)
    if lister is None:
        return
    try:
        contracts = lister()
    except Exception:
        # 수집 실패 시 기존 캐시 유지 (다음 요청에서 재시도)
        return
    if not contracts:
        return
    repo.replace_symbol_catalog(
        [
            CatalogSymbol(
                symbol=item["symbol"],
                base_coin=item.get("base_coin", ""),
                quote_coin=item.get("quote_coin", ""),
                status=item.get("status", ""),
            )
            for item in contracts
        ]
    )


@router.get("/api/symbols")
def search_symbols(query: str = "", limit: int = 20) -> dict:
    _ensure_catalog()
    symbols = _repo().search_symbols(query, min(max(limit, 1), 50))
    return {"symbols": [item.model_dump(mode="json") for item in symbols]}


@router.get("/api/watchlist")
def list_watchlist() -> dict:
    return {"items": [item.model_dump(mode="json") for item in _repo().list_watchlist()]}


@router.post("/api/watchlist")
def add_watchlist_item(request: WatchlistRequest) -> dict:
    symbol = request.symbol.strip().upper()
    if not symbol:
        raise HTTPException(status_code=422, detail="symbol이 비어 있습니다.")
    item = WatchlistItem(symbol=symbol, note=request.note, default_timeframe=request.default_timeframe)
    _repo().upsert_watchlist_item(item)
    return {"item": item.model_dump(mode="json")}


@router.delete("/api/watchlist/{symbol}")
def remove_watchlist_item(symbol: str) -> dict:
    removed = _repo().remove_watchlist_item(symbol)
    if not removed:
        raise HTTPException(status_code=404, detail="관심종목에 없는 심볼입니다.")
    return {"removed": symbol.upper()}


@router.get("/api/scout/{symbol}/analysis")
def scout_analysis(symbol: str, timeframe: str = "4h", force: bool = False) -> dict:
    entry = _analysis_entry(symbol, timeframe, force=force, include_trade_flow=True)
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "as_of": entry["as_of"],
        "cache_age_seconds": round(time.monotonic() - entry["cached_at_monotonic"], 1),
        "analysis": entry["analysis"],
        "summary": entry["summary"],
    }


class ScanRequest(BaseModel):
    timeframe: str | None = None
    force: bool = False


@router.post("/api/scout/scan")
def scan_watchlist(request: ScanRequest | None = None) -> dict:
    request = request or ScanRequest()
    items = _repo().list_watchlist()
    rows: list[dict[str, Any]] = []
    for item in items:  # 순차 실행 — 레이트리밋 방어 (클라이언트 백오프는 provider 내장)
        timeframe = request.timeframe or item.default_timeframe or "4h"
        try:
            entry = _analysis_entry(item.symbol, timeframe, force=request.force, include_trade_flow=False)
        except Exception as exc:
            rows.append({"symbol": item.symbol, "timeframe": timeframe, "error": str(exc)})
            continue
        rows.append({**entry["summary"], "timeframe": timeframe, "as_of": entry["as_of"], "note": item.note})
    rows.sort(key=lambda row: row.get("setup_proximity_pct") if row.get("setup_proximity_pct") is not None else float("inf"))
    return {
        "rows": rows,
        "scanned_at": utc_now().isoformat(),
        "cache_ttl_seconds": SCAN_CACHE_TTL_SECONDS,
        "count": len(rows),
    }


def _analysis_entry(symbol: str, timeframe: str, force: bool, include_trade_flow: bool) -> dict[str, Any]:
    key = (symbol.upper(), timeframe)
    cached = _ANALYSIS_CACHE.get(key)
    now = time.monotonic()
    if cached and not force and now - cached["cached_at_monotonic"] < SCAN_CACHE_TTL_SECONDS:
        return cached
    provider = _provider()
    try:
        snapshot = provider.get_snapshot(symbol, timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    trade_flow = None
    if include_trade_flow and isinstance(provider, BitgetMarketDataProvider):
        trade_flow = provider.get_trade_flow(symbol, timeframe, snapshot.candles)
    try:
        analysis = build_chart_analysis(snapshot, None, trade_flow)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    entry = {
        "analysis": analysis,
        "summary": _summary_row(symbol, snapshot, analysis),
        "as_of": utc_now().isoformat(),
        "cached_at_monotonic": now,
    }
    _ANALYSIS_CACHE[key] = entry
    return entry


def _summary_row(symbol: str, snapshot, analysis: dict[str, Any]) -> dict[str, Any]:
    indicators = calculate_indicators(snapshot)
    structure = analyze_structure(snapshot, indicators)
    mark = analysis.get("mark_price")

    markers = analysis.get("wyckoff_markers", [])
    top_event = max(markers, key=lambda item: item.get("confidence", 0), default=None)

    prz_distance = _nearest_prz_distance_pct(analysis.get("harmonic_patterns", []), mark)
    level_distance = _nearest_level_distance_pct(analysis.get("price_levels", {}), mark)
    proximity_candidates = [value for value in (prz_distance, level_distance) if value is not None]

    return {
        "symbol": symbol.upper(),
        "long_score": direction_aware_score("long", structure, indicators),
        "short_score": direction_aware_score("short", structure, indicators),
        "wyckoff_phase": analysis.get("wyckoff_phase", {}).get("phase", "undetermined"),
        "top_event": {"label": top_event.get("label"), "confidence": top_event.get("confidence")} if top_event else None,
        "harmonic_active": bool(analysis.get("harmonic_patterns")),
        "prz_distance_pct": prz_distance,
        "nearest_level_distance_pct": level_distance,
        "volume_state": analysis.get("volume_xray", {}).get("volume_state", "data_unavailable"),
        "change_24h": snapshot.change_24h,
        "funding_rate": snapshot.funding_rate,
        "setup_proximity_pct": min(proximity_candidates) if proximity_candidates else None,
        "mark_price": mark,
    }


def _nearest_prz_distance_pct(patterns: list[dict[str, Any]], mark: float | None) -> float | None:
    if not patterns or not mark:
        return None
    distances = []
    for pattern in patterns:
        prz = pattern.get("prz")
        if not isinstance(prz, dict):
            continue
        mid = prz.get("mid")
        if mid is None:
            low, high = prz.get("low"), prz.get("high")
            if low is None or high is None:
                continue
            mid = (low + high) / 2
        distances.append(abs((mid - mark) / mark) * 100)
    return round(min(distances), 2) if distances else None


def _nearest_level_distance_pct(price_levels: dict[str, Any], mark: float | None) -> float | None:
    if not mark:
        return None
    candidates = []
    for side in ("support", "resistance"):
        levels = price_levels.get(side) or []
        if levels and levels[0].get("price") is not None:
            candidates.append(abs((levels[0]["price"] - mark) / mark) * 100)
    return round(min(candidates), 2) if candidates else None
