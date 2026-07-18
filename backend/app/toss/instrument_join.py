from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import threading
import time
from typing import Any, Callable

from fastapi import HTTPException

from app.core.config import Settings
from app.db.models import CatalogSymbol, DataQuality, InstrumentMap, MarketCandle, MarketSnapshot, PositionStatus, utc_now
from app.db.repository import Repository
from app.positions.chart_analysis import build_chart_analysis

from .client import TossReadOnlyClient
from .instrument_identity import canonical_underlying, compare_underlying_identity, toss_underlying_kind
from .service import _normalize_candles, _session_state
from .signals import warning_gate
from .store import TossStockStore

ClientFactory = Callable[..., TossReadOnlyClient]
JOIN_CACHE_SECONDS = 30
_join_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_join_locks: dict[str, threading.Lock] = {}
_join_locks_guard = threading.Lock()


def target_universe(repo: Repository) -> list[dict[str, Any]]:
    sources: dict[str, set[str]] = {}
    for position in repo.list_positions(PositionStatus.open):
        sources.setdefault(position.symbol.upper(), set()).add("position")
    for item in repo.list_watchlist():
        sources.setdefault(item.symbol.upper(), set()).add("watchlist")
    catalog = {item.symbol.upper(): item for item in repo.search_symbols("", limit=10_000)}
    rows: list[dict[str, Any]] = []
    for symbol, source_set in sorted(sources.items()):
        item = catalog.get(symbol)
        is_rwa = _is_bitget_rwa(item)
        rows.append(
            {
                "symbol": symbol,
                "sources": sorted(source_set),
                "asset_class": item.asset_class if item else "unknown",
                "source_category": item.source_category if item else "catalog_missing",
                "join_eligible": is_rwa,
                "join_reason": "Bitget RWA 계약" if is_rwa else "순수 크립토 또는 검증되지 않은 계약 · Toss 미연결",
            }
        )
    return rows


def list_mapping_state(repo: Repository) -> dict[str, Any]:
    targets = target_universe(repo)
    items = repo.list_instrument_maps()
    by_symbol = {item.bitget_symbol: item for item in items}
    return {
        "targets": [
            {
                **target,
                "mapping_status": (
                    by_symbol[target["symbol"]].verification_status if target["join_eligible"] and target["symbol"] in by_symbol else "not_applicable"
                ),
            }
            for target in targets
        ],
        "items": [item.model_dump(mode="json") for item in items],
        "policy": {
            "price_of_record": "Bitget",
            "structure_source": "Toss verified underlying only",
            "pending_join_enabled": False,
            "crypto_toss_enabled": False,
        },
    }


def sync_mapping_candidates(
    repo: Repository,
    settings: Settings,
    *,
    client_factory: ClientFactory = TossReadOnlyClient,
) -> dict[str, Any]:
    targets = [target for target in target_universe(repo) if target["join_eligible"]]
    if not targets or not settings.toss_client_id or not settings.toss_client_secret:
        return list_mapping_state(repo)
    catalog = {item.symbol.upper(): item for item in repo.search_symbols("", limit=10_000)}
    tickers = [catalog[target["symbol"]].base_coin.upper() for target in targets]
    stocks = _run(_load_toss_stocks(settings, tickers, client_factory))
    stock_by_symbol = {str(row.get("symbol") or "").upper(): row for row in stocks}
    for target in targets:
        contract = catalog[target["symbol"]]
        ticker = contract.base_coin.upper()
        _upsert_candidate(repo, contract, stock_by_symbol.get(ticker))
    return list_mapping_state(repo)


def approve_mapping(repo: Repository, bitget_symbol: str) -> InstrumentMap:
    normalized = bitget_symbol.upper()
    if normalized not in _eligible_target_symbols(repo):
        raise HTTPException(status_code=409, detail="현재 포지션·워치리스트의 검증된 Bitget RWA 계약만 승인할 수 있습니다.")
    item = repo.get_instrument_map(normalized)
    if item is None:
        raise HTTPException(status_code=404, detail="매핑 후보가 없습니다.")
    if item.verification_status != "pending":
        raise HTTPException(status_code=409, detail="승인 대기 중인 매핑만 승인할 수 있습니다.")
    if not item.identity_match:
        raise HTTPException(status_code=409, detail="정식 명칭·거래소·자산유형 검증을 모두 통과해야 승인할 수 있습니다.")
    now = utc_now()
    return repo.upsert_instrument_map(
        item.model_copy(
            update={
                "verification_status": "verified",
                "verified_by": "manual",
                "verified_at": now,
                "updated_at": now,
                "notes": "사용자 수동 승인 완료",
            }
        )
    )


