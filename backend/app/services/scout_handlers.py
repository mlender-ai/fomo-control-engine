"""Pre-Entry Scout API (WO-FCE-12, WO-FCE-13).

심볼 카탈로그 검색, 관심종목 CRUD, 포지션 무관 분석/일괄 스캔,
진입 시뮬레이터, 시나리오 저장·매칭(라이프사이클 연결).
runtime 리포지토리/프로바이더는 routes 모듈 전역을 그대로 공유한다
(configure_runtime으로 교체되므로 호출 시점에 조회).
"""

from __future__ import annotations

import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, Field

from app.analyst.briefing import build_analyst_briefing, hysteresis_params_from_settings, load_directional_prior
from app.backtest.regimes import label_regime
from app.backtest.service import _regime_params, backtest_line, historical_context_for_analysis
from app.services import http_handlers as runtime
from app.db.models import (
    CatalogSymbol,
    Direction,
    EntryChecklistItem,
    EntryIntent,
    EntryScenario,
    JudgmentLedgerEntry,
    WatchlistItem,
    utc_now,
)
from app.derivatives.context import derivative_context_for_chart
from app.exchange.bitget.provider import BitgetMarketDataProvider
from app.indicators.engine import calculate_indicators
from app.marketdata.assets import classify_asset_class
from app.performance.metrics import attach_kelly_to_simulation
from app.positions.chart_analysis import build_chart_analysis
from app.positions.engine import direction_aware_score
from app.positions.simulator import simulate_entry
from app.review.params import engine_param_snapshot
from app.scout.monitor import (
    SCOUT_SENTINEL_POSITION_ID,
    arm_manual_setup,
    disarm_setup,
    scout_rate_budget,
    setup_candidates_from_analysis,
)
from app.structure.wyckoff.engine import analyze_structure


SCENARIO_MATCH_WINDOW_HOURS = 72
SCENARIO_SLIPPAGE_FLAG_PCT = 1.5

SCAN_CACHE_TTL_SECONDS = 300  # 심볼+타임프레임당 최소 재계산 간격 (Bitget 레이트리밋 방어)
CATALOG_REFRESH_SECONDS = 86400  # 카탈로그 일 1회 갱신

_ANALYSIS_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_BTC_REGIME_CACHE: dict[str, Any] = {}
BTC_REGIME_TTL_SECONDS = 900  # BTC 레짐은 자주 안 바뀌므로 15분 캐시 (레이트리밋 방어)
logger = logging.getLogger(__name__)


class WatchlistRequest(BaseModel):
    symbol: str
    note: str = ""
    default_timeframe: str = "4h"


def _repo():
    return runtime.repository


def _provider():
    return runtime.market_provider


def _btc_market_regime(timeframe: str) -> dict[str, Any] | None:
    """WO-36 §5: 크립토 알트 신호에 병기할 BTC 시장 레짐 (15분 캐시)."""
    cache = _BTC_REGIME_CACHE.get(timeframe)
    now = time.monotonic()
    if cache and now - cache["cached_at_monotonic"] < BTC_REGIME_TTL_SECONDS:
        return cache["regime"]
    try:
        snapshot = _provider().get_snapshot("BTCUSDT", timeframe)
        regime = label_regime(snapshot.candles, **_regime_params(runtime.settings))
    except Exception:
        return cache["regime"] if cache else None
    _BTC_REGIME_CACHE[timeframe] = {"regime": regime, "cached_at_monotonic": now}
    return regime


def reset_scout_cache() -> None:
    _ANALYSIS_CACHE.clear()
    _BTC_REGIME_CACHE.clear()


