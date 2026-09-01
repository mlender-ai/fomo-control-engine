from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import threading
import time
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.services import http_handlers as runtime
from app.services import scout_handlers
from app.services.scout_handlers import ScanRequest, SimulateRequest
from app.analyst.briefing import briefing_summary
from app.analyst.alignment import build_full_alignment
from app.backtest.candidate_scoring import score_candidates as _score_legacy_candidates
from app.backtest.candidate_scoring import score_live_candidate_judgments
from app.backtest.stance_validation import (
    refresh_stance_backtests as _refresh_stance_backtests,
    stance_backtest_dashboard as _stance_backtest_dashboard,
)
from app.db.maintenance import (
    enforce_retention,
    run_database_backup,
    run_database_maintenance,
)
from app.db.models import (
    AlertRecord,
    DerivativeMetric,
    JudgmentLedgerEntry,
    MarketSnapshotRecord,
    Position,
    PositionSnapshot,
    PositionStatus,
    utc_now,
)
from app.derivatives.context import derivative_context_for_symbol
from app.derivatives.engine import coinglass_status_snapshot
from app.derivatives.liquidation_heatmap import build_realized_liquidation_heatmap, build_unified_liquidation_heatmap
from app.demo.derivatives import FakeDerivativesProvider
from app.demo.seed import seed_demo_data as _seed_demo_data
from app.marketdata.bitget_derivatives import BitgetDerivProvider
from app.marketdata.bitget_liquidations import collect_bitget_liquidations
from app.marketdata.coinglass import CoinglassProvider
from app.marketdata.money_flow import flow_observation
from app.marketdata.signals import build_derivative_signals
from app.exchange.bitget.trades import timeframe_seconds
from app.paper.service import paper_dashboard as _paper_dashboard
from app.paper.service import paper_gate_funnel as _paper_gate_funnel
from app.paper.service import paper_universe as _paper_universe
from app.paper.service import paper_scoreboard as _paper_scoreboard
from app.paper.service import sync_user_fills as _sync_user_fills
from app.paper.service import start_paper_benchmark as _start_paper_benchmark
from app.paper.service import paper_universe
from app.paper.service import run_paper_engine as _run_paper_engine
from app.validation import history_backfill
from app.onchain.service import (
    add_whale_wallet as _add_whale_wallet,
    collect as _collect_whales,
    discover as _discover_whales,
    remove_whale_wallet as _remove_whale_wallet,
    whale_dashboard as _whale_dashboard,
)
from app.review.engine import (
    score_interim_judgments,
)
from app.review.autonomy import veto_suggestion
from app.review.alert_responses import (
    alert_history_line,
    detect_alert_response,
    score_alert_response,
)
from app.review.params import (
    apply_engine_param_overrides as _apply_engine_param_overrides,
    engine_param_snapshot,
)
from app.scout.monitor import process_scout_scan
from app.scout.universe import run_universe_scan
from app.validation.candidates import score_candidates as _score_candidates


@dataclass(frozen=True)
class SymbolMatch:
    position: Position | None
    candidates: list[Position]


_coinglass_round_robin_cursor = 0
_unified_heatmap_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_unified_heatmap_cache_lock = threading.Lock()
_UNIFIED_HEATMAP_CACHE_SECONDS = 4.0


logger = logging.getLogger(__name__)


def provider_name() -> str:
    return runtime._provider_name()


def add_whale_wallet(address: str, label: str | None = None, source: str = "manual") -> dict[str, Any]:
    return _add_whale_wallet(runtime.repository, runtime.settings, address, label, source=source).model_dump(mode="json")


def remove_whale_wallet(address: str) -> bool:
    return _remove_whale_wallet(runtime.repository, address)


def whale_dashboard() -> dict[str, Any]:
    return _whale_dashboard(runtime.repository, runtime.settings)


def collect_whales() -> dict[str, Any]:
    return _collect_whales(runtime.repository, runtime.settings)


def discover_whales() -> dict[str, Any]:
    return _discover_whales(runtime.repository, runtime.settings)


def sync_and_analyze_positions() -> dict[str, Any]:
    """Sync Bitget positions and analyze open positions using the same route path."""
    payload = runtime.sync_live_positions()
    removed = [str(symbol).upper() for symbol in payload.get("scout_tracking_removed", [])]
    removed.extend(clear_scout_tracking_for_open_positions()["removed"])
    payload["scout_tracking_removed"] = sorted(set(removed))
    try:
        from app.toss.store import TossStockStore

        prices: dict[str, float] = {}
        for position in runtime.repository.list_positions(PositionStatus.open):
            current_price = position.mark_price or position.current_price
            if current_price is not None:
                prices[position.symbol.upper()] = float(current_price)
        database_path = getattr(runtime.repository, "database_path", None)
        store = TossStockStore(f"sqlite:///{database_path}" if database_path else "memory://")
        for symbol in store.due_position_symbols() if store.enabled else []:
            if symbol in prices:
                continue
            try:
                snapshot = runtime.market_provider.get_snapshot(symbol, "4h")
            except Exception:
                continue
            if snapshot.price > 0:
                prices[symbol] = float(snapshot.price)
        payload["position_deepdive_outcomes_recorded"] = store.record_due_outcomes(prices) if store.enabled else 0
    except Exception as exc:
        payload["position_deepdive_outcome_error"] = f"{type(exc).__name__}: {exc}"
    return payload


def apply_engine_param_overrides() -> dict[str, Any]:
    return _apply_engine_param_overrides(runtime.settings, runtime.repository)


def seed_demo_data() -> dict[str, Any]:
    if not runtime.settings.demo_mode:
        return {"enabled": False, "seeded": False, "positions": 0}
    return {"enabled": True, **_seed_demo_data(runtime.repository, runtime.market_provider)}


def refresh_market_data() -> dict[str, Any]:
    """Refresh report and market snapshot cache for held and tracked symbols."""
    pairs = sorted(tracked_market_pairs(), key=_market_pair_stale_sort)
    refreshed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for symbol, timeframe in pairs:
        try:
            report = runtime._generate_and_store_report(symbol, timeframe)
            latest_candle = None
            candles = getattr(report.data_quality, "candles", 0)
            if isinstance(report.raw_json, dict):
                candles_payload = report.raw_json.get("candles")
                if isinstance(candles_payload, list) and candles_payload:
                    latest_candle = candles_payload[-1]
            runtime.repository.add_market_snapshot(
                MarketSnapshotRecord(
                    symbol=report.symbol,
                    timeframe=report.timeframe,
                    provider=report.provider,
                    candle_count=candles,
                    latest_price=report.price,
                    latest_candle_time=report.data_quality.last_candle_at,
                    data_quality=report.data_quality.model_dump(mode="json"),
                    indicators=report.raw_json.get("indicators", {}) if isinstance(report.raw_json, dict) else {},
                    scores=report.scores.model_dump(mode="json"),
                    reason_codes=report.raw_json.get("reason_codes", []) if isinstance(report.raw_json, dict) else [],
                )
            )
            refreshed.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "report_id": str(report.id),
                    "latest_candle": latest_candle,
                    "as_of": (report.data_quality.last_candle_at or report.created_at).isoformat(),
                }
            )
        except HTTPException as exc:
            errors.append({"symbol": symbol, "timeframe": timeframe, "error": str(exc.detail)})
        except Exception as exc:
            errors.append({"symbol": symbol, "timeframe": timeframe, "error": f"{type(exc).__name__}: {exc}"})
    symbols = sorted({symbol for symbol, _timeframe in pairs})
    return {
        "symbols": symbols,
        "pairs": [{"symbol": symbol, "timeframe": timeframe} for symbol, timeframe in pairs],
        "refreshed": refreshed,
        "errors": errors,
        "count": len(refreshed),
    }


