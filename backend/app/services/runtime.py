from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
from app.marketdata.money_flow import flow_observation
from app.marketdata.signals import build_derivative_signals
from app.exchange.bitget.trades import timeframe_seconds
from app.paper.service import paper_dashboard as _paper_dashboard
from app.paper.service import paper_exit_monitor
from app.paper.service import paper_gate_funnel as _paper_gate_funnel
from app.paper.service import paper_universe as _paper_universe
from app.paper.service import paper_scoreboard as _paper_scoreboard
from app.paper.service import sync_user_fills as _sync_user_fills
from app.paper.service import start_paper_benchmark as _start_paper_benchmark
from app.paper.service import run_paper_engine as _run_paper_engine
from app.onchain.service import (
    add_whale_wallet as _add_whale_wallet,
    collect as _collect_whales,
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


def sync_and_analyze_positions() -> dict[str, Any]:
    """Sync Bitget positions and analyze open positions using the same route path."""
    payload = runtime.sync_live_positions()
    removed = [str(symbol).upper() for symbol in payload.get("scout_tracking_removed", [])]
    removed.extend(clear_scout_tracking_for_open_positions()["removed"])
    payload["scout_tracking_removed"] = sorted(set(removed))
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
    return weekly_improvement_digest(
        scores,
        suggestions,
        runtime.repository.list_engine_params(limit=200),
        runtime.repository.list_autonomy_logs(limit=1000),
        state_map(runtime.repository),
    )


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

    return _run_paper_engine(
        runtime.repository,
        runtime.settings,
        analysis_loader=load,
        simulation_loader=simulate,
    )


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
    payload = _paper_dashboard(
        runtime.repository,
        runtime.settings,
        calibration=calibration_snapshot(),
    )
    for trade in payload.get("open_trades", []):
        try:
            snapshot = runtime.market_provider.get_snapshot(str(trade.get("symbol")), str(trade.get("timeframe") or "4h"))
            monitor = paper_exit_monitor(trade, snapshot.price)
            if monitor is not None:
                trade["exit_monitor"] = monitor
        except Exception:
            continue
    return payload


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
