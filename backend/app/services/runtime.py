from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from app.services import http_handlers as runtime
from app.services import scout_handlers
from app.services.scout_handlers import ScanRequest, SimulateRequest
from app.analyst.briefing import briefing_summary
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
from app.demo.derivatives import FakeDerivativesProvider
from app.demo.seed import seed_demo_data as _seed_demo_data
from app.marketdata.bitget_derivatives import BitgetDerivProvider
from app.marketdata.coinglass import CoinglassProvider
from app.review.engine import (
    build_calibration_summary,
    build_weekly_calibration_report,
    generate_calibration_suggestions,
    score_interim_judgments,
)
from app.review.autonomy import process_parameter_autonomy, veto_suggestion
from app.validation.decay import build_self_audit, run_decay_sweep
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


@dataclass(frozen=True)
class SymbolMatch:
    position: Position | None
    candidates: list[Position]


_coinglass_round_robin_cursor = 0


def provider_name() -> str:
    return runtime._provider_name()


def sync_and_analyze_positions() -> dict[str, Any]:
    """Sync Bitget positions and analyze open positions using the same route path."""
    return runtime.sync_live_positions()


def apply_engine_param_overrides() -> dict[str, Any]:
    return _apply_engine_param_overrides(runtime.settings, runtime.repository)


def seed_demo_data() -> dict[str, Any]:
    if not runtime.settings.demo_mode:
        return {"enabled": False, "seeded": False, "positions": 0}
    return {"enabled": True, **_seed_demo_data(runtime.repository, runtime.market_provider)}


def refresh_market_data() -> dict[str, Any]:
    """Refresh report and market snapshot cache for currently held symbols."""
    symbols = sorted({position.symbol.upper() for position in runtime.repository.list_positions(PositionStatus.open)})
    refreshed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for symbol in symbols:
        try:
            report = runtime._generate_and_store_report(symbol, "4h")
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
                    "report_id": str(report.id),
                    "latest_candle": latest_candle,
                }
            )
        except HTTPException as exc:
            errors.append({"symbol": symbol, "error": str(exc.detail)})
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "symbols": symbols,
        "refreshed": refreshed,
        "errors": errors,
        "count": len(refreshed),
    }


def tracked_symbols() -> list[str]:
    symbols = {position.symbol.upper() for position in runtime.repository.list_positions(PositionStatus.open)}
    symbols.update(item.symbol.upper() for item in runtime.repository.list_watchlist())
    if not symbols:
        symbols.update(runtime.settings.symbol_list)
    return sorted(symbols)


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
            runtime.repository.add_derivative_metric(bitget_metric)
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


def latest_flow(symbol: str) -> dict[str, Any]:
    normalized = symbol.upper()
    context = derivative_context_for_symbol(runtime.repository, runtime.settings, normalized)
    return {
        **context,
        "history": [item.model_dump(mode="json") for item in runtime.repository.list_derivative_snapshots(symbol=normalized, provider="bitget", limit=50)],
        "rate_budget": _coinglass_budget(runtime.settings, len(tracked_symbols())),
    }


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
                param_version=engine_param_snapshot(runtime.repository),
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
        return scout_handlers._analysis_entry(symbol, timeframe, force=True, include_trade_flow=False)

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
    scores = runtime.repository.list_judgment_scores(limit=2000)
    for suggestion in generate_calibration_suggestions(scores):
        if runtime.repository.get_calibration_suggestion(suggestion.id) is None:
            runtime.repository.add_calibration_suggestion(suggestion)
    suggestions = process_parameter_autonomy(
        runtime.settings,
        runtime.repository,
        runtime.repository.list_calibration_suggestions(limit=100),
    )
    return build_calibration_summary(
        scores,
        suggestions,
        runtime.repository.list_alert_responses(limit=2000),
    )


def weekly_calibration_report() -> dict[str, Any]:
    scores = runtime.repository.list_judgment_scores(limit=2000)
    for suggestion in generate_calibration_suggestions(scores):
        if runtime.repository.get_calibration_suggestion(suggestion.id) is None:
            runtime.repository.add_calibration_suggestion(suggestion)
    suggestions = process_parameter_autonomy(
        runtime.settings,
        runtime.repository,
        runtime.repository.list_calibration_suggestions(limit=100),
    )
    # WO-37: 주간 부패 스윕 (자율 강등/격리 + 복귀 제안) → 셀프 오딧 첨부.
    sweep = run_decay_sweep(runtime.repository, runtime.settings)
    self_audit = build_self_audit(runtime.repository, sweep=sweep)
    payload = build_weekly_calibration_report(
        scores,
        suggestions,
        runtime.repository.list_alert_responses(limit=2000),
        self_audit=self_audit,
    )
    payload["performance"] = performance_summary()
    return payload


def veto_calibration_suggestion(suggestion_id: str) -> dict[str, Any]:
    suggestion = veto_suggestion(runtime.repository, UUID(str(suggestion_id)))
    return suggestion.model_dump(mode="json")


def calibration_experiments() -> dict[str, Any]:
    payload = calibration_snapshot()
    return {
        "autonomy": payload.get("autonomy", {}),
        "suggestions": [
            item
            for item in payload.get("suggestions", [])
            if item.get("status") in {"scheduled", "experiment"}
        ],
    }


def performance_summary() -> dict[str, Any]:
    return runtime.performance_summary()


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
                        "invalidation_distance_pct": preview.get("invalidation_distance_pct"),
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
                        "briefing_direction_conflict": preview.get("briefing_direction_conflict"),
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