def score_candidates() -> dict[str, Any]:
    """Run the low-priority candidate replay over every held/tracked pair."""

    def load(symbol: str, timeframe: str) -> list[Any]:
        return runtime.market_provider.get_snapshot(symbol, timeframe).candles

    result = _score_candidates(
        runtime.repository,
        runtime.settings,
        targets=tracked_market_pairs(),
        candle_loader=load,
    )
    live_scoring = score_live_candidate_judgments(
        runtime.repository,
        runtime.market_provider,
        runtime.settings,
    )
    whale_scoring = _score_legacy_candidates(
        runtime.repository,
        runtime.settings,
        engines={"whale"},
    )
    result["live_scoring"] = live_scoring
    result["whale_scoring"] = whale_scoring
    try:
        result["calibration_cache"] = runtime.refresh_calibration_report_cache()
    except Exception as exc:
        result["calibration_cache_error"] = f"{type(exc).__name__}: {exc}"
    return result


def refresh_stance_backtests() -> dict[str, Any]:
    """Collect and score the fixed real-history validation cohort."""

    return _refresh_stance_backtests(
        runtime.repository,
        runtime.market_provider,
        runtime.settings,
        symbols=runtime.settings.stance_backtest_symbol_list,
        history_bars=runtime.settings.stance_backtest_history_bars,
        horizon_bars=runtime.settings.stance_backtest_horizon_bars,
    )


def stance_backtest_dashboard() -> dict[str, Any]:
    return _stance_backtest_dashboard(
        runtime.repository,
        symbols=runtime.settings.stance_backtest_symbol_list,
    )


def tracked_market_pairs() -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = {(symbol.upper(), timeframe or "4h") for symbol, timeframe in _paper_universe(runtime.repository)}
    for setup in runtime.repository.list_armed_setups(status="armed", limit=1000):
        pairs.add((setup.symbol.upper(), setup.timeframe or "4h"))
    if not pairs:
        pairs.update((symbol.upper(), "4h") for symbol in runtime.settings.symbol_list)
    return sorted(pairs)


def tracked_symbols() -> list[str]:
    return sorted({symbol for symbol, _timeframe in tracked_market_pairs()})


def _market_pair_stale_sort(pair: tuple[str, str]) -> tuple[int, datetime]:
    symbol, timeframe = pair
    report = runtime.repository.latest_report(symbol)
    as_of = _report_analysis_as_of(report)
    if as_of is None:
        return (0, datetime.min.replace(tzinfo=timezone.utc))
    age_seconds = (utc_now() - as_of).total_seconds()
    stale = age_seconds > timeframe_seconds(timeframe) * 2
    return (0 if stale else 1, as_of)


def _report_analysis_as_of(report: Any | None) -> datetime | None:
    if report is None:
        return None
    value = getattr(getattr(report, "data_quality", None), "last_candle_at", None) or getattr(report, "created_at", None)
    return value if isinstance(value, datetime) else None


