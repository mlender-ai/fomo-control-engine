from uuid import UUID

from app.db.models import MonitoringLog, Position, PositionStatus, Report, Trade


class MemoryRepository:
    def __init__(self) -> None:
        self.reports: dict[UUID, Report] = {}
        self.reports_by_symbol: dict[str, list[UUID]] = {}
        self.positions: dict[UUID, Position] = {}
        self.monitoring_logs: dict[UUID, list[MonitoringLog]] = {}
        self.trades: dict[UUID, Trade] = {}

    def add_report(self, report: Report) -> Report:
        self.reports[report.id] = report
        symbol = report.symbol.upper()
        self.reports_by_symbol.setdefault(symbol, []).insert(0, report.id)
        return report

    def latest_report(self, symbol: str) -> Report | None:
        report_ids = self.reports_by_symbol.get(symbol.upper(), [])
        return self.reports[report_ids[0]] if report_ids else None

    def recent_reports(self, limit: int = 8) -> list[Report]:
        return sorted(self.reports.values(), key=lambda item: item.created_at, reverse=True)[:limit]

    def add_position(self, position: Position) -> Position:
        self.positions[position.id] = position
        return position

    def list_positions(self, status: PositionStatus | None = None) -> list[Position]:
        positions = list(self.positions.values())
        if status:
            positions = [position for position in positions if position.status == status]
        return sorted(positions, key=lambda item: item.opened_at, reverse=True)

    def get_position(self, position_id: UUID) -> Position | None:
        return self.positions.get(position_id)

    def update_position(self, position: Position) -> Position:
        self.positions[position.id] = position
        return position

    def add_monitoring_log(self, log: MonitoringLog) -> MonitoringLog:
        self.monitoring_logs.setdefault(log.position_id, []).insert(0, log)
        return log

    def add_trade(self, trade: Trade) -> Trade:
        self.trades[trade.id] = trade
        return trade

    def list_trades(self) -> list[Trade]:
        return sorted(self.trades.values(), key=lambda item: item.created_at, reverse=True)


repository = MemoryRepository()