def _ensure_catalog() -> None:
    repo = _repo()
    updated_at = repo.symbol_catalog_updated_at()
    if updated_at is not None and (utc_now() - updated_at).total_seconds() < CATALOG_REFRESH_SECONDS and not _catalog_has_stale_asset_classes(repo):
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
    catalog: list[CatalogSymbol] = []
    for item in contracts:
        asset_class = item.get("asset_class") or classify_asset_class(
            item["symbol"],
            item.get("base_coin", ""),
            item.get("quote_coin", ""),
            item.get("raw_metadata") if isinstance(item.get("raw_metadata"), dict) else item,
        )
        if asset_class == "unknown":
            logger.warning("symbol asset class unknown", extra={"symbol": item.get("symbol")})
        catalog.append(
            CatalogSymbol(
                symbol=item["symbol"],
                base_coin=item.get("base_coin", ""),
                quote_coin=item.get("quote_coin", ""),
                status=item.get("status", ""),
                asset_class=asset_class,
                source_category=item.get("source_category", ""),
                funding_rate_interval_hours=item.get("funding_rate_interval_hours"),
                raw_metadata=item.get("raw_metadata") if isinstance(item.get("raw_metadata"), dict) else {},
                maintenance_margin_rate=item.get("maintenance_margin_rate"),
                taker_fee_rate=item.get("taker_fee_rate"),
            )
        )
    repo.replace_symbol_catalog(catalog)


def search_symbols(query: str = "", limit: int = 20) -> dict:
    _ensure_catalog()
    symbols = _repo().search_symbols(query, min(max(limit, 1), 50))
    return {"symbols": [_resolve_catalog_asset_class(item).model_dump(mode="json") for item in symbols]}


def normalize_scout_symbol(symbol: str) -> str:
    """Normalize Telegram/API shorthand such as BTC into the Bitget contract symbol."""
    raw = symbol.strip().upper().replace("/", "")
    if not raw:
        raise HTTPException(status_code=422, detail="symbol이 비어 있습니다.")
    _ensure_catalog()
    matches = _repo().search_symbols(raw, 50)
    exact = next((item for item in matches if item.symbol.upper() == raw), None)
    if exact is not None:
        return exact.symbol.upper()
    base = next((item for item in matches if item.base_coin.upper() == raw), None)
    if base is not None:
        return base.symbol.upper()
    usdt_symbol = raw if raw.endswith("USDT") else f"{raw}USDT"
    appended = next((item for item in matches if item.symbol.upper() == usdt_symbol), None)
    if appended is not None:
        return appended.symbol.upper()
    return usdt_symbol


def list_watchlist() -> dict:
    return {"items": [item.model_dump(mode="json") for item in _repo().list_watchlist()]}


def add_watchlist_item(request: WatchlistRequest) -> dict:
    symbol = normalize_scout_symbol(request.symbol)
    catalog = next((item for item in _repo().search_symbols(symbol, 20) if item.symbol.upper() == symbol), None)
    asset_class = catalog.asset_class if catalog and catalog.asset_class != "unknown" else classify_asset_class(symbol)
    item = WatchlistItem(symbol=symbol, note=request.note, default_timeframe=request.default_timeframe, asset_class=asset_class)
    _repo().upsert_watchlist_item(item)
    return {"item": item.model_dump(mode="json")}


def remove_watchlist_item(symbol: str) -> dict:
    normalized = normalize_scout_symbol(symbol)
    removed = _repo().remove_watchlist_item(normalized)
    if not removed:
        raise HTTPException(status_code=404, detail="관심종목에 없는 심볼입니다.")
    return {"removed": normalized}


def _catalog_has_stale_asset_classes(repo: Any) -> bool:
    for probe in ("TSLA", "MSTR", "QQQ"):
        for item in repo.search_symbols(probe, 5):
            if item.symbol.upper().endswith("USDT") and item.asset_class == "unknown":
                return True
    return False


def _resolve_catalog_asset_class(item: CatalogSymbol) -> CatalogSymbol:
    if item.asset_class != "unknown":
        return item
    asset_class = classify_asset_class(item.symbol, item.base_coin, item.quote_coin, item.raw_metadata)
    return item.model_copy(update={"asset_class": asset_class})


