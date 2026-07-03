from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.db.models import ExitRequest, Position, PositionCreate, PositionStatus, ReportRequest, Trade
from app.db.repository import Repository, create_repository
from app.exchange.base import MarketDataProvider
from app.exchange.errors import MarketDataError
from app.exchange.factory import create_market_data_provider
from app.monitoring.engine import build_monitoring_log, calculate_pnl
from app.report.engine import generate_report
from app.review.engine import render_review

router = APIRouter()
settings = get_settings()
repository: Repository = create_repository(settings.database_url)
market_provider: MarketDataProvider = create_market_data_provider(settings)


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


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "fomo-control-engine"}


@router.get("/api/system/status")
def system_status() -> dict:
    return {
        "service": "fomo-control-engine",
        "environment": settings.env,
        "market_data_provider": settings.market_data_provider,
        "database_url": settings.database_url,
        "default_symbols": settings.symbol_list,
    }


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
        "market_data_provider": settings.market_data_provider,
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


@router.get("/api/positions")
def list_positions():
    return repository.list_positions()


@router.post("/api/positions")
def create_position(request: PositionCreate):
    report = repository.get_report(request.entry_report_id) if request.entry_report_id else repository.latest_report(request.symbol)
    position = Position(
        symbol=request.symbol.upper(),
        direction=request.direction,
        entry_price=request.entry_price,
        quantity=request.quantity,
        leverage=request.leverage,
        entry_report_id=report.id if report else None,
        entry_score=report.entry_score if report else None,
        current_score=report.entry_score if report else None,
        current_price=report.price if report else request.entry_price,
        memo=request.memo,
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
    repository.update_position(position)
    repository.add_monitoring_log(log)
    return log


@router.post("/api/positions/{position_id}/exit")
def exit_position(position_id: UUID, request: ExitRequest):
    position = repository.get_position(position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    if position.status != PositionStatus.open:
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
    )
    trade.review_text = render_review(trade)
    position.status = PositionStatus.closed
    position.closed_at = datetime.now(timezone.utc)
    position.current_price = request.exit_price
    position.current_score = report.entry_score
    position.pnl_percent = trade.pnl_percent
    repository.update_position(position)
    return repository.add_trade(trade)


@router.get("/api/trades")
def list_trades():
    return repository.list_trades()
