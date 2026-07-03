from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.db.models import Direction, ExitRequest, Position, PositionCreate, PositionStatus, ReportRequest, Trade, utc_now
from app.db.repository import Repository, create_repository
from app.exchange.base import MarketDataProvider
from app.exchange.bitget.errors import BitgetAPIError, BitgetNotConfiguredError, BitgetPermissionError
from app.exchange.bitget.provider import BitgetMarketDataProvider
from app.exchange.bitget.schemas import BitgetPosition
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
        liquidation_price=exchange_position.liquidation_price,
        margin_mode=exchange_position.margin_mode,
        position_mode=exchange_position.position_mode,
        margin_ratio=exchange_position.margin_ratio,
        break_even_price=exchange_position.break_even_price,
        source="bitget",
        synced_at=utc_now(),
        opened_at=exchange_position.created_at or utc_now(),
        memo="Synced from Bitget read-only position API",
    )
    if exchange_position.mark_price:
        position.pnl_percent = round(calculate_pnl(position, exchange_position.mark_price), 2)
    return position


def _merge_bitget_position(position: Position, exchange_position: BitgetPosition) -> Position:
    position.status = PositionStatus.open
    position.entry_price = exchange_position.open_price_avg
    position.quantity = exchange_position.total
    position.leverage = exchange_position.leverage or position.leverage
    position.current_price = exchange_position.mark_price
    position.mark_price = exchange_position.mark_price
    position.unrealized_pl = exchange_position.unrealized_pl
    position.liquidation_price = exchange_position.liquidation_price
    position.margin_mode = exchange_position.margin_mode
    position.position_mode = exchange_position.position_mode
    position.margin_ratio = exchange_position.margin_ratio
    position.break_even_price = exchange_position.break_even_price
    position.source = "bitget"
    position.synced_at = utc_now()
    if exchange_position.mark_price:
        position.pnl_percent = round(calculate_pnl(position, exchange_position.mark_price), 2)
    return position


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