def scout_analysis(symbol: str, timeframe: str = "4h", force: bool = False) -> dict:
    symbol = normalize_scout_symbol(symbol)
    entry = _analysis_entry(symbol, timeframe, force=force, include_trade_flow=True)
    briefing = _briefing_for_entry(symbol, timeframe, entry, action_plan=None, context="pre_entry")
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": entry["as_of"],
        "cache_age_seconds": round(time.monotonic() - entry["cached_at_monotonic"], 1),
        "analysis": entry["analysis"],
        "summary": entry["summary"],
        "historical_backtest": entry["historical_backtest"],
        "analyst_briefing": briefing,
    }


def scout_briefing(symbol: str, timeframe: str = "4h", force: bool = False) -> dict:
    symbol = normalize_scout_symbol(symbol)
    entry = _analysis_entry(symbol, timeframe, force=force, include_trade_flow=True)
    briefing = _briefing_for_entry(symbol, timeframe, entry, action_plan=None, context="pre_entry")
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": entry["as_of"],
        "historical_backtest": entry["historical_backtest"],
        "analyst_briefing": briefing,
    }


def scout_backtest(symbol: str, timeframe: str = "4h", force: bool = False) -> dict:
    symbol = normalize_scout_symbol(symbol)
    entry = _analysis_entry(symbol, timeframe, force=force, include_trade_flow=False)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "as_of": entry["as_of"],
        "historical_backtest": entry["historical_backtest"],
    }


class ScanRequest(BaseModel):
    timeframe: str | None = None
    force: bool = False


def scan_watchlist(request: ScanRequest | None = None) -> dict:
    request = request or ScanRequest()
    items = _repo().list_watchlist()
    rows: list[dict[str, Any]] = []
    for item in items:  # 순차 실행 — 레이트리밋 방어 (클라이언트 백오프는 provider 내장)
        timeframe = request.timeframe or item.default_timeframe or "4h"
        try:
            entry = _analysis_entry(item.symbol, timeframe, force=request.force, include_trade_flow=False)
        except Exception as exc:
            rows.append({"symbol": item.symbol, "timeframe": timeframe, "asset_class": item.asset_class, "error": str(exc)})
            continue
        rows.append(
            {
                **entry["summary"],
                "timeframe": timeframe,
                "as_of": entry["as_of"],
                "note": item.note,
                "asset_class": entry["summary"].get("asset_class") or item.asset_class,
            }
        )
    rows.sort(key=lambda row: row.get("setup_proximity_pct") if row.get("setup_proximity_pct") is not None else float("inf"))
    intents = _repo().list_entry_intents(limit=300)
    return {
        "rows": rows,
        "armed_setups": [setup.model_dump(mode="json") for setup in _repo().list_armed_setups(limit=300)],
        "entry_intents": [intent.model_dump(mode="json") for intent in intents],
        "scanned_at": utc_now().isoformat(),
        "cache_ttl_seconds": SCAN_CACHE_TTL_SECONDS,
        "count": len(rows),
        "rate_budget": scout_rate_budget(runtime.settings, len(items)),
    }


def list_universe_discoveries(symbol: str | None = None, status: str | None = None, limit: int = 50) -> dict:
    items = _repo().list_universe_discoveries(symbol=symbol, status=status, limit=min(max(limit, 1), 200))
    return {"discoveries": [item.model_dump(mode="json") for item in items]}


def scan_universe(request: ScanRequest | None = None) -> dict:
    from app.scout.universe import run_universe_scan

    request = request or ScanRequest()

    def load(symbol: str, timeframe: str) -> dict[str, Any]:
        return _analysis_entry(symbol, timeframe, force=request.force, include_trade_flow=False)

    payload = run_universe_scan(
        _repo(),
        runtime.settings,
        analysis_loader=load,
        timeframe=request.timeframe or "4h",
        ticker_rows=_market_tickers(),
    )
    payload.pop("_alert_candidate_objects", None)
    return payload