def reject_mapping(repo: Repository, bitget_symbol: str, note: str = "") -> InstrumentMap:
    item = repo.get_instrument_map(bitget_symbol)
    if item is None:
        raise HTTPException(status_code=404, detail="매핑 후보가 없습니다.")
    now = utc_now()
    return repo.upsert_instrument_map(
        item.model_copy(
            update={
                "verification_status": "rejected",
                "verified_by": "manual",
                "verified_at": None,
                "updated_at": now,
                "notes": note.strip() or "사용자 수동 거부",
            }
        )
    )


def decorate_chart_analysis(
    repo: Repository,
    settings: Settings,
    analysis: dict[str, Any],
    *,
    client_factory: ClientFactory = TossReadOnlyClient,
) -> dict[str, Any]:
    symbol = str(analysis.get("symbol") or "").upper()
    if symbol not in _eligible_target_symbols(repo):
        return analysis
    mapping = repo.get_instrument_map(symbol)
    if mapping is None or mapping.verification_status != "verified" or not mapping.identity_match:
        return analysis
    if not settings.toss_client_id or not settings.toss_client_secret:
        return {**analysis, "underlying_join": _unavailable_context(mapping, "Toss 인증값 없음")}
    try:
        observation = _cached_join_observation(mapping, settings, client_factory)
        return _joined_display_analysis(analysis, observation)
    except Exception as exc:
        return {**analysis, "underlying_join": _unavailable_context(mapping, f"{type(exc).__name__}: {exc}")}


def reset_join_cache() -> None:
    _join_cache.clear()


def _upsert_candidate(repo: Repository, contract: CatalogSymbol, toss_stock: dict[str, Any] | None) -> InstrumentMap:
    now = utc_now()
    ticker = contract.base_coin.upper()
    canonical = canonical_underlying(ticker)
    current = repo.get_instrument_map(contract.symbol)
    if toss_stock is None:
        identity_match = bool(current and current.identity_match)
        status = current.verification_status if current else "pending"
        notes = "Toss 종목 메타데이터를 찾지 못해 수동 확인 대기"
        underlying_name = canonical.official_name if canonical else ticker
        exchange = canonical.exchange if canonical else "UNKNOWN"
        kind = canonical.kind if canonical else "stock"
        evidence = current.verification_evidence if current else {"checks": {}, "reason": "toss_metadata_missing"}
    elif canonical is None:
        identity_match = False
        status = "pending"
        notes = "버전 관리 신원 레지스트리 없음 · 자동 검증 금지"
        underlying_name = str(toss_stock.get("englishName") or toss_stock.get("name") or ticker)
        exchange = str(toss_stock.get("market") or "UNKNOWN")
        kind = toss_underlying_kind(toss_stock) or "stock"
        evidence = _verification_evidence(contract, None, toss_stock, {})
    else:
        checks = compare_underlying_identity(canonical, toss_stock)
        identity_match = all(checks.values())
        manual_rejected = bool(current and current.verification_status == "rejected" and current.verified_by == "manual")
        status = "rejected" if not identity_match or manual_rejected else "verified" if current and current.verification_status == "verified" else "pending"
        notes = "정식 명칭·거래소·자산유형 일치 · 사용자 승인 대기" if identity_match else _mismatch_note(checks)
        underlying_name = canonical.official_name
        exchange = canonical.exchange
        kind = canonical.kind
        evidence = _verification_evidence(contract, canonical, toss_stock, checks)
    item = InstrumentMap(
        bitget_symbol=contract.symbol.upper(),
        bitget_type="usdt_futures",
        underlying_name=underlying_name,
        underlying_kind=kind,
        toss_symbol=ticker,
        toss_market="US",
        toss_exchange=exchange,
        leverage_note=canonical.leverage_note if canonical else None,
        verification_status=status,
        verified_by=current.verified_by if current and status in {"verified", "rejected"} else "auto-candidate",
        verified_at=current.verified_at if current and status == "verified" else None,
        identity_match=identity_match,
        notes=notes if not current or status != "verified" else current.notes,
        verification_evidence=evidence,
        created_at=current.created_at if current else now,
        updated_at=now,
    )
    return repo.upsert_instrument_map(item)


async def _load_toss_stocks(settings: Settings, tickers: list[str], client_factory: ClientFactory) -> list[dict[str, Any]]:
    client = client_factory(
        settings.toss_client_id,
        settings.toss_client_secret,
        base_url=settings.toss_base_url,
        timeout_seconds=settings.toss_timeout_seconds,
    )
    try:
        payload = await client.get("/api/v1/stocks", params={"symbols": ",".join(dict.fromkeys(tickers))})
        result = payload.get("result") or []
        return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []
    finally:
        await client.close()


