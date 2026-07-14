from __future__ import annotations

from uuid import UUID
from app.db.models import AlertRecord, AlertResponseRecord, MonitoringLog, Position, PositionEvent, PositionInsight, PositionSnapshot, PositionStatus, Report
from .base import _dump_model


class MemoryPositionsRepositoryMixin:
    def add_report(self, report: Report) -> Report:
        self.reports[report.id] = report
        symbol = report.symbol.upper()
        self.reports_by_symbol.setdefault(symbol, []).insert(0, report.id)
        return report

    def get_report(self, report_id: UUID) -> Report | None:
        return self.reports.get(report_id)

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

    def list_monitoring_logs(self, position_id: UUID, limit: int = 50) -> list[MonitoringLog]:
        return self.monitoring_logs.get(position_id, [])[:limit]

    def add_position_snapshot(self, snapshot: PositionSnapshot) -> PositionSnapshot:
        self.position_snapshots.setdefault(snapshot.position_id, []).insert(0, snapshot)
        return snapshot

    def list_position_snapshots(self, position_id: UUID, limit: int = 50) -> list[PositionSnapshot]:
        return self.position_snapshots.get(position_id, [])[:limit]

    def add_position_insight(self, insight: PositionInsight) -> PositionInsight:
        self.position_insights.setdefault(insight.position_id, []).insert(0, insight)
        return insight

    def list_position_insights(self, position_id: UUID, limit: int = 20) -> list[PositionInsight]:
        return self.position_insights.get(position_id, [])[:limit]

    def add_position_event(self, event: PositionEvent) -> PositionEvent:
        self.position_events.setdefault(event.position_id, []).insert(0, event)
        return event

    def list_position_events(self, position_id: UUID, limit: int = 50) -> list[PositionEvent]:
        return self.position_events.get(position_id, [])[:limit]

    def add_alert(self, alert: AlertRecord) -> AlertRecord:
        self.alerts[alert.id] = alert
        return alert

    def list_alerts(self, position_id: UUID | None = None, limit: int = 100) -> list[AlertRecord]:
        alerts = list(self.alerts.values())
        if position_id is not None:
            alerts = [alert for alert in alerts if alert.position_id == position_id]
        return sorted(alerts, key=lambda item: item.fired_at, reverse=True)[:limit]

    def add_alert_response(self, response: AlertResponseRecord) -> AlertResponseRecord:
        existing = self.get_alert_response(response.alert_id)
        if existing is not None and existing.id != response.id:
            self.alert_responses.pop(existing.id, None)
        self.alert_responses[response.id] = response
        return response

    def get_alert_response(self, alert_id: UUID) -> AlertResponseRecord | None:
        return next(
            (response for response in self.alert_responses.values() if response.alert_id == alert_id),
            None,
        )

    def list_alert_responses(
        self,
        position_id: UUID | None = None,
        rule_id: str | None = None,
        limit: int = 200,
    ) -> list[AlertResponseRecord]:
        responses = list(self.alert_responses.values())
        if position_id is not None:
            responses = [response for response in responses if response.position_id == position_id]
        if rule_id is not None:
            responses = [response for response in responses if response.rule_id == rule_id]
        return sorted(responses, key=lambda item: item.detected_at, reverse=True)[:limit]


