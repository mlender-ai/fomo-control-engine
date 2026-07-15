from datetime import datetime, timezone
import logging
from threading import Lock
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException

from app.agents.orchestrator import create_research_run
from app.analyst.briefing import build_analyst_briefing, hysteresis_params_from_settings, load_directional_prior, persist_directional_state
from app.analyst.gauges import build_gauges
from app.backtest.candidate_scoring import apply_signature_promotion
from app.backtest.service import validated_event_stats_for_symbol
from app.core.config import get_settings
from app.db.models import (
    CalibrationSuggestion,
    Direction,
    ExitRequest,
    JudgmentLedgerEntry,
    LiquidityAnalyzeRequest,
    Position,
    PositionCreate,
    PositionEvent,
    PositionInsight,
    PositionMemoUpdate,
    PositionSnapshot,
    PositionStatus,
    ReportRequest,
    ResearchRunRequest,
    ShadowExtractRequest,
    TradeMemoUpdate,
    Trade,
    ValidationRunRequest,
    utc_now,
)
from app.db.repository import Repository, create_repository
from app.derivatives.context import (
    compact_derivative_context,
    derivative_context_for_chart,
)
from app.exchange.base import MarketDataProvider
from app.exchange.bitget.errors import (
    BitgetAPIError,
    BitgetNotConfiguredError,
    BitgetPermissionError,
)
from app.exchange.bitget.provider import BitgetMarketDataProvider
from app.exchange.bitget.schemas import BitgetPosition
from app.exchange.errors import MarketDataError
from app.exchange.factory import create_market_data_provider
from app.liquidity.liquidation_clusters import analyze_liquidation
from app.memory.engine import (
    memory_from_shadow,
    memory_from_trade,
    memory_from_validation,
)
from app.marketdata.occ_options import occ_options_for_analysis
from app.monitoring.engine import build_monitoring_log, calculate_pnl
from app.performance.metrics import PerformanceConfig, build_performance_report
from app.positions.action_plan import build_action_plan
from app.positions.chart_analysis import PositionContext, apply_position_context, build_chart_analysis
from app.positions.engine import (
    build_events,
    build_position_state,
    calculate_liquidation_distance,
    direction_aware_score,
    make_snapshot,
)
from app.positions.insight import build_position_insight_input, make_ai_position_insight
from app.positions.pnl import resolve_position_pnl_percent
from app.onchain.service import chart_onchain_context
from app.report.engine import generate_report
from app.review.engine import (
    build_calibration_summary,
    build_judgment_entries,
    build_review_v2,
    build_weekly_calibration_report,
    generate_calibration_suggestions,
    generate_review_text,
    score_judgments,
)
from app.review.autonomy import adopt_suggestion, process_parameter_autonomy, veto_suggestion
from app.review.params import (
    engine_param_snapshot,
)
from app.shadow.engine import (
    ShadowSampleError,
    compare_shadow_profile,
    extract_shadow_profile,
)
from app.validation.decay import apply_recovery, build_self_audit
from app.validation.candidates import (
    approve_candidate_promotion as _approve_candidate_promotion,
    candidate_review_status,
    veto_candidate_promotion as _veto_candidate_promotion,
)
from app.validation.engine import run_validation

logger = logging.getLogger(__name__)
settings = get_settings()
repository: Repository = create_repository(settings.database_url)
market_provider: MarketDataProvider = create_market_data_provider(settings)

INSIGHT_STALE_PNL_DELTA_POINTS = 2.0
INSIGHT_STALE_HEALTH_DELTA_POINTS = 5
EXIT_RECORDABLE_STATUSES = {
    PositionStatus.open,
    PositionStatus.missing_from_exchange,
    PositionStatus.needs_exit_record,
}
_report_locks_guard = Lock()
_report_locks: dict[tuple[str, str], Lock] = {}


def configure_runtime(repo: Repository | None = None, provider: MarketDataProvider | None = None) -> None:
    global repository, market_provider
    if repo is not None:
        repository = repo
    if provider is not None:
        market_provider = provider


def _generate_and_store_report(symbol: str, timeframe: str = "4h"):
    key = (symbol.upper(), timeframe)
    with _report_locks_guard:
        report_lock = _report_locks.setdefault(key, Lock())
    with report_lock:
        try:
            snapshot = market_provider.get_snapshot(symbol, timeframe)
            report = generate_report(snapshot)
            latest = repository.latest_report(symbol)
            same_closed_bar = bool(
                latest
                and latest.timeframe == report.timeframe
                and latest.data_quality.last_candle_at
                and latest.data_quality.last_candle_at == report.data_quality.last_candle_at
            )
            # Report payloads contain candles and are intentionally large. A
            # symbol can be evaluated concurrently by sync, insight and API
            # paths, so the latest-check and insert must remain one operation.
            return report if same_closed_bar else repository.add_report(report)
        except MarketDataError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc


def _derivative_context(symbol: str) -> dict:
    try:
        return derivative_context_for_chart(repository, settings, symbol)
    except Exception as exc:
        return {
            "symbol": symbol.upper(),
            "as_of": None,
            "latest": None,
            "coinglass": None,
            "signals": {
                "as_of": None,
                "coverage": {"metric_samples": 0, "liquidation_samples": 0},
                "oi_price_divergence": None,
                "funding_state": None,
                "crowding_score": None,
                "liquidation_clusters": [],
            },
            "metrics": [],
            "liquidation_events": [],
            "source_status": "error",
            "notes": [f"derivative context unavailable: {type(exc).__name__}"],
        }


def _attach_derivatives(report, derivatives: dict) -> None:
    report.raw_json["derivatives"] = compact_derivative_context(derivatives)
    liquidity = report.raw_json.get("liquidity")
    if not isinstance(liquidity, dict):
        liquidity = {}
        report.raw_json["liquidity"] = liquidity
    liquidity["derivatives"] = compact_derivative_context(derivatives)


def _provider_name() -> str:
    return getattr(market_provider, "name", settings.market_data_provider)


def _database_status() -> str:
    try:
        # ``reports`` is a large append-only table and has no global created_at
        # index. Sorting it for every 30-second shell poll caused multi-second
        # stalls. Positions are tiny and still prove the operational schema is
        # readable without touching market or report generation paths.
        repository.list_positions(status=PositionStatus.open)
        return "ok"
    except Exception:
        return "error"


def health() -> dict:
    return {"status": "ok", "service": "fomo-control-engine"}


def system_status() -> dict:
    private_status = "not_configured"
    if isinstance(market_provider, BitgetMarketDataProvider) and market_provider.client.private_configured:
        private_status = "configured"
    return {
        "app": "fomo-control-engine",
        "status": "ok",
        "service": "fomo-control-engine",
        "environment": settings.env,
        "demo_mode": settings.demo_mode,
        "market_data_provider": _provider_name(),
        "database": _database_status(),
        "database_url": settings.database_url,
        "bitget_public_api": "available" if isinstance(market_provider, BitgetMarketDataProvider) else "not_active",
        "bitget_private_api": private_status,
        "default_symbols": settings.symbol_list,
        "refresh_policy": {
            "live_position_sync_interval_seconds": settings.live_position_sync_interval_seconds,
            "insight_stale_after_minutes": settings.insight_stale_after_minutes,
            "insight_price_drift_stale_pct": settings.insight_price_drift_stale_pct,
            "insight_auto_refresh_enabled": settings.insight_auto_refresh_enabled,
            "insight_model": settings.insight_model,
            "insight_min_regeneration_interval_minutes": settings.insight_min_regeneration_interval_minutes,
            "bitget_trade_fill_lookback_hours": settings.bitget_trade_fill_lookback_hours,
            "bitget_trade_fill_cache_ttl_seconds": settings.bitget_trade_fill_cache_ttl_seconds,
            "harmonic_zigzag_atr_multiplier": settings.harmonic_zigzag_atr_multiplier,
            "harmonic_min_confidence": settings.harmonic_min_confidence,
            "harmonic_ratio_tolerance_multiplier": settings.harmonic_ratio_tolerance_multiplier,
            "wyckoff_event_min_confidence": settings.wyckoff_event_min_confidence,
            "min_invalidation_level_score": settings.min_invalidation_level_score,
        },
        "engine_params": engine_param_snapshot(repository),
        "verdict_state_distribution": _verdict_state_distribution(),
        "directional_states": repository.list_directional_states(limit=100),
        "timestamp": utc_now(),
    }