def _cached_join_observation(mapping: InstrumentMap, settings: Settings, client_factory: ClientFactory) -> dict[str, Any]:
    key = mapping.bitget_symbol
    cached = _join_cache.get(key)
    if cached and time.monotonic() - cached[0] < JOIN_CACHE_SECONDS:
        return deepcopy(cached[1])
    with _join_locks_guard:
        lock = _join_locks.setdefault(key, threading.Lock())
    with lock:
        cached = _join_cache.get(key)
        if cached and time.monotonic() - cached[0] < JOIN_CACHE_SECONDS:
            return deepcopy(cached[1])
        observation = _run(_load_join_observation(mapping, settings, client_factory))
        _join_cache[key] = (time.monotonic(), observation)
        return deepcopy(observation)


async def _load_join_observation(mapping: InstrumentMap, settings: Settings, client_factory: ClientFactory) -> dict[str, Any]:
    client = client_factory(
        settings.toss_client_id,
        settings.toss_client_secret,
        base_url=settings.toss_base_url,
        timeout_seconds=settings.toss_timeout_seconds,
    )
    try:
        price_payload, candle_payload, calendar_payload, warnings_payload = await asyncio.gather(
            client.get("/api/v1/prices", params={"symbols": mapping.toss_symbol}),
            client.get(
                "/api/v1/candles",
                params={"symbol": mapping.toss_symbol, "interval": "1d", "count": 200, "adjusted": "true"},
            ),
            client.get(f"/api/v1/market-calendar/{mapping.toss_market}"),
            client.get(f"/api/v1/stocks/{mapping.toss_symbol}/warnings"),
        )
    finally:
        await client.close()
    prices = price_payload.get("result") or []
    price_row = next((row for row in prices if isinstance(row, dict) and str(row.get("symbol") or "").upper() == mapping.toss_symbol), None)
    if not isinstance(price_row, dict) or price_row.get("lastPrice") is None:
        raise ValueError("Toss 기초자산 현재가 없음")
    result = candle_payload.get("result") or {}
    raw_rows = result.get("candles") if isinstance(result, dict) else []
    normalized = sorted(_normalize_candles(raw_rows if isinstance(raw_rows, list) else []), key=lambda item: str(item["opened_at"]))
    if len(normalized) < 100:
        raise ValueError(f"Toss 기초자산 일봉 부족 ({len(normalized)}/100)")
    store = TossStockStore(settings.database_url)
    loaded_at = datetime.now(timezone.utc).isoformat()
    if store.enabled:
        store.upsert_candles(
            mapping.toss_market,
            mapping.toss_symbol,
            "1d",
            "toss_1d_adjusted",
            loaded_at,
            normalized,
        )
        store.upsert_warning(mapping.toss_market, mapping.toss_symbol, loaded_at, warnings_payload)
    warning_rows = warnings_payload.get("result") or []
    warnings = (
        [str(row.get("warningType") or row.get("type") or "") for row in warning_rows if isinstance(row, dict) and (row.get("warningType") or row.get("type"))]
        if isinstance(warning_rows, list)
        else []
    )
    warning_blocked, warning_badges = warning_gate(warnings)
    return {
        "mapping": mapping.model_dump(mode="json"),
        "toss_price": float(price_row["lastPrice"]),
        "toss_price_at": price_row.get("timestamp"),
        "market_state": _session_state(calendar_payload),
        "warnings": warnings,
        "warning_gate_blocked": warning_blocked,
        "warning_badges": warning_badges,
        "raw_candles": normalized,
        "loaded_at": loaded_at,
    }


