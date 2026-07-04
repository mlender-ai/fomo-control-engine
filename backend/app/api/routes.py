from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.agents.orchestrator import create_research_run
from app.core.config import get_settings
from app.db.models import (
    Direction,
    ExitRequest,
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
from app.exchange.base import MarketDataProvider
from app.exchange.bitget.errors import BitgetAPIError, BitgetNotConfiguredError, BitgetPermissionError
from app.exchange.bitget.provider import BitgetMarketDataProvider
from app.exchange.bitget.schemas import BitgetPosition
from app.exchange.errors import MarketDataError
from app.exchange.factory import create_market_data_provider
from app.liquidity.liquidation_clusters import analyze_liquidation
from app.memory.engine import memory_from_shadow, memory_from_trade, memory_from_validation
from app.monitoring.engine import build_monitoring_log, calculate_pnl
from app.positions.chart_analysis import build_chart_analysis
from app.positions.engine import build_events, build_position_state, direction_aware_score, make_snapshot
from app.positions.insight import build_position_insight_input, make_ai_position_insight
from app.positions.pnl import resolve_position_pnl_percent
from app.report.engine import generate_report
from app.review.engine import render_review
from app.shadow.engine import ShadowSampleError, compare_shadow_profile, extract_shadow_profile
from app.validation.engine import run_validation

router = APIRouter()
settings = get_settings()
repository: Repository = create_repository(settings.database_url)
market_provider: MarketDataProvider = create_market_data_provider(settings)

INSIGHT_STALE_PNL_DELTA_POINTS = 2.0
INSIGHT_STALE_HEALTH_DELTA_POINTS = 5


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


def _provider_name() -> str:
    return getattr(market_provider, "name", settings.market_data_provider)


def _database_status() -> str:
    try:
        repository.recent_reports(1)
        return "ok"
    except Exception:
        return "error"


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "fomo-control-engine"}


@router.get("/api/system/status")
def system_status() -> dict:
    private_status = "not_configured"
    if isinstance(market_provider, BitgetMarketDataProvider) and market_provider.client.private_configured:
        private_status = "configured"
    return {
        "app": "fomo-control-engine",
        "status": "ok",
        "service": "fomo-control-engine",
        "environment": settings.env,
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
        },
        "timestamp": utc_now(),
    }


@router.post("/api/system/bitget/test-connection")
def test_bitget_connection() -> dict:
    if not isinstance(market_provider, BitgetMarketDataProvider):
        report = _generate_and_store_report("BTCUSDT", "4h")
        return {
            "provider": _provider_name(),
            "public_market_data": {"ok": True, "sample_symbol": report.symbol, "candles": report.data_quality.candles},
            "funding_rate": {"ok": report.data_quality.funding_ok, "value": report.scores.liquidity},
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
        result["funding_rate"] = {"ok": snapshot.data_quality.funding_ok, "value": snapshot.funding_rate}
        result["open_interest"] = {"ok": snapshot.data_quality.open_interest_ok, "value": snapshot.open_interest_change}
    except MarketDataError as exc:
        result["public_market_data"]["error"] = str(exc)

    if not market_provider.client.private_configured:
        return result

    try:
        positions = market_provider.get_positions()
        result["private_positions"] = {"status": "ok", "ok": True, "count": len(positions)}
    except BitgetNotConfiguredError:
        result["private_positions"] = {"status": "not_configured", "ok": False, "count": 0}
    except BitgetPermissionError as exc:
        result["private_positions"] = {"status": "permission_error", "ok": False, "count": 0, "error": exc.message}
    except BitgetAPIError as exc:
        result["private_positions"] = {"status": "error", "ok": False, "count": 0, "error": exc.message}
    return result


@router.get("/api/market/summary")
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


@router.post("/api/reports")
def create_report(request: ReportRequest):
    return _generate_and_store_report(request.symbol, request.timeframe)


@router.get("/api/reports/{symbol}")
def get_report(symbol: str):
    report = repository.latest_report(symbol)
    if report is None:
        report = _generate_and_store_report(symbol)
    return report


@router.post("/api/research-runs")
def create_research_run_api(request: ResearchRunRequest) -> dict:
    report = _generate_and_store_report(request.symbol, request.timeframe)
    memories = [memory.model_dump(mode="json") for memory in repository.list_decision_memories(report.symbol, limit=8)]
    run, outputs = create_research_run(repository, report, memories=memories)
    return _research_run_payload(run, outputs)


@router.get("/api/research-runs")
def list_research_runs(symbol: str | None = None, limit: int = 20) -> dict:
    runs = repository.list_research_runs(symbol=symbol, limit=limit)
    return {"research_runs": [_research_run_summary(run) for run in runs]}


@router.get("/api/research-runs/compare")
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
                "agents": _agent_summaries(repository.list_agent_outputs(run.id)),
            }
            for run in runs
        ],
    }


