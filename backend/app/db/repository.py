from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.db.models import (
    CatalogSymbol,
    AgentOutput,
    CalibrationSuggestion,
    DecisionMemory,
    JudgmentLedgerEntry,
    JudgmentScore,
    MarketSnapshotRecord,
    MonitoringLog,
    Position,
    PositionEvent,
    PositionInsight,
    PositionSnapshot,
    PositionStatus,
    Report,
    ResearchRun,
    ShadowProfile,
    Trade,
    ValidationRun,
    WatchlistItem,
)


class Repository(Protocol):
    def add_report(self, report: Report) -> Report: ...
    def get_report(self, report_id: UUID) -> Report | None: ...
    def latest_report(self, symbol: str) -> Report | None: ...
    def recent_reports(self, limit: int = 8) -> list[Report]: ...
    def add_position(self, position: Position) -> Position: ...
    def list_positions(self, status: PositionStatus | None = None) -> list[Position]: ...
    def get_position(self, position_id: UUID) -> Position | None: ...
    def update_position(self, position: Position) -> Position: ...
    def add_monitoring_log(self, log: MonitoringLog) -> MonitoringLog: ...
    def list_monitoring_logs(self, position_id: UUID, limit: int = 50) -> list[MonitoringLog]: ...
    def add_position_snapshot(self, snapshot: PositionSnapshot) -> PositionSnapshot: ...
    def list_position_snapshots(self, position_id: UUID, limit: int = 50) -> list[PositionSnapshot]: ...
    def add_position_insight(self, insight: PositionInsight) -> PositionInsight: ...
    def list_position_insights(self, position_id: UUID, limit: int = 20) -> list[PositionInsight]: ...
    def add_position_event(self, event: PositionEvent) -> PositionEvent: ...
    def list_position_events(self, position_id: UUID, limit: int = 50) -> list[PositionEvent]: ...
    def add_trade(self, trade: Trade) -> Trade: ...
    def get_trade(self, trade_id: UUID) -> Trade | None: ...
    def list_trades(self) -> list[Trade]: ...
    def add_judgment(self, judgment: JudgmentLedgerEntry) -> JudgmentLedgerEntry: ...
    def list_judgments(self, position_id: UUID, limit: int = 200) -> list[JudgmentLedgerEntry]: ...
    def add_judgment_score(self, score: JudgmentScore) -> JudgmentScore: ...
    def list_judgment_scores(self, position_id: UUID | None = None, trade_id: UUID | None = None, limit: int = 500) -> list[JudgmentScore]: ...
    def add_calibration_suggestion(self, suggestion: CalibrationSuggestion) -> CalibrationSuggestion: ...
    def get_calibration_suggestion(self, suggestion_id: UUID) -> CalibrationSuggestion | None: ...
    def list_calibration_suggestions(self, status: str | None = None, limit: int = 50) -> list[CalibrationSuggestion]: ...
    def add_market_snapshot(self, snapshot: MarketSnapshotRecord) -> MarketSnapshotRecord: ...
    def add_research_run(self, run: ResearchRun) -> ResearchRun: ...
    def get_research_run(self, run_id: UUID) -> ResearchRun | None: ...
    def list_research_runs(self, symbol: str | None = None, limit: int = 20) -> list[ResearchRun]: ...
    def add_agent_output(self, output: AgentOutput) -> AgentOutput: ...
    def list_agent_outputs(self, research_run_id: UUID) -> list[AgentOutput]: ...
    def add_shadow_profile(self, profile: ShadowProfile) -> ShadowProfile: ...
    def get_shadow_profile(self, shadow_id: str) -> ShadowProfile | None: ...
    def list_shadow_profiles(self, limit: int = 20) -> list[ShadowProfile]: ...
    def add_decision_memory(self, memory: DecisionMemory) -> DecisionMemory: ...
    def list_decision_memories(self, symbol: str | None = None, limit: int = 20) -> list[DecisionMemory]: ...
    def add_validation_run(self, run: ValidationRun) -> ValidationRun: ...
    def get_validation_run(self, run_id: UUID) -> ValidationRun | None: ...
    def list_validation_runs(self, limit: int = 20) -> list[ValidationRun]: ...
    def list_watchlist(self) -> list[WatchlistItem]: ...
    def upsert_watchlist_item(self, item: WatchlistItem) -> WatchlistItem: ...
    def remove_watchlist_item(self, symbol: str) -> bool: ...
    def replace_symbol_catalog(self, symbols: list[CatalogSymbol]) -> int: ...
    def search_symbols(self, query: str, limit: int = 20) -> list[CatalogSymbol]: ...
    def symbol_catalog_updated_at(self) -> datetime | None: ...