def _verdict_state_distribution() -> dict:
    counts = {"holding": 0, "weakening": 0, "danger": 0, "standby": 0, "unknown": 0}
    open_positions = [position for position in repository.list_positions() if position.status == PositionStatus.open]
    for position in open_positions:
        # System status is polled by the persistent app shell. It must never
        # trigger live market analysis. Prefer the latest stored action plan,
        # then map the last persisted severity to the four verdict states.
        insights = repository.list_position_insights(position.id, limit=1)
        plan = insights[0].action_plan if insights else {}
        state_name = str(plan.get("verdict_state") or "") if isinstance(plan, dict) else ""
        if state_name not in counts:
            snapshots = repository.list_position_snapshots(position.id, limit=1)
            severity = snapshots[0].severity_rank if snapshots else None
            if severity is None:
                state_name = "unknown"
            elif severity >= 4:
                state_name = "danger"
            elif severity >= 1:
                state_name = "weakening"
            else:
                state_name = "holding"
        counts[state_name if state_name in counts else "unknown"] += 1
    total = sum(counts.values())
    standby_ratio = round((counts["standby"] / total) * 100, 2) if total else 0.0
    return {
        "counts": counts,
        "total": total,
        "standby_ratio_pct": standby_ratio,
        "slo": {"standby_ratio_pct_max": 20, "ok": standby_ratio <= 20 if total else True},
    }


def test_bitget_connection() -> dict:
    if not isinstance(market_provider, BitgetMarketDataProvider):
        report = _generate_and_store_report("BTCUSDT", "4h")
        return {
            "provider": _provider_name(),
            "public_market_data": {
                "ok": True,
                "sample_symbol": report.symbol,
                "candles": report.data_quality.candles,
            },
            "funding_rate": {
                "ok": report.data_quality.funding_ok,
                "value": report.scores.liquidity,
            },
            "open_interest": {"ok": report.data_quality.open_interest_ok, "value": 0},
            "private_positions": {"status": "not_active", "ok": False, "count": 0},
        }

    result = {
        "provider": "bitget",
        "public_market_data": {"ok": False, "sample_symbol": "BTCUSDT", "candles": 0},
        "funding_rate": {"ok": False, "value": None},
        "open_interest": {"ok": False, "value": None},
        "private_positions": {"status": "not_configured", "ok": False, "count": 0},
    }
    try:
        snapshot = market_provider.get_snapshot("BTCUSDT", "4h")
        result["public_market_data"] = {
            "ok": True,
            "sample_symbol": snapshot.symbol,
            "candles": snapshot.data_quality.candles,
        }
        result["funding_rate"] = {
            "ok": snapshot.data_quality.funding_ok,
            "value": snapshot.funding_rate,
        }
        result["open_interest"] = {
            "ok": snapshot.data_quality.open_interest_ok,
            "value": snapshot.open_interest_change,
        }
    except MarketDataError as exc:
        result["public_market_data"]["error"] = str(exc)

    if not market_provider.client.private_configured:
        return result

    try:
        positions = market_provider.get_positions()
        result["private_positions"] = {
            "status": "ok",
            "ok": True,
            "count": len(positions),
        }
    except BitgetNotConfiguredError:
        result["private_positions"] = {
            "status": "not_configured",
            "ok": False,
            "count": 0,
        }
    except BitgetPermissionError as exc:
        result["private_positions"] = {
            "status": "permission_error",
            "ok": False,
            "count": 0,
            "error": exc.message,
        }
    except BitgetAPIError as exc:
        result["private_positions"] = {
            "status": "error",
            "ok": False,
            "count": 0,
            "error": exc.message,
        }
    return result


def market_summary() -> dict:
    symbols = settings.symbol_list
    reports = []
    for symbol in symbols:
        report = repository.latest_report(symbol)
        if report is None:
            report = _generate_and_store_report(symbol)
        reports.append(report)
    return {
        "reports": reports,
        "positions": repository.list_positions(PositionStatus.open),
        "trades": repository.list_trades(),
        "market_data_provider": _provider_name(),
    }


def create_report(request: ReportRequest):
    return _generate_and_store_report(request.symbol, request.timeframe)


def get_report(symbol: str):
    report = repository.latest_report(symbol)
    if report is None:
        report = _generate_and_store_report(symbol)
    return report


def create_research_run_api(request: ResearchRunRequest) -> dict:
    report = _generate_and_store_report(request.symbol, request.timeframe)
    memories = [memory.model_dump(mode="json") for memory in repository.list_decision_memories(report.symbol, limit=8)]
    run, outputs = create_research_run(repository, report, memories=memories)
    return _research_run_payload(run, outputs)


def list_research_runs(symbol: str | None = None, limit: int = 20) -> dict:
    runs = repository.list_research_runs(symbol=symbol, limit=limit)
    return {"research_runs": [_research_run_summary(run) for run in runs]}


def compare_research_runs(symbol: str, limit: int = 5) -> dict:
    runs = repository.list_research_runs(symbol=symbol, limit=limit)
    return {
        "symbol": symbol.upper(),
        "runs": [
            {
                "research_run_id": str(run.id),
                "symbol": run.symbol,
                "timeframe": run.timeframe,
                "entry_score": run.entry_score,
                "fomo_index": run.fomo_index,
                "final_action_label": run.final_action_label,
                "created_at": run.created_at,
                "checklists": _rule_check_summaries(repository.list_agent_outputs(run.id)),
            }
            for run in runs
        ],
    }


def get_research_run(run_id: UUID) -> dict:
    run = repository.get_research_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Research run not found")
    outputs = repository.list_agent_outputs(run.id)
    return {
        **_research_run_payload(run, outputs),
        "raw_input": run.raw_input,
        "raw_output": _sanitized_research_raw_output(run.raw_output),
    }


def analyze_liquidity_api(request: LiquidityAnalyzeRequest):
    report = _generate_and_store_report(request.symbol, request.timeframe)
    analysis = analyze_liquidation(report)
    return {
        **analysis.model_dump(mode="json"),
        "clusters": [*analysis.upper_clusters, *analysis.lower_clusters],
    }


def extract_shadow(request: ShadowExtractRequest):
    try:
        profile = extract_shadow_profile(repository.list_trades(), request)
    except ShadowSampleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repository.add_shadow_profile(profile)
    repository.add_decision_memory(memory_from_shadow(profile))
    return profile


def list_shadow_profiles(limit: int = 20) -> dict:
    return {"shadow_profiles": repository.list_shadow_profiles(limit=limit)}