def _joined_display_analysis(analysis: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    bitget_price = float(analysis.get("mark_price") or 0)
    toss_price = float(observation["toss_price"])
    if bitget_price <= 0 or toss_price <= 0:
        raise ValueError("베이시스 계산 가격 없음")
    scale = bitget_price / toss_price
    raw_candles = observation["raw_candles"]
    aligned_models = [
        MarketCandle(
            timestamp=datetime.fromisoformat(str(row["opened_at"]).replace("Z", "+00:00")),
            open=float(row["open"]) * scale,
            high=float(row["high"]) * scale,
            low=float(row["low"]) * scale,
            close=float(row["close"]) * scale,
            volume=float(row["volume"]),
        )
        for row in raw_candles
    ]
    change = ((aligned_models[-1].close / aligned_models[-2].close) - 1) * 100 if len(aligned_models) > 1 else 0
    structure = build_chart_analysis(
        MarketSnapshot(
            symbol=str(analysis.get("symbol") or ""),
            timeframe="1d",
            price=bitget_price,
            change_24h=change,
            funding_rate=0,
            open_interest_change=0,
            candles=aligned_models,
            provider="bitget+toss_underlying",
            data_quality=DataQuality(
                ohlcv_ok=True,
                funding_ok=False,
                open_interest_ok=False,
                min_candles_met=True,
                candles=len(aligned_models),
                last_candle_at=aligned_models[-1].timestamp,
            ),
        )
    )
    joined = {**structure}
    for key in (
        "position_id",
        "direction",
        "entry_price",
        "liquidation_price",
        "position_context",
        "derivatives",
        "trade_flow",
        "options",
        "onchain",
        "validated_onchain_evidence",
        "historical_backtest",
        "full_alignment",
    ):
        if key in analysis:
            joined[key] = analysis[key]
    levels = joined.get("price_levels") if isinstance(joined.get("price_levels"), dict) else {}
    original_levels = analysis.get("price_levels") if isinstance(analysis.get("price_levels"), dict) else {}
    joined["price_levels"] = {
        **levels,
        "entry": original_levels.get("entry", analysis.get("entry_price")),
        "mark": bitget_price,
        "liquidation": original_levels.get("liquidation", analysis.get("liquidation_price")),
    }
    mapping = observation["mapping"]
    joined["underlying_join"] = {
        "status": "joined",
        "price_of_record": "bitget",
        "structure_source": "toss",
        "structure_timeframe": "1d",
        "bitget_symbol": mapping["bitget_symbol"],
        "bitget_price": bitget_price,
        "toss_symbol": mapping["toss_symbol"],
        "toss_price": toss_price,
        "toss_price_at": observation["toss_price_at"],
        "basis_pct": (bitget_price / toss_price - 1) * 100,
        "basis_scale": scale,
        "market_state": observation["market_state"],
        "stale": observation["market_state"] != "open",
        "underlying_name": mapping["underlying_name"],
        "underlying_kind": mapping["underlying_kind"],
        "toss_exchange": mapping["toss_exchange"],
        "leverage_note": mapping.get("leverage_note"),
        "flow_status": "unavailable_us" if mapping["toss_market"] == "US" else "available",
        "flow_note": "Toss US 투자자별 수급 미제공 · 해당 신호 비활성" if mapping["toss_market"] == "US" else None,
        "toss_warnings": observation["warnings"],
        "warning_gate_blocked": observation["warning_gate_blocked"],
        "warning_badges": observation["warning_badges"],
        "verified_at": mapping.get("verified_at"),
        "raw_candles": raw_candles,
        "loaded_at": observation["loaded_at"],
        "disclaimer": "차트 구조=Toss 기초자산 · 현재가/실행/파생지표=Bitget 퍼페추얼 · 자동 진입 근거로 미사용",
    }
    return joined


def _verification_evidence(contract: CatalogSymbol, canonical: Any, toss_stock: dict[str, Any], checks: dict[str, bool]) -> dict[str, Any]:
    return {
        "bitget": {
            "symbol": contract.symbol,
            "base_coin": contract.base_coin,
            "is_rwa": contract.raw_metadata.get("isRwa"),
            "underlying_name": canonical.official_name if canonical else None,
            "exchange": canonical.exchange if canonical else None,
            "asset_type": canonical.kind if canonical else None,
        },
        "toss": {
            "symbol": toss_stock.get("symbol"),
            "official_name": toss_stock.get("englishName"),
            "exchange": toss_stock.get("market"),
            "asset_type": toss_underlying_kind(toss_stock),
            "security_type": toss_stock.get("securityType"),
            "leverage_factor": toss_stock.get("leverageFactor"),
        },
        "checks": checks,
        "ticker_only_match_used": False,
    }


def _mismatch_note(checks: dict[str, bool]) -> str:
    labels = {"official_name": "정식 명칭", "exchange": "거래소", "asset_type": "자산유형"}
    failed = [labels[key] for key, passed in checks.items() if not passed]
    return f"신원 불일치로 자동 거부: {', '.join(failed)}"


def _is_bitget_rwa(item: CatalogSymbol | None) -> bool:
    if item is None:
        return False
    return item.source_category == "bitget_rwa" and str(item.raw_metadata.get("isRwa") or "").upper() == "YES"


def _eligible_target_symbols(repo: Repository) -> set[str]:
    return {target["symbol"] for target in target_universe(repo) if target["join_eligible"]}


def _unavailable_context(mapping: InstrumentMap, reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "bitget_symbol": mapping.bitget_symbol,
        "toss_symbol": mapping.toss_symbol,
        "reason": reason,
        "price_of_record": "bitget",
        "structure_source": "toss",
    }


def _run(coro):
    return asyncio.run(coro)