@router.get("/api/research-runs/{run_id}")
def get_research_run(run_id: UUID) -> dict:
    run = repository.get_research_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Research run not found")
    outputs = repository.list_agent_outputs(run.id)
    return {**_research_run_payload(run, outputs), "raw_input": run.raw_input, "raw_output": run.raw_output}


@router.post("/api/liquidity/analyze")
def analyze_liquidity_api(request: LiquidityAnalyzeRequest):
    report = _generate_and_store_report(request.symbol, request.timeframe)
    analysis = analyze_liquidation(report)
    return {
        **analysis.model_dump(mode="json"),
        "clusters": [*analysis.upper_clusters, *analysis.lower_clusters],
    }


@router.post("/api/shadow/extract")
def extract_shadow(request: ShadowExtractRequest):
    try:
        profile = extract_shadow_profile(repository.list_trades(), request)
    except ShadowSampleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    repository.add_shadow_profile(profile)
    repository.add_decision_memory(memory_from_shadow(profile))
    return profile


@router.get("/api/shadow")
def list_shadow_profiles(limit: int = 20) -> dict:
    return {"shadow_profiles": repository.list_shadow_profiles(limit=limit)}


@router.get("/api/shadow/{shadow_id}")
def get_shadow_profile(shadow_id: str):
    profile = repository.get_shadow_profile(shadow_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Shadow profile not found")
    return profile


@router.post("/api/shadow/{shadow_id}/compare")
def compare_shadow(shadow_id: str):
    profile = repository.get_shadow_profile(shadow_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Shadow profile not found")
    return compare_shadow_profile(profile, repository.list_trades())


@router.post("/api/validation/run")
def run_validation_api(request: ValidationRunRequest):
    validation_run = run_validation(repository.list_trades(), request)
    repository.add_validation_run(validation_run)
    repository.add_decision_memory(memory_from_validation(validation_run.id, validation_run.symbol, validation_run.summary, validation_run.warnings))
    return validation_run


@router.get("/api/validation/runs")
def list_validation_runs(limit: int = 20) -> dict:
    return {"validation_runs": repository.list_validation_runs(limit=limit)}


@router.get("/api/validation/runs/{run_id}")
def get_validation_run(run_id: UUID):
    validation_run = repository.get_validation_run(run_id)
    if validation_run is None:
        raise HTTPException(status_code=404, detail="Validation run not found")
    return validation_run


@router.get("/api/memory")
def list_memory(symbol: str | None = None, limit: int = 20) -> dict:
    return {"memories": repository.list_decision_memories(symbol=symbol, limit=limit)}


@router.post("/api/memory/reflect")
def reflect_memory() -> dict:
    created = 0
    for trade in repository.list_trades():
        repository.add_decision_memory(memory_from_trade(trade))
        created += 1
    return {"created": created}


@router.get("/api/positions")
def list_positions():
    return repository.list_positions()


@router.get("/api/account/bitget/positions")
def list_bitget_positions() -> dict:
    if not isinstance(market_provider, BitgetMarketDataProvider):
        return {"provider": _provider_name(), "status": "not_active", "positions": []}
    if not market_provider.client.private_configured:
        return {"provider": "bitget", "status": "not_configured", "positions": []}
    try:
        positions = market_provider.get_positions()
    except BitgetPermissionError as exc:
        return {"provider": "bitget", "status": "permission_error", "error": exc.message, "positions": []}
    except BitgetAPIError as exc:
        return {"provider": "bitget", "status": "error", "error": exc.message, "positions": []}
    return {"provider": "bitget", "status": "ok", "positions": [position.model_dump(mode="json") for position in positions]}


@router.post("/api/account/bitget/sync-positions")
def sync_bitget_positions() -> dict:
    return _sync_bitget_positions()


def _sync_bitget_positions() -> dict:
    if not isinstance(market_provider, BitgetMarketDataProvider):
        return {"provider": _provider_name(), "status": "not_active", "synced": 0, "created": 0, "updated": 0, "missing_from_exchange": 0}
    if not market_provider.client.private_configured:
        return {"provider": "bitget", "status": "not_configured", "synced": 0, "created": 0, "updated": 0, "missing_from_exchange": 0}

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
        }
    except BitgetAPIError as exc:
        return {"provider": "bitget", "status": "error", "error": exc.message, "synced": 0, "created": 0, "updated": 0, "missing_from_exchange": 0}

    created = 0
    updated = 0
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
        if position.source == "bitget" and position.status == PositionStatus.open and key not in seen_keys:
            position.status = PositionStatus.missing_from_exchange
            position.synced_at = utc_now()
            repository.update_position(position)
            missing += 1

    return {
        "provider": "bitget",
        "status": "ok",
        "synced": len(exchange_positions),
        "created": created,
        "updated": updated,
        "missing_from_exchange": missing,
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
        "agents": _agent_summaries(outputs),
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
        "agents": _agent_summaries(outputs),
        "created_at": run.created_at,
    }


def _agent_summaries(outputs) -> list[dict]:
    return [
        {
            "id": str(output.id),
            "agent": output.agent_name,
            "stance": output.stance,
            "confidence": output.confidence,
            "text_output": output.text_output,
            "raw_json": output.raw_json,
        }
        for output in outputs
    ]


def _find_bitget_position(positions: list[Position], symbol: str, direction: Direction) -> Position | None:
    for position in positions:
        if position.source == "bitget" and position.symbol == symbol and position.direction == direction:
            return position
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


@router.get("/api/live/positions")
def list_live_positions() -> dict:
    positions = [position for position in repository.list_positions() if position.status != PositionStatus.closed]
    return {
        "provider": _provider_name(),
        "positions": [_live_position_payload(position, store_snapshot=False) for position in positions],
        "open_count": len([position for position in positions if position.status == PositionStatus.open]),
        "needs_exit_record_count": len([position for position in positions if position.status in {PositionStatus.missing_from_exchange, PositionStatus.needs_exit_record}]),
        "timestamp": utc_now(),
    }


@router.post("/api/live/positions/sync")
def sync_live_positions() -> dict:
    sync_result = _sync_bitget_positions()
    positions = [position for position in repository.list_positions() if position.status != PositionStatus.closed]
    analyzed = []
    for position in positions:
        try:
            analyzed.append(_live_position_payload(position, store_snapshot=True))
        except HTTPException:
            continue
    return {**sync_result, "positions": analyzed, "timestamp": utc_now()}


@router.get("/api/live/positions/{position_id}")
def get_live_position(position_id: UUID) -> dict:
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return _live_position_detail(position)


@router.get("/api/live/positions/{position_id}/chart-analysis")
def get_position_chart_analysis(position_id: UUID, timeframe: str = "4h") -> dict:
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    try:
        snapshot = market_provider.get_snapshot(position.symbol, timeframe)
        return build_chart_analysis(position, snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/live/positions/{position_id}/snapshots")
def get_position_snapshots(position_id: UUID, limit: int = 50) -> dict:
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return {"snapshots": repository.list_position_snapshots(position_id, limit=limit)}


@router.post("/api/live/positions/{position_id}/analyze")
def analyze_live_position(position_id: UUID) -> dict:
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return _live_position_payload(position, store_snapshot=True)


@router.post("/api/live/positions/{position_id}/insight")
def create_position_insight(position_id: UUID) -> dict:
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    payload = _live_position_payload(position, store_snapshot=True)
    snapshot = PositionSnapshot.model_validate(payload["latest_snapshot"])
    previous_insights = repository.list_position_insights(position.id, limit=1)
    try:
        chart_analysis = build_chart_analysis(position, market_provider.get_snapshot(position.symbol, "4h"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MarketDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    snapshots = repository.list_position_snapshots(position.id, limit=100)
    previous_insight = previous_insights[0] if previous_insights else None
    input_json = build_position_insight_input(position, snapshot, chart_analysis, snapshots, previous_insight)
    insight = make_ai_position_insight(position, snapshot, input_json, previous_insight)
    saved_insight = repository.add_position_insight(insight)
    repository.add_position_event(
        PositionEvent(
            position_id=position.id,
            event_type="ai_insight",
            severity="low",
            title="AI Position Insight 생성",
            description=saved_insight.status_label,
            data={"insight_id": str(saved_insight.id), "snapshot_id": str(snapshot.id)},
        )
    )
    insight_status = _insight_status(saved_insight, snapshot)
    return {**payload, "latest_insight": _insight_payload(saved_insight, insight_status), "insight_status": insight_status}


@router.get("/api/live/positions/{position_id}/events")
def get_position_events(position_id: UUID, limit: int = 50) -> dict:
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return {"events": repository.list_position_events(position_id, limit=limit)}


@router.patch("/api/live/positions/{position_id}/memo")
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


@router.post("/api/live/positions/{position_id}/record-exit")
def record_live_position_exit(position_id: UUID, request: ExitRequest):
    return _record_exit(position_id, request, allow_missing=True)


def _live_position_payload(position: Position, store_snapshot: bool = False) -> dict:
    report = _generate_and_store_report(position.symbol)
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
    insights = repository.list_position_insights(position.id, limit=20)
    return {
        **payload,
        "snapshots": repository.list_position_snapshots(position.id, limit=50),
        "insights": [_insight_payload(insight, _insight_status(insight, snapshot)) for insight in insights],
        "events": repository.list_position_events(position.id, limit=50),
        "monitoring_logs": repository.list_monitoring_logs(position.id, limit=30),
    }


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


@router.post("/api/positions")
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


@router.post("/api/positions/{position_id}/monitor")
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


@router.post("/api/positions/{position_id}/exit")
def exit_position(position_id: UUID, request: ExitRequest):
    return _record_exit(position_id, request, allow_missing=False)


def _record_exit(position_id: UUID, request: ExitRequest, allow_missing: bool = False):
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    allowed_statuses = {PositionStatus.open}
    if allow_missing:
        allowed_statuses.update({PositionStatus.missing_from_exchange, PositionStatus.needs_exit_record})
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
    trade.review_text = render_review(trade)
    position.status = PositionStatus.closed
    position.closed_at = datetime.now(timezone.utc)
    position.current_price = request.exit_price
    position.current_score = report.entry_score
    position.pnl_percent = trade.pnl_percent
    repository.update_position(position)
    saved_trade = repository.add_trade(trade)
    repository.add_decision_memory(memory_from_trade(saved_trade))
    repository.add_position_event(
        PositionEvent(
            position_id=position.id,
            event_type="exit_recorded",
            severity="medium",
            title="청산 기록 완료",
            description=request.exit_reason,
            data={"trade_id": str(saved_trade.id), "exit_price": request.exit_price, "pnl_percent": saved_trade.pnl_percent},
        )
    )
    return saved_trade


@router.post("/api/trades/{trade_id}/review")
def review_trade(trade_id: UUID):
    trade = repository.get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    trade.review_text = render_review(trade)
    repository.add_trade(trade)
    repository.add_decision_memory(memory_from_trade(trade))
    return trade


@router.get("/api/trades")
def list_trades():
    return repository.list_trades()


@router.get("/api/trades/{trade_id}")
def get_trade(trade_id: UUID):
    trade = repository.get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade


@router.get("/api/trades/{trade_id}/timeline")
def get_trade_timeline(trade_id: UUID) -> dict:
    trade = repository.get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return {
        "trade": trade,
        "snapshots": repository.list_position_snapshots(trade.position_id, limit=100),
        "events": repository.list_position_events(trade.position_id, limit=100),
        "monitoring_logs": repository.list_monitoring_logs(trade.position_id, limit=100),
    }


@router.patch("/api/trades/{trade_id}/memo")
def update_trade_memo(trade_id: UUID, request: TradeMemoUpdate):
    trade = repository.get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    trade.memo = request.memo
    return repository.add_trade(trade)