class SQLitePositionsRepositoryMixin:
    def add_report(self, report: Report) -> Report:
        payload = _dump_model(report)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO reports
                    (id, symbol, timeframe, entry_score, fomo_index, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(report.id),
                    report.symbol.upper(),
                    report.timeframe,
                    report.entry_score,
                    report.scores.fomo,
                    report.created_at.isoformat(),
                    payload,
                ),
            )
        return report

    def get_report(self, report_id: UUID) -> Report | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM reports WHERE id = ?", (str(report_id),)).fetchone()
        return Report.model_validate_json(row["payload"]) if row else None

    def latest_report(self, symbol: str) -> Report | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM reports WHERE symbol = ? ORDER BY created_at DESC LIMIT 1",
                (symbol.upper(),),
            ).fetchone()
        return Report.model_validate_json(row["payload"]) if row else None

    def recent_reports(self, limit: int = 8) -> list[Report]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM reports ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [Report.model_validate_json(row["payload"]) for row in rows]

    def add_position(self, position: Position) -> Position:
        return self._upsert_position(position)

    def list_positions(self, status: PositionStatus | None = None) -> list[Position]:
        query = "SELECT payload FROM positions"
        params: tuple[str, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status.value,)
        query += " ORDER BY opened_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [Position.model_validate_json(row["payload"]) for row in rows]

    def get_position(self, position_id: UUID) -> Position | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM positions WHERE id = ?", (str(position_id),)).fetchone()
        return Position.model_validate_json(row["payload"]) if row else None

    def update_position(self, position: Position) -> Position:
        return self._upsert_position(position)

    def add_monitoring_log(self, log: MonitoringLog) -> MonitoringLog:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO monitoring_logs
                    (id, position_id, created_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(log.id),
                    str(log.position_id),
                    log.created_at.isoformat(),
                    _dump_model(log),
                ),
            )
        return log

    def list_monitoring_logs(self, position_id: UUID, limit: int = 50) -> list[MonitoringLog]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM monitoring_logs WHERE position_id = ? ORDER BY created_at DESC LIMIT ?",
                (str(position_id), limit),
            ).fetchall()
        return [MonitoringLog.model_validate_json(row["payload"]) for row in rows]

    def add_position_snapshot(self, snapshot: PositionSnapshot) -> PositionSnapshot:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO position_snapshots
                    (id, position_id, symbol, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(snapshot.id),
                    str(snapshot.position_id),
                    snapshot.symbol.upper(),
                    snapshot.created_at.isoformat(),
                    _dump_model(snapshot),
                ),
            )
        return snapshot

    def list_position_snapshots(self, position_id: UUID, limit: int = 50) -> list[PositionSnapshot]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM position_snapshots WHERE position_id = ? ORDER BY created_at DESC LIMIT ?",
                (str(position_id), limit),
            ).fetchall()
        return [PositionSnapshot.model_validate_json(row["payload"]) for row in rows]

    def add_position_insight(self, insight: PositionInsight) -> PositionInsight:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO position_insights
                    (id, position_id, snapshot_id, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(insight.id),
                    str(insight.position_id),
                    str(insight.snapshot_id) if insight.snapshot_id else None,
                    insight.created_at.isoformat(),
                    _dump_model(insight),
                ),
            )
        return insight

    def list_position_insights(self, position_id: UUID, limit: int = 20) -> list[PositionInsight]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM position_insights WHERE position_id = ? ORDER BY created_at DESC LIMIT ?",
                (str(position_id), limit),
            ).fetchall()
        return [PositionInsight.model_validate_json(row["payload"]) for row in rows]

    def add_position_event(self, event: PositionEvent) -> PositionEvent:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO position_events
                    (id, position_id, event_type, severity, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.id),
                    str(event.position_id),
                    event.event_type,
                    event.severity,
                    event.created_at.isoformat(),
                    _dump_model(event),
                ),
            )
        return event

    def list_position_events(self, position_id: UUID, limit: int = 50) -> list[PositionEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM position_events WHERE position_id = ? ORDER BY created_at DESC LIMIT ?",
                (str(position_id), limit),
            ).fetchall()
        return [PositionEvent.model_validate_json(row["payload"]) for row in rows]

    def add_alert(self, alert: AlertRecord) -> AlertRecord:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO alerts
                    (id, rule_id, position_id, symbol, severity, fired_at, delivered, acked, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(alert.id),
                    alert.rule_id,
                    str(alert.position_id) if alert.position_id else None,
                    alert.symbol.upper(),
                    alert.severity,
                    alert.fired_at.isoformat(),
                    1 if alert.delivered else 0,
                    1 if alert.acked else 0,
                    alert.created_at.isoformat(),
                    _dump_model(alert),
                ),
            )
        return alert

    def list_alerts(self, position_id: UUID | None = None, limit: int = 100) -> list[AlertRecord]:
        query = "SELECT payload FROM alerts"
        params: tuple[str | int, ...]
        if position_id is not None:
            query += " WHERE position_id = ?"
            params = (str(position_id), limit)
        else:
            params = (limit,)
        query += " ORDER BY fired_at DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [AlertRecord.model_validate_json(row["payload"]) for row in rows]

    def add_alert_response(self, response: AlertResponseRecord) -> AlertResponseRecord:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM alert_responses WHERE alert_id = ? AND id != ?",
                (str(response.alert_id), str(response.id)),
            )
            connection.execute(
                """
                INSERT OR REPLACE INTO alert_responses
                    (id, alert_id, position_id, rule_id, symbol, response, detected_at, outcome, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(response.id),
                    str(response.alert_id),
                    str(response.position_id),
                    response.rule_id,
                    response.symbol.upper(),
                    response.response,
                    response.detected_at.isoformat(),
                    response.outcome,
                    response.created_at.isoformat(),
                    _dump_model(response),
                ),
            )
        return response

    def get_alert_response(self, alert_id: UUID) -> AlertResponseRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM alert_responses WHERE alert_id = ? LIMIT 1",
                (str(alert_id),),
            ).fetchone()
        return AlertResponseRecord.model_validate_json(row["payload"]) if row else None

    def list_alert_responses(
        self,
        position_id: UUID | None = None,
        rule_id: str | None = None,
        limit: int = 200,
    ) -> list[AlertResponseRecord]:
        query = "SELECT payload FROM alert_responses"
        clauses: list[str] = []
        params: list[str | int] = []
        if position_id is not None:
            clauses.append("position_id = ?")
            params.append(str(position_id))
        if rule_id is not None:
            clauses.append("rule_id = ?")
            params.append(rule_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY detected_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [AlertResponseRecord.model_validate_json(row["payload"]) for row in rows]

    def _upsert_position(self, position: Position) -> Position:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO positions
                    (id, symbol, status, opened_at, closed_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(position.id),
                    position.symbol.upper(),
                    position.status.value,
                    position.opened_at.isoformat(),
                    position.closed_at.isoformat() if position.closed_at else None,
                    _dump_model(position),
                ),
            )
        return position