def _analysis_entry(symbol: str, timeframe: str, force: bool, include_trade_flow: bool) -> dict[str, Any]:
    symbol = normalize_scout_symbol(symbol)
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
        derivatives = _derivative_context(symbol)
        analysis = build_chart_analysis(snapshot, None, trade_flow, derivatives=derivatives)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    market_regime = None
    if str(analysis.get("asset_class") or "").startswith("crypto") and snapshot.symbol.upper() not in {"BTCUSDT", "BTCUSD"}:
        market_regime = _btc_market_regime(snapshot.timeframe)
    historical = historical_context_for_analysis(
        _repo(),
        runtime.settings,
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        analysis=analysis,
        candles=snapshot.candles,
        force=force,
        market_regime=market_regime,
    )
    analysis["historical_backtest"] = historical
    entry = {
        "analysis": analysis,
        "summary": _summary_row(symbol, snapshot, analysis, derivatives),
        "historical_backtest": historical,
        "as_of": utc_now().isoformat(),
        "cached_at_monotonic": now,
    }
    _ANALYSIS_CACHE[key] = entry
    return entry


def _market_tickers() -> list[dict[str, Any]]:
    lister = getattr(_provider(), "list_tickers", None)
    if not callable(lister):
        return []
    try:
        rows = lister()
    except Exception:
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _derivative_context(symbol: str) -> dict[str, Any]:
    try:
        return derivative_context_for_chart(_repo(), runtime.settings, symbol)
    except Exception:
        return {}


def _summary_row(
    symbol: str,
    snapshot,
    analysis: dict[str, Any],
    derivatives: dict[str, Any] | None = None,
) -> dict[str, Any]:
    indicators = calculate_indicators(snapshot)
    structure = analyze_structure(snapshot, indicators)
    mark = analysis.get("mark_price")

    markers = analysis.get("wyckoff_markers", [])
    top_event = max(markers, key=lambda item: item.get("confidence", 0), default=None)

    prz_distance = _nearest_prz_distance_pct(analysis.get("harmonic_patterns", []), mark)
    level_distance = _nearest_level_distance_pct(analysis.get("price_levels", {}), mark)
    liquidity_pool = _nearest_liquidity_pool(analysis.get("liquidity", {}), mark)
    liquidity_distance = liquidity_pool.get("distance_pct") if liquidity_pool else None
    proximity_candidates = [value for value in (prz_distance, level_distance, liquidity_distance) if value is not None]

    signals = derivatives.get("signals") if isinstance(derivatives, dict) and isinstance(derivatives.get("signals"), dict) else {}
    crowding = signals.get("crowding_score") if isinstance(signals.get("crowding_score"), dict) else None
    funding = signals.get("funding_state") if isinstance(signals.get("funding_state"), dict) else None
    setup_candidates = setup_candidates_from_analysis(symbol, snapshot.timeframe, analysis, runtime.settings)
    _attach_backtest_to_candidates(setup_candidates, analysis.get("historical_backtest"))
    long_evidence = _direction_evidence_count("long", structure, indicators)
    short_evidence = _direction_evidence_count("short", structure, indicators)
    long_score = direction_aware_score("long", structure, indicators) if long_evidence >= 3 else None
    short_score = direction_aware_score("short", structure, indicators) if short_evidence >= 3 else None
    return {
        "symbol": symbol.upper(),
        "asset_class": analysis.get("asset_class") or classify_asset_class(symbol),
        "session": analysis.get("session"),
        "long_score": long_score,
        "short_score": short_score,
        "long_evidence_count": long_evidence,
        "short_evidence_count": short_evidence,
        "wyckoff_phase": analysis.get("wyckoff_phase", {}).get("phase", "undetermined"),
        "top_event": {
            "label": top_event.get("label"),
            "confidence": top_event.get("confidence"),
        }
        if top_event
        else None,
        "harmonic_active": bool(analysis.get("harmonic_patterns")),
        "prz_distance_pct": prz_distance,
        "nearest_level_distance_pct": level_distance,
        "liquidity_nearest_pool": liquidity_pool,
        "liquidity_pool_distance_pct": liquidity_distance,
        "volume_state": analysis.get("volume_xray", {}).get("volume_state", "data_unavailable"),
        "change_24h": snapshot.change_24h,
        "funding_rate": snapshot.funding_rate,
        "funding_state": funding.get("label") if funding else None,
        "crowding_score": crowding.get("score") if crowding else None,
        "quote_volume_24h": _quote_volume_24h(snapshot.candles),
        "setup_proximity_pct": min(proximity_candidates) if proximity_candidates else None,
        "entry_intent_distance_pct": _nearest_intent_distance(symbol, mark),
        "mark_price": mark,
        "setup_candidates": setup_candidates,
        "backtest_summary": backtest_line(analysis.get("historical_backtest")),
    }


