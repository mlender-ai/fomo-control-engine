"""Pre-Entry Scout API (WO-FCE-12, WO-FCE-13).

심볼 카탈로그 검색, 관심종목 CRUD, 포지션 무관 분석/일괄 스캔,
진입 시뮬레이터, 시나리오 저장·매칭(라이프사이클 연결).
runtime 리포지토리/프로바이더는 routes 모듈 전역을 그대로 공유한다
(configure_runtime으로 교체되므로 호출 시점에 조회).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api import routes as runtime
from app.db.models import (
    CatalogSymbol,
    Direction,
    EntryChecklistItem,
    EntryScenario,
    JudgmentLedgerEntry,
    WatchlistItem,
    utc_now,
)
from app.exchange.bitget.provider import BitgetMarketDataProvider
from app.indicators.engine import calculate_indicators
from app.positions.chart_analysis import build_chart_analysis
from app.positions.engine import direction_aware_score
from app.positions.simulator import simulate_entry
from app.structure.wyckoff.engine import analyze_structure

router = APIRouter()

SCENARIO_MATCH_WINDOW_HOURS = 72
SCENARIO_SLIPPAGE_FLAG_PCT = 1.5

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
                maintenance_margin_rate=item.get("maintenance_margin_rate"),
                taker_fee_rate=item.get("taker_fee_rate"),
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


# ---- WO-FCE-13: 진입 시뮬레이터 + 시나리오 라이프사이클 ----


class SimulateRequest(BaseModel):
    symbol: str
    direction: str
    entry_price: float | None = None
    leverage: float = 1
    margin_usdt: float | None = None
    margin_mode: str = "isolated"
    timeframe: str = "4h"


def _simulate(request: SimulateRequest) -> dict[str, Any]:
    symbol = request.symbol.strip().upper()
    if request.direction not in ("long", "short"):
        raise HTTPException(status_code=422, detail="direction은 long 또는 short여야 합니다.")
    if request.leverage <= 0:
        raise HTTPException(status_code=422, detail="레버리지는 0보다 커야 합니다.")
    entry = _analysis_entry(symbol, request.timeframe, force=False, include_trade_flow=False)
    analysis = dict(entry["analysis"])
    summary = entry["summary"]
    analysis["funding_rate"] = summary.get("funding_rate")
    entry_price = request.entry_price if request.entry_price and request.entry_price > 0 else analysis.get("mark_price")
    if not entry_price:
        raise HTTPException(status_code=422, detail="진입가를 확인할 수 없습니다.")
    direction_score = summary.get("long_score") if request.direction == "long" else summary.get("short_score")
    result = simulate_entry(
        symbol=symbol,
        direction=request.direction,
        entry_price=float(entry_price),
        leverage=request.leverage,
        margin_usdt=request.margin_usdt,
        margin_mode=request.margin_mode,
        chart_analysis=analysis,
        mmr=_mmr_for_symbol(symbol),
        direction_score=direction_score,
    )
    result["analysis_as_of"] = entry["as_of"]
    return result


@router.post("/api/scout/simulate")
def simulate(request: SimulateRequest) -> dict:
    return _simulate(request)


def _mmr_for_symbol(symbol: str) -> float | None:
    _ensure_catalog()
    for item in _repo().search_symbols(symbol, limit=5):
        if item.symbol.upper() == symbol.upper():
            return item.maintenance_margin_rate
    return None


class SaveScenarioRequest(BaseModel):
    symbol: str
    direction: str
    entry_price: float
    leverage: float
    margin_usdt: float | None = None
    margin_mode: str = "isolated"
    timeframe: str = "4h"
    note: str = ""


@router.post("/api/scout/scenarios")
def save_scenario(request: SaveScenarioRequest) -> dict:
    sim = _simulate(SimulateRequest(**request.model_dump()))
    scenario = EntryScenario(
        symbol=request.symbol.strip().upper(),
        direction=Direction.long if request.direction == "long" else Direction.short,
        entry_price=request.entry_price,
        leverage=request.leverage,
        margin_usdt=request.margin_usdt,
        margin_mode=request.margin_mode,
        timeframe=request.timeframe,
        estimated_liquidation=sim.get("estimated_liquidation"),
        action_plan=sim.get("action_plan", {}),
        checklist=[EntryChecklistItem(**item) for item in sim.get("checklist", [])],
        rr_ratio=sim.get("rr_ratio"),
        analysis_as_of=_parse_dt(sim.get("analysis_as_of")),
        note=request.note,
    )
    _repo().add_entry_scenario(scenario)
    return {"scenario": scenario.model_dump(mode="json")}


@router.get("/api/scout/scenarios")
def list_scenarios(symbol: str | None = None, limit: int = 50) -> dict:
    items = _repo().list_entry_scenarios(symbol, min(max(limit, 1), 100))
    return {"scenarios": [item.model_dump(mode="json") for item in items]}


@router.get("/api/scout/match/{position_id}")
def match_scenario(position_id: UUID) -> dict:
    """열린 포지션에 연결 가능한 최근 시나리오를 읽기 시점에 탐색한다(자동 확정 아님)."""
    position = _repo().get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="포지션을 찾을 수 없습니다.")
    if position.scenario_id is not None:
        linked = _repo().get_entry_scenario(position.scenario_id)
        return {"already_linked": True, "scenario": linked.model_dump(mode="json") if linked else None, "suggestion": None}
    scenario = _repo().find_matching_scenario(position.symbol, position.direction.value, SCENARIO_MATCH_WINDOW_HOURS)
    if scenario is None:
        return {"already_linked": False, "scenario": None, "suggestion": None}
    slippage = _slippage_pct(scenario.entry_price, position.entry_price, position.direction.value)
    return {
        "already_linked": False,
        "scenario": scenario.model_dump(mode="json"),
        "suggestion": {
            "entry_memo": scenario.note,
            "thesis_text": _thesis_from_scenario(scenario),
            "planned_stop_price": _plan_price(scenario.action_plan, "invalidation"),
            "planned_take_profit_price": _plan_price(scenario.action_plan, "take_profit"),
            "slippage_pct": slippage,
            "slippage_flag": slippage is not None and abs(slippage) > SCENARIO_SLIPPAGE_FLAG_PCT,
        },
    }


class LinkScenarioRequest(BaseModel):
    position_id: UUID
    apply_prefill: bool = True


@router.post("/api/scout/scenarios/{scenario_id}/link")
def link_scenario(scenario_id: UUID, request: LinkScenarioRequest) -> dict:
    repo = _repo()
    scenario = repo.get_entry_scenario(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail="시나리오를 찾을 수 없습니다.")
    position = repo.get_position(request.position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="포지션을 찾을 수 없습니다.")

    stop_price = _plan_price(scenario.action_plan, "invalidation")
    tp_price = _plan_price(scenario.action_plan, "take_profit")
    updates: dict[str, Any] = {"scenario_id": scenario.id}
    if request.apply_prefill:
        if scenario.note and not position.entry_memo:
            updates["entry_memo"] = scenario.note
        thesis = _thesis_from_scenario(scenario)
        if thesis and not position.thesis_text:
            updates["thesis_text"] = thesis
        if stop_price is not None and position.planned_stop_price is None:
            updates["planned_stop_price"] = stop_price
        if tp_price is not None and position.planned_take_profit_price is None:
            updates["planned_take_profit_price"] = tp_price
    updated_position = position.model_copy(update=updates)
    repo.update_position(updated_position)
    repo.link_scenario_position(scenario.id, position.id)

    # WO-08 연동: 시나리오의 계획 가격과 체크리스트를 진입 전 판단으로 원장에 등록.
    _register_scenario_judgments(scenario, updated_position)

    slippage = _slippage_pct(scenario.entry_price, position.entry_price, position.direction.value)
    return {
        "linked": True,
        "position_id": str(position.id),
        "scenario_id": str(scenario.id),
        "slippage_pct": slippage,
        "slippage_flag": slippage is not None and abs(slippage) > SCENARIO_SLIPPAGE_FLAG_PCT,
        "position": updated_position.model_dump(mode="json"),
    }


def _register_scenario_judgments(scenario: EntryScenario, position) -> None:
    repo = _repo()
    as_of = scenario.analysis_as_of or scenario.created_at
    entries: list[JudgmentLedgerEntry] = []
    stop_price = _plan_price(scenario.action_plan, "invalidation")
    if stop_price is not None:
        entries.append(
            JudgmentLedgerEntry(
                judgment_id=f"scenario:{scenario.id}:planned_invalidation",
                position_id=position.id,
                source_type="entry_scenario",
                source_id=str(scenario.id),
                as_of=as_of,
                type="planned_invalidation",
                claim={"price": stop_price, "planned_entry": scenario.entry_price, "actual_entry": position.entry_price},
            )
        )
    tp_price = _plan_price(scenario.action_plan, "take_profit")
    if tp_price is not None:
        entries.append(
            JudgmentLedgerEntry(
                judgment_id=f"scenario:{scenario.id}:planned_take_profit",
                position_id=position.id,
                source_type="entry_scenario",
                source_id=str(scenario.id),
                as_of=as_of,
                type="planned_take_profit",
                claim={"price": tp_price},
            )
        )
    slippage = _slippage_pct(scenario.entry_price, position.entry_price, position.direction.value)
    entries.append(
        JudgmentLedgerEntry(
            judgment_id=f"scenario:{scenario.id}:entry_checklist",
            position_id=position.id,
            source_type="entry_scenario",
            source_id=str(scenario.id),
            as_of=as_of,
            type="entry_checklist",
            claim={
                "checklist": [item.model_dump() for item in scenario.checklist],
                "rr_ratio": scenario.rr_ratio,
                "planned_entry": scenario.entry_price,
                "actual_entry": position.entry_price,
                "slippage_pct": slippage,
                "slippage_flag": slippage is not None and abs(slippage) > SCENARIO_SLIPPAGE_FLAG_PCT,
            },
        )
    )
    for entry in entries:
        repo.add_judgment(entry)


def _plan_price(action_plan: dict[str, Any], kind: str) -> float | None:
    if not isinstance(action_plan, dict):
        return None
    if kind == "invalidation":
        source = action_plan.get("invalidation") or action_plan.get("engine_invalidation")
        return source.get("price") if isinstance(source, dict) else None
    targets = action_plan.get("take_profit") or []
    if targets and isinstance(targets[0], dict):
        return targets[0].get("price")
    return None


def _thesis_from_scenario(scenario: EntryScenario) -> str:
    rr = f"R:R {scenario.rr_ratio}" if scenario.rr_ratio is not None else "R:R 미산정"
    return f"진입 시나리오({scenario.direction.value} {scenario.leverage:g}x · {rr}) 기반 진입"


def _slippage_pct(planned_entry: float, actual_entry: float, direction: str) -> float | None:
    if not planned_entry or not actual_entry:
        return None
    side = 1 if direction == "long" else -1
    return round(((actual_entry - planned_entry) / planned_entry) * 100 * side, 2)


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