def get_shadow_profile(shadow_id: str):
    profile = repository.get_shadow_profile(shadow_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Shadow profile not found")
    return profile


def compare_shadow(shadow_id: str):
    profile = repository.get_shadow_profile(shadow_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Shadow profile not found")
    return compare_shadow_profile(profile, repository.list_trades())


def run_validation_api(request: ValidationRunRequest):
    validation_run = run_validation(repository.list_trades(), request)
    repository.add_validation_run(validation_run)
    repository.add_decision_memory(
        memory_from_validation(
            validation_run.id,
            validation_run.symbol,
            validation_run.summary,
            validation_run.warnings,
        )
    )
    return validation_run


def list_validation_runs(limit: int = 20) -> dict:
    return {"validation_runs": repository.list_validation_runs(limit=limit)}


def get_validation_run(run_id: UUID):
    validation_run = repository.get_validation_run(run_id)
    if validation_run is None:
        raise HTTPException(status_code=404, detail="Validation run not found")
    return validation_run


def list_memory(symbol: str | None = None, limit: int = 20) -> dict:
    return {"memories": repository.list_decision_memories(symbol=symbol, limit=limit)}


def reflect_memory() -> dict:
    created = 0
    for trade in repository.list_trades():
        repository.add_decision_memory(memory_from_trade(trade))
        created += 1
    return {"created": created}


def list_positions():
    return repository.list_positions()


def list_bitget_positions() -> dict:
    if settings.demo_mode:
        return {
            "provider": "demo",
            "status": "demo",
            "positions": [position.model_dump(mode="json") for position in repository.list_positions(PositionStatus.open) if position.source == "demo"],
        }
    if not isinstance(market_provider, BitgetMarketDataProvider):
        return {"provider": _provider_name(), "status": "not_active", "positions": []}
    if not market_provider.client.private_configured:
        return {"provider": "bitget", "status": "not_configured", "positions": []}
    try:
        positions = market_provider.get_positions()
    except BitgetPermissionError as exc:
        return {
            "provider": "bitget",
            "status": "permission_error",
            "error": exc.message,
            "positions": [],
        }
    except BitgetAPIError as exc:
        return {
            "provider": "bitget",
            "status": "error",
            "error": exc.message,
            "positions": [],
        }
    return {
        "provider": "bitget",
        "status": "ok",
        "positions": [position.model_dump(mode="json") for position in positions],
    }


def sync_bitget_positions() -> dict:
    return _sync_bitget_positions()


def _sync_bitget_positions() -> dict:
    if settings.demo_mode:
        return {
            "provider": "demo",
            "status": "ok",
            "synced": len([position for position in repository.list_positions(PositionStatus.open) if position.source == "demo"]),
            "created": 0,
            "updated": 0,
            "missing_from_exchange": 0,
            "auto_closed": 0,
            "exit_record_errors": [],
        }
    if not isinstance(market_provider, BitgetMarketDataProvider):
        return {
            "provider": _provider_name(),
            "status": "not_active",
            "synced": 0,
            "created": 0,
            "updated": 0,
            "missing_from_exchange": 0,
            "auto_closed": 0,
            "exit_record_errors": [],
        }
    if not market_provider.client.private_configured:
        return {
            "provider": "bitget",
            "status": "not_configured",
            "synced": 0,
            "created": 0,
            "updated": 0,
            "missing_from_exchange": 0,
            "auto_closed": 0,
            "exit_record_errors": [],
        }

    try:
        exchange_positions = market_provider.get_positions()
    except BitgetPermissionError as exc:
        return {
            "provider": "bitget",
            "status": "permission_error",
            "error": exc.message,
            "synced": 0,
            "created": 0,
            "updated": 0,
            "missing_from_exchange": 0,
            "auto_closed": 0,
            "exit_record_errors": [],
        }
    except BitgetAPIError as exc:
        return {
            "provider": "bitget",
            "status": "error",
            "error": exc.message,
            "synced": 0,
            "created": 0,
            "updated": 0,
            "missing_from_exchange": 0,
            "auto_closed": 0,
            "exit_record_errors": [],
        }

    created = 0
    updated = 0
    auto_closed = 0
    created_position_ids: list[str] = []
    closed_positions: list[dict] = []
    exit_record_errors: list[dict[str, str]] = []
    seen_keys: set[tuple[str, Direction]] = set()
    existing = repository.list_positions()
    for exchange_position in exchange_positions:
        key = (exchange_position.symbol, Direction(exchange_position.hold_side))
        seen_keys.add(key)
        current = _find_bitget_position(existing, exchange_position.symbol, Direction(exchange_position.hold_side))
        if current is None:
            saved = repository.add_position(_position_from_bitget(exchange_position))
            created += 1
            created_position_ids.append(str(saved.id))
        else:
            merged = _merge_bitget_position(current, exchange_position)
            # WO-44 Part C: 재등장 → 부재 카운터 리셋 (일시 오류로 인한 가짜 종료 방지).
            merged.sync_miss_count = 0
            repository.update_position(merged)
            updated += 1

    # WO-44 Part C: 종료 확정은 N틱 연속 부재 확인 후 — sync 간극/거래소 일시 오류 오탐 방지.
    confirm_ticks = max(1, int(getattr(settings, "alert_closure_confirm_ticks", 2)))
    missing = 0
    for position in existing:
        key = (position.symbol, position.direction)
        if position.source == "bitget" and position.status in EXIT_RECORDABLE_STATUSES and key not in seen_keys:
            missing += 1
            position.sync_miss_count = int(position.sync_miss_count or 0) + 1
            if position.sync_miss_count < confirm_ticks:
                position.synced_at = utc_now()
                repository.update_position(position)
                continue
            closed, error = _auto_record_missing_bitget_exit(position)
            if closed:
                auto_closed += 1
                trade = _trade_for_position(position.id)
                closed_positions.append(
                    {
                        "position": (repository.get_position(position.id) or position).model_dump(mode="json"),
                        "trade": trade.model_dump(mode="json") if trade is not None else None,
                    }
                )
            elif error:
                exit_record_errors.append(error)

    return {
        "provider": "bitget",
        "status": "ok",
        "synced": len(exchange_positions),
        "created": created,
        "updated": updated,
        "missing_from_exchange": missing,
        "auto_closed": auto_closed,
        "created_position_ids": created_position_ids,
        "closed_positions": closed_positions,
        "exit_record_errors": exit_record_errors,
    }


def _research_run_summary(run) -> dict:
    outputs = repository.list_agent_outputs(run.id)
    return {
        "research_run_id": str(run.id),
        "symbol": run.symbol,
        "timeframe": run.timeframe,
        "entry_score": run.entry_score,
        "fomo_index": run.fomo_index,
        "state_label": run.state_label,
        "final_action_label": run.final_action_label,
        "summary": run.final_summary,
        "created_at": run.created_at,
        "checklists": _rule_check_summaries(outputs),
    }


def _research_run_payload(run, outputs) -> dict:
    return {
        "research_run_id": str(run.id),
        "symbol": run.symbol,
        "timeframe": run.timeframe,
        "entry_score": run.entry_score,
        "fomo_index": run.fomo_index,
        "state_label": run.state_label,
        "final_action_label": run.final_action_label,
        "summary": run.final_summary,
        "checklists": _rule_check_summaries(outputs),
        "created_at": run.created_at,
    }


def _rule_check_summaries(outputs) -> list[dict]:
    return [
        {
            "id": str(output.id),
            "check": _check_name(output.raw_json.get("check", output.agent_name)),
            "stance": output.stance,
            "rule_score": output.confidence,
            "text_output": output.text_output,
            "raw_json": _sanitized_rule_json(output.raw_json, output.agent_name, output.confidence),
        }
        for output in outputs
    ]


def _check_name(value: str) -> str:
    mapping = {
        "market_structure_analyst": "market_structure",
        "liquidity_analyst": "liquidity",
        "momentum_analyst": "momentum",
        "bull_researcher": "bull_case",
        "bear_researcher": "bear_case",
        "risk_guardian": "risk_guardian",
        "fomo_gatekeeper": "fomo_gate",
    }
    return mapping.get(value, value)


def _sanitized_rule_json(raw_json: dict, fallback_name: str, rule_score: float) -> dict:
    payload = dict(raw_json)
    payload.pop("agent", None)
    payload.pop("confidence", None)
    payload["check"] = _check_name(str(payload.get("check", fallback_name)))
    payload["rule_score"] = rule_score
    return payload


def _sanitized_research_raw_output(raw_output: dict) -> dict:
    if "checklists" in raw_output:
        return raw_output
    legacy_agents = raw_output.get("agents")
    if not isinstance(legacy_agents, list):
        return raw_output
    checklists = []
    for item in legacy_agents:
        if not isinstance(item, dict):
            continue
        raw_json = item.get("raw_json") if isinstance(item.get("raw_json"), dict) else {}
        rule_score = item.get("confidence", raw_json.get("confidence", 0))
        checklists.append(
            {
                "check": _check_name(str(raw_json.get("check", item.get("agent", "")))),
                "stance": item.get("stance"),
                "rule_score": rule_score,
                "raw_json": _sanitized_rule_json(raw_json, str(item.get("agent", "")), rule_score),
                "text_output": item.get("text_output", ""),
            }
        )
    return {"checklists": checklists}


def _find_bitget_position(positions: list[Position], symbol: str, direction: Direction) -> Position | None:
    for position in positions:
        if position.source == "bitget" and position.status != PositionStatus.closed and position.symbol == symbol and position.direction == direction:
            return position
    return None


def _auto_record_missing_bitget_exit(
    position: Position,
) -> tuple[bool, dict[str, str] | None]:
    existing_trade = _trade_for_position(position.id)
    if existing_trade is not None:
        position.status = PositionStatus.closed
        position.closed_at = existing_trade.created_at
        position.synced_at = utc_now()
        repository.update_position(position)
        return False, None

    exit_price = _exit_price_for_missing_position(position)
    position.synced_at = utc_now()
    if exit_price is None:
        position.status = PositionStatus.needs_exit_record
        repository.update_position(position)
        return False, {
            "position_id": str(position.id),
            "symbol": position.symbol,
            "error": "missing_exit_price",
        }

    try:
        _record_exit(
            position.id,
            ExitRequest(
                exit_price=exit_price,
                exit_reason="Bitget read-only sync에서 포지션이 더 이상 감지되지 않아 자동 종료 기록을 생성했습니다.",
                memo="거래소 open-position 목록에서 사라진 포지션입니다. 체결가는 마지막 수신 mark/current 가격 기준입니다.",
            ),
            allow_missing=True,
        )
    except HTTPException as exc:
        latest = repository.get_position(position.id) or position
        latest.status = PositionStatus.needs_exit_record
        latest.synced_at = utc_now()
        repository.update_position(latest)
        return False, {
            "position_id": str(position.id),
            "symbol": position.symbol,
            "error": str(exc.detail),
        }
    return True, None


def _trade_for_position(position_id: UUID) -> Trade | None:
    for trade in repository.list_trades():
        if trade.position_id == position_id:
            return trade
    return None


def _exit_price_for_missing_position(position: Position) -> float | None:
    for value in (
        position.mark_price,
        position.current_price,
        position.break_even_price,
        position.entry_price,
    ):
        if value is not None and value > 0:
            return float(value)
    return None


def _position_from_bitget(exchange_position: BitgetPosition) -> Position:
    direction = Direction(exchange_position.hold_side)
    position = Position(
        symbol=exchange_position.symbol,
        direction=direction,
        entry_price=exchange_position.open_price_avg,
        quantity=exchange_position.total,
        leverage=exchange_position.leverage or 1,
        status=PositionStatus.open,
        current_price=exchange_position.mark_price,
        mark_price=exchange_position.mark_price,
        unrealized_pl=exchange_position.unrealized_pl,
        margin_size=exchange_position.margin_size,
        liquidation_price=exchange_position.liquidation_price,
        margin_mode=exchange_position.margin_mode,
        position_mode=exchange_position.position_mode,
        margin_ratio=exchange_position.margin_ratio,
        break_even_price=exchange_position.break_even_price,
        source="bitget",
        detected_source="bitget",
        synced_at=utc_now(),
        opened_at=exchange_position.created_at or utc_now(),
        memo="Synced from Bitget read-only position API",
    )
    if exchange_position.mark_price:
        pnl_result = resolve_position_pnl_percent(position, exchange_position.mark_price)
        position.pnl_percent = round(pnl_result.pnl_percent, 2)
        position.pnl_source = pnl_result.source
    return position


def _merge_bitget_position(position: Position, exchange_position: BitgetPosition) -> Position:
    position.status = PositionStatus.open
    position.entry_price = exchange_position.open_price_avg
    position.quantity = exchange_position.total
    position.leverage = exchange_position.leverage or position.leverage
    position.current_price = exchange_position.mark_price
    position.mark_price = exchange_position.mark_price
    position.unrealized_pl = exchange_position.unrealized_pl
    position.margin_size = exchange_position.margin_size
    position.liquidation_price = exchange_position.liquidation_price
    position.margin_mode = exchange_position.margin_mode
    position.position_mode = exchange_position.position_mode
    position.margin_ratio = exchange_position.margin_ratio
    position.break_even_price = exchange_position.break_even_price
    position.source = "bitget"
    position.detected_source = "bitget"
    position.synced_at = utc_now()
    if exchange_position.mark_price:
        pnl_result = resolve_position_pnl_percent(position, exchange_position.mark_price)
        position.pnl_percent = round(pnl_result.pnl_percent, 2)
        position.pnl_source = pnl_result.source
    return position


def list_live_positions(compact: bool = False) -> dict:
    all_positions = repository.list_positions()
    positions = [position for position in all_positions if position.status == PositionStatus.open]
    return {
        "provider": _provider_name(),
        "positions": [_cached_live_position_payload(position) if compact else _live_position_payload(position, store_snapshot=False) for position in positions],
        "open_count": len(positions),
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
        "timestamp": utc_now(),
    }


def sync_live_positions() -> dict:
    sync_result = _sync_bitget_positions()
    all_positions = repository.list_positions()
    positions = [position for position in all_positions if position.status == PositionStatus.open]
    scout_tracking_removed = _clear_watchlist_for_open_positions(positions)
    analyzed = []
    for position in positions:
        try:
            analyzed.append(_live_position_payload(position, store_snapshot=True))
        except HTTPException:
            continue
    return {
        **sync_result,
        "positions": analyzed,
        "open_count": len(positions),
        "scout_tracking_removed": scout_tracking_removed,
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
        "timestamp": utc_now(),
    }


def _clear_watchlist_for_open_positions(open_positions: list[Position]) -> list[str]:
    open_symbols = {position.symbol.upper() for position in open_positions if position.status == PositionStatus.open}
    removed: list[str] = []
    for item in list(repository.list_watchlist()):
        symbol = item.symbol.upper()
        if symbol in open_symbols and repository.remove_watchlist_item(symbol):
            removed.append(symbol)
    return removed


def get_live_position(position_id: UUID) -> dict:
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return _live_position_detail(position)


def get_position_chart_analysis(position_id: UUID, timeframe: str = "4h", compact: bool = False) -> dict:
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    try:
        market_analysis, position_analysis = _chart_analysis_bundle_for_position(position, timeframe, include_trade_flow=not compact)
        analysis = market_analysis if compact else position_analysis
        if compact:
            return {**_compact_chart_analysis(analysis), "position_id": str(position.id)}
        return {**analysis, "detail_level": "full"}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def get_position_snapshots(position_id: UUID, limit: int = 50) -> dict:
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return {"snapshots": repository.list_position_snapshots(position_id, limit=limit)}


def analyze_live_position(position_id: UUID) -> dict:
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return _live_position_payload(position, store_snapshot=True)


def create_position_insight(position_id: UUID) -> dict:
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    payload = _live_position_payload(position, store_snapshot=True)
    snapshot = PositionSnapshot.model_validate(payload["latest_snapshot"])
    saved_insight = _create_and_store_position_insight(position, snapshot, auto_generated=False)
    repository.add_position_event(
        PositionEvent(
            position_id=position.id,
            event_type="ai_insight",
            severity="low",
            title="AI Position Insight 생성",
            description=saved_insight.status_label,
            data={
                "insight_id": str(saved_insight.id),
                "snapshot_id": str(snapshot.id),
                "insight_source": saved_insight.insight_source,
            },
        )
    )
    insight_status = _insight_status(saved_insight, snapshot)
    return {
        **payload,
        "latest_insight": _insight_payload(saved_insight, insight_status),
        "insight_status": insight_status,
    }


def get_position_events(position_id: UUID, limit: int = 50) -> dict:
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return {"events": repository.list_position_events(position_id, limit=limit)}


def update_position_memo(position_id: UUID, request: PositionMemoUpdate):
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    update = request.model_dump(exclude_unset=True)
    for key, value in update.items():
        setattr(position, key, value if value is not None else getattr(position, key))
    repository.update_position(position)
    repository.add_position_event(
        PositionEvent(
            position_id=position.id,
            event_type="memo_updated",
            severity="low",
            title="포지션 메모 업데이트",
            description="진입 논리 또는 계획 가격이 수정되었습니다.",
            data=update,
        )
    )
    return position


def record_live_position_exit(position_id: UUID, request: ExitRequest):
    return _record_exit(position_id, request, allow_missing=True)


def _live_position_payload(position: Position, store_snapshot: bool = False) -> dict:
    report = _generate_and_store_report(position.symbol)
    derivatives = _derivative_context(position.symbol)
    _attach_derivatives(report, derivatives)
    previous_snapshots = repository.list_position_snapshots(position.id, limit=30)
    state = build_position_state(position, report, previous_snapshots)
    position.current_score = state["current_score"]
    position.current_price = state["mark_price"]
    position.pnl_percent = state["pnl_percent"]
    position.pnl_source = state["pnl_source"]
    repository.update_position(position)
    snapshot = make_snapshot(position, state)
    events: list[PositionEvent] = []
    if store_snapshot:
        previous_snapshot = previous_snapshots[0] if previous_snapshots else None
        snapshot = repository.add_position_snapshot(snapshot)
        for event in build_events(position, snapshot, previous_snapshot):
            events.append(repository.add_position_event(event))
    latest_insights = repository.list_position_insights(position.id, limit=1)
    latest_events = repository.list_position_events(position.id, limit=5)
    latest_insight = latest_insights[0] if latest_insights else None
    insight_status = _insight_status(latest_insight, snapshot)
    action_plan = build_action_plan(position, snapshot, _action_plan_context_from_report(report, derivatives))
    return {
        "position": position,
        "state": state,
        "latest_snapshot": snapshot,
        "action_plan": action_plan,
        "latest_insight": _insight_payload(latest_insight, insight_status) if latest_insight else None,
        "insight_status": insight_status,
        "recent_events": latest_events if latest_events else events,
    }


def _cached_live_position_payload(position: Position) -> dict:
    """Serve the cockpit from worker-produced state without recalculating reports.

    The browser polls this endpoint for display refreshes. Re-running the full
    indicator/report pipeline here made navigation compete with the worker and
    occasionally left a transient fetch error over otherwise valid data.
    """

    snapshots = repository.list_position_snapshots(position.id, limit=1)
    if not snapshots:
        return _live_position_payload(position, store_snapshot=False)

    stored_snapshot = snapshots[0]
    current_as_of = position.synced_at or stored_snapshot.as_of
    mark_price = position.mark_price or position.current_price or stored_snapshot.mark_price
    compact_analysis = _compact_position_analysis(stored_snapshot.analysis_json)
    compact_score = _compact_score_json(stored_snapshot.score_json)
    snapshot = stored_snapshot.model_copy(
        update={
            "as_of": current_as_of,
            "mark_price": mark_price,
            "pnl_percent": position.pnl_percent,
            "pnl_amount": position.unrealized_pl if position.unrealized_pl is not None else stored_snapshot.pnl_amount,
            "pnl_source": position.pnl_source,
            "liquidation_price": position.liquidation_price,
            "liquidation_distance_pct": calculate_liquidation_distance(position, mark_price),
            "analysis_json": compact_analysis,
            "score_json": compact_score,
        }
    )
    position_analysis = snapshot.analysis_json.get("position_analysis", {})
    analysis_as_of = snapshot.analysis_json.get("analysis_as_of") or position_analysis.get("analysis_as_of") or stored_snapshot.as_of
    score_json = snapshot.score_json if isinstance(snapshot.score_json, dict) else {}
    state = {
        "position": position.model_dump(mode="json"),
        "as_of": snapshot.as_of,
        "analysis_as_of": analysis_as_of,
        "mark_price": snapshot.mark_price,
        "pnl_percent": snapshot.pnl_percent,
        "pnl_amount": snapshot.pnl_amount,
        "pnl_source": snapshot.pnl_source,
        "liquidation_distance_pct": snapshot.liquidation_distance_pct,
        "health_score": snapshot.health_score,
        "status": position_analysis.get("status", "unknown"),
        "status_label": snapshot.status_label,
        "severity_rank": snapshot.severity_rank,
        "risk_score": snapshot.risk_score,
        "score_change": score_json.get("score_change", 0),
        "entry_direction_score": position_analysis.get("entry_direction_score", 50),
        "current_direction_score": position_analysis.get("current_direction_score", 50),
        "thesis_delta": position_analysis.get("thesis_delta", 0),
        "entry_score": score_json.get("entry_score", position.entry_score or 0),
        "current_score": score_json.get("current_score", position.current_score or 0),
        "analysis": compact_analysis,
        "score_json": score_json,
    }
    latest_insights = repository.list_position_insights(position.id, limit=1)
    latest_insight = latest_insights[0] if latest_insights else None
    insight_status = _insight_status(latest_insight, snapshot)
    action_plan = build_action_plan(position, snapshot, _action_plan_context_from_snapshot(snapshot))
    public_snapshot = snapshot.model_copy(update={"analysis_json": {}, "score_json": {}})
    return {
        "position": position,
        "state": state,
        "latest_snapshot": public_snapshot,
        "action_plan": action_plan,
        "latest_insight": _compact_insight_payload(latest_insight, insight_status) if latest_insight else None,
        "insight_status": insight_status,
        "recent_events": [],
    }


def _compact_position_analysis(analysis: Any) -> dict[str, Any]:
    source = analysis if isinstance(analysis, dict) else {}
    position_analysis = source.get("position_analysis") if isinstance(source.get("position_analysis"), dict) else {}
    derivatives = source.get("derivatives") if isinstance(source.get("derivatives"), dict) else {}
    return {
        "position_analysis": position_analysis,
        "analysis_as_of": source.get("analysis_as_of") or position_analysis.get("analysis_as_of"),
        "technical": source.get("technical", {}),
        "derivatives": _slim_derivatives(derivatives, metric_limit=0, event_limit=0),
        "risk": source.get("risk", {}),
    }


def _compact_score_json(score_json: Any) -> dict[str, Any]:
    source = score_json if isinstance(score_json, dict) else {}
    return {key: source.get(key) for key in ("entry_score", "current_score", "score_change", "health_components", "fomo_index") if key in source}


def _compact_insight_payload(insight: PositionInsight, status: dict[str, Any]) -> dict[str, Any]:
    payload = _insight_payload(insight, status)
    payload["input_json"] = {}
    payload["action_plan"] = {}
    payload["insight_text"] = ""
    return payload


def _action_plan_context_from_snapshot(snapshot: PositionSnapshot) -> dict[str, Any]:
    analysis = snapshot.analysis_json if isinstance(snapshot.analysis_json, dict) else {}
    risk = analysis.get("risk") if isinstance(analysis.get("risk"), dict) else {}
    levels = risk.get("critical_levels") if isinstance(risk.get("critical_levels"), list) else []
    support = [level for level in levels if isinstance(level, dict) and level.get("type") == "support"]
    resistance = [level for level in levels if isinstance(level, dict) and level.get("type") == "resistance"]
    position_analysis = analysis.get("position_analysis") if isinstance(analysis.get("position_analysis"), dict) else {}
    direction = position_analysis.get("direction")
    invalidation = support if direction == "long" else resistance if direction == "short" else []
    return {
        "mark_price": snapshot.mark_price,
        "price_levels": {
            "support": support,
            "resistance": resistance,
            "invalidation": invalidation,
        },
        "derivatives": analysis.get("derivatives", {}),
        "liquidity": {},
        "volume_profile": {},
        "volume_xray": {},
        "candles": [],
    }


def _compact_chart_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    # PositionChart requires at least 100 candles. Keep a small safety margin
    # while the minimal canvas still renders only its latest 72 candles.
    compact_window = 120
    indicator_window = 72
    compact = {**analysis, "detail_level": "compact"}
    compact["candles"] = list(analysis.get("candles") or [])[-compact_window:]

    trade_flow = analysis.get("trade_flow") if isinstance(analysis.get("trade_flow"), dict) else {}
    compact["trade_flow"] = {
        **trade_flow,
        "buckets": list(trade_flow.get("buckets") or [])[-compact_window:],
        "cvd": list(trade_flow.get("cvd") or [])[-compact_window:],
    }

    derivatives = analysis.get("derivatives") if isinstance(analysis.get("derivatives"), dict) else {}
    compact["derivatives"] = _slim_derivatives(derivatives, metric_limit=48, event_limit=12)

    liquidity = analysis.get("liquidity") if isinstance(analysis.get("liquidity"), dict) else {}
    compact["liquidity"] = {
        **liquidity,
        "pools": list(liquidity.get("pools") or [])[:12],
        "sweeps": list(liquidity.get("sweeps") or [])[-12:],
        "rejected_sweeps": [],
        "htf_sweeps": list(liquidity.get("htf_sweeps") or [])[-4:],
        "bos": list(liquidity.get("bos") or [])[-6:],
        "choch": list(liquidity.get("choch") or [])[-6:],
    }
    compact["wyckoff_markers_low_confidence"] = []
    compact["harmonic_patterns"] = sorted(
        list(analysis.get("harmonic_patterns") or []),
        key=lambda item: float(item.get("confidence", 0)) if isinstance(item, dict) else 0,
        reverse=True,
    )[:2]

    indicators = analysis.get("indicators") if isinstance(analysis.get("indicators"), dict) else {}
    bollinger = indicators.get("bollinger") if isinstance(indicators.get("bollinger"), dict) else {}
    compact["indicators"] = {
        **indicators,
        "bollinger": {key: list(points or [])[-indicator_window:] for key, points in bollinger.items()},
    }
    return compact


def _slim_derivatives(context: dict[str, Any], metric_limit: int, event_limit: int) -> dict[str, Any]:
    latest = _without_raw_payload(context.get("latest"))
    coinglass = _without_raw_payload(context.get("coinglass"))
    metrics = [_slim_derivative_metric(item) for item in list(context.get("metrics") or [])[-metric_limit:]] if metric_limit else []
    events = [_without_raw_payload(item) for item in list(context.get("liquidation_events") or [])[-event_limit:]] if event_limit else []
    signals = context.get("signals") if isinstance(context.get("signals"), dict) else {}
    return {
        "symbol": context.get("symbol"),
        "as_of": context.get("as_of"),
        "latest": latest,
        "coinglass": coinglass,
        "signals": {
            **signals,
            "liquidation_clusters": list(signals.get("liquidation_clusters") or [])[:6],
        },
        "metrics": metrics,
        "liquidation_events": events,
        "source_status": context.get("source_status"),
    }


def _without_raw_payload(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items() if key not in {"raw_json", "data_quality"}}


def _slim_derivative_metric(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    keys = (
        "symbol",
        "source",
        "tier",
        "as_of",
        "open_interest",
        "open_interest_value",
        "oi_change_pct",
        "funding",
        "funding_interval_hours",
        "funding_next",
        "taker_ls",
        "top_ls",
        "long_account_ratio",
        "short_account_ratio",
        "oi_weighted_funding",
        "source_status",
    )
    return {key: value.get(key) for key in keys}


def _live_position_detail(position: Position) -> dict:
    payload = _live_position_payload(position, store_snapshot=False)
    snapshot = payload["latest_snapshot"]
    try:
        market_analysis, chart_analysis = _chart_analysis_bundle_for_position(position, "4h", include_trade_flow=True)
    except HTTPException:
        market_analysis = {}
        chart_analysis = {}
    current_action_plan = build_action_plan(position, snapshot, chart_analysis)
    analyst_briefing = _build_position_analyst_briefing(position, snapshot, market_analysis, current_action_plan)
    _store_judgment_entries(
        position,
        snapshot,
        current_action_plan,
        chart_analysis,
        source_type="position_detail",
        source_id=str(snapshot.id),
    )
    latest_insight = repository.list_position_insights(position.id, limit=1)
    if latest_insight:
        status = _insight_status(latest_insight[0], snapshot)
        refreshed = _maybe_auto_regenerate_insight(position, snapshot, status)
        if refreshed is not None:
            insight_status = _insight_status(refreshed, snapshot)
            payload = {
                **payload,
                "latest_insight": _insight_payload(refreshed, insight_status),
                "insight_status": insight_status,
            }
    insights = repository.list_position_insights(position.id, limit=20)
    # WO-55A: 압축 차트 2게이지 — 포지션 보유이므로 익절 게이지 활성.
    briefing_confluence: dict = analyst_briefing["confluence"] if isinstance(analyst_briefing.get("confluence"), dict) else {}
    event_history = {
        "stats": [],
        "event_stats": validated_event_stats_for_symbol(
            repository,
            settings,
            symbol=position.symbol,
            timeframe=str(market_analysis.get("timeframe") or "4h"),
        ),
    }
    gauges = build_gauges(
        analysis=market_analysis,
        confluence=briefing_confluence,
        historical_backtest=event_history,
        position={"direction": position.direction.value, "entry_price": position.entry_price},
        now=utc_now(),
        timeframe=str(market_analysis.get("timeframe") or "4h"),
        hysteresis_params=hysteresis_params_from_settings(settings),
    )
    return {
        **payload,
        "action_plan": current_action_plan,
        "analyst_briefing": analyst_briefing,
        "chart_analysis": chart_analysis,
        "gauges": gauges,
        "snapshots": repository.list_position_snapshots(position.id, limit=50),
        "insights": [_insight_payload(insight, _insight_status(insight, snapshot)) for insight in insights],
        "events": repository.list_position_events(position.id, limit=50),
        "monitoring_logs": repository.list_monitoring_logs(position.id, limit=30),
        "judgments": repository.list_judgments(position.id, limit=20),
        "judgment_scores": repository.list_judgment_scores(position_id=position.id, limit=20),
    }


def _action_plan_context_from_report(report, derivatives: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = report.raw_json if isinstance(report.raw_json, dict) else {}
    structure_levels = raw.get("structure_levels") if isinstance(raw.get("structure_levels"), dict) else {}
    return {
        "mark_price": report.price,
        "price_levels": {
            "support": structure_levels.get("support", []),
            "resistance": structure_levels.get("resistance", []),
            "invalidation": [],
        },
        "liquidity": raw.get("liquidity", {}) if isinstance(raw.get("liquidity"), dict) else {},
        "derivatives": derivatives or {},
        "volume_profile": raw.get("volume_profile", {}) if isinstance(raw.get("volume_profile"), dict) else {},
        "volume_xray": raw.get("volume_xray", {}) if isinstance(raw.get("volume_xray"), dict) else {},
        "candles": raw.get("candles", []) if isinstance(raw.get("candles"), list) else [],
    }


def _create_and_store_position_insight(position: Position, snapshot: PositionSnapshot, *, auto_generated: bool) -> PositionInsight:
    stored_snapshot = repository.add_position_snapshot(snapshot)
    chart_analysis = _chart_analysis_for_position(position)
    previous_insights = repository.list_position_insights(position.id, limit=1)
    previous_insight = previous_insights[0] if previous_insights else None
    snapshots = repository.list_position_snapshots(position.id, limit=100)
    action_plan = build_action_plan(position, stored_snapshot, chart_analysis)
    _store_judgment_entries(
        position,
        stored_snapshot,
        action_plan,
        chart_analysis,
        source_type="insight_input",
        source_id=str(stored_snapshot.id),
    )
    input_json = build_position_insight_input(position, stored_snapshot, chart_analysis, snapshots, previous_insight)
    input_json["action_plan"] = action_plan
    insight = make_ai_position_insight(
        position,
        stored_snapshot,
        input_json,
        action_plan,
        api_key=settings.openai_api_key,
        model=settings.insight_model,
        previous_insight=previous_insight,
        auto_generated=auto_generated,
    )
    return repository.add_position_insight(insight)


def _store_judgment_entries(
    position: Position,
    snapshot: PositionSnapshot,
    action_plan: dict,
    chart_analysis: dict,
    *,
    source_type: str,
    source_id: str | None = None,
) -> None:
    try:
        entries = build_judgment_entries(
            position,
            snapshot,
            action_plan,
            chart_analysis,
            source_type=source_type,
            source_id=source_id,
            param_version=engine_param_snapshot(repository),
        )
    except Exception:
        return
    for entry in entries:
        repository.add_judgment(entry)


def _build_position_analyst_briefing(
    position: Position,
    snapshot: PositionSnapshot,
    chart_analysis: dict,
    action_plan: dict,
) -> dict:
    timeframe = chart_analysis.get("timeframe") or "4h"
    # WO-57: 포지션도 스카우트 여부와 무관하게 심볼·TF 전용 상태를 이어받는다.
    briefing = build_analyst_briefing(
        symbol=position.symbol,
        timeframe=timeframe,
        analysis=chart_analysis,
        action_plan=action_plan,
        calibration_scores=repository.list_judgment_scores(limit=2000),
        context="position",
        prior_state=load_directional_prior(repository, position.symbol, timeframe),
        hysteresis_params=hysteresis_params_from_settings(settings),
    )
    persist_directional_state(repository, position.symbol, timeframe, briefing)
    _store_analyst_briefing_judgment(position, snapshot, briefing)
    return briefing


def _store_analyst_briefing_judgment(position: Position, snapshot: PositionSnapshot, briefing: dict) -> None:
    confluence = briefing.get("confluence") if isinstance(briefing.get("confluence"), dict) else {}
    stance = str(confluence.get("stance") or "")
    price = snapshot.mark_price or position.mark_price or position.current_price
    if price is None:
        return
    expected_move = "up" if stance == "long_leaning" else "down" if stance == "short_leaning" else None
    confidence = int(round(float(confluence.get("composite_score") or 0))) if stance in {"long_leaning", "short_leaning", "conflicted"} else None
    repository.add_judgment(
        JudgmentLedgerEntry(
            judgment_id=str(uuid5(NAMESPACE_URL, f"fce:analyst-briefing:{position.id}:{snapshot.as_of.isoformat()}")),
            position_id=position.id,
            source_type="analyst_briefing",
            source_id=str(snapshot.id),
            as_of=snapshot.as_of,
            type="analyst_briefing",
            claim={
                "price": price,
                "expected_move": expected_move,
                "stance": stance,
                "stance_label": confluence.get("stance_label"),
                "composite_score": confluence.get("composite_score"),
                "long_score": confluence.get("long_score"),
                "short_score": confluence.get("short_score"),
                "evidence_count": confluence.get("evidence_count"),
                "counter_count": len(confluence.get("counter_evidence") or []),
            },
            confidence=confidence,
            param_version=engine_param_snapshot(repository),
        )
    )


def _attach_review_v2(trade: Trade) -> Trade:
    judgments = repository.list_judgments(trade.position_id, limit=500)
    snapshots = repository.list_position_snapshots(trade.position_id, limit=500)
    monitoring_logs = repository.list_monitoring_logs(trade.position_id, limit=500)
    scores = score_judgments(trade, judgments, snapshots, monitoring_logs)
    for score in scores:
        repository.add_judgment_score(score)
    review_v2 = build_review_v2(trade, judgments, scores)
    review_text, source, fallback_reason = generate_review_text(
        trade,
        review_v2,
        api_key=settings.openai_api_key,
        model=settings.insight_model,
    )
    review_v2["narrative_source"] = source
    review_v2["fallback_reason"] = fallback_reason
    trade.review_v2 = review_v2
    trade.judgment_scorecard = review_v2.get("scorecard", {})
    trade.review_text = review_text
    return trade


def _chart_analysis_for_position(position: Position) -> dict:
    return _chart_analysis_bundle_for_position(position, "4h", include_trade_flow=True)[1]


def _chart_analysis_bundle_for_position(
    position: Position,
    timeframe: str,
    *,
    include_trade_flow: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        snapshot = market_provider.get_snapshot(position.symbol, timeframe)
        market_analysis = build_chart_analysis(
            snapshot,
            None,
            _trade_flow_for_snapshot(position.symbol, timeframe, snapshot.candles) if include_trade_flow else None,
            derivatives=_derivative_context(position.symbol),
        )
        market_analysis["options"] = occ_options_for_analysis(
            position.symbol,
            str(market_analysis.get("asset_class") or "unknown"),
            settings,
        )
        onchain = chart_onchain_context(repository, position.symbol, timeframe, list(market_analysis.get("candles") or []))
        market_analysis["onchain"] = onchain
        market_analysis["validated_onchain_evidence"] = list(onchain.get("validated_evidence") or [])
        return market_analysis, apply_position_context(market_analysis, PositionContext.from_position(position))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _trade_flow_for_snapshot(symbol: str, timeframe: str, candles: list) -> dict | None:
    if hasattr(market_provider, "get_trade_flow"):
        return market_provider.get_trade_flow(symbol, timeframe, candles)
    if isinstance(market_provider, BitgetMarketDataProvider):
        return market_provider.get_trade_flow(symbol, timeframe, candles)
    return None


def _maybe_auto_regenerate_insight(position: Position, snapshot: PositionSnapshot, status: dict) -> PositionInsight | None:
    if not settings.insight_auto_refresh_enabled or not status.get("is_stale"):
        return None
    reasons = set(status.get("reasons", []))
    trigger_reasons = {
        "INSIGHT_OLDER_THAN_30M",
        "STATUS_CHANGED",
        "MARK_PRICE_CHANGED",
        "PNL_CHANGED",
        "HEALTH_CHANGED",
    }
    if not reasons.intersection(trigger_reasons):
        return None
    previous = repository.list_position_insights(position.id, limit=1)
    if not previous:
        return None
    min_age_seconds = settings.insight_min_regeneration_interval_minutes * 60
    if (utc_now() - previous[0].created_at).total_seconds() < min_age_seconds:
        return None
    refreshed = _create_and_store_position_insight(position, snapshot, auto_generated=True)
    repository.add_position_event(
        PositionEvent(
            position_id=position.id,
            event_type="ai_insight_auto_refresh",
            severity="low",
            title="AI Position Insight 자동 재생성",
            description=refreshed.status_label,
            data={
                "insight_id": str(refreshed.id),
                "snapshot_id": str(snapshot.id),
                "reasons": sorted(reasons),
                "insight_source": refreshed.insight_source,
            },
        )
    )
    return refreshed


def _insight_payload(insight: PositionInsight | None, status: dict) -> dict | None:
    if insight is None:
        return None
    payload = insight.model_dump(mode="json")
    snapshot_meta = insight.input_json.get("snapshot", {}) if isinstance(insight.input_json, dict) else {}
    generated_as_of = snapshot_meta.get("as_of") or payload.get("as_of") or payload.get("created_at")
    payload.update(
        {
            "as_of": generated_as_of,
            "age_minutes": status["age_minutes"],
            "is_stale": status["is_stale"],
            "price_drift_pct": status["price_drift_pct"],
            "basis_mark_price": status["generated_for"]["mark_price"] if status.get("generated_for") else None,
            "stale_reasons": status["reasons"],
        }
    )
    return payload


def _insight_status(insight: PositionInsight | None, snapshot: PositionSnapshot) -> dict:
    if insight is None:
        return {
            "has_insight": False,
            "is_stale": True,
            "age_minutes": None,
            "price_drift_pct": None,
            "reasons": ["NO_INSIGHT"],
            "message": "아직 생성된 인사이트가 없습니다.",
            "insight_created_at": None,
            "current_snapshot_created_at": snapshot.created_at,
            "current_as_of": snapshot.as_of,
            "generated_for": None,
            "current": _snapshot_status_payload(snapshot),
        }

    now = utc_now()
    age_minutes = int(max(0, (now - insight.created_at).total_seconds()) // 60)
    generated_position = insight.input_json.get("position", {}) if isinstance(insight.input_json, dict) else {}
    generated_snapshot = insight.input_json.get("snapshot", {}) if isinstance(insight.input_json, dict) else {}
    generated_mark = _optional_float(generated_position.get("mark_price"))
    generated_pnl = _optional_float(generated_position.get("pnl_percent"))
    current_mark = snapshot.mark_price
    current_pnl = snapshot.pnl_percent
    mark_delta_pct = _pct_delta(current_mark, generated_mark)
    pnl_delta_points = None if generated_pnl is None else round(current_pnl - generated_pnl, 2)
    health_delta = snapshot.health_score - insight.health_score
    reasons: list[str] = []
    if age_minutes > settings.insight_stale_after_minutes:
        reasons.append("INSIGHT_OLDER_THAN_30M")
    if pnl_delta_points is not None and abs(pnl_delta_points) >= INSIGHT_STALE_PNL_DELTA_POINTS:
        reasons.append("PNL_CHANGED")
    if mark_delta_pct is not None and abs(mark_delta_pct) >= settings.insight_price_drift_stale_pct:
        reasons.append("MARK_PRICE_CHANGED")
    if abs(health_delta) >= INSIGHT_STALE_HEALTH_DELTA_POINTS:
        reasons.append("HEALTH_CHANGED")
    if insight.status_label != snapshot.status_label:
        reasons.append("STATUS_CHANGED")

    return {
        "has_insight": True,
        "is_stale": bool(reasons),
        "age_minutes": age_minutes,
        "price_drift_pct": mark_delta_pct,
        "reasons": reasons,
        "message": _insight_status_message(reasons, age_minutes),
        "insight_created_at": insight.created_at,
        "current_snapshot_created_at": snapshot.created_at,
        "current_as_of": snapshot.as_of,
        "generated_for": {
            "snapshot_id": str(insight.snapshot_id) if insight.snapshot_id else None,
            "as_of": generated_snapshot.get("as_of") or insight.as_of,
            "mark_price": generated_mark,
            "pnl_percent": generated_pnl,
            "health_score": insight.health_score,
            "status_label": insight.status_label,
        },
        "current": {
            **_snapshot_status_payload(snapshot),
            "mark_delta_pct": mark_delta_pct,
            "pnl_delta_points": pnl_delta_points,
            "health_delta": health_delta,
        },
    }


def _snapshot_status_payload(snapshot: PositionSnapshot) -> dict:
    return {
        "snapshot_id": str(snapshot.id),
        "as_of": snapshot.as_of,
        "mark_price": snapshot.mark_price,
        "pnl_percent": snapshot.pnl_percent,
        "health_score": snapshot.health_score,
        "status_label": snapshot.status_label,
    }


def _insight_status_message(reasons: list[str], age_minutes: float) -> str:
    if not reasons:
        return "현재 데이터 기준으로 사용할 수 있는 인사이트입니다."
    if "INSIGHT_OLDER_THAN_30M" in reasons:
        return f"인사이트 생성 후 {age_minutes}분이 지나 현재 판단으로 사용하지 않습니다."
    if "PNL_CHANGED" in reasons or "MARK_PRICE_CHANGED" in reasons:
        return "가격 또는 손익률이 생성 시점과 달라 현재 판단으로 사용하지 않습니다."
    if "HEALTH_CHANGED" in reasons or "STATUS_CHANGED" in reasons:
        return "건강도 또는 상태 라벨이 생성 시점과 달라 현재 판단으로 사용하지 않습니다."
    return "현재 데이터와 생성 시점 데이터가 달라 다시 생성이 필요합니다."


def _optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_delta(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 3)


def create_position(request: PositionCreate):
    report = repository.get_report(request.entry_report_id) if request.entry_report_id else repository.latest_report(request.symbol)
    entry_direction_score = request.entry_direction_score
    if entry_direction_score is None and report is not None:
        entry_direction_score = direction_aware_score(
            request.direction,
            report.raw_json.get("structure", {}),
            report.raw_json.get("indicators", {}),
        )
    position = Position(
        symbol=request.symbol.upper(),
        direction=request.direction,
        entry_price=request.entry_price,
        quantity=request.quantity,
        leverage=request.leverage,
        entry_report_id=report.id if report else None,
        entry_score=report.entry_score if report else None,
        entry_direction_score=entry_direction_score,
        current_score=report.entry_score if report else None,
        current_price=report.price if report else request.entry_price,
        memo=request.memo,
        entry_memo=request.entry_memo,
        planned_stop_price=request.planned_stop_price,
        planned_take_profit_price=request.planned_take_profit_price,
        thesis_text=request.thesis_text,
    )
    saved = repository.add_position(position)
    _clear_watchlist_for_open_positions([saved])
    return saved


def monitor_position(position_id: UUID):
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    if position.status != PositionStatus.open:
        raise HTTPException(status_code=400, detail="Position is already closed")
    report = _generate_and_store_report(position.symbol)
    log = build_monitoring_log(position, report)
    position.current_price = report.price
    position.current_score = report.entry_score
    position.pnl_percent = log.pnl_percent
    position.pnl_source = resolve_position_pnl_percent(position, report.price).source
    repository.update_position(position)
    repository.add_monitoring_log(log)
    return log


def exit_position(position_id: UUID, request: ExitRequest):
    return _record_exit(position_id, request, allow_missing=False)


def _record_exit(position_id: UUID, request: ExitRequest, allow_missing: bool = False):
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    allowed_statuses = EXIT_RECORDABLE_STATUSES if allow_missing else {PositionStatus.open}
    if position.status not in allowed_statuses:
        raise HTTPException(status_code=400, detail="Position is already closed")
    report = _generate_and_store_report(position.symbol)
    pnl_percent = calculate_pnl(position, request.exit_price)
    side_multiplier = 1 if position.direction == "long" else -1
    pnl_amount = (request.exit_price - position.entry_price) * position.quantity * side_multiplier
    holding_minutes = int((datetime.now(timezone.utc) - position.opened_at).total_seconds() // 60)
    trade = Trade(
        position_id=position.id,
        symbol=position.symbol,
        direction=position.direction,
        entry_price=position.entry_price,
        exit_price=request.exit_price,
        quantity=position.quantity,
        pnl_percent=round(pnl_percent, 2),
        pnl_amount=round(pnl_amount, 2),
        entry_score=position.entry_score,
        exit_score=report.entry_score,
        holding_minutes=holding_minutes,
        exit_reason=request.exit_reason,
        review_text="",
        memo=request.memo,
    )
    position.status = PositionStatus.closed
    position.closed_at = datetime.now(timezone.utc)
    position.current_price = request.exit_price
    position.current_score = report.entry_score
    position.pnl_percent = trade.pnl_percent
    position.pnl_source = "computed"
    repository.update_position(position)
    trade = _attach_review_v2(trade)
    saved_trade = repository.add_trade(trade)
    repository.add_decision_memory(memory_from_trade(saved_trade))
    repository.add_position_event(
        PositionEvent(
            position_id=position.id,
            event_type="exit_recorded",
            severity="medium",
            title="청산 기록 완료",
            description=request.exit_reason,
            data={
                "trade_id": str(saved_trade.id),
                "exit_price": request.exit_price,
                "pnl_percent": saved_trade.pnl_percent,
            },
        )
    )
    try:
        refresh_calibration_report_cache()
    except Exception:
        logger.exception("calibration cache refresh failed after exit scoring")
    return saved_trade


def review_trade(trade_id: UUID):
    trade = repository.get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    trade = _attach_review_v2(trade)
    repository.add_trade(trade)
    repository.add_decision_memory(memory_from_trade(trade))
    return trade


def list_trades():
    return [_trade_list_payload(trade) for trade in repository.list_trades()]


def _trade_list_payload(trade: Trade) -> dict:
    payload = trade.model_dump(mode="json")
    payload["review_v2"] = _review_v2_list_summary(payload.get("review_v2"))
    payload["judgment_scorecard"] = _scorecard_list_summary(payload.get("judgment_scorecard"))
    return payload


def _review_v2_list_summary(review_v2: object) -> dict:
    if not isinstance(review_v2, dict):
        return {}
    summary = dict(review_v2)
    summary["scorecard"] = _scorecard_list_summary(summary.get("scorecard"))
    return summary


def _scorecard_list_summary(scorecard: object) -> dict:
    if not isinstance(scorecard, dict):
        return {}
    return {key: value for key, value in scorecard.items() if key != "scores"}


def _build_performance_summary() -> dict:
    positions = repository.list_positions()
    latest_snapshots = {}
    for position in positions:
        snapshots = repository.list_position_snapshots(position.id, limit=1)
        if snapshots:
            latest_snapshots[position.id] = snapshots[0]
    mdd_limit = settings.performance_monthly_mdd_limit_pct if settings.performance_monthly_mdd_limit_pct > 0 else None
    return build_performance_report(
        repository.list_trades(),
        positions=positions,
        latest_snapshots=latest_snapshots,
        config=PerformanceConfig(
            capital_base_usdt=settings.performance_capital_base_usdt,
            monthly_mdd_limit_pct=mdd_limit,
        ),
    )


def performance_summary() -> dict:
    cached = repository.get_calibration_report_cache("performance")
    if cached is not None:
        return {**cached["payload"], "computed_at": cached["computed_at"], "cache_status": "ready"}
    return {**_build_performance_summary(), "cache_status": "live_fallback"}


def get_trade(trade_id: UUID):
    trade = repository.get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


def get_trade_timeline(trade_id: UUID) -> dict:
    trade = repository.get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return {
        "trade": trade,
        "snapshots": repository.list_position_snapshots(trade.position_id, limit=100),
        "events": repository.list_position_events(trade.position_id, limit=100),
        "monitoring_logs": repository.list_monitoring_logs(trade.position_id, limit=100),
        "judgments": repository.list_judgments(trade.position_id, limit=200),
        "judgment_scores": repository.list_judgment_scores(position_id=trade.position_id, trade_id=trade.id, limit=200),
    }


def _calibration_preparing_payload() -> dict:
    return {
        "status": "preparing",
        "cache_status": "preparing",
        "computed_at": None,
        "generated_at": utc_now().isoformat(),
        "totals": {"total": 0, "tested": 0, "accuracy_pct": None},
        "invalidation": {},
        "take_profit": {},
        "judgment_types": {},
        "confidence_curve": [],
        "level_quality": {},
        "score_contexts": {},
        "alert_response_summary": {},
        "scout_setup_summary": {},
        "candidate_review": {
            "generated_at": utc_now().isoformat(),
            "policy": "candidate scoring pending",
            "veto_window_hours": 48,
            "pending_promotions": 0,
            "items": [],
        },
        "briefing_performance": {},
        "wyckoff_confidence": [],
        "suggestion_status_counts": {},
        "autonomy": {},
        "weekly_report": {},
        "engine_params": [],
        "suggestions": [],
        "sample_warning": "집계 준비 중 · 워커 실행 대기",
    }


def _cached_calibration_payload(report_key: str) -> dict | None:
    cached = repository.get_calibration_report_cache(report_key)
    if cached is None:
        return None
    return {**cached["payload"], "computed_at": cached["computed_at"], "cache_status": "ready"}


def refresh_calibration_report_cache() -> dict:
    """Build calibration views in the worker; HTTP GET paths only read this cache."""
    scores = repository.list_judgment_scores(limit=2000)
    for suggestion in generate_calibration_suggestions(scores):
        existing = repository.get_calibration_suggestion(suggestion.id)
        if existing is None:
            repository.add_calibration_suggestion(suggestion)
    suggestions = process_parameter_autonomy(settings, repository, repository.list_calibration_suggestions(limit=100))
    summary = build_calibration_summary(
        scores,
        suggestions,
        repository.list_alert_responses(limit=2000),
        self_audit=build_self_audit(repository),
    )
    candidate_review = candidate_review_status(repository, settings)
    summary["candidate_review"] = candidate_review
    summary["engine_params"] = [param.model_dump(mode="json") for param in repository.list_engine_params(limit=100)]
    # WO-45: 대시보드 개선 카드가 이 경로의 weekly_report를 소비한다 — 다이제스트 임베드.
    weekly = summary.get("weekly_report")
    if isinstance(weekly, dict):
        from app.services import runtime as service_runtime

        weekly["improvement_digest"] = service_runtime.improvement_digest(scores=scores, suggestions=suggestions)
        weekly["candidate_review"] = candidate_review
    weekly_payload = build_weekly_calibration_report(
        scores,
        suggestions,
        repository.list_alert_responses(limit=2000),
        self_audit=build_self_audit(repository),
    )
    weekly_payload["candidate_review"] = summary["candidate_review"]
    weekly_payload["improvement_digest"] = review_improvement()["digest"]
    performance = _build_performance_summary()
    weekly_payload["performance"] = performance
    weekly_payload["candidate_review"] = candidate_review
    repository.upsert_calibration_report_cache("calibration", summary)
    repository.upsert_calibration_report_cache("weekly", weekly_payload)
    repository.upsert_calibration_report_cache("performance", performance)
    return {"scores": len(scores), "suggestions": len(suggestions), "computed": 3}


def review_calibration() -> dict:
    return _cached_calibration_payload("calibration") or _calibration_preparing_payload()


def review_weekly_calibration() -> dict:
    return _cached_calibration_payload("weekly") or {
        "status": "preparing",
        "cache_status": "preparing",
        "computed_at": None,
        "sample_warning": "집계 준비 중 · 워커 실행 대기",
    }


def review_improvement() -> dict:
    """WO-45: 개선 다이제스트 + 조치별 효과표 (읽기 전용, 결정론)."""
    from app.services import runtime as service_runtime

    scores = repository.list_judgment_scores(limit=5000)
    digest = service_runtime.improvement_digest(scores=scores)
    from app.review.improvement import action_effect_table

    effects = action_effect_table(
        scores,
        repository.list_engine_params(limit=200),
        repository.list_autonomy_logs(limit=1000),
    )
    return {"digest": digest, "action_effects": effects}


def approve_signature_recovery(signature_key: str) -> dict:
    """WO-37: 복귀 제안 승인 — 제안-승인 경유로만 validated 복귀 (자율 아님)."""
    log = apply_recovery(repository, signature_key, approved_by="manual")
    return log.model_dump(mode="json")


def approve_candidate_promotion(signature_key: str) -> dict:
    try:
        log = _approve_candidate_promotion(repository, signature_key, approved_by="manual")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return log.model_dump(mode="json")


def veto_candidate_promotion(signature_key: str) -> dict:
    try:
        log = _veto_candidate_promotion(repository, signature_key, vetoed_by="manual")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return log.model_dump(mode="json")


def approve_calibration_suggestion(suggestion_id: UUID) -> CalibrationSuggestion:
    return _set_calibration_suggestion_status(suggestion_id, "approved")


def reject_calibration_suggestion(suggestion_id: UUID) -> CalibrationSuggestion:
    try:
        return veto_suggestion(repository, suggestion_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Calibration suggestion not found") from None


def _set_calibration_suggestion_status(suggestion_id: UUID, status: str) -> CalibrationSuggestion:
    suggestion = repository.get_calibration_suggestion(suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Calibration suggestion not found")
    suggestion.status = status
    suggestion.updated_at = utc_now()
    saved = repository.add_calibration_suggestion(suggestion)
    if status == "approved":
        if saved.suggestion_type == "signature_promotion":
            transition = apply_signature_promotion(repository, saved)
            saved.autonomy = {
                **saved.autonomy,
                "approved_at": utc_now().isoformat(),
                "transition_id": str(transition.id),
                "applied_state": "validated",
            }
            saved = repository.add_calibration_suggestion(saved)
        else:
            saved = adopt_suggestion(settings, repository, saved, adopted_by="manual")
    return saved


def update_trade_memo(trade_id: UUID, request: TradeMemoUpdate):
    trade = repository.get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    trade.memo = request.memo
    return repository.add_trade(trade)