def _direction_evidence_count(direction: str, structure: dict[str, Any], indicators: dict[str, Any]) -> int:
    trend = structure.get("trend") if isinstance(structure.get("trend"), dict) else {}
    wyckoff = structure.get("wyckoff") if isinstance(structure.get("wyckoff"), dict) else {}
    count = 0
    if trend.get("direction") not in (None, "", "unknown"):
        count += 1
    if direction == "long":
        count += int(bool(trend.get("higher_low")))
        count += int(bool(wyckoff.get("spring_candidate")))
        count += int(bool(wyckoff.get("sos_confirmed")))
        count += int(_safe_float(wyckoff.get("accumulation_score")) > 0)
    else:
        count += int(bool(trend.get("lower_high")))
        count += int(bool(wyckoff.get("utad_candidate")))
        count += int(bool(wyckoff.get("sow_confirmed")))
        count += int(_safe_float(wyckoff.get("distribution_score")) > 0)
    for key in ("last_close", "rsi", "macd", "bollinger_upper", "bollinger_lower"):
        if indicators.get(key) is not None:
            count += 1
    return count


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _quote_volume_24h(candles: list[Any]) -> float | None:
    recent = list(candles[-6:]) if candles else []
    if not recent:
        return None
    total = 0.0
    for candle in recent:
        quote_volume = getattr(candle, "quote_volume", None)
        if quote_volume is not None:
            total += float(quote_volume)
            continue
        total += float(getattr(candle, "volume", 0.0) or 0.0) * float(getattr(candle, "close", 0.0) or 0.0)
    return round(total, 2)


def _attach_backtest_to_candidates(candidates: list[dict[str, Any]], context: Any) -> None:
    line = backtest_line(context if isinstance(context, dict) else None)
    if not line:
        return
    for candidate in candidates:
        preview = candidate.get("preview") if isinstance(candidate.get("preview"), dict) else {}
        preview["backtest_summary"] = line
        candidate["preview"] = preview
        candidate["backtest_summary"] = line


def _nearest_intent_distance(symbol: str, mark: float | None) -> float | None:
    if mark is None:
        return None
    distances: list[float] = []
    for intent in _repo().list_entry_intents(symbol=symbol, status="active", limit=20):
        if intent.zone_lower <= mark <= intent.zone_upper:
            distances.append(0.0)
        elif mark < intent.zone_lower:
            distances.append(abs((intent.zone_lower - mark) / mark) * 100)
        else:
            distances.append(abs((mark - intent.zone_upper) / mark) * 100)
    return round(min(distances), 2) if distances else None