class MemoryRepository:
    def __init__(self) -> None:
        self.reports: dict[UUID, Report] = {}
        self.reports_by_symbol: dict[str, list[UUID]] = {}
        self.positions: dict[UUID, Position] = {}
        self.monitoring_logs: dict[UUID, list[MonitoringLog]] = {}
        self.position_snapshots: dict[UUID, list[PositionSnapshot]] = {}
        self.position_insights: dict[UUID, list[PositionInsight]] = {}
        self.position_events: dict[UUID, list[PositionEvent]] = {}
        self.trades: dict[UUID, Trade] = {}
        self.judgments: dict[UUID, list[JudgmentLedgerEntry]] = {}
        self.judgment_scores: dict[UUID, JudgmentScore] = {}
        self.calibration_suggestions: dict[UUID, CalibrationSuggestion] = {}
        self.market_snapshots: dict[UUID, MarketSnapshotRecord] = {}
        self.research_runs: dict[UUID, ResearchRun] = {}
        self.agent_outputs: dict[UUID, list[AgentOutput]] = {}
        self.shadow_profiles: dict[str, ShadowProfile] = {}
        self.decision_memories: dict[UUID, DecisionMemory] = {}
        self.validation_runs: dict[UUID, ValidationRun] = {}
        self.watchlist: dict[str, WatchlistItem] = {}
        self.symbol_catalog: dict[str, CatalogSymbol] = {}

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

    def add_trade(self, trade: Trade) -> Trade:
        self.trades[trade.id] = trade
        return trade

    def get_trade(self, trade_id: UUID) -> Trade | None:
        return self.trades.get(trade_id)

    def list_trades(self) -> list[Trade]:
        return sorted(self.trades.values(), key=lambda item: item.created_at, reverse=True)

    def add_judgment(self, judgment: JudgmentLedgerEntry) -> JudgmentLedgerEntry:
        entries = self.judgments.setdefault(judgment.position_id, [])
        entries = [item for item in entries if item.id != judgment.id and item.judgment_id != judgment.judgment_id]
        entries.insert(0, judgment)
        self.judgments[judgment.position_id] = entries
        return judgment

    def list_judgments(self, position_id: UUID, limit: int = 200) -> list[JudgmentLedgerEntry]:
        return sorted(self.judgments.get(position_id, []), key=lambda item: item.as_of, reverse=True)[:limit]

    def add_judgment_score(self, score: JudgmentScore) -> JudgmentScore:
        self.judgment_scores[score.id] = score
        return score

    def list_judgment_scores(self, position_id: UUID | None = None, trade_id: UUID | None = None, limit: int = 500) -> list[JudgmentScore]:
        scores = list(self.judgment_scores.values())
        if position_id is not None:
            scores = [score for score in scores if score.position_id == position_id]
        if trade_id is not None:
            scores = [score for score in scores if score.trade_id == trade_id]
        return sorted(scores, key=lambda item: item.created_at, reverse=True)[:limit]

    def add_calibration_suggestion(self, suggestion: CalibrationSuggestion) -> CalibrationSuggestion:
        self.calibration_suggestions[suggestion.id] = suggestion
        return suggestion

    def get_calibration_suggestion(self, suggestion_id: UUID) -> CalibrationSuggestion | None:
        return self.calibration_suggestions.get(suggestion_id)

    def list_calibration_suggestions(self, status: str | None = None, limit: int = 50) -> list[CalibrationSuggestion]:
        suggestions = list(self.calibration_suggestions.values())
        if status:
            suggestions = [suggestion for suggestion in suggestions if suggestion.status == status]
        return sorted(suggestions, key=lambda item: item.created_at, reverse=True)[:limit]

    def add_market_snapshot(self, snapshot: MarketSnapshotRecord) -> MarketSnapshotRecord:
        self.market_snapshots[snapshot.id] = snapshot
        return snapshot

    def add_research_run(self, run: ResearchRun) -> ResearchRun:
        self.research_runs[run.id] = run
        return run

    def get_research_run(self, run_id: UUID) -> ResearchRun | None:
        return self.research_runs.get(run_id)

    def list_research_runs(self, symbol: str | None = None, limit: int = 20) -> list[ResearchRun]:
        runs = list(self.research_runs.values())
        if symbol:
            runs = [run for run in runs if run.symbol == symbol.upper()]
        return sorted(runs, key=lambda item: item.created_at, reverse=True)[:limit]

    def add_agent_output(self, output: AgentOutput) -> AgentOutput:
        self.agent_outputs.setdefault(output.research_run_id, []).append(output)
        return output

    def list_agent_outputs(self, research_run_id: UUID) -> list[AgentOutput]:
        return sorted(self.agent_outputs.get(research_run_id, []), key=lambda item: item.created_at)

    def add_shadow_profile(self, profile: ShadowProfile) -> ShadowProfile:
        self.shadow_profiles[profile.shadow_id] = profile
        return profile

    def get_shadow_profile(self, shadow_id: str) -> ShadowProfile | None:
        return self.shadow_profiles.get(shadow_id)

    def list_shadow_profiles(self, limit: int = 20) -> list[ShadowProfile]:
        return sorted(self.shadow_profiles.values(), key=lambda item: item.created_at, reverse=True)[:limit]

    def add_decision_memory(self, memory: DecisionMemory) -> DecisionMemory:
        self.decision_memories[memory.id] = memory
        return memory

    def list_decision_memories(self, symbol: str | None = None, limit: int = 20) -> list[DecisionMemory]:
        memories = list(self.decision_memories.values())
        if symbol:
            memories = [memory for memory in memories if memory.symbol in {symbol.upper(), None}]
        return sorted(memories, key=lambda item: item.created_at, reverse=True)[:limit]

    def add_validation_run(self, run: ValidationRun) -> ValidationRun:
        self.validation_runs[run.id] = run
        return run

    def get_validation_run(self, run_id: UUID) -> ValidationRun | None:
        return self.validation_runs.get(run_id)

    def list_validation_runs(self, limit: int = 20) -> list[ValidationRun]:
        return sorted(self.validation_runs.values(), key=lambda item: item.created_at, reverse=True)[:limit]

    def list_watchlist(self) -> list[WatchlistItem]:
        return sorted(self.watchlist.values(), key=lambda item: item.added_at, reverse=True)

    def upsert_watchlist_item(self, item: WatchlistItem) -> WatchlistItem:
        self.watchlist[item.symbol.upper()] = item
        return item

    def remove_watchlist_item(self, symbol: str) -> bool:
        return self.watchlist.pop(symbol.upper(), None) is not None

    def replace_symbol_catalog(self, symbols: list[CatalogSymbol]) -> int:
        self.symbol_catalog = {item.symbol.upper(): item for item in symbols}
        return len(self.symbol_catalog)

    def search_symbols(self, query: str, limit: int = 20) -> list[CatalogSymbol]:
        needle = query.strip().upper()
        matches = [item for symbol, item in self.symbol_catalog.items() if needle in symbol] if needle else list(self.symbol_catalog.values())
        return sorted(matches, key=lambda item: (len(item.symbol), item.symbol))[:limit]

    def symbol_catalog_updated_at(self) -> datetime | None:
        if not self.symbol_catalog:
            return None
        return max(item.updated_at for item in self.symbol_catalog.values())


class SQLiteRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._lock = threading.RLock()
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    entry_score INTEGER NOT NULL,
                    fomo_index INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reports_symbol_created
                    ON reports(symbol, created_at DESC);

                CREATE TABLE IF NOT EXISTS positions (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    status TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_positions_status_opened
                    ON positions(status, opened_at DESC);

                CREATE TABLE IF NOT EXISTS monitoring_logs (
                    id TEXT PRIMARY KEY,
                    position_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_monitoring_position_created
                    ON monitoring_logs(position_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS position_snapshots (
                    id TEXT PRIMARY KEY,
                    position_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_position_snapshots_position_created
                    ON position_snapshots(position_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS position_insights (
                    id TEXT PRIMARY KEY,
                    position_id TEXT NOT NULL,
                    snapshot_id TEXT,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_position_insights_position_created
                    ON position_insights(position_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS position_events (
                    id TEXT PRIMARY KEY,
                    position_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_position_events_position_created
                    ON position_events(position_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS trades (
                    id TEXT PRIMARY KEY,
                    position_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trades_created
                    ON trades(created_at DESC);

                CREATE TABLE IF NOT EXISTS judgment_ledger (
                    id TEXT PRIMARY KEY,
                    position_id TEXT NOT NULL,
                    judgment_id TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_judgment_ledger_judgment_id
                    ON judgment_ledger(judgment_id);
                CREATE INDEX IF NOT EXISTS idx_judgment_ledger_position_asof
                    ON judgment_ledger(position_id, as_of DESC);

                CREATE TABLE IF NOT EXISTS judgment_scores (
                    id TEXT PRIMARY KEY,
                    position_id TEXT NOT NULL,
                    trade_id TEXT,
                    judgment_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_judgment_scores_position_created
                    ON judgment_scores(position_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_judgment_scores_trade_created
                    ON judgment_scores(trade_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS calibration_suggestions (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_calibration_suggestions_status_created
                    ON calibration_suggestions(status, created_at DESC);

                CREATE TABLE IF NOT EXISTS market_snapshots (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_market_snapshots_symbol_created
                    ON market_snapshots(symbol, created_at DESC);

                CREATE TABLE IF NOT EXISTS research_runs (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    report_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_research_runs_symbol_created
                    ON research_runs(symbol, created_at DESC);

                CREATE TABLE IF NOT EXISTS agent_outputs (
                    id TEXT PRIMARY KEY,
                    research_run_id TEXT NOT NULL,
                    agent_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_agent_outputs_run_created
                    ON agent_outputs(research_run_id, created_at ASC);

                CREATE TABLE IF NOT EXISTS shadow_profiles (
                    shadow_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS decision_memories (
                    id TEXT PRIMARY KEY,
                    symbol TEXT,
                    memory_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_decision_memories_symbol_created
                    ON decision_memories(symbol, created_at DESC);

                CREATE TABLE IF NOT EXISTS validation_runs (
                    id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    strategy_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_validation_runs_created
                    ON validation_runs(created_at DESC);
                CREATE TABLE IF NOT EXISTS watchlist (
                    symbol TEXT PRIMARY KEY,
                    added_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS symbol_catalog (
                    symbol TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                """
            )

    def add_report(self, report: Report) -> Report:
        payload = _dump_model(report)
        with self._lock, self._connect() as connection:
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
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT payload FROM reports WHERE id = ?", (str(report_id),)).fetchone()
        return Report.model_validate_json(row["payload"]) if row else None

    def latest_report(self, symbol: str) -> Report | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM reports WHERE symbol = ? ORDER BY created_at DESC LIMIT 1",
                (symbol.upper(),),
            ).fetchone()
        return Report.model_validate_json(row["payload"]) if row else None

    def recent_reports(self, limit: int = 8) -> list[Report]:
        with self._lock, self._connect() as connection:
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
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [Position.model_validate_json(row["payload"]) for row in rows]

    def get_position(self, position_id: UUID) -> Position | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT payload FROM positions WHERE id = ?", (str(position_id),)).fetchone()
        return Position.model_validate_json(row["payload"]) if row else None

    def update_position(self, position: Position) -> Position:
        return self._upsert_position(position)

    def add_monitoring_log(self, log: MonitoringLog) -> MonitoringLog:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO monitoring_logs
                    (id, position_id, created_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (str(log.id), str(log.position_id), log.created_at.isoformat(), _dump_model(log)),
            )
        return log

    def list_monitoring_logs(self, position_id: UUID, limit: int = 50) -> list[MonitoringLog]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM monitoring_logs WHERE position_id = ? ORDER BY created_at DESC LIMIT ?",
                (str(position_id), limit),
            ).fetchall()
        return [MonitoringLog.model_validate_json(row["payload"]) for row in rows]

    def add_position_snapshot(self, snapshot: PositionSnapshot) -> PositionSnapshot:
        with self._lock, self._connect() as connection:
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
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM position_snapshots WHERE position_id = ? ORDER BY created_at DESC LIMIT ?",
                (str(position_id), limit),
            ).fetchall()
        return [PositionSnapshot.model_validate_json(row["payload"]) for row in rows]

    def add_position_insight(self, insight: PositionInsight) -> PositionInsight:
        with self._lock, self._connect() as connection:
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
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM position_insights WHERE position_id = ? ORDER BY created_at DESC LIMIT ?",
                (str(position_id), limit),
            ).fetchall()
        return [PositionInsight.model_validate_json(row["payload"]) for row in rows]

    def add_position_event(self, event: PositionEvent) -> PositionEvent:
        with self._lock, self._connect() as connection:
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
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM position_events WHERE position_id = ? ORDER BY created_at DESC LIMIT ?",
                (str(position_id), limit),
            ).fetchall()
        return [PositionEvent.model_validate_json(row["payload"]) for row in rows]

    def add_trade(self, trade: Trade) -> Trade:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO trades
                    (id, position_id, symbol, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(trade.id), str(trade.position_id), trade.symbol.upper(), trade.created_at.isoformat(), _dump_model(trade)),
            )
        return trade

    def get_trade(self, trade_id: UUID) -> Trade | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT payload FROM trades WHERE id = ?", (str(trade_id),)).fetchone()
        return Trade.model_validate_json(row["payload"]) if row else None

    def list_trades(self) -> list[Trade]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT payload FROM trades ORDER BY created_at DESC").fetchall()
        return [Trade.model_validate_json(row["payload"]) for row in rows]

    def add_judgment(self, judgment: JudgmentLedgerEntry) -> JudgmentLedgerEntry:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO judgment_ledger
                    (id, position_id, judgment_id, as_of, type, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(judgment.id),
                    str(judgment.position_id),
                    judgment.judgment_id,
                    judgment.as_of.isoformat(),
                    judgment.type,
                    judgment.created_at.isoformat(),
                    _dump_model(judgment),
                ),
            )
        return judgment

    def list_judgments(self, position_id: UUID, limit: int = 200) -> list[JudgmentLedgerEntry]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM judgment_ledger WHERE position_id = ? ORDER BY as_of DESC LIMIT ?",
                (str(position_id), limit),
            ).fetchall()
        return [JudgmentLedgerEntry.model_validate_json(row["payload"]) for row in rows]

    def add_judgment_score(self, score: JudgmentScore) -> JudgmentScore:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO judgment_scores
                    (id, position_id, trade_id, judgment_id, outcome, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(score.id),
                    str(score.position_id),
                    str(score.trade_id) if score.trade_id else None,
                    score.judgment_id,
                    score.outcome,
                    score.created_at.isoformat(),
                    _dump_model(score),
                ),
            )
        return score

    def list_judgment_scores(self, position_id: UUID | None = None, trade_id: UUID | None = None, limit: int = 500) -> list[JudgmentScore]:
        query = "SELECT payload FROM judgment_scores"
        clauses: list[str] = []
        params: list[str | int] = []
        if position_id is not None:
            clauses.append("position_id = ?")
            params.append(str(position_id))
        if trade_id is not None:
            clauses.append("trade_id = ?")
            params.append(str(trade_id))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [JudgmentScore.model_validate_json(row["payload"]) for row in rows]

    def add_calibration_suggestion(self, suggestion: CalibrationSuggestion) -> CalibrationSuggestion:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO calibration_suggestions
                    (id, status, created_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (str(suggestion.id), suggestion.status, suggestion.created_at.isoformat(), _dump_model(suggestion)),
            )
        return suggestion

    def get_calibration_suggestion(self, suggestion_id: UUID) -> CalibrationSuggestion | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT payload FROM calibration_suggestions WHERE id = ?", (str(suggestion_id),)).fetchone()
        return CalibrationSuggestion.model_validate_json(row["payload"]) if row else None

    def list_calibration_suggestions(self, status: str | None = None, limit: int = 50) -> list[CalibrationSuggestion]:
        query = "SELECT payload FROM calibration_suggestions"
        params: tuple[str | int, ...]
        if status:
            query += " WHERE status = ?"
            params = (status, limit)
        else:
            params = (limit,)
        query += " ORDER BY created_at DESC LIMIT ?"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [CalibrationSuggestion.model_validate_json(row["payload"]) for row in rows]

    def add_market_snapshot(self, snapshot: MarketSnapshotRecord) -> MarketSnapshotRecord:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO market_snapshots
                    (id, symbol, timeframe, provider, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(snapshot.id), snapshot.symbol.upper(), snapshot.timeframe, snapshot.provider, snapshot.created_at.isoformat(), _dump_model(snapshot)),
            )
        return snapshot

    def add_research_run(self, run: ResearchRun) -> ResearchRun:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO research_runs
                    (id, symbol, timeframe, report_id, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(run.id), run.symbol.upper(), run.timeframe, str(run.report_id), run.created_at.isoformat(), _dump_model(run)),
            )
        return run

    def get_research_run(self, run_id: UUID) -> ResearchRun | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT payload FROM research_runs WHERE id = ?", (str(run_id),)).fetchone()
        return ResearchRun.model_validate_json(row["payload"]) if row else None

    def list_research_runs(self, symbol: str | None = None, limit: int = 20) -> list[ResearchRun]:
        query = "SELECT payload FROM research_runs"
        params: tuple = ()
        if symbol:
            query += " WHERE symbol = ?"
            params = (symbol.upper(),)
        query += " ORDER BY created_at DESC LIMIT ?"
        params = (*params, limit)
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [ResearchRun.model_validate_json(row["payload"]) for row in rows]

    def add_agent_output(self, output: AgentOutput) -> AgentOutput:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO agent_outputs
                    (id, research_run_id, agent_name, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(output.id), str(output.research_run_id), output.agent_name, output.created_at.isoformat(), _dump_model(output)),
            )
        return output

    def list_agent_outputs(self, research_run_id: UUID) -> list[AgentOutput]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM agent_outputs WHERE research_run_id = ? ORDER BY created_at ASC",
                (str(research_run_id),),
            ).fetchall()
        return [AgentOutput.model_validate_json(row["payload"]) for row in rows]

    def add_shadow_profile(self, profile: ShadowProfile) -> ShadowProfile:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO shadow_profiles (shadow_id, created_at, payload) VALUES (?, ?, ?)",
                (profile.shadow_id, profile.created_at.isoformat(), _dump_model(profile)),
            )
        return profile

    def get_shadow_profile(self, shadow_id: str) -> ShadowProfile | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT payload FROM shadow_profiles WHERE shadow_id = ?", (shadow_id,)).fetchone()
        return ShadowProfile.model_validate_json(row["payload"]) if row else None

    def list_shadow_profiles(self, limit: int = 20) -> list[ShadowProfile]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT payload FROM shadow_profiles ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [ShadowProfile.model_validate_json(row["payload"]) for row in rows]

    def add_decision_memory(self, memory: DecisionMemory) -> DecisionMemory:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO decision_memories
                    (id, symbol, memory_type, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (str(memory.id), memory.symbol.upper() if memory.symbol else None, memory.memory_type, memory.created_at.isoformat(), _dump_model(memory)),
            )
        return memory

    def list_decision_memories(self, symbol: str | None = None, limit: int = 20) -> list[DecisionMemory]:
        if symbol:
            query = "SELECT payload FROM decision_memories WHERE symbol = ? OR symbol IS NULL ORDER BY created_at DESC LIMIT ?"
            params = (symbol.upper(), limit)
        else:
            query = "SELECT payload FROM decision_memories ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [DecisionMemory.model_validate_json(row["payload"]) for row in rows]

    def add_validation_run(self, run: ValidationRun) -> ValidationRun:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO validation_runs
                    (id, symbol, timeframe, strategy_type, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(run.id), run.symbol.upper(), run.timeframe, run.strategy_type, run.created_at.isoformat(), _dump_model(run)),
            )
        return run

    def get_validation_run(self, run_id: UUID) -> ValidationRun | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT payload FROM validation_runs WHERE id = ?", (str(run_id),)).fetchone()
        return ValidationRun.model_validate_json(row["payload"]) if row else None

    def list_validation_runs(self, limit: int = 20) -> list[ValidationRun]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT payload FROM validation_runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [ValidationRun.model_validate_json(row["payload"]) for row in rows]

    def list_watchlist(self) -> list[WatchlistItem]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT payload FROM watchlist ORDER BY added_at DESC").fetchall()
        return [WatchlistItem.model_validate_json(row["payload"]) for row in rows]

    def upsert_watchlist_item(self, item: WatchlistItem) -> WatchlistItem:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO watchlist (symbol, added_at, payload) VALUES (?, ?, ?)",
                (item.symbol.upper(), item.added_at.isoformat(), _dump_model(item)),
            )
        return item

    def remove_watchlist_item(self, symbol: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
        return cursor.rowcount > 0

    def replace_symbol_catalog(self, symbols: list[CatalogSymbol]) -> int:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM symbol_catalog")
            connection.executemany(
                "INSERT OR REPLACE INTO symbol_catalog (symbol, updated_at, payload) VALUES (?, ?, ?)",
                [(item.symbol.upper(), item.updated_at.isoformat(), _dump_model(item)) for item in symbols],
            )
        return len(symbols)

    def search_symbols(self, query: str, limit: int = 20) -> list[CatalogSymbol]:
        needle = query.strip().upper()
        with self._lock, self._connect() as connection:
            if needle:
                rows = connection.execute(
                    "SELECT payload FROM symbol_catalog WHERE symbol LIKE ? ORDER BY length(symbol), symbol LIMIT ?",
                    (f"%{needle}%", limit),
                ).fetchall()
            else:
                rows = connection.execute("SELECT payload FROM symbol_catalog ORDER BY length(symbol), symbol LIMIT ?", (limit,)).fetchall()
        return [CatalogSymbol.model_validate_json(row["payload"]) for row in rows]

    def symbol_catalog_updated_at(self) -> datetime | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT MAX(updated_at) AS updated_at FROM symbol_catalog").fetchone()
        value = row["updated_at"] if row else None
        return datetime.fromisoformat(value) if value else None

    def _upsert_position(self, position: Position) -> Position:
        with self._lock, self._connect() as connection:
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


def create_repository(database_url: str) -> Repository:
    if database_url == "memory://":
        return MemoryRepository()
    if database_url.startswith("sqlite:///"):
        return SQLiteRepository(database_url.removeprefix("sqlite:///"))
    raise ValueError(f"Unsupported database URL: {database_url}")


def _dump_model(model) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False)