def refresh_derivative_data() -> dict[str, Any]:
    if not runtime.settings.derivative_tracking_enabled:
        return {"enabled": False, "symbols": [], "snapshots": [], "errors": []}
    symbols = tracked_symbols()
    bitget_provider = FakeDerivativesProvider() if runtime.settings.demo_mode else BitgetDerivProvider(runtime.market_provider, runtime.settings)
    coinglass_provider = CoinglassProvider(runtime.settings)
    coinglass_budget = _coinglass_budget(runtime.settings, len(symbols))
    coinglass_symbols = _coinglass_symbols_for_tick(symbols, coinglass_budget["max_symbols_per_tick"]) if coinglass_provider.configured else symbols
    snapshots: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    liquidation_events: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for symbol in symbols:
        try:
            bitget_collection = bitget_provider.collect(symbol)
            bitget_metric = _with_oi_change(bitget_collection.metrics[0])
            reference_price: float | None = None
            if not runtime.settings.demo_mode and hasattr(runtime.market_provider, "get_spot_trade_flow"):
                market_snapshot = runtime.market_provider.get_snapshot(symbol, "4h")
                confirmed_candles, confirmed_at, confirmed_change = _confirmed_money_flow_context(
                    market_snapshot.candles,
                    "4h",
                )
                futures_flow = runtime.market_provider.get_trade_flow(symbol, "4h", confirmed_candles)
                spot_flow = runtime.market_provider.get_spot_trade_flow(symbol, "4h", confirmed_candles)
                observation = flow_observation(
                    price_change_pct=confirmed_change,
                    spot_flow=spot_flow,
                    futures_flow=futures_flow,
                    oi_change_pct=bitget_metric.oi_change_pct,
                    as_of=confirmed_at,
                    confirmed=bool(confirmed_candles),
                )
                reference_price = float(confirmed_candles[-1].close) if confirmed_candles else None
                bitget_metric = bitget_metric.model_copy(update={"raw_json": {**bitget_metric.raw_json, "money_flow_observation": observation}})
            runtime.repository.add_derivative_metric(bitget_metric)
            _record_money_flow_candidate(bitget_metric, reference_price=reference_price)
            for event in bitget_collection.liquidation_events:
                runtime.repository.add_liquidation_event(event)
                liquidation_events.append(event.model_dump(mode="json"))
            if bitget_collection.snapshot is not None:
                bitget_snapshot = bitget_collection.snapshot.model_copy(update={"open_interest_change_pct": bitget_metric.oi_change_pct})
                runtime.repository.add_derivative_snapshot(bitget_snapshot)
                snapshots.append(bitget_snapshot.model_dump(mode="json"))
            metrics.append(bitget_metric.model_dump(mode="json"))
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
        try:
            if runtime.settings.demo_mode:
                continue
            if symbol not in coinglass_symbols:
                skipped.append(
                    {
                        "symbol": symbol,
                        "provider": "coinglass",
                        "reason": "rate_budget_round_robin",
                    }
                )
                continue
            coinglass_collection = coinglass_provider.collect(symbol)
            for metric in coinglass_collection.metrics:
                runtime.repository.add_derivative_metric(metric)
                metrics.append(metric.model_dump(mode="json"))
            for event in coinglass_collection.liquidation_events:
                runtime.repository.add_liquidation_event(event)
                liquidation_events.append(event.model_dump(mode="json"))
            if coinglass_collection.snapshot is not None:
                runtime.repository.add_derivative_snapshot(coinglass_collection.snapshot)
                snapshots.append(coinglass_collection.snapshot.model_dump(mode="json"))
        except Exception as exc:
            errors.append(
                {
                    "symbol": symbol,
                    "provider": "coinglass",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            coinglass_snapshot = coinglass_status_snapshot(symbol, runtime.settings).model_copy(
                update={
                    "source_status": "error",
                    "notes": [f"Coinglass collection failed: {type(exc).__name__}: {exc}"],
                }
            )
            runtime.repository.add_derivative_snapshot(coinglass_snapshot)
    return {
        "enabled": True,
        "symbols": symbols,
        "snapshots": snapshots,
        "metrics": metrics,
        "liquidation_events": liquidation_events,
        "skipped": skipped,
        "errors": errors,
        "rate_budget": coinglass_budget,
        "count": len(metrics),
    }


def _confirmed_money_flow_context(candles: list[Any], timeframe: str) -> tuple[list[Any], Any, float | None]:
    now = utc_now()
    duration = timedelta(seconds=timeframe_seconds(timeframe))
    confirmed = sorted(
        (candle for candle in candles if candle.timestamp + duration <= now),
        key=lambda candle: candle.timestamp,
    )
    if not confirmed:
        return [], now, None
    current = confirmed[-1]
    window = confirmed[-24:]
    reference = window[0]
    change = ((current.close / reference.close) - 1) * 100 if reference.close else None
    return confirmed, current.timestamp + duration, change


def latest_flow(symbol: str) -> dict[str, Any]:
    normalized = symbol.upper()
    context = derivative_context_for_symbol(runtime.repository, runtime.settings, normalized)
    return {
        **context,
        "history": [item.model_dump(mode="json") for item in runtime.repository.list_derivative_snapshots(symbol=normalized, provider="bitget", limit=50)],
        "rate_budget": _coinglass_budget(runtime.settings, len(tracked_symbols())),
    }


def liquidation_heatmap(symbol: str, window_hours: int = 72) -> dict[str, Any]:
    normalized = symbol.upper()
    report = runtime.repository.latest_report(normalized)
    current_price = float(report.price) if report is not None else None
    events = runtime.repository.list_liquidation_events(
        symbol=normalized,
        source="bitget",
        limit=2000,
    )
    return build_realized_liquidation_heatmap(
        events,
        normalized,
        current_price=current_price,
        window_hours=window_hours,
    )


def unified_liquidation_heatmap(
    symbol: str,
    *,
    timeframe: str = "4h",
    range_key: str = "3D",
    side: str = "all",
    size_filter: str = "all",
    min_size: float | None = None,
    mode: str = "persist",
    price_bins: int = 120,
    source: str = "realized",
    from_at: datetime | None = None,
    to_at: datetime | None = None,
) -> dict[str, Any]:
    normalized = symbol.upper()
    cache_key = (
        id(runtime.repository),
        id(runtime.market_provider),
        normalized,
        timeframe,
        range_key,
        side,
        size_filter,
        min_size,
        mode,
        price_bins,
        source,
        from_at.isoformat() if from_at else None,
        to_at.isoformat() if to_at else None,
    )
    monotonic_now = time.monotonic()
    with _unified_heatmap_cache_lock:
        cached = _unified_heatmap_cache.get(cache_key)
        if cached and monotonic_now - cached[0] < _UNIFIED_HEATMAP_CACHE_SECONDS:
            return cached[1]
    snapshot = runtime.market_provider.get_snapshot(normalized, timeframe)
    events = runtime.repository.list_liquidation_events(
        symbol=normalized,
        source="bitget" if source != "coinglass_est" else "coinglass",
        limit=10_000,
    )
    payload = build_unified_liquidation_heatmap(
        events,
        snapshot.candles,
        normalized,
        timeframe_seconds=timeframe_seconds(timeframe),
        range_key=range_key,
        side=side,
        size_filter=size_filter,
        min_size=min_size,
        mode=mode,
        price_bins=price_bins,
        source=source,
        from_at=from_at,
        to_at=to_at,
    )
    with _unified_heatmap_cache_lock:
        if len(_unified_heatmap_cache) >= 128:
            _unified_heatmap_cache.clear()
        _unified_heatmap_cache[cache_key] = (monotonic_now, payload)
    return payload


def refresh_liquidation_heatmap(symbol: str, window_hours: int = 72) -> dict[str, Any]:
    normalized = symbol.upper()
    if runtime.settings.demo_mode:
        events = FakeDerivativesProvider().collect(normalized).liquidation_events
    elif not runtime.settings.bitget_liquidation_history_enabled:
        payload = liquidation_heatmap(normalized, window_hours)
        payload.update(
            {
                "source_status": "locked",
                "notes": ["Bitget 공개 청산 이력 수집이 설정에서 비활성화되어 있습니다."],
            }
        )
        return payload
    else:
        events = collect_bitget_liquidations(
            runtime.market_provider,
            normalized,
            max_pages=runtime.settings.bitget_liquidation_history_pages,
        )
        if not events:
            payload = liquidation_heatmap(normalized, window_hours)
            if runtime.settings.market_data_provider.lower() != "bitget":
                payload.update(
                    {
                        "source_status": "locked",
                        "notes": ["Bitget market data provider가 활성화되어야 공개 청산 이력을 수집합니다."],
                    }
                )
            return payload
    for event in events:
        runtime.repository.add_liquidation_event(event)
    payload = liquidation_heatmap(normalized, window_hours)
    payload["refresh"] = {"stored": len(events), "pages": runtime.settings.bitget_liquidation_history_pages}
    return payload


def _record_money_flow_candidate(metric: DerivativeMetric, *, reference_price: float | None = None) -> None:
    history = runtime.repository.list_derivative_metrics(symbol=metric.symbol, limit=500)
    flow = build_derivative_signals(history).get("money_flow")
    if not isinstance(flow, dict) or flow.get("state") != "futures_led" or flow.get("provisional"):
        return
    try:
        as_of = datetime.fromisoformat(str(flow.get("as_of")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        as_of = metric.as_of
    runtime.repository.add_judgment(
        JudgmentLedgerEntry(
            judgment_id=f"candidate:{metric.symbol}:4h:futures_led:{as_of.isoformat()}",
            position_id=UUID(int=0),
            source_type="candidate_signature",
            source_id=f"futures_led:{metric.symbol}:{as_of.isoformat()}",
            as_of=as_of,
            type="candidate_signature",
            claim={
                "symbol": metric.symbol,
                "timeframe": "4h",
                "engine": "money_flow",
                "event_type": "futures_led_rally",
                "direction": "short",
                "condition": "observe_pullback_after_futures_led_rally",
                "expected_move": "down",
                "price": reference_price,
                "lifecycle_state": "candidate",
                "components": flow,
            },
        )
    )


def _with_oi_change(metric: DerivativeMetric) -> DerivativeMetric:
    if metric.open_interest is None or metric.open_interest <= 0:
        return metric
    history = runtime.repository.list_derivative_metrics(symbol=metric.symbol, source=metric.source, limit=500)
    if not history:
        return metric
    reference = _oi_reference(metric, history)
    if reference is None or reference.open_interest is None or reference.open_interest <= 0:
        return metric
    change = ((metric.open_interest - reference.open_interest) / reference.open_interest) * 100
    coverage = {
        **metric.coverage,
        "oi_change_reference_as_of": reference.as_of.isoformat(),
        "oi_change_window": "24h" if (metric.as_of - reference.as_of).total_seconds() >= 20 * 3600 else "latest_available",
    }
    return metric.model_copy(update={"oi_change_pct": round(change, 4), "coverage": coverage})


def _oi_reference(metric: DerivativeMetric, history: list[DerivativeMetric]) -> DerivativeMetric | None:
    target_seconds = 24 * 3600
    older = [item for item in history if item.as_of < metric.as_of and item.open_interest is not None]
    if not older:
        return None
    suitable = [item for item in older if (metric.as_of - item.as_of).total_seconds() >= 20 * 3600]
    if suitable:
        return min(
            suitable,
            key=lambda item: abs((metric.as_of - item.as_of).total_seconds() - target_seconds),
        )
    return older[-1]


def _coinglass_budget(settings, symbol_count: int) -> dict[str, Any]:
    interval_seconds = max(60, int(settings.derivative_tracking_interval_seconds))
    requests_per_tick = max(0, int(settings.coinglass_rate_limit_per_minute * (interval_seconds / 60)))
    requests_per_symbol = max(1, int(settings.coinglass_requests_per_symbol))
    max_symbols = requests_per_tick // requests_per_symbol if settings.coinglass_api_key.strip() else 0
    return {
        "provider": "coinglass",
        "configured": bool(settings.coinglass_api_key.strip()),
        "rate_limit_per_minute": settings.coinglass_rate_limit_per_minute,
        "job_interval_seconds": interval_seconds,
        "requests_per_tick": requests_per_tick,
        "requests_per_symbol": requests_per_symbol,
        "tracked_symbols": symbol_count,
        "max_symbols_per_tick": max_symbols,
        "round_robin_required": bool(settings.coinglass_api_key.strip()) and symbol_count > max_symbols,
    }


def _coinglass_symbols_for_tick(symbols: list[str], max_symbols: int) -> set[str]:
    global _coinglass_round_robin_cursor
    if max_symbols <= 0:
        return set()
    if len(symbols) <= max_symbols:
        return set(symbols)
    selected = []
    for offset in range(max_symbols):
        selected.append(symbols[(_coinglass_round_robin_cursor + offset) % len(symbols)])
    _coinglass_round_robin_cursor = (_coinglass_round_robin_cursor + max_symbols) % len(symbols)
    return set(selected)


def database_backup() -> dict[str, Any]:
    return run_database_backup(runtime.settings, runtime.repository)


def database_retention() -> dict[str, Any]:
    return enforce_retention(runtime.settings, runtime.repository)


def database_maintenance() -> dict[str, Any]:
    return run_database_maintenance(runtime.settings, runtime.repository)


def regenerate_stale_insights() -> dict[str, Any]:
    """Regenerate stale position insights using the route layer's stale policy."""
    refreshed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    for position in runtime.repository.list_positions(PositionStatus.open):
        try:
            payload = runtime._live_position_payload(position, store_snapshot=True)
            snapshot = PositionSnapshot.model_validate(payload["latest_snapshot"])
            latest = runtime.repository.list_position_insights(position.id, limit=1)
            if not latest:
                skipped.append({"symbol": position.symbol, "reason": "no_insight"})
                continue
            status = runtime._insight_status(latest[0], snapshot)
            refreshed_insight = runtime._maybe_auto_regenerate_insight(position, snapshot, status)
            if refreshed_insight is None:
                skipped.append({"symbol": position.symbol, "reason": "fresh_or_rate_limited"})
                continue
            refreshed.append({"symbol": position.symbol, "insight_id": str(refreshed_insight.id)})
        except Exception as exc:
            errors.append({"symbol": position.symbol, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "refreshed": refreshed,
        "skipped": skipped,
        "errors": errors,
        "count": len(refreshed),
    }


def alert_delivery_stats_24h() -> dict[str, Any]:
    """WO-44 Part C: 최근 24h 발화/발송/실패 — 알림 침묵과 시스템 고장의 구분 근거."""
    from datetime import timedelta

    cutoff = utc_now() - timedelta(hours=24)
    alerts = runtime.repository.list_alerts(limit=2000)
    recent = [alert for alert in alerts if _aware(alert.fired_at) >= cutoff]
    delivered = len([alert for alert in recent if alert.delivered])
    return {
        "window_hours": 24,
        "fired": len(recent),
        "delivered": delivered,
        "failed": len(recent) - delivered,
    }


def _aware(value):
    from datetime import timezone as _tz

    return value if value.tzinfo else value.replace(tzinfo=_tz.utc)


def detect_closures() -> dict[str, Any]:
    """Expose closure detection as a service hook.

    The current sync path owns exchange disappearance detection. This hook records
    the latest state without duplicating that route logic; WO-17 can attach alert
    evaluation after the sync payload.
    """
    positions = runtime.repository.list_positions()
    return {
        "open_count": len([position for position in positions if position.status == PositionStatus.open]),
        "needs_exit_record_count": len(
            [
                position
                for position in positions
                if position.status
                in {
                    PositionStatus.missing_from_exchange,
                    PositionStatus.needs_exit_record,
                }
            ]
        ),
    }


def interim_score_open_positions() -> dict[str, Any]:
    scored = 0
    positions_scored = 0
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    as_of = utc_now()
    for position in runtime.repository.list_positions(PositionStatus.open):
        try:
            judgments = runtime.repository.list_judgments(position.id, limit=500)
            if not judgments:
                skipped.append({"symbol": position.symbol, "reason": "no_judgments"})
                continue
            snapshots = runtime.repository.list_position_snapshots(position.id, limit=500)
            monitoring_logs = runtime.repository.list_monitoring_logs(position.id, limit=500)
            scores = score_interim_judgments(position, judgments, snapshots, monitoring_logs, as_of=as_of)
            for score in scores:
                runtime.repository.add_judgment_score(score)
            if scores:
                positions_scored += 1
                scored += len(scores)
            else:
                skipped.append({"symbol": position.symbol, "reason": "no_price_path"})
        except Exception as exc:
            errors.append({"symbol": position.symbol, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "positions": positions_scored,
        "scores": scored,
        "skipped": skipped,
        "errors": errors,
        "as_of": as_of.isoformat(),
    }


def list_live_positions(*, store_snapshot: bool = False) -> dict[str, Any]:
    all_positions = runtime.repository.list_positions()
    open_positions = [position for position in all_positions if position.status == PositionStatus.open]
    payloads: list[dict[str, Any]] = []
    for position in open_positions:
        try:
            payloads.append(runtime._live_position_payload(position, store_snapshot=store_snapshot))
        except HTTPException:
            continue
    return {
        "provider": provider_name(),
        "positions": payloads,
        "open_count": len(open_positions),
        "needs_exit_record_count": len(
            [
                position
                for position in all_positions
                if position.status
                in {
                    PositionStatus.missing_from_exchange,
                    PositionStatus.needs_exit_record,
                }
            ]
        ),
        "timestamp": runtime.utc_now(),
    }


def list_open_position_refs() -> list[dict[str, Any]]:
    return [{"id": position.id, "symbol": position.symbol} for position in runtime.repository.list_positions(PositionStatus.open)]


def cached_live_position_detail(position_id: UUID) -> dict[str, Any]:
    position = runtime.repository.get_position(position_id)
    if position is None:
        raise LookupError("Position not found")
    return runtime._cached_live_position_payload(position)


def live_position_detail(position_id: UUID) -> dict[str, Any]:
    position = runtime.repository.get_position(position_id)
    if position is None:
        raise LookupError("Position not found")
    return runtime._live_position_detail(position)


def analyst_briefing(symbol: str, timeframe: str = "4h") -> dict[str, Any]:
    match = match_position_symbol(symbol)
    if match.position is not None:
        payload = live_position_detail(match.position.id)
        return {
            "symbol": match.position.symbol,
            "timeframe": timeframe,
            "source": "position",
            "position": payload.get("position"),
            "analyst_briefing": payload.get("analyst_briefing"),
        }
    if match.candidates:
        return {"candidates": [position.model_dump(mode="json") for position in match.candidates]}
    return {
        **scout_handlers.scout_briefing(symbol, timeframe=timeframe, force=False),
        "source": "scout",
    }


def live_position_alert_context(position_id: UUID) -> dict[str, Any]:
    position = runtime.repository.get_position(position_id)
    if position is None:
        raise LookupError("Position not found")
    payload = runtime._live_position_payload(position, store_snapshot=False)
    snapshot = runtime.PositionSnapshot.model_validate(payload["latest_snapshot"])
    try:
        chart_analysis = runtime._chart_analysis_for_position(position)
    except HTTPException:
        chart_analysis = {}
    action_plan = runtime.build_action_plan(position, snapshot, chart_analysis)
    return {
        **payload,
        "action_plan": action_plan,
        "chart_analysis": chart_analysis,
        "snapshots": runtime.repository.list_position_snapshots(position.id, limit=5),
        "events": runtime.repository.list_position_events(position.id, limit=20),
    }


def minimal_position_payload(position_id: UUID) -> dict[str, Any] | None:
    """원장 행만으로 만든 포지션 페이로드. **네트워크를 타지 않는다.**

    `live_position_alert_context` 가 실패했을 때 진입 알림을 살리는 용도다. 그 함수는
    거래소 스냅샷·차트 분석을 타므로 실패할 수 있고, 그 실패가 **진입 사실**까지 삼키면
    안 된다(1차 정보 우선).

    포지션 행조차 없으면 `None` 이다 — 없는 것을 지어내지 않는다.
    """
    position = runtime.repository.get_position(position_id)
    return position.model_dump(mode="json") if position is not None else None


def create_position_insight(position_id: UUID, *, auto_generated: bool = False) -> dict[str, Any]:
    position = runtime.repository.get_position(position_id)
    if position is None:
        raise LookupError("Position not found")
    payload = runtime._live_position_payload(position, store_snapshot=True)
    snapshot = runtime.PositionSnapshot.model_validate(payload["latest_snapshot"])
    insight = runtime._create_and_store_position_insight(position, snapshot, auto_generated=auto_generated)
    status = runtime._insight_status(insight, snapshot)
    return {
        **payload,
        "latest_insight": runtime._insight_payload(insight, status),
        "insight_status": status,
    }


def record_alert(alert: AlertRecord) -> AlertRecord:
    if alert.position_id is not None:
        position = runtime.repository.get_position(alert.position_id)
        if position is not None:
            alert = alert.model_copy(
                update={
                    "payload": {
                        **alert.payload,
                        "quantity_at_alert": position.quantity,
                        "planned_stop_at_alert": position.planned_stop_price,
                        "position_direction": position.direction.value,
                        "mark_price": position.mark_price or position.current_price,
                    }
                }
            )
    saved = runtime.repository.add_alert(alert)
    if saved.position_id is not None:
        params = engine_param_snapshot(runtime.repository)
        runtime.repository.add_judgment(
            JudgmentLedgerEntry(
                judgment_id=f"alert:{saved.rule_id}:{saved.position_id}:{saved.id}",
                position_id=saved.position_id,
                source_type="alert",
                source_id=str(saved.id),
                as_of=saved.fired_at,
                type="alert_fired",
                claim=saved.payload,
                confidence=None,
                param_version=params,
                created_at=utc_now(),
            )
        )
        if saved.rule_id in {"stance_flipped", "verdict_changed"}:
            semantic_type = "stance_flipped" if saved.rule_id == "stance_flipped" else "position_status_transition"
            runtime.repository.add_judgment(
                JudgmentLedgerEntry(
                    judgment_id=f"judgment:{saved.rule_id}:{saved.position_id}:{saved.id}",
                    position_id=saved.position_id,
                    source_type="lifecycle_alert",
                    source_id=str(saved.id),
                    as_of=saved.fired_at,
                    type=semantic_type,
                    claim={"rule_id": saved.rule_id, **saved.payload},
                    confidence=None,
                    param_version=params,
                    created_at=utc_now(),
                )
            )
    return saved


def score_alert_responses() -> dict[str, Any]:
    created = 0
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    as_of = utc_now()
    trades = runtime.repository.list_trades()
    for alert in runtime.repository.list_alerts(limit=500):
        if alert.position_id is None:
            skipped.append({"alert_id": str(alert.id), "reason": "system_alert"})
            continue
        if runtime.repository.get_alert_response(alert.id) is not None:
            skipped.append({"alert_id": str(alert.id), "reason": "already_scored"})
            continue
        try:
            position = runtime.repository.get_position(alert.position_id)
            position_trades = [trade for trade in trades if trade.position_id == alert.position_id]
            response = detect_alert_response(
                alert,
                position,
                position_trades,
                as_of=as_of,
                window_hours=runtime.settings.alert_response_window_hours,
            )
            if response is None:
                skipped.append({"alert_id": str(alert.id), "reason": "response_window_open"})
                continue
            snapshots = runtime.repository.list_position_snapshots(alert.position_id, limit=500)
            logs = runtime.repository.list_monitoring_logs(alert.position_id, limit=500)
            scored = score_alert_response(
                response,
                alert,
                position,
                snapshots,
                logs,
                trades=position_trades,
                outcome_hours=runtime.settings.alert_response_outcome_hours,
            )
            runtime.repository.add_alert_response(scored)
            created += 1
        except Exception as exc:
            errors.append({"alert_id": str(alert.id), "error": f"{type(exc).__name__}: {exc}"})
    return {
        "responses": created,
        "skipped": skipped,
        "errors": errors,
        "as_of": as_of.isoformat(),
    }


def alert_response_history_line(rule_id: str) -> str | None:
    return alert_history_line(runtime.repository.list_alert_responses(rule_id=rule_id, limit=50), rule_id)


def match_position_symbol(query: str) -> SymbolMatch:
    needle = query.strip().upper()
    if not needle:
        return SymbolMatch(None, [])
    open_positions = [position for position in runtime.repository.list_positions() if position.status == PositionStatus.open]
    exact = [position for position in open_positions if position.symbol.upper() == needle or position.symbol.upper().replace("USDT", "") == needle]
    if len(exact) == 1:
        return SymbolMatch(exact[0], exact)
    partial = [position for position in open_positions if needle in position.symbol.upper()]
    if len(partial) == 1:
        return SymbolMatch(partial[0], partial)
    return SymbolMatch(None, exact or partial)


def scout_scan(limit: int = 5) -> dict[str, Any]:
    payload = scout_handlers.scan_watchlist(ScanRequest(force=False))
    rows = payload.get("rows", [])
    setups = runtime.repository.list_armed_setups(limit=200)
    return {
        **payload,
        "rows": rows[:limit],
        "armed_setups": [setup.model_dump(mode="json") for setup in setups],
        "entry_intents": [intent.model_dump(mode="json") for intent in runtime.repository.list_entry_intents(limit=200)],
    }


def scout_tracking_status() -> dict[str, Any]:
    """Return persistent scout tracking symbols without running a market scan."""
    cleanup = clear_scout_tracking_for_open_positions()
    open_symbols = {position.symbol.upper() for position in runtime.repository.list_positions(PositionStatus.open)}
    items = [item.model_dump(mode="json") for item in runtime.repository.list_watchlist() if item.symbol.upper() not in open_symbols]
    return {
        "items": items,
        "count": len(items),
        "scout_tracking_removed": cleanup["removed"],
    }


def scout_quick_answer(symbol: str, timeframe: str = "4h") -> dict[str, Any]:
    """Single-symbol scout answer used by the web quick card and Telegram /q."""
    return scout_handlers.scout_analysis(symbol, timeframe=timeframe, force=False)


def start_scout_tracking(symbol: str, timeframe: str = "4h") -> dict[str, Any]:
    """Register a symbol for persistent scout tracking until the user stops it or a live position appears."""
    normalized = scout_handlers.normalize_scout_symbol(symbol)
    open_position = next(
        (position for position in runtime.repository.list_positions(PositionStatus.open) if position.symbol.upper() == normalized),
        None,
    )
    if open_position is not None:
        removed = runtime.repository.remove_watchlist_item(normalized)
        return {
            "symbol": normalized,
            "timeframe": timeframe,
            "tracking": {
                "active": False,
                "mode": "position",
                "removed_watchlist": removed,
                "message": "이미 열린 포지션입니다. 스카우트 추적은 포지션 관제로 전환됩니다.",
            },
            "position_payload": live_position_detail(open_position.id),
        }
    watchlist = scout_handlers.add_watchlist_item(
        scout_handlers.WatchlistRequest(
            symbol=normalized,
            note="telegram scout tracking",
            default_timeframe=timeframe,
        )
    )
    payload = scout_handlers.scout_analysis(normalized, timeframe=timeframe, force=True)
    return {
        **payload,
        "tracking": {
            "active": True,
            "mode": "scout",
            "watchlist_item": watchlist.get("item"),
            "message": "스카우트 추적을 시작했습니다. 포지션 진입 전까지 워커가 계속 관제합니다.",
        },
    }


def stop_scout_tracking(symbol: str) -> dict[str, Any]:
    normalized = scout_handlers.normalize_scout_symbol(symbol)
    removed = runtime.repository.remove_watchlist_item(normalized)
    return {
        "symbol": normalized,
        "removed": removed,
        "tracking": {
            "active": False,
            "mode": "stopped",
            "message": "스카우트 추적을 중지했습니다." if removed else "이미 스카우트 추적 대상이 아닙니다.",
        },
    }


def clear_scout_tracking_for_open_positions() -> dict[str, Any]:
    open_symbols = {position.symbol.upper() for position in runtime.repository.list_positions(PositionStatus.open)}
    removed: list[str] = []
    cancelled_intents: list[str] = []
    disarmed_setups: list[str] = []
    for item in list(runtime.repository.list_watchlist()):
        symbol = item.symbol.upper()
        if symbol in open_symbols and runtime.repository.remove_watchlist_item(symbol):
            removed.append(symbol)
    now = utc_now()
    for intent in runtime.repository.list_entry_intents(status="active", limit=1000):
        if intent.symbol.upper() not in open_symbols:
            continue
        runtime.repository.upsert_entry_intent(intent.model_copy(update={"status": "cancelled", "updated_at": now}))
        cancelled_intents.append(str(intent.id))
    for setup in runtime.repository.list_armed_setups(status="armed", limit=1000):
        if setup.symbol.upper() not in open_symbols:
            continue
        runtime.repository.upsert_armed_setup(setup.model_copy(update={"status": "disarmed", "updated_at": now}))
        disarmed_setups.append(str(setup.id))
    return {
        "removed": removed,
        "count": len(removed) + len(cancelled_intents) + len(disarmed_setups),
        "cancelled_intents": cancelled_intents,
        "disarmed_setups": disarmed_setups,
    }


def entry_intents(symbol: str | None = None, status: str | None = None) -> dict[str, Any]:
    return scout_handlers.list_entry_intents(symbol=symbol, status=status)


def create_entry_intent(symbol: str, direction: str, zone: str, timeframe: str = "4h") -> dict[str, Any]:
    lower, upper = _parse_zone(zone)
    return scout_handlers.create_entry_intent(
        symbol,
        scout_handlers.EntryIntentRequest(
            direction=direction,
            zone_lower=lower,
            zone_upper=upper,
            timeframe=timeframe,
        ),
    )


def refresh_scout_scan_cache() -> dict[str, Any]:
    payload = scout_handlers.scan_watchlist(ScanRequest(force=True))
    payload = _attach_scout_previews(payload)
    return process_scout_scan(runtime.repository, runtime.settings, payload)


def refresh_universe_scan_cache() -> dict[str, Any]:
    def load(symbol: str, timeframe: str) -> dict[str, Any]:
        entry = scout_handlers._analysis_entry(symbol, timeframe, force=True, include_trade_flow=False)
        briefing = scout_handlers._briefing_for_entry(symbol, timeframe, entry, action_plan=None, context="pre_entry")
        confluence = briefing.get("confluence") if isinstance(briefing.get("confluence"), dict) else {}
        alignment = build_full_alignment(confluence, entry.get("historical_backtest"))
        entry["analysis"]["full_alignment"] = alignment
        scout_handlers._record_full_alignment_judgment(symbol, timeframe, entry, alignment)
        return entry

    return run_universe_scan(runtime.repository, runtime.settings, analysis_loader=load, ticker_rows=_market_tickers())


def _market_tickers() -> list[dict[str, Any]]:
    lister = getattr(runtime.market_provider, "list_tickers", None)
    if not callable(lister):
        return []
    try:
        rows = lister()
    except Exception:
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def simulate_entry(symbol: str, direction: str, leverage: float, entry_price: float | None = None) -> dict[str, Any]:
    return scout_handlers._simulate(
        SimulateRequest(
            symbol=symbol,
            direction=direction,
            leverage=leverage,
            entry_price=entry_price,
        )
    )


def recent_reviews(limit: int = 3) -> list[Any]:
    return runtime.repository.list_trades()[:limit]


def calibration_snapshot() -> dict[str, Any]:
    return runtime.review_calibration()


def weekly_calibration_report() -> dict[str, Any]:
    return runtime.review_weekly_calibration()


def refresh_calibration_report_cache() -> dict[str, Any]:
    return runtime.refresh_calibration_report_cache()


def refresh_symbol_catalog() -> dict[str, Any]:
    return scout_handlers.refresh_symbol_catalog(force=True)


def improvement_digest(
    scores: list[Any] | None = None,
    suggestions: list[Any] | None = None,
) -> dict[str, Any]:
    """WO-45: 주간 개선 다이제스트 + 12주 스파크라인 (결정론, 읽기 전용)."""
    from app.analyst.signature_registry import state_map
    from app.review.improvement import weekly_improvement_digest

    scores = scores if scores is not None else runtime.repository.list_judgment_scores(limit=5000)
    suggestions = suggestions if suggestions is not None else runtime.repository.list_calibration_suggestions(limit=100)
    digest = weekly_improvement_digest(
        scores,
        suggestions,
        runtime.repository.list_engine_params(limit=200),
        runtime.repository.list_autonomy_logs(limit=1000),
        state_map(runtime.repository),
    )
    from app.review.coverage import judgment_coverage

    coverage = judgment_coverage(runtime.repository)
    digest["judgment_coverage"] = coverage
    digest["judgment_coverage_line"] = (
        f"판단 원장 커버리지 {coverage['coverage_pct']}% · 기록 {coverage['recorded']}/"
        f"{coverage['total'] - coverage['unscorable']} · 채점 불가 {coverage['unscorable']}"
    )
    stance = stance_backtest_dashboard()
    fomo = performance_summary().get("fomo_attribution", {})
    digest["moat_h1"] = {
        "batch": "WO-FCE-88~91",
        "real_history": {
            "status": stance.get("status", "pending"),
            "available": int(stance.get("available") or 0),
            "expected": int(stance.get("expected") or 0),
        },
        "ledger": {
            "coverage_pct": coverage["coverage_pct"],
            "recorded": coverage["recorded"],
            "eligible": coverage["total"] - coverage["unscorable"],
        },
        "fomo": {
            "sample_sufficient": bool(fomo.get("sample_sufficient")),
            "eligible_trades": int(fomo.get("eligible_trades") or 0),
            "sample_floor": int(fomo.get("sample_floor") or 0),
            "statement": str(fomo.get("statement") or "표본 없음"),
        },
        "routes": {
            "status": "consolidated",
            "canonical_page_routes": 9,
            "removed_legacy_pages": 15,
        },
        "honesty": "구현 완료와 성과 달성을 구분하며 현재 실데이터·표본만 발행",
    }
    return digest


def veto_calibration_suggestion(suggestion_id: str) -> dict[str, Any]:
    suggestion = veto_suggestion(runtime.repository, UUID(str(suggestion_id)))
    return suggestion.model_dump(mode="json")


def calibration_experiments() -> dict[str, Any]:
    payload = calibration_snapshot()
    return {
        "autonomy": payload.get("autonomy", {}),
        "suggestions": [item for item in payload.get("suggestions", []) if item.get("status") in {"scheduled", "experiment"}],
    }


def performance_summary() -> dict[str, Any]:
    return runtime.performance_summary()


def paper_performance() -> dict[str, Any]:
    """4트랙(크립토·주식 KR·주식 US·폴리) 페이퍼 성과 (WO-FCE-PERFORMANCE-REPORT-01).

    원장 단일 경로: 별도 집계 테이블 없이 각 트랙의 기존 원장/대시보드를 읽는다(작업 3).
    """
    from app.poly_paper.store import PolyPaperStore
    from app.review.paper_performance import paper_performance_report
    from app.stock_paper.store import StockPaperStore

    stock_store = StockPaperStore(runtime.settings.database_url)
    poly_store = PolyPaperStore(runtime.settings.database_url)
    return paper_performance_report(
        crypto_trades=runtime.repository.list_paper_trades(limit=1000),
        stock_dashboard=stock_store.dashboard() if stock_store.enabled else {},
        poly_dashboard=poly_store.dashboard() if poly_store.enabled else {},
    )


def sample_verdicts() -> dict[str, dict[str, Any]]:
    """트랙별 완주 가능성 판정 (WO-FCE-VALIDATION-VERDICT-01 Phase 1).

    발행(주간 리포트)과 전이 감지(알림)가 **같은 값**을 봐야 한다. 두 경로가 각자 계산하면
    "리포트는 SLOW 인데 알림은 VIABLE" 같은 어긋남이 생긴다.
    """
    from app.db.maintenance import sqlite_path
    from app.db.sqlite_utils import connect_sqlite
    from app.validation import verdict_watch

    path = sqlite_path(runtime.settings.database_url)
    if path is None or not path.exists():
        return {}
    try:
        with connect_sqlite(str(path)) as connection:
            return verdict_watch.current_verdicts(connection)
    except Exception as exc:  # 판정 실패가 리포트·알림 경로를 죽이지 않게 한다
        logger.warning("sample verdict computation failed: %s", exc)
        return {}


def replay_history_backfill() -> dict[str, Any]:
    """라이브 유니버스 캔들 히스토리 수집 (WO-FCE-REPLAY-DEPTH-01 4-2).

    기본값 꺼짐이며 켜도 **저장만** 한다 — 분석 페이로드·게이트는 건드리지 않는다.
    """
    settings = runtime.settings
    if not bool(getattr(settings, "replay_history_backfill_enabled", False)):
        return {"enabled": False, "effective_run": False}
    loader = getattr(runtime.market_provider, "get_history_ohlcv", None)
    if not callable(loader):
        return {"enabled": True, "effective_run": False, "reason": "provider_lacks_history_loader"}
    pairs = paper_universe(runtime.repository)
    result = history_backfill.backfill_universe(
        runtime.repository,
        pairs=pairs,
        history_loader=loader,
        history_bars=int(getattr(settings, "replay_history_retention_bars", 2_196)),
        retention_bars=int(getattr(settings, "replay_history_retention_bars", 2_196)),
        max_symbols=int(getattr(settings, "replay_history_backfill_max_symbols", 25)),
    )
    return {"enabled": True, **result}


def replay_history_coverage() -> dict[str, Any]:
    """저장 실태와 라이브 유니버스 교집합 — 재판정 가능 범위."""
    return history_backfill.coverage_report(runtime.repository, pairs=paper_universe(runtime.repository))


def run_paper_engine() -> dict[str, Any]:
    def load(symbol: str, timeframe: str) -> dict[str, Any]:
        return scout_handlers.scout_analysis(symbol, timeframe=timeframe, force=False, detail=True)

    def simulate(symbol: str, timeframe: str, direction: str, entry_price: float) -> dict[str, Any]:
        return scout_handlers._simulate(
            SimulateRequest(
                symbol=symbol,
                direction=direction,
                entry_price=entry_price,
                leverage=runtime.settings.paper_leverage,
                margin_usdt=runtime.settings.paper_margin_usdt,
                timeframe=timeframe,
            )
        )

    def load_depth(symbol: str) -> dict[str, Any] | None:
        """호가 깊이 조회 (Phase 4-1). 관측 전용이며 판정 경로에 영향이 없다.

        데모 모드나 Bitget 이 아닌 제공자에서는 `None` 을 돌려 관측을 남기지 않는다 —
        모의 호가로 슬리피지를 재면 그 표본이 가정보다 못하다.
        """
        if runtime.settings.demo_mode or runtime.settings.market_data_provider.lower() != "bitget":
            return None
        getter = getattr(runtime.market_provider, "get_order_book", None)
        if getter is None:
            return None
        return getter(symbol)

    return _run_paper_engine(
        runtime.repository,
        runtime.settings,
        analysis_loader=load,
        simulation_loader=simulate,
        depth_loader=load_depth,
    )


def whale_follow_eligibility() -> dict[str, Any]:
    """추종 자격 판정. 규칙 하나, 조건 셋 — N>=30 · 승률 점추정>=55% · MM 추정 아님.

    계산은 `onchain.follow_report` 가 한다. **이 저장소에서 자격 목록의 출처는 그것 하나다**
    (WO-FCE-REPORT-DEFECTS-01 7-2) — 리포트 계층이 따로 세다가 자격 밖 지갑을 `대상` 으로
    표시한 것이 D2 였다.
    """
    from app.onchain import follow_report

    return {**follow_report.follow_eligibility_report(runtime.repository), "as_of": utc_now().isoformat()}


# 자격 판정은 지갑 전수 조회다. 체결은 30초마다 오지만 자격은 시간 단위로 변하므로
# 이벤트 구동 경로가 매번 전수 조회를 돌리지 않게 캐시한다(C9).
_follow_eligibility_cache: dict[str, Any] = {"at": 0.0, "payload": None}
_follow_eligibility_lock = threading.Lock()


def cached_whale_follow_eligibility(*, max_age_seconds: int | None = None) -> dict[str, Any]:
    """캐시된 자격 판정. 만료 전이면 재계산하지 않는다.

    수명 0 이면 캐시를 쓰지 않는다 — 원복 경로다.
    """
    ttl = int(runtime.settings.whale_follow_eligibility_cache_seconds if max_age_seconds is None else max_age_seconds)
    with _follow_eligibility_lock:
        cached = _follow_eligibility_cache.get("payload")
        if ttl > 0 and cached is not None and time.monotonic() - float(_follow_eligibility_cache["at"]) < ttl:
            return cached
        payload = whale_follow_eligibility()
        _follow_eligibility_cache["at"] = time.monotonic()
        _follow_eligibility_cache["payload"] = payload
        return payload


def whale_follow_has_fresh_signal(events: list[dict[str, Any]] | None) -> bool:
    """방금 수집된 체결 중 **추종 자격 지갑의 진입 체결**이 있는가 (7-2 항목 1).

    고래 수집 잡(30초)이 이것을 물어보고, 참이면 추종 엔진을 **즉시** 돌린다. 15분 주기
    잡만 두면 그 주기가 곧 지연의 바닥이 되고, 그러면 "체결 근처 진입"이 성립하지 않는다.

    자격 조회는 캐시를 쓴다 — 30초마다 지갑 전수 조회를 돌리면 그 자체가 예산 사고다(C9).
    """
    from app.paper import whale_follow

    if not events:
        return False
    if not bool(getattr(runtime.settings, "whale_follow_track_enabled", False)):
        return False
    entry_events = [item for item in events if str(item.get("event") or "").lower() in whale_follow.ENTRY_EVENTS]
    if not entry_events:
        return False
    eligible = {str(address).lower() for address in cached_whale_follow_eligibility().get("eligible_addresses") or []}
    if not eligible:
        return False
    return any(str(item.get("wallet_address") or "").lower() in eligible for item in entry_events)


def run_whale_follow_engine(trigger: str = "scheduled") -> dict[str, Any]:
    """고래 추종 트랙 1회 실행. 진입과 출구를 같은 잡에서 돌린다.

    한쪽만 도는 상태가 생기면 진입만 쌓이고 표본이 0 이 된다.

    `trigger` 는 이 실행이 **체결 감지로 즉시** 돈 것인지 주기 잡으로 돈 것인지다. 지연
    분포를 해석할 때 그 구분이 필요하다 — 두 경로의 지연은 구조적으로 다르다.
    """
    from app.notify import whale_follow_alerts
    from app.onchain import follow_eligibility
    from app.paper import whale_follow

    def load(symbol: str, timeframe: str) -> dict[str, Any]:
        # `force=True` — 진입가가 **현재가**이므로 캐시된(최대 5분 묵은) 분석을 쓰면
        # "현재가"가 현재가가 아니게 된다. 조회 상한(3건/실행)이 비용을 묶는다(C9).
        return scout_handlers.scout_analysis(symbol, timeframe=timeframe, force=True, detail=True)

    def load_cached(symbol: str, timeframe: str) -> dict[str, Any]:
        # 출구는 확정봉으로 판정한다 — 갱신을 강제할 이유가 없다.
        return scout_handlers.scout_analysis(symbol, timeframe=timeframe, force=False, detail=True)

    def simulate(symbol: str, timeframe: str, direction: str, entry_price: float) -> dict[str, Any]:
        return scout_handlers._simulate(
            SimulateRequest(
                symbol=symbol,
                direction=direction,
                entry_price=entry_price,
                leverage=runtime.settings.paper_leverage,
                margin_usdt=runtime.settings.paper_margin_usdt,
                timeframe=timeframe,
            )
        )

    if not bool(getattr(runtime.settings, "whale_follow_track_enabled", False)):
        return {"enabled": False, "opened": 0, "reason": "추종 트랙이 꺼져 있다 (FCE_WHALE_FOLLOW_TRACK_ENABLED)"}

    eligibility = cached_whale_follow_eligibility()
    statuses = eligibility.get("statuses") or {}
    eligible = {address: follow_eligibility.QUALIFICATION_FOLLOW for address in eligibility.get("eligible_addresses") or []}
    context = {
        address: {
            "win_pct": payload.get("win_pct"),
            "participant_type": payload.get("participant_type"),
            "participant_confidence": payload.get("participant_confidence"),
            "unclassified_flag": payload.get("unclassified_flag"),
            "sample_size": payload.get("sample_size"),
            "ci_low": payload.get("ci_low"),
        }
        for address, payload in statuses.items()
    }

    entries = whale_follow.run_entries(
        runtime.repository,
        runtime.settings,
        eligible=eligible,
        analysis_loader=load,
        simulation_loader=simulate,
        signal_context=context,
        max_entries=int(runtime.settings.whale_follow_max_entries_per_run),
        max_latency_minutes=int(runtime.settings.whale_follow_max_latency_minutes),
        max_drift_pct_of_stop=float(runtime.settings.whale_follow_max_drift_pct_of_stop),
    )
    exits = whale_follow.run_exits(runtime.repository, runtime.settings, analysis_loader=load_cached)

    # 알림 후보. 발송은 **기존 통합 관문**을 지난다 — 여기서 보내지 않는다.
    # 워커가 `evaluate_scout_setups` 로 넘기고, 그 안에서 `delivery_gate.evaluate_rule` 이
    # 판정한다. 새 발송 경로를 만들지 않는 것이 우회 0건의 형태다(C7).
    now = utc_now()
    changed_ids = {str(item["id"]) for item in entries.get("entries") or []} | {str(item["id"]) for item in exits.get("closed") or []}
    recent = runtime.repository.list_whale_follow_trades(limit=200)
    changed = [trade for trade in recent if str(trade.id) in changed_ids]
    alerts = whale_follow_alerts.build_candidates(changed, now=now, recent_trades=recent)

    return {
        "enabled": True,
        "trigger": trigger,
        **entries,
        "exits": exits,
        "eligible_wallets": len(eligible),
        "follow_eligible": eligibility.get("eligible"),
        "passers": eligibility.get("passers"),
        "zero_passers_note": eligibility.get("zero_passers_note"),
        "contaminated_sample_total": eligibility.get("contaminated_sample_total"),
        "alerts": {
            "candidates": len(alerts["candidates"]),
            "blocked": alerts["blocked"],
            "caps": alerts["caps"],
            "rule_id": alerts["rule_id"],
        },
        "_alert_candidate_objects": alerts["candidates"],
    }


def whale_follow_trades(status: str | None = None, symbol: str | None = None, limit: int = 200) -> dict[str, Any]:
    """추종 트랙 원장 조회. `paper_trades` 와 다른 테이블이다(C3)."""
    from app.paper import whale_follow

    rows = runtime.repository.list_whale_follow_trades(status=status, symbol=symbol, limit=limit)
    return {
        "count": len(rows),
        "trades": [item.model_dump(mode="json") for item in rows],
        "performance": whale_follow.performance_by_qualification(rows),
        # 7-4 항목 2·3 — 상한이 무엇을 걸렀는지, 상한 값이 적정한지 읽는 근거.
        "latency": whale_follow.latency_distribution(rows),
        "drift": whale_follow.drift_distribution(rows),
        "caps": {
            "max_latency_minutes": int(runtime.settings.whale_follow_max_latency_minutes),
            "max_drift_pct_of_stop": float(runtime.settings.whale_follow_max_drift_pct_of_stop),
        },
        "ledger": "whale_follow_trades",
        "label": "미검증 추종 자격 트랙 — 승격 근거로 쓰지 않는다",
    }


def crypto_drawdown_watch(limit: int = 500) -> dict[str, Any]:
    """MDD 서명값 초과 관측 (WO-FCE-MAKE-IT-RUN-01 Phase 4).

    낙폭 구간과 동시 보유 상한 반사실을 함께 낸다. **임계를 바꾸지 않는다**(C2) —
    초과 사실과 그 구간을 보이게 할 뿐이다.
    """
    from app.paper import service as paper_service
    from app.validation import mdd_watch

    rows = runtime.repository.list_paper_trades(limit=limit)
    board = paper_service.paper_scoreboard(runtime.repository, runtime.settings)
    metrics = dict((board.get("competition") or {}).get("engine") or {})
    cap = int(getattr(runtime.settings, "paper_max_open_positions", 0) or 0)
    return {
        "status": mdd_watch.mdd_status(metrics.get("mdd_pct")),
        "window": mdd_watch.drawdown_window(rows),
        "concurrent_cap_counterfactual": mdd_watch.concurrent_cap_counterfactual(rows, max_concurrent=cap)
        if cap
        else {"available": False, "reason": "동시 보유 상한이 설정에 없다"},
        "note": "서명값 20% 는 표시용이다 — 진입도 전환도 막지 않는다. 게이트로 쓰려면 별도 결정이 필요하다.",
    }


def paper_trades(status: str | None = None, symbol: str | None = None, limit: int = 200) -> dict[str, Any]:
    rows = runtime.repository.list_paper_trades(status=status, symbol=symbol, limit=limit)
    return {"count": len(rows), "trades": [item.model_dump(mode="json") for item in rows]}


def paper_scoreboard() -> dict[str, Any]:
    return _paper_scoreboard(runtime.repository, runtime.settings)


def sync_user_fills() -> dict[str, Any]:
    return _sync_user_fills(
        runtime.repository,
        runtime.market_provider,
    )


def paper_dashboard() -> dict[str, Any]:
    return _paper_dashboard(
        runtime.repository,
        runtime.settings,
        calibration=calibration_snapshot(),
    )


def start_paper_benchmark(reset: bool = False) -> dict[str, Any]:
    return _start_paper_benchmark(runtime.repository, reset=reset)


def paper_pulse_summary() -> dict[str, Any]:
    funnel = _paper_gate_funnel(runtime.repository, days=1)
    return {
        "evaluations": int(funnel.get("evaluations") or 0),
        "entries": int(funnel.get("entered") or 0),
        "open": len(runtime.repository.list_paper_trades(status="open", limit=100)),
        "targets": len(_paper_universe(runtime.repository)),
    }


def _attach_scout_previews(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return payload
    enriched_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            enriched_rows.append(row)
            continue
        candidates = row.get("setup_candidates")
        if not isinstance(candidates, list):
            enriched_rows.append(row)
            continue
        enriched_candidates: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            direction = candidate.get("direction")
            if direction not in {"long", "short"}:
                enriched_candidates.append(candidate)
                continue
            try:
                preview = scout_handlers._simulate(
                    SimulateRequest(
                        symbol=str(row.get("symbol") or ""),
                        direction=direction,
                        leverage=10,
                        entry_price=_to_float(candidate.get("trigger_price")) or _to_float(row.get("mark_price")),
                        timeframe=str(row.get("timeframe") or "4h"),
                    )
                )
            except Exception:
                enriched_candidates.append(candidate)
                continue
            enriched_candidates.append(
                {
                    **candidate,
                    "preview": {
                        "rr_ratio": preview.get("rr_ratio"),
                        "rr_ratio_raw": preview.get("rr_ratio_raw"),
                        "rr_ratio_display": preview.get("rr_ratio_display"),
                        "invalidation_distance_pct": preview.get("invalidation_distance_pct"),
                        "invalidation_too_close": preview.get("invalidation_too_close"),
                        "min_invalidation_distance_pct": preview.get("min_invalidation_distance_pct"),
                        "quality_anomalies": preview.get("quality_anomalies"),
                        "estimated_liquidation_distance_pct": preview.get("estimated_liquidation_distance_pct"),
                        "checklist_passed": preview.get("checklist_passed"),
                        "checklist_total": preview.get("checklist_total"),
                        "verdict_line": preview.get("verdict_line"),
                        "briefing_summary": briefing_summary(preview.get("analyst_briefing") or {}, max_evidence=1)
                        if isinstance(preview.get("analyst_briefing"), dict)
                        else None,
                        "briefing_stance": (
                            ((preview.get("analyst_briefing") or {}).get("confluence") or {}).get("stance_label")
                            if isinstance(preview.get("analyst_briefing"), dict)
                            else None
                        ),
                        "briefing_stance_state": (
                            ((preview.get("analyst_briefing") or {}).get("confluence") or {}).get("stance")
                            if isinstance(preview.get("analyst_briefing"), dict)
                            else None
                        ),
                        "briefing_direction_conflict": preview.get("briefing_direction_conflict"),
                        "htf_trend": ((preview.get("mtf") or {}).get("htf_trend") if isinstance(preview.get("mtf"), dict) else None),
                        "htf_alignment": ((preview.get("mtf") or {}).get("alignment") if isinstance(preview.get("mtf"), dict) else None),
                    },
                }
            )
        enriched_rows.append({**row, "setup_candidates": enriched_candidates})
    return {**payload, "rows": enriched_rows}


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_zone(value: str) -> tuple[float, float]:
    parts = [part.strip() for part in value.replace("~", "-").split("-") if part.strip()]
    if len(parts) == 1:
        price = float(parts[0])
        return price * 0.995, price * 1.005
    if len(parts) >= 2:
        first, second = float(parts[0]), float(parts[1])
        return min(first, second), max(first, second)
    raise ValueError("zone is required")