def _nearest_liquidity_pool(liquidity: Any, mark: Any) -> dict[str, Any] | None:
    if not isinstance(liquidity, dict) or not isinstance(mark, (int, float)) or mark <= 0:
        return None
    pools = liquidity.get("pools")
    if not isinstance(pools, list):
        return None
    candidates: list[tuple[float, dict[str, Any]]] = []
    for pool in pools:
        if not isinstance(pool, dict) or pool.get("swept"):
            continue
        price = pool.get("price")
        if not isinstance(price, (int, float)) or price <= 0:
            continue
        distance = abs((price - mark) / mark) * 100
        label = _liquidity_pool_label(pool)
        candidates.append(
            (
                distance,
                {
                    "price": price,
                    "distance_pct": round(distance, 2),
                    "label": label,
                    "kind": pool.get("kind"),
                    "side": pool.get("side"),
                    "touch_count": pool.get("touch_count") or pool.get("touches") or 1,
                    "score": pool.get("score"),
                    "grade": pool.get("grade"),
                },
            )
        )
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _liquidity_pool_label(pool: dict[str, Any]) -> str:
    touches = pool.get("touch_count") or pool.get("touches") or 1
    kind = str(pool.get("kind") or "")
    if kind == "eqh":
        return f"상단 풀(EQH {touches}터치)"
    if kind == "eql":
        return f"하단 풀(EQL {touches}터치)"
    if kind == "old_high":
        return f"상단 풀(전고 {touches}터치)"
    if kind == "old_low":
        return f"하단 풀(전저 {touches}터치)"
    return str(pool.get("label") or "유동성 풀")


class ManualSetupRequest(BaseModel):
    trigger_price: float
    label: str = "수동 감시"
    condition: str = "가격 접근 시 반응 확인"
    direction: str | None = None
    timeframe: str = "4h"


def list_scout_setups(symbol: str | None = None, status: str | None = None, limit: int = 100) -> dict:
    setups = _repo().list_armed_setups(symbol=symbol, status=status, limit=min(max(limit, 1), 300))
    return {"setups": [setup.model_dump(mode="json") for setup in setups]}


def create_manual_setup(symbol: str, request: ManualSetupRequest) -> dict:
    if request.trigger_price <= 0:
        raise HTTPException(status_code=422, detail="trigger_price는 0보다 커야 합니다.")
    setup = arm_manual_setup(
        _repo(),
        symbol=symbol,
        timeframe=request.timeframe,
        trigger_price=request.trigger_price,
        label=request.label,
        condition=request.condition,
        direction=request.direction,
    )
    return {"setup": setup.model_dump(mode="json")}


def disarm_scout_setup(setup_id: UUID) -> dict:
    setup = disarm_setup(_repo(), setup_id)
    if setup is None:
        raise HTTPException(status_code=404, detail="셋업을 찾을 수 없습니다.")
    return {"setup": setup.model_dump(mode="json")}


class EntryIntentRequest(BaseModel):
    direction: str
    zone_lower: float | None = None
    zone_upper: float | None = None
    price: float | None = None
    conditions: list[str] = Field(default_factory=lambda: ["price_in_zone"])
    tolerance: str = "normal"
    expires_at: datetime | None = None
    note: str = ""
    timeframe: str = "4h"
    leverage: float = 10


def list_entry_intents(symbol: str | None = None, status: str | None = None, limit: int = 100) -> dict:
    intents = _repo().list_entry_intents(symbol=symbol, status=status, limit=min(max(limit, 1), 300))
    return {"intents": [intent.model_dump(mode="json") for intent in intents]}


