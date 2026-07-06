from datetime import datetime, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException

from app.agents.orchestrator import create_research_run
from app.analyst.briefing import build_analyst_briefing
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
from app.monitoring.engine import build_monitoring_log, calculate_pnl
from app.positions.action_plan import build_action_plan
from app.positions.chart_analysis import PositionContext, build_chart_analysis
from app.positions.engine import (
    build_events,
    build_position_state,
    direction_aware_score,
    make_snapshot,
)
from app.positions.insight import build_position_insight_input, make_ai_position_insight
from app.positions.pnl import resolve_position_pnl_percent
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
from app.review.params import (
    apply_engine_param_overrides,
    engine_param_from_suggestion,
    engine_param_snapshot,
)
from app.shadow.engine import (
    ShadowSampleError,
    compare_shadow_profile,
    extract_shadow_profile,
)
from app.validation.engine import run_validation

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


def configure_runtime(repo: Repository | None = None, provider: MarketDataProvider | None = None) -> None:
    global repository, market_provider
    if repo is not None:
        repository = repo
    if provider is not None:
        market_provider = provider


def _generate_and_store_report(symbol: str, timeframe: str = "4h"):
    try:
        snapshot = market_provider.get_snapshot(symbol, timeframe)
        return repository.add_report(generate_report(snapshot))
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
        repository.recent_reports(1)
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
        "timestamp": utc_now(),
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
    exit_record_errors: list[dict[str, str]] = []
    seen_keys: set[tuple[str, Direction]] = set()
    existing = repository.list_positions()
    for exchange_position in exchange_positions:
        key = (exchange_position.symbol, Direction(exchange_position.hold_side))
        seen_keys.add(key)
        current = _find_bitget_position(existing, exchange_position.symbol, Direction(exchange_position.hold_side))
        if current is None:
            repository.add_position(_position_from_bitget(exchange_position))
            created += 1
        else:
            repository.update_position(_merge_bitget_position(current, exchange_position))
            updated += 1

    missing = 0
    for position in existing:
        key = (position.symbol, position.direction)
        if position.source == "bitget" and position.status in EXIT_RECORDABLE_STATUSES and key not in seen_keys:
            missing += 1
            closed, error = _auto_record_missing_bitget_exit(position)
            if closed:
                auto_closed += 1
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


def list_live_positions() -> dict:
    all_positions = repository.list_positions()
    positions = [position for position in all_positions if position.status == PositionStatus.open]
    return {
        "provider": _provider_name(),
        "positions": [_live_position_payload(position, store_snapshot=False) for position in positions],
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


def get_live_position(position_id: UUID) -> dict:
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return _live_position_detail(position)


def get_position_chart_analysis(position_id: UUID, timeframe: str = "4h") -> dict:
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    try:
        snapshot = market_provider.get_snapshot(position.symbol, timeframe)
        return build_chart_analysis(
            snapshot,
            PositionContext.from_position(position),
            _trade_flow_for_snapshot(position.symbol, timeframe, snapshot.candles),
            derivatives=_derivative_context(position.symbol),
        )
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
    return {
        "position": position,
        "state": state,
        "latest_snapshot": snapshot,
        "latest_insight": _insight_payload(latest_insight, insight_status) if latest_insight else None,
        "insight_status": insight_status,
        "recent_events": latest_events if latest_events else events,
    }


def _live_position_detail(position: Position) -> dict:
    payload = _live_position_payload(position, store_snapshot=False)
    snapshot = payload["latest_snapshot"]
    try:
        chart_analysis = _chart_analysis_for_position(position)
    except HTTPException:
        chart_analysis = {}
    current_action_plan = build_action_plan(position, snapshot, chart_analysis)
    analyst_briefing = _build_position_analyst_briefing(position, snapshot, chart_analysis, current_action_plan)
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
    return {
        **payload,
        "action_plan": current_action_plan,
        "analyst_briefing": analyst_briefing,
        "chart_analysis": chart_analysis,
        "snapshots": repository.list_position_snapshots(position.id, limit=50),
        "insights": [_insight_payload(insight, _insight_status(insight, snapshot)) for insight in insights],
        "events": repository.list_position_events(position.id, limit=50),
        "monitoring_logs": repository.list_monitoring_logs(position.id, limit=30),
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
    briefing = build_analyst_briefing(
        symbol=position.symbol,
        timeframe=chart_analysis.get("timeframe") or "4h",
        analysis=chart_analysis,
        action_plan=action_plan,
        calibration_scores=repository.list_judgment_scores(limit=2000),
        context="position",
    )
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
    try:
        snapshot = market_provider.get_snapshot(position.symbol, "4h")
        return build_chart_analysis(
            snapshot,
            PositionContext.from_position(position),
            _trade_flow_for_snapshot(position.symbol, "4h", snapshot.candles),
            derivatives=_derivative_context(position.symbol),
        )
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
    return repository.add_position(position)


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
    return repository.list_trades()


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


def review_calibration() -> dict:
    scores = repository.list_judgment_scores(limit=2000)
    for suggestion in generate_calibration_suggestions(scores):
        existing = repository.get_calibration_suggestion(suggestion.id)
        if existing is None:
            repository.add_calibration_suggestion(suggestion)
    suggestions = repository.list_calibration_suggestions(limit=100)
    summary = build_calibration_summary(scores, suggestions, repository.list_alert_responses(limit=2000))
    summary["engine_params"] = [param.model_dump(mode="json") for param in repository.list_engine_params(limit=100)]
    return summary


def review_weekly_calibration() -> dict:
    scores = repository.list_judgment_scores(limit=2000)
    for suggestion in generate_calibration_suggestions(scores):
        existing = repository.get_calibration_suggestion(suggestion.id)
        if existing is None:
            repository.add_calibration_suggestion(suggestion)
    suggestions = repository.list_calibration_suggestions(limit=100)
    return build_weekly_calibration_report(scores, suggestions, repository.list_alert_responses(limit=2000))


def approve_calibration_suggestion(suggestion_id: UUID) -> CalibrationSuggestion:
    return _set_calibration_suggestion_status(suggestion_id, "approved")


def reject_calibration_suggestion(suggestion_id: UUID) -> CalibrationSuggestion:
    return _set_calibration_suggestion_status(suggestion_id, "rejected")


def _set_calibration_suggestion_status(suggestion_id: UUID, status: str) -> CalibrationSuggestion:
    suggestion = repository.get_calibration_suggestion(suggestion_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Calibration suggestion not found")
    suggestion.status = status
    suggestion.updated_at = utc_now()
    saved = repository.add_calibration_suggestion(suggestion)
    if status == "approved":
        version = engine_param_from_suggestion(settings, saved)
        if version is not None:
            repository.add_engine_param_version(version)
            apply_engine_param_overrides(settings, repository)
    return saved


def update_trade_memo(trade_id: UUID, request: TradeMemoUpdate):
    trade = repository.get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    trade.memo = request.memo
    return repository.add_trade(trade)