def create_entry_intent(symbol: str, request: EntryIntentRequest) -> dict:
    normalized = symbol.strip().upper()
    if request.direction not in {"long", "short"}:
        raise HTTPException(status_code=422, detail="direction은 long 또는 short여야 합니다.")
    lower, upper = _intent_zone(request)
    if lower <= 0 or upper <= 0 or lower >= upper:
        raise HTTPException(status_code=422, detail="존 lower/upper는 0보다 크고 lower < upper여야 합니다.")
    conditions = _intent_conditions(request.conditions)
    tolerance = _intent_tolerance(request.tolerance)
    tolerance_pct = _intent_tolerance_pct(tolerance)
    expires_at = request.expires_at or (utc_now() + timedelta(days=max(1, runtime.settings.entry_intent_default_expiry_days)))
    if expires_at <= utc_now():
        raise HTTPException(status_code=422, detail="expires_at은 현재 이후여야 합니다.")
    active_for_symbol = _repo().list_entry_intents(symbol=normalized, status="active", limit=100)
    if len(active_for_symbol) >= runtime.settings.entry_intent_max_per_symbol:
        raise HTTPException(status_code=409, detail=f"심볼당 활성 의도는 {runtime.settings.entry_intent_max_per_symbol}개까지입니다.")
    active_total = _repo().list_entry_intents(status="active", limit=1000)
    if len(active_total) >= runtime.settings.entry_intent_max_active:
        raise HTTPException(status_code=409, detail=f"전체 활성 의도는 {runtime.settings.entry_intent_max_active}개까지입니다.")

    midpoint = (lower + upper) / 2
    preview: dict[str, Any] = {}
    try:
        preview_result = _simulate(
            SimulateRequest(
                symbol=normalized,
                direction=request.direction,
                entry_price=midpoint,
                leverage=request.leverage,
                timeframe=request.timeframe,
            )
        )
        preview = {
            "entry_price": midpoint,
            "leverage": request.leverage,
            "rr_ratio": preview_result.get("rr_ratio"),
            "invalidation_distance_pct": preview_result.get("invalidation_distance_pct"),
            "estimated_liquidation_distance_pct": preview_result.get("estimated_liquidation_distance_pct"),
            "checklist_passed": preview_result.get("checklist_passed"),
            "checklist_total": preview_result.get("checklist_total"),
            "verdict_line": preview_result.get("verdict_line"),
            "briefing_direction_conflict": preview_result.get("briefing_direction_conflict"),
        }
    except Exception as exc:
        preview = {"error": str(exc), "entry_price": midpoint, "leverage": request.leverage}

    now = utc_now()
    intent_id = uuid5(
        NAMESPACE_URL,
        f"fce:entry-intent:{normalized}:{request.timeframe}:{request.direction}:{round(lower, 8)}:{round(upper, 8)}:{expires_at.isoformat()}",
    )
    intent = EntryIntent(
        id=intent_id,
        symbol=normalized,
        timeframe=request.timeframe,
        direction=request.direction,  # type: ignore[arg-type]
        zone_lower=lower,
        zone_upper=upper,
        conditions=conditions,  # type: ignore[arg-type]
        tolerance=tolerance,  # type: ignore[arg-type]
        tolerance_pct=tolerance_pct,
        note=request.note,
        preview=preview,
        judgment_id=f"entry_intent:{intent_id}",
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
        last_seen_at=now,
    )
    saved = _repo().upsert_entry_intent(intent)
    _repo().add_judgment(
        JudgmentLedgerEntry(
            judgment_id=f"{saved.judgment_id or f'entry_intent:{saved.id}'}:registered",
            position_id=SCOUT_SENTINEL_POSITION_ID,
            source_type="entry_intent",
            source_id=str(saved.id),
            as_of=now,
            type="entry_intent_registered",
            claim={
                "symbol": saved.symbol,
                "direction": saved.direction,
                "zone_lower": saved.zone_lower,
                "zone_upper": saved.zone_upper,
                "conditions": saved.conditions,
                "expires_at": saved.expires_at.isoformat(),
            },
            param_version=engine_param_snapshot(_repo()),
        )
    )
    return {"intent": saved.model_dump(mode="json")}


def cancel_entry_intent(intent_id: UUID) -> dict:
    intent = _repo().get_entry_intent(intent_id)
    if intent is None:
        raise HTTPException(status_code=404, detail="진입 의도를 찾을 수 없습니다.")
    cancelled = intent.model_copy(update={"status": "cancelled", "updated_at": utc_now()})
    saved = _repo().upsert_entry_intent(cancelled)
    return {"intent": saved.model_dump(mode="json")}


def _intent_zone(request: EntryIntentRequest) -> tuple[float, float]:
    if request.zone_lower is not None and request.zone_upper is not None:
        return (min(float(request.zone_lower), float(request.zone_upper)), max(float(request.zone_lower), float(request.zone_upper)))
    if request.price is None:
        raise HTTPException(status_code=422, detail="zone_lower/zone_upper 또는 price가 필요합니다.")
    price = float(request.price)
    return (price * 0.995, price * 1.005)


def _intent_conditions(values: list[str]) -> list[str]:
    allowed = {"price_in_zone", "sweep_confirmed", "wyckoff_event", "volume_spike", "briefing_aligned"}
    normalized = [value for value in values if value in allowed]
    if "price_in_zone" not in normalized:
        normalized.insert(0, "price_in_zone")
    return list(dict.fromkeys(normalized))


def _intent_tolerance(value: str) -> str:
    if value in {"tight", "normal", "loose"}:
        return value
    return "normal"


def _intent_tolerance_pct(value: str) -> float:
    if value == "tight":
        return runtime.settings.entry_intent_tight_tolerance_pct
    if value == "loose":
        return runtime.settings.entry_intent_loose_tolerance_pct
    return runtime.settings.entry_intent_normal_tolerance_pct


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
    briefing = _briefing_for_entry(symbol, request.timeframe, entry, action_plan=result.get("action_plan"), context="pre_entry")
    result["analyst_briefing"] = briefing
    result["briefing_direction_conflict"] = _briefing_direction_conflict(briefing, request.direction)
    attach_kelly_to_simulation(result, entry.get("historical_backtest"))
    return result


def _briefing_for_entry(
    symbol: str,
    timeframe: str,
    entry: dict[str, Any],
    *,
    action_plan: dict[str, Any] | None,
    context: str,
) -> dict[str, Any]:
    # WO-53: 직전 방향 히스테리시스 상태를 최근 스냅샷에서 로드해 주입 (전환에 관성).
    return build_analyst_briefing(
        symbol=symbol,
        timeframe=timeframe,
        analysis=entry["analysis"],
        action_plan=action_plan,
        calibration_scores=_repo().list_judgment_scores(limit=2000),
        context=context,
        prior_state=load_directional_prior(_repo(), symbol, timeframe),
        hysteresis_params=hysteresis_params_from_settings(runtime.settings),
    )


def _briefing_direction_conflict(briefing: dict[str, Any], direction: str) -> bool:
    confluence = briefing.get("confluence") if isinstance(briefing.get("confluence"), dict) else {}
    stance = confluence.get("stance")
    return bool((direction == "long" and stance == "short_leaning") or (direction == "short" and stance == "long_leaning"))


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


def list_scenarios(symbol: str | None = None, limit: int = 50) -> dict:
    items = _repo().list_entry_scenarios(symbol, min(max(limit, 1), 100))
    return {"scenarios": [item.model_dump(mode="json") for item in items]}


def match_scenario(position_id: UUID) -> dict:
    """열린 포지션에 연결 가능한 최근 시나리오를 읽기 시점에 탐색한다(자동 확정 아님)."""
    position = _repo().get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="포지션을 찾을 수 없습니다.")
    if position.scenario_id is not None:
        linked = _repo().get_entry_scenario(position.scenario_id)
        return {
            "already_linked": True,
            "scenario": linked.model_dump(mode="json") if linked else None,
            "suggestion": None,
        }
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
    params = engine_param_snapshot(repo)
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
                claim={
                    "price": stop_price,
                    "planned_entry": scenario.entry_price,
                    "actual_entry": position.entry_price,
                },
                param_version=params,
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
                param_version=params,
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
            param_version=params,
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
