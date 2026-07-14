from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.db.models import (
    AgentOutput,
    AlertRecord,
    AlertResponseRecord,
    ArmedSetup,
    AutonomyLog,
    BacktestStat,
    CalibrationSuggestion,
    CatalogSymbol,
    DatabaseMaintenanceEvent,
    DecisionMemory,
    DerivativeDataSnapshot,
    DerivativeMetric,
    EngineParamVersion,
    EntryIntent,
    EntryScenario,
    JudgmentLedgerEntry,
    JudgmentScore,
    LiquidationEvent,
    MarketSnapshotRecord,
    MonitoringLog,
    Position,
    PositionEvent,
    PositionInsight,
    PositionSnapshot,
    PositionStatus,
    PaperTrade,
    Report,
    ResearchRun,
    ScoutSnapshot,
    ShadowProfile,
    Trade,
    UniverseDiscovery,
    UserTrade,
    ValidationRun,
    WatchlistItem,
    WhaleEvent,
    WhaleWallet,
    utc_now,
)
from app.db.sqlite_utils import SQLITE_WRITE_LOCK, connect_sqlite
from app.db.migrations import run_migrations


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
    def add_alert(self, alert: AlertRecord) -> AlertRecord: ...
    def list_alerts(self, position_id: UUID | None = None, limit: int = 100) -> list[AlertRecord]: ...
    def add_alert_response(self, response: AlertResponseRecord) -> AlertResponseRecord: ...
    def get_alert_response(self, alert_id: UUID) -> AlertResponseRecord | None: ...
    def list_alert_responses(
        self,
        position_id: UUID | None = None,
        rule_id: str | None = None,
        limit: int = 200,
    ) -> list[AlertResponseRecord]: ...
    def add_scout_snapshot(self, snapshot: ScoutSnapshot) -> ScoutSnapshot: ...
    def list_scout_snapshots(self, symbol: str | None = None, limit: int = 100) -> list[ScoutSnapshot]: ...
    def latest_scout_snapshot(self, symbol: str, timeframe: str | None = None) -> ScoutSnapshot | None: ...
    def get_directional_state(self, symbol: str, timeframe: str) -> dict | None: ...
    def upsert_directional_state(self, symbol: str, timeframe: str, state: dict) -> bool: ...
    def list_directional_states(self, limit: int = 100) -> list[dict]: ...
    def upsert_armed_setup(self, setup: ArmedSetup) -> ArmedSetup: ...
    def get_armed_setup(self, setup_id: UUID) -> ArmedSetup | None: ...
    def list_armed_setups(self, symbol: str | None = None, status: str | None = None, limit: int = 200) -> list[ArmedSetup]: ...
    def upsert_entry_intent(self, intent: EntryIntent) -> EntryIntent: ...
    def get_entry_intent(self, intent_id: UUID) -> EntryIntent | None: ...
    def list_entry_intents(
        self,
        symbol: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[EntryIntent]: ...
    def upsert_backtest_stat(self, stat: BacktestStat) -> BacktestStat: ...
    def list_backtest_stats(
        self,
        symbol: str | None = None,
        signature_key: str | None = None,
        limit: int = 100,
    ) -> list[BacktestStat]: ...
    def upsert_universe_discovery(self, discovery: UniverseDiscovery) -> UniverseDiscovery: ...
    def list_universe_discoveries(
        self,
        symbol: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[UniverseDiscovery]: ...
    def add_trade(self, trade: Trade) -> Trade: ...
    def get_trade(self, trade_id: UUID) -> Trade | None: ...
    def list_trades(self) -> list[Trade]: ...
    def upsert_paper_trade(self, trade: PaperTrade) -> PaperTrade: ...
    def get_paper_trade(self, trade_id: UUID) -> PaperTrade | None: ...
    def list_paper_trades(
        self,
        status: str | None = None,
        symbol: str | None = None,
        limit: int = 500,
    ) -> list[PaperTrade]: ...
    def upsert_user_trade(self, trade: UserTrade) -> UserTrade: ...
    def list_user_trades(self, since: datetime | None = None, limit: int = 5000) -> list[UserTrade]: ...
    def upsert_user_account_fill(self, fill: dict) -> bool: ...
    def list_user_account_fills(self, since: datetime | None = None, limit: int = 10000) -> list[dict]: ...
    def get_paper_engine_state(self, symbol: str, timeframe: str) -> dict | None: ...
    def upsert_paper_engine_state(self, symbol: str, timeframe: str, state: dict) -> bool: ...
    def upsert_paper_gate_funnel(self, record: dict) -> bool: ...
    def list_paper_gate_funnel(
        self,
        since: datetime | None = None,
        symbol: str | None = None,
        limit: int = 10000,
    ) -> list[dict]: ...
    def add_judgment(self, judgment: JudgmentLedgerEntry) -> JudgmentLedgerEntry: ...
    def list_judgments(self, position_id: UUID, limit: int = 200) -> list[JudgmentLedgerEntry]: ...
    def add_judgment_score(self, score: JudgmentScore) -> JudgmentScore: ...
    def list_judgment_scores(
        self,
        position_id: UUID | None = None,
        trade_id: UUID | None = None,
        limit: int = 500,
    ) -> list[JudgmentScore]: ...
    def add_calibration_suggestion(self, suggestion: CalibrationSuggestion) -> CalibrationSuggestion: ...
    def get_calibration_suggestion(self, suggestion_id: UUID) -> CalibrationSuggestion | None: ...
    def list_calibration_suggestions(self, status: str | None = None, limit: int = 50) -> list[CalibrationSuggestion]: ...
    def add_engine_param_version(self, version: EngineParamVersion) -> EngineParamVersion: ...
    def latest_engine_param(self, param: str) -> EngineParamVersion | None: ...
    def list_engine_params(self, limit: int = 100) -> list[EngineParamVersion]: ...
    def add_autonomy_log(self, log: AutonomyLog) -> AutonomyLog: ...
    def list_autonomy_logs(
        self,
        signature_key: str | None = None,
        new_state: str | None = None,
        since: datetime | None = None,
        limit: int = 200,
    ) -> list[AutonomyLog]: ...
    def latest_autonomy_states(self) -> dict[str, str]: ...
    def add_market_snapshot(self, snapshot: MarketSnapshotRecord) -> MarketSnapshotRecord: ...
    def add_derivative_snapshot(self, snapshot: DerivativeDataSnapshot) -> DerivativeDataSnapshot: ...
    def list_derivative_snapshots(self, symbol: str | None = None, provider: str | None = None, limit: int = 100) -> list[DerivativeDataSnapshot]: ...
    def latest_derivative_snapshot(self, symbol: str, provider: str | None = None) -> DerivativeDataSnapshot | None: ...
    def delete_derivative_snapshots_before(self, cutoff: datetime) -> int: ...
    def add_derivative_metric(self, metric: DerivativeMetric) -> DerivativeMetric: ...
    def list_derivative_metrics(self, symbol: str | None = None, source: str | None = None, limit: int = 100) -> list[DerivativeMetric]: ...
    def latest_derivative_metric(self, symbol: str, source: str | None = None) -> DerivativeMetric | None: ...
    def delete_derivative_metrics_before(self, cutoff: datetime) -> int: ...
    def add_liquidation_event(self, event: LiquidationEvent) -> LiquidationEvent: ...
    def list_liquidation_events(self, symbol: str | None = None, source: str | None = None, limit: int = 100) -> list[LiquidationEvent]: ...
    def delete_liquidation_events_before(self, cutoff: datetime) -> int: ...
    def add_database_maintenance_event(self, event: DatabaseMaintenanceEvent) -> DatabaseMaintenanceEvent: ...
    def list_database_maintenance_events(self, event_type: str | None = None, limit: int = 50) -> list[DatabaseMaintenanceEvent]: ...
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
    def get_calibration_report_cache(self, report_key: str) -> dict | None: ...
    def upsert_calibration_report_cache(self, report_key: str, payload: dict) -> dict: ...
    def add_entry_scenario(self, scenario: EntryScenario) -> EntryScenario: ...
    def get_entry_scenario(self, scenario_id: UUID) -> EntryScenario | None: ...
    def list_entry_scenarios(self, symbol: str | None = None, limit: int = 50) -> list[EntryScenario]: ...
    def find_matching_scenario(self, symbol: str, direction: str, within_hours: int = 72) -> EntryScenario | None: ...
    def link_scenario_position(self, scenario_id: UUID, position_id: UUID) -> EntryScenario | None: ...
    def upsert_whale_wallet(self, wallet: WhaleWallet) -> WhaleWallet: ...
    def get_whale_wallet(self, address: str) -> WhaleWallet | None: ...
    def list_whale_wallets(self, active: bool | None = None, limit: int = 20) -> list[WhaleWallet]: ...
    def remove_whale_wallet(self, address: str) -> bool: ...
    def add_whale_event(self, event: WhaleEvent) -> bool: ...
    def list_whale_events(
        self,
        symbol: str | None = None,
        wallet_address: str | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[WhaleEvent]: ...
    def get_whale_position_state(self, wallet_address: str, coin: str) -> dict | None: ...
    def list_whale_position_states(self, wallet_address: str | None = None, limit: int = 500) -> list[dict]: ...
    def upsert_whale_position_state(self, wallet_address: str, coin: str, state: dict) -> bool: ...
    def delete_whale_position_state(self, wallet_address: str, coin: str) -> bool: ...


class MemoryRepository:
    def __init__(self) -> None:
        self.reports: dict[UUID, Report] = {}
        self.reports_by_symbol: dict[str, list[UUID]] = {}
        self.positions: dict[UUID, Position] = {}
        self.monitoring_logs: dict[UUID, list[MonitoringLog]] = {}
        self.position_snapshots: dict[UUID, list[PositionSnapshot]] = {}
        self.position_insights: dict[UUID, list[PositionInsight]] = {}
        self.position_events: dict[UUID, list[PositionEvent]] = {}
        self.alerts: dict[UUID, AlertRecord] = {}
        self.alert_responses: dict[UUID, AlertResponseRecord] = {}
        self.scout_snapshots: dict[UUID, ScoutSnapshot] = {}
        self.directional_states: dict[tuple[str, str], dict] = {}
        self.armed_setups: dict[UUID, ArmedSetup] = {}
        self.entry_intents: dict[UUID, EntryIntent] = {}
        self.backtest_stats: dict[UUID, BacktestStat] = {}
        self.universe_discoveries: dict[UUID, UniverseDiscovery] = {}
        self.trades: dict[UUID, Trade] = {}
        self.paper_trades: dict[UUID, PaperTrade] = {}
        self.user_trades: dict[UUID, UserTrade] = {}
        self.user_account_fills: dict[str, dict] = {}
        self.paper_engine_states: dict[tuple[str, str], dict] = {}
        self.paper_gate_funnel: dict[tuple[str, str, str], dict] = {}
        self.judgments: dict[UUID, list[JudgmentLedgerEntry]] = {}
        self.judgment_scores: dict[UUID, JudgmentScore] = {}
        self.calibration_suggestions: dict[UUID, CalibrationSuggestion] = {}
        self.engine_params: dict[UUID, EngineParamVersion] = {}
        self.autonomy_logs: dict[UUID, AutonomyLog] = {}
        self.market_snapshots: dict[UUID, MarketSnapshotRecord] = {}
        self.derivative_snapshots: dict[UUID, DerivativeDataSnapshot] = {}
        self.derivative_metrics: dict[UUID, DerivativeMetric] = {}
        self.liquidation_events: dict[UUID, LiquidationEvent] = {}
        self.database_maintenance_events: dict[UUID, DatabaseMaintenanceEvent] = {}
        self.research_runs: dict[UUID, ResearchRun] = {}
        self.agent_outputs: dict[UUID, list[AgentOutput]] = {}
        self.shadow_profiles: dict[str, ShadowProfile] = {}
        self.decision_memories: dict[UUID, DecisionMemory] = {}
        self.validation_runs: dict[UUID, ValidationRun] = {}
        self.watchlist: dict[str, WatchlistItem] = {}
        self.symbol_catalog: dict[str, CatalogSymbol] = {}
        self.calibration_report_cache: dict[str, dict] = {}
        self.entry_scenarios: dict[UUID, EntryScenario] = {}
        self.whale_wallets: dict[str, WhaleWallet] = {}
        self.whale_events: dict[UUID, WhaleEvent] = {}
        self.whale_position_states: dict[tuple[str, str], dict] = {}

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

    def add_scout_snapshot(self, snapshot: ScoutSnapshot) -> ScoutSnapshot:
        self.scout_snapshots[snapshot.id] = snapshot
        return snapshot

    def list_scout_snapshots(self, symbol: str | None = None, limit: int = 100) -> list[ScoutSnapshot]:
        snapshots = list(self.scout_snapshots.values())
        if symbol:
            snapshots = [snapshot for snapshot in snapshots if snapshot.symbol.upper() == symbol.upper()]
        return sorted(snapshots, key=lambda item: item.as_of, reverse=True)[:limit]

    def latest_scout_snapshot(self, symbol: str, timeframe: str | None = None) -> ScoutSnapshot | None:
        snapshots = self.list_scout_snapshots(symbol=symbol, limit=500)
        if timeframe:
            snapshots = [snapshot for snapshot in snapshots if snapshot.timeframe == timeframe]
        return snapshots[0] if snapshots else None

    def get_directional_state(self, symbol: str, timeframe: str) -> dict | None:
        state = self.directional_states.get((symbol.upper(), timeframe))
        return dict(state) if isinstance(state, dict) else None

    def upsert_directional_state(self, symbol: str, timeframe: str, state: dict) -> bool:
        key = (symbol.upper(), timeframe)
        current = self.directional_states.get(key)
        if _same_directional_bar(current, state):
            return False
        persisted = dict(state)
        persisted["updated_at"] = utc_now().isoformat()
        self.directional_states[key] = persisted
        return True

    def list_directional_states(self, limit: int = 100) -> list[dict]:
        rows = [
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "stance": state.get("stance"),
                "since": state.get("since"),
                "last_bar_at": state.get("last_bar_at"),
                "updated_at": state.get("updated_at"),
            }
            for (symbol, timeframe), state in self.directional_states.items()
        ]
        return sorted(rows, key=lambda item: str(item.get("updated_at") or ""), reverse=True)[:limit]

    def upsert_armed_setup(self, setup: ArmedSetup) -> ArmedSetup:
        self.armed_setups[setup.id] = setup
        return setup

    def get_armed_setup(self, setup_id: UUID) -> ArmedSetup | None:
        return self.armed_setups.get(setup_id)

    def list_armed_setups(self, symbol: str | None = None, status: str | None = None, limit: int = 200) -> list[ArmedSetup]:
        setups = list(self.armed_setups.values())
        if symbol:
            setups = [setup for setup in setups if setup.symbol.upper() == symbol.upper()]
        if status:
            setups = [setup for setup in setups if setup.status == status]
        return sorted(setups, key=lambda item: item.updated_at, reverse=True)[:limit]

    def upsert_entry_intent(self, intent: EntryIntent) -> EntryIntent:
        normalized = intent.symbol.upper()
        self.entry_intents[intent.id] = intent.model_copy(update={"symbol": normalized})
        return self.entry_intents[intent.id]

    def get_entry_intent(self, intent_id: UUID) -> EntryIntent | None:
        return self.entry_intents.get(intent_id)

    def list_entry_intents(
        self,
        symbol: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[EntryIntent]:
        intents = list(self.entry_intents.values())
        if symbol:
            intents = [intent for intent in intents if intent.symbol.upper() == symbol.upper()]
        if status:
            intents = [intent for intent in intents if intent.status == status]
        return sorted(intents, key=lambda item: item.updated_at, reverse=True)[:limit]

    def upsert_backtest_stat(self, stat: BacktestStat) -> BacktestStat:
        normalized = stat.model_copy(update={"symbol": stat.symbol.upper()})
        existing = next(
            (
                item_id
                for item_id, item in self.backtest_stats.items()
                if item.symbol == normalized.symbol
                and item.timeframe == normalized.timeframe
                and item.signature_key == normalized.signature_key
                and item.scope == normalized.scope
            ),
            None,
        )
        if existing is not None and existing != normalized.id:
            self.backtest_stats.pop(existing, None)
        self.backtest_stats[normalized.id] = normalized
        return normalized

    def list_backtest_stats(
        self,
        symbol: str | None = None,
        signature_key: str | None = None,
        limit: int = 100,
    ) -> list[BacktestStat]:
        stats = list(self.backtest_stats.values())
        if symbol:
            stats = [stat for stat in stats if stat.symbol.upper() == symbol.upper()]
        if signature_key:
            stats = [stat for stat in stats if stat.signature_key == signature_key]
        return sorted(stats, key=lambda item: item.generated_at, reverse=True)[:limit]

    def upsert_universe_discovery(self, discovery: UniverseDiscovery) -> UniverseDiscovery:
        normalized = discovery.model_copy(update={"symbol": discovery.symbol.upper()})
        self.universe_discoveries[normalized.id] = normalized
        return normalized

    def list_universe_discoveries(
        self,
        symbol: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[UniverseDiscovery]:
        discoveries = list(self.universe_discoveries.values())
        if symbol:
            discoveries = [item for item in discoveries if item.symbol.upper() == symbol.upper()]
        if status:
            discoveries = [item for item in discoveries if item.status == status]
        return sorted(discoveries, key=lambda item: item.created_at, reverse=True)[:limit]

    def add_trade(self, trade: Trade) -> Trade:
        self.trades[trade.id] = trade
        return trade

    def get_trade(self, trade_id: UUID) -> Trade | None:
        return self.trades.get(trade_id)

    def list_trades(self) -> list[Trade]:
        return sorted(self.trades.values(), key=lambda item: item.created_at, reverse=True)

    def upsert_paper_trade(self, trade: PaperTrade) -> PaperTrade:
        normalized = trade.model_copy(update={"symbol": trade.symbol.upper(), "updated_at": utc_now()})
        self.paper_trades[normalized.id] = normalized
        return normalized

    def get_paper_trade(self, trade_id: UUID) -> PaperTrade | None:
        return self.paper_trades.get(trade_id)

    def list_paper_trades(
        self,
        status: str | None = None,
        symbol: str | None = None,
        limit: int = 500,
    ) -> list[PaperTrade]:
        trades = list(self.paper_trades.values())
        if status:
            trades = [trade for trade in trades if trade.status == status]
        if symbol:
            trades = [trade for trade in trades if trade.symbol.upper() == symbol.upper()]
        return sorted(trades, key=lambda item: item.updated_at, reverse=True)[:limit]

    def upsert_user_trade(self, trade: UserTrade) -> UserTrade:
        normalized = trade.model_copy(update={"symbol": trade.symbol.upper(), "updated_at": utc_now()})
        self.user_trades[normalized.id] = normalized
        return normalized

    def list_user_trades(self, since: datetime | None = None, limit: int = 5000) -> list[UserTrade]:
        trades = list(self.user_trades.values())
        if since is not None:
            trades = [trade for trade in trades if trade.exit_at >= since]
        return sorted(trades, key=lambda item: item.exit_at, reverse=True)[:limit]

    def upsert_user_account_fill(self, fill: dict) -> bool:
        trade_id = str(fill.get("trade_id") or "")
        if not trade_id:
            raise ValueError("account fill trade_id is required")
        created = trade_id not in self.user_account_fills
        self.user_account_fills[trade_id] = dict(fill)
        return created

    def list_user_account_fills(self, since: datetime | None = None, limit: int = 10000) -> list[dict]:
        fills = list(self.user_account_fills.values())
        if since is not None:
            fills = [fill for fill in fills if _timestamp_or_min(fill.get("timestamp")) >= since]
        return sorted(fills, key=lambda fill: str(fill.get("timestamp") or ""))[-limit:]

    def get_paper_engine_state(self, symbol: str, timeframe: str) -> dict | None:
        state = self.paper_engine_states.get((symbol.upper(), timeframe))
        return dict(state) if isinstance(state, dict) else None

    def upsert_paper_engine_state(self, symbol: str, timeframe: str, state: dict) -> bool:
        key = (symbol.upper(), timeframe)
        current = self.paper_engine_states.get(key)
        if current == state:
            return False
        self.paper_engine_states[key] = dict(state)
        return True

    def upsert_paper_gate_funnel(self, record: dict) -> bool:
        key = (
            str(record.get("symbol") or "").upper(),
            str(record.get("timeframe") or "4h"),
            str(record.get("bar_at") or ""),
        )
        if key in self.paper_gate_funnel:
            return False
        self.paper_gate_funnel[key] = dict(record)
        return True

    def list_paper_gate_funnel(
        self,
        since: datetime | None = None,
        symbol: str | None = None,
        limit: int = 10000,
    ) -> list[dict]:
        rows = list(self.paper_gate_funnel.values())
        if since is not None:
            rows = [row for row in rows if (_timestamp_or_min(row.get("bar_at")) >= since)]
        if symbol is not None:
            rows = [row for row in rows if str(row.get("symbol") or "").upper() == symbol.upper()]
        return sorted(rows, key=lambda row: str(row.get("bar_at") or ""), reverse=True)[:limit]

    def add_judgment(self, judgment: JudgmentLedgerEntry) -> JudgmentLedgerEntry:
        entries = self.judgments.setdefault(judgment.position_id, [])
        entries = [item for item in entries if item.id != judgment.id and item.judgment_id != judgment.judgment_id]
        entries.insert(0, judgment)
        self.judgments[judgment.position_id] = entries
        return judgment

    def list_judgments(self, position_id: UUID, limit: int = 200) -> list[JudgmentLedgerEntry]:
        return sorted(
            self.judgments.get(position_id, []),
            key=lambda item: item.as_of,
            reverse=True,
        )[:limit]

    def add_judgment_score(self, score: JudgmentScore) -> JudgmentScore:
        self.judgment_scores[score.id] = score
        return score

    def list_judgment_scores(
        self,
        position_id: UUID | None = None,
        trade_id: UUID | None = None,
        limit: int = 500,
    ) -> list[JudgmentScore]:
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

    def add_engine_param_version(self, version: EngineParamVersion) -> EngineParamVersion:
        for key, existing in list(self.engine_params.items()):
            if existing.param == version.param and existing.status == "active" and existing.id != version.id:
                self.engine_params[key] = existing.model_copy(update={"status": "superseded"})
        self.engine_params[version.id] = version
        return version

    def latest_engine_param(self, param: str) -> EngineParamVersion | None:
        versions = [version for version in self.engine_params.values() if version.param == param and version.status == "active"]
        if not versions:
            return None
        return sorted(versions, key=lambda item: item.approved_at, reverse=True)[0]

    def list_engine_params(self, limit: int = 100) -> list[EngineParamVersion]:
        return sorted(self.engine_params.values(), key=lambda item: item.approved_at, reverse=True)[:limit]

    def add_autonomy_log(self, log: AutonomyLog) -> AutonomyLog:
        self.autonomy_logs[log.id] = log
        return log

    def list_autonomy_logs(
        self,
        signature_key: str | None = None,
        new_state: str | None = None,
        since: datetime | None = None,
        limit: int = 200,
    ) -> list[AutonomyLog]:
        logs = list(self.autonomy_logs.values())
        if signature_key:
            logs = [log for log in logs if log.signature_key == signature_key]
        if new_state:
            logs = [log for log in logs if log.new_state == new_state]
        if since is not None:
            logs = [log for log in logs if _aware_dt(log.created_at) >= since]
        return sorted(logs, key=lambda item: item.created_at, reverse=True)[:limit]

    def latest_autonomy_states(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for log in sorted(self.autonomy_logs.values(), key=lambda item: item.created_at, reverse=True):
            result.setdefault(log.signature_key, log.new_state)
        return result

    def add_market_snapshot(self, snapshot: MarketSnapshotRecord) -> MarketSnapshotRecord:
        self.market_snapshots[snapshot.id] = snapshot
        return snapshot

    def add_derivative_snapshot(self, snapshot: DerivativeDataSnapshot) -> DerivativeDataSnapshot:
        self.derivative_snapshots[snapshot.id] = snapshot
        return snapshot

    def list_derivative_snapshots(self, symbol: str | None = None, provider: str | None = None, limit: int = 100) -> list[DerivativeDataSnapshot]:
        snapshots = list(self.derivative_snapshots.values())
        if symbol:
            snapshots = [snapshot for snapshot in snapshots if snapshot.symbol.upper() == symbol.upper()]
        if provider:
            snapshots = [snapshot for snapshot in snapshots if snapshot.provider == provider]
        return sorted(snapshots, key=lambda item: item.created_at, reverse=True)[:limit]

    def latest_derivative_snapshot(self, symbol: str, provider: str | None = None) -> DerivativeDataSnapshot | None:
        snapshots = self.list_derivative_snapshots(symbol=symbol, provider=provider, limit=1)
        return snapshots[0] if snapshots else None

    def delete_derivative_snapshots_before(self, cutoff: datetime) -> int:
        ids = [snapshot_id for snapshot_id, snapshot in self.derivative_snapshots.items() if snapshot.as_of < cutoff]
        for snapshot_id in ids:
            self.derivative_snapshots.pop(snapshot_id, None)
        return len(ids)

    def add_derivative_metric(self, metric: DerivativeMetric) -> DerivativeMetric:
        self.derivative_metrics[metric.id] = metric
        return metric

    def list_derivative_metrics(self, symbol: str | None = None, source: str | None = None, limit: int = 100) -> list[DerivativeMetric]:
        metrics = list(self.derivative_metrics.values())
        if symbol:
            metrics = [metric for metric in metrics if metric.symbol.upper() == symbol.upper()]
        if source:
            metrics = [metric for metric in metrics if metric.source == source]
        return sorted(metrics, key=lambda item: item.as_of, reverse=True)[:limit]

    def latest_derivative_metric(self, symbol: str, source: str | None = None) -> DerivativeMetric | None:
        metrics = self.list_derivative_metrics(symbol=symbol, source=source, limit=1)
        return metrics[0] if metrics else None

    def delete_derivative_metrics_before(self, cutoff: datetime) -> int:
        ids = [metric_id for metric_id, metric in self.derivative_metrics.items() if metric.as_of < cutoff]
        for metric_id in ids:
            self.derivative_metrics.pop(metric_id, None)
        return len(ids)

    def add_liquidation_event(self, event: LiquidationEvent) -> LiquidationEvent:
        self.liquidation_events[event.id] = event
        return event

    def list_liquidation_events(self, symbol: str | None = None, source: str | None = None, limit: int = 100) -> list[LiquidationEvent]:
        events = list(self.liquidation_events.values())
        if symbol:
            events = [event for event in events if event.symbol.upper() == symbol.upper()]
        if source:
            events = [event for event in events if event.source == source]
        return sorted(events, key=lambda item: item.bucket_start, reverse=True)[:limit]

    def delete_liquidation_events_before(self, cutoff: datetime) -> int:
        ids = [event_id for event_id, event in self.liquidation_events.items() if event.bucket_start < cutoff]
        for event_id in ids:
            self.liquidation_events.pop(event_id, None)
        return len(ids)

    def add_database_maintenance_event(self, event: DatabaseMaintenanceEvent) -> DatabaseMaintenanceEvent:
        self.database_maintenance_events[event.id] = event
        return event

    def list_database_maintenance_events(self, event_type: str | None = None, limit: int = 50) -> list[DatabaseMaintenanceEvent]:
        events = list(self.database_maintenance_events.values())
        if event_type:
            events = [event for event in events if event.event_type == event_type]
        return sorted(events, key=lambda item: item.created_at, reverse=True)[:limit]

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
        return sorted(
            self.agent_outputs.get(research_run_id, []),
            key=lambda item: item.created_at,
        )

    def add_shadow_profile(self, profile: ShadowProfile) -> ShadowProfile:
        self.shadow_profiles[profile.shadow_id] = profile
        return profile

    def get_shadow_profile(self, shadow_id: str) -> ShadowProfile | None:
        return self.shadow_profiles.get(shadow_id)

    def list_shadow_profiles(self, limit: int = 20) -> list[ShadowProfile]:
        return sorted(
            self.shadow_profiles.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )[:limit]

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
        return sorted(
            self.validation_runs.values(),
            key=lambda item: item.created_at,
            reverse=True,
        )[:limit]

    def list_watchlist(self) -> list[WatchlistItem]:
        return sorted(self.watchlist.values(), key=lambda item: item.added_at, reverse=True)

    def upsert_watchlist_item(self, item: WatchlistItem) -> WatchlistItem:
        normalized = item.symbol.upper()
        self.watchlist[normalized] = item.model_copy(update={"symbol": normalized})
        return item

    def remove_watchlist_item(self, symbol: str) -> bool:
        return self.watchlist.pop(symbol.upper(), None) is not None

    def replace_symbol_catalog(self, symbols: list[CatalogSymbol]) -> int:
        self.symbol_catalog = {item.symbol.upper(): item for item in symbols}
        return len(self.symbol_catalog)

    def search_symbols(self, query: str, limit: int = 20) -> list[CatalogSymbol]:
        needle = query.strip().upper()
        matches = (
            [
                item
                for symbol, item in self.symbol_catalog.items()
                if needle in symbol or needle in item.base_coin.upper() or needle in item.quote_coin.upper() or needle in item.asset_class.upper()
            ]
            if needle
            else list(self.symbol_catalog.values())
        )
        return sorted(matches, key=lambda item: (len(item.symbol), item.symbol))[:limit]

    def symbol_catalog_updated_at(self) -> datetime | None:
        if not self.symbol_catalog:
            return None
        return max(item.updated_at for item in self.symbol_catalog.values())

    def get_calibration_report_cache(self, report_key: str) -> dict | None:
        cached = self.calibration_report_cache.get(report_key)
        return dict(cached) if isinstance(cached, dict) else None

    def upsert_calibration_report_cache(self, report_key: str, payload: dict) -> dict:
        cached = {"payload": dict(payload), "computed_at": utc_now().isoformat()}
        self.calibration_report_cache[report_key] = cached
        return dict(cached)

    def add_entry_scenario(self, scenario: EntryScenario) -> EntryScenario:
        self.entry_scenarios[scenario.id] = scenario
        return scenario

    def get_entry_scenario(self, scenario_id: UUID) -> EntryScenario | None:
        return self.entry_scenarios.get(scenario_id)

    def list_entry_scenarios(self, symbol: str | None = None, limit: int = 50) -> list[EntryScenario]:
        items = list(self.entry_scenarios.values())
        if symbol:
            items = [item for item in items if item.symbol.upper() == symbol.upper()]
        return sorted(items, key=lambda item: item.created_at, reverse=True)[:limit]

    def find_matching_scenario(self, symbol: str, direction: str, within_hours: int = 72) -> EntryScenario | None:
        from datetime import timedelta

        cutoff = utc_now() - timedelta(hours=within_hours)
        candidates = [
            item
            for item in self.entry_scenarios.values()
            if item.symbol.upper() == symbol.upper() and item.direction.value == direction and item.linked_position_id is None and item.created_at >= cutoff
        ]
        return max(candidates, key=lambda item: item.created_at, default=None)

    def link_scenario_position(self, scenario_id: UUID, position_id: UUID) -> EntryScenario | None:
        scenario = self.entry_scenarios.get(scenario_id)
        if scenario is None:
            return None
        updated = scenario.model_copy(update={"linked_position_id": position_id})
        self.entry_scenarios[scenario_id] = updated
        return updated

    def upsert_whale_wallet(self, wallet: WhaleWallet) -> WhaleWallet:
        self.whale_wallets[wallet.address.lower()] = wallet
        return wallet

    def get_whale_wallet(self, address: str) -> WhaleWallet | None:
        return self.whale_wallets.get(address.lower())

    def list_whale_wallets(self, active: bool | None = None, limit: int = 20) -> list[WhaleWallet]:
        wallets = list(self.whale_wallets.values())
        if active is not None:
            wallets = [wallet for wallet in wallets if wallet.active is active]
        return sorted(wallets, key=lambda wallet: wallet.added_at)[:limit]

    def remove_whale_wallet(self, address: str) -> bool:
        return self.whale_wallets.pop(address.lower(), None) is not None

    def add_whale_event(self, event: WhaleEvent) -> bool:
        if event.id in self.whale_events:
            return False
        self.whale_events[event.id] = event
        return True

    def list_whale_events(
        self,
        symbol: str | None = None,
        wallet_address: str | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[WhaleEvent]:
        events = list(self.whale_events.values())
        if symbol:
            events = [event for event in events if event.symbol.upper() == symbol.upper()]
        if wallet_address:
            events = [event for event in events if event.wallet_address.lower() == wallet_address.lower()]
        if since:
            events = [event for event in events if event.event_at >= since]
        return sorted(events, key=lambda event: event.event_at, reverse=True)[:limit]

    def get_whale_position_state(self, wallet_address: str, coin: str) -> dict | None:
        state = self.whale_position_states.get((wallet_address.lower(), coin.upper()))
        return dict(state) if state is not None else None

    def list_whale_position_states(self, wallet_address: str | None = None, limit: int = 500) -> list[dict]:
        rows = [
            dict(state)
            for (address, _coin), state in self.whale_position_states.items()
            if wallet_address is None or address == wallet_address.lower()
        ]
        return sorted(rows, key=lambda row: str(row.get("coin") or ""))[:limit]

    def upsert_whale_position_state(self, wallet_address: str, coin: str, state: dict) -> bool:
        self.whale_position_states[(wallet_address.lower(), coin.upper())] = dict(state)
        return True

    def delete_whale_position_state(self, wallet_address: str, coin: str) -> bool:
        return self.whale_position_states.pop((wallet_address.lower(), coin.upper()), None) is not None


class SQLiteRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._lock = SQLITE_WRITE_LOCK
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return connect_sqlite(self.database_path)

    def _init_schema(self) -> None:
        with self._connect() as connection:
            run_migrations(connection)

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

    def add_scout_snapshot(self, snapshot: ScoutSnapshot) -> ScoutSnapshot:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO scout_snapshots
                    (id, symbol, timeframe, as_of, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(snapshot.id),
                    snapshot.symbol.upper(),
                    snapshot.timeframe,
                    snapshot.as_of.isoformat(),
                    snapshot.created_at.isoformat(),
                    _dump_model(snapshot),
                ),
            )
        return snapshot

    def list_scout_snapshots(self, symbol: str | None = None, limit: int = 100) -> list[ScoutSnapshot]:
        query = "SELECT payload FROM scout_snapshots"
        params: tuple[str | int, ...]
        if symbol:
            query += " WHERE symbol = ?"
            params = (symbol.upper(), limit)
        else:
            params = (limit,)
        query += " ORDER BY as_of DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [ScoutSnapshot.model_validate_json(row["payload"]) for row in rows]

    def latest_scout_snapshot(self, symbol: str, timeframe: str | None = None) -> ScoutSnapshot | None:
        query = "SELECT payload FROM scout_snapshots WHERE symbol = ?"
        params: list[str | int] = [symbol.upper()]
        if timeframe:
            query += " AND timeframe = ?"
            params.append(timeframe)
        query += " ORDER BY as_of DESC LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return ScoutSnapshot.model_validate_json(row["payload"]) if row else None

    def get_directional_state(self, symbol: str, timeframe: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM directional_states WHERE symbol = ? AND timeframe = ?",
                (symbol.upper(), timeframe),
            ).fetchone()
        if row is None:
            return None
        try:
            state = json.loads(row["state"])
        except (TypeError, json.JSONDecodeError):
            return None
        return state if isinstance(state, dict) else None

    def upsert_directional_state(self, symbol: str, timeframe: str, state: dict) -> bool:
        normalized_symbol = symbol.upper()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM directional_states WHERE symbol = ? AND timeframe = ?",
                (normalized_symbol, timeframe),
            ).fetchone()
            current: dict | None = None
            if row is not None:
                try:
                    decoded = json.loads(row["state"])
                    current = decoded if isinstance(decoded, dict) else None
                except (TypeError, json.JSONDecodeError):
                    current = None
            if _same_directional_bar(current, state):
                return False
            updated_at = utc_now().isoformat()
            persisted = dict(state)
            persisted["updated_at"] = updated_at
            connection.execute(
                """
                INSERT INTO directional_states (symbol, timeframe, state, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe) DO UPDATE SET
                    state = excluded.state,
                    updated_at = excluded.updated_at
                """,
                (normalized_symbol, timeframe, json.dumps(persisted, ensure_ascii=False, sort_keys=True), updated_at),
            )
        return True

    def list_directional_states(self, limit: int = 100) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT symbol, timeframe, state, updated_at FROM directional_states ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            try:
                state = json.loads(row["state"])
            except (TypeError, json.JSONDecodeError):
                state = {}
            state = state if isinstance(state, dict) else {}
            result.append(
                {
                    "symbol": row["symbol"],
                    "timeframe": row["timeframe"],
                    "stance": state.get("stance"),
                    "since": state.get("since"),
                    "last_bar_at": state.get("last_bar_at"),
                    "updated_at": row["updated_at"],
                }
            )
        return result

    def upsert_armed_setup(self, setup: ArmedSetup) -> ArmedSetup:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO armed_setups
                    (id, symbol, timeframe, source, setup_type, status, trigger_price, updated_at, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(setup.id),
                    setup.symbol.upper(),
                    setup.timeframe,
                    setup.source,
                    setup.setup_type,
                    setup.status,
                    setup.trigger_price,
                    setup.updated_at.isoformat(),
                    setup.created_at.isoformat(),
                    _dump_model(setup),
                ),
            )
        return setup

    def get_armed_setup(self, setup_id: UUID) -> ArmedSetup | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM armed_setups WHERE id = ?", (str(setup_id),)).fetchone()
        return ArmedSetup.model_validate_json(row["payload"]) if row else None

    def list_armed_setups(self, symbol: str | None = None, status: str | None = None, limit: int = 200) -> list[ArmedSetup]:
        query = "SELECT payload FROM armed_setups"
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [ArmedSetup.model_validate_json(row["payload"]) for row in rows]

    def upsert_entry_intent(self, intent: EntryIntent) -> EntryIntent:
        normalized = intent.symbol.upper()
        saved = intent.model_copy(update={"symbol": normalized})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO entry_intents
                    (id, symbol, timeframe, direction, status, zone_lower, zone_upper, expires_at, updated_at, created_at, payload, kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(saved.id),
                    saved.symbol,
                    saved.timeframe,
                    saved.direction,
                    saved.status,
                    saved.zone_lower,
                    saved.zone_upper,
                    saved.expires_at.isoformat(),
                    saved.updated_at.isoformat(),
                    saved.created_at.isoformat(),
                    _dump_model(saved),
                    saved.kind,
                ),
            )
        return saved

    def get_entry_intent(self, intent_id: UUID) -> EntryIntent | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM entry_intents WHERE id = ?", (str(intent_id),)).fetchone()
        return EntryIntent.model_validate_json(row["payload"]) if row else None

    def list_entry_intents(
        self,
        symbol: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[EntryIntent]:
        query = "SELECT payload FROM entry_intents"
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [EntryIntent.model_validate_json(row["payload"]) for row in rows]

    def upsert_backtest_stat(self, stat: BacktestStat) -> BacktestStat:
        normalized = stat.model_copy(update={"symbol": stat.symbol.upper()})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO backtest_stats
                    (id, signature_key, symbol, timeframe, asset_class, scope, generated_at, sample_size, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(normalized.id),
                    normalized.signature_key,
                    normalized.symbol,
                    normalized.timeframe,
                    normalized.asset_class,
                    normalized.scope,
                    normalized.generated_at.isoformat(),
                    normalized.sample_size,
                    _dump_model(normalized),
                ),
            )
        return normalized

    def list_backtest_stats(
        self,
        symbol: str | None = None,
        signature_key: str | None = None,
        limit: int = 100,
    ) -> list[BacktestStat]:
        query = "SELECT payload FROM backtest_stats"
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if signature_key:
            clauses.append("signature_key = ?")
            params.append(signature_key)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY generated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [BacktestStat.model_validate_json(row["payload"]) for row in rows]

    def upsert_universe_discovery(self, discovery: UniverseDiscovery) -> UniverseDiscovery:
        normalized = discovery.model_copy(update={"symbol": discovery.symbol.upper()})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO universe_discoveries
                    (id, symbol, timeframe, asset_class, signature_key, status, gate_passed, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(normalized.id),
                    normalized.symbol.upper(),
                    normalized.timeframe,
                    normalized.asset_class,
                    normalized.signature_key,
                    normalized.status,
                    1 if normalized.gate_passed else 0,
                    normalized.created_at.isoformat(),
                    normalized.updated_at.isoformat(),
                    _dump_model(normalized),
                ),
            )
        return normalized

    def list_universe_discoveries(
        self,
        symbol: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[UniverseDiscovery]:
        query = "SELECT payload FROM universe_discoveries"
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if status:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [UniverseDiscovery.model_validate_json(row["payload"]) for row in rows]

    def add_trade(self, trade: Trade) -> Trade:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO trades
                    (id, position_id, symbol, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(trade.id),
                    str(trade.position_id),
                    trade.symbol.upper(),
                    trade.created_at.isoformat(),
                    _dump_model(trade),
                ),
            )
        return trade

    def get_trade(self, trade_id: UUID) -> Trade | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM trades WHERE id = ?", (str(trade_id),)).fetchone()
        return Trade.model_validate_json(row["payload"]) if row else None

    def list_trades(self) -> list[Trade]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM trades ORDER BY created_at DESC").fetchall()
        return [Trade.model_validate_json(row["payload"]) for row in rows]

    def upsert_paper_trade(self, trade: PaperTrade) -> PaperTrade:
        normalized = trade.model_copy(update={"symbol": trade.symbol.upper(), "updated_at": utc_now()})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO paper_trades
                    (id, symbol, timeframe, status, entry_bar_at, exit_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(normalized.id),
                    normalized.symbol,
                    normalized.timeframe,
                    normalized.status,
                    normalized.entry_bar_at.isoformat(),
                    normalized.exit_at.isoformat() if normalized.exit_at else None,
                    normalized.updated_at.isoformat(),
                    _dump_model(normalized),
                ),
            )
        return normalized

    def get_paper_trade(self, trade_id: UUID) -> PaperTrade | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM paper_trades WHERE id = ?",
                (str(trade_id),),
            ).fetchone()
        return PaperTrade.model_validate_json(row["payload"]) if row else None

    def list_paper_trades(
        self,
        status: str | None = None,
        symbol: str | None = None,
        limit: int = 500,
    ) -> list[PaperTrade]:
        query = "SELECT payload FROM paper_trades"
        clauses: list[str] = []
        params: list[str | int] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [PaperTrade.model_validate_json(row["payload"]) for row in rows]

    def upsert_user_trade(self, trade: UserTrade) -> UserTrade:
        normalized = trade.model_copy(update={"symbol": trade.symbol.upper(), "updated_at": utc_now()})
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO user_trades
                    (id, symbol, direction, entry_at, exit_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(normalized.id),
                    normalized.symbol,
                    normalized.direction.value,
                    normalized.entry_at.isoformat(),
                    normalized.exit_at.isoformat(),
                    normalized.updated_at.isoformat(),
                    _dump_model(normalized),
                ),
            )
        return normalized

    def list_user_trades(self, since: datetime | None = None, limit: int = 5000) -> list[UserTrade]:
        query = "SELECT payload FROM user_trades"
        params: list[str | int] = []
        if since is not None:
            query += " WHERE exit_at >= ?"
            params.append(since.isoformat())
        query += " ORDER BY exit_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [UserTrade.model_validate_json(row["payload"]) for row in rows]

    def upsert_user_account_fill(self, fill: dict) -> bool:
        trade_id = str(fill.get("trade_id") or "")
        timestamp = str(fill.get("timestamp") or "")
        if not trade_id or not timestamp:
            raise ValueError("account fill trade_id and timestamp are required")
        encoded = json.dumps(fill, ensure_ascii=False, sort_keys=True, default=_json_cache_default)
        with self._connect() as connection:
            existed = connection.execute(
                "SELECT 1 FROM user_account_fills WHERE trade_id = ?",
                (trade_id,),
            ).fetchone()
            connection.execute(
                """
                INSERT OR REPLACE INTO user_account_fills (trade_id, symbol, timestamp, payload, fetched_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (trade_id, str(fill.get("symbol") or "").upper(), timestamp, encoded, utc_now().isoformat()),
            )
        return existed is None

    def list_user_account_fills(self, since: datetime | None = None, limit: int = 10000) -> list[dict]:
        query = "SELECT payload FROM user_account_fills"
        params: list[str | int] = []
        if since is not None:
            query += " WHERE timestamp >= ?"
            params.append(since.isoformat())
        query += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def get_paper_engine_state(self, symbol: str, timeframe: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM paper_engine_states WHERE symbol = ? AND timeframe = ?",
                (symbol.upper(), timeframe),
            ).fetchone()
        return json.loads(row["state"]) if row else None

    def upsert_paper_engine_state(self, symbol: str, timeframe: str, state: dict) -> bool:
        encoded = json.dumps(state, ensure_ascii=True, sort_keys=True)
        with self._connect() as connection:
            current = connection.execute(
                "SELECT state FROM paper_engine_states WHERE symbol = ? AND timeframe = ?",
                (symbol.upper(), timeframe),
            ).fetchone()
            if current and current["state"] == encoded:
                return False
            connection.execute(
                """
                INSERT INTO paper_engine_states (symbol, timeframe, state, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe) DO UPDATE SET
                    state = excluded.state,
                    updated_at = excluded.updated_at
                """,
                (symbol.upper(), timeframe, encoded, utc_now().isoformat()),
            )
        return True

    def upsert_paper_gate_funnel(self, record: dict) -> bool:
        symbol = str(record.get("symbol") or "").upper()
        timeframe = str(record.get("timeframe") or "4h")
        bar_at = str(record.get("bar_at") or "")
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, default=_json_cache_default)
        with self._connect() as connection:
            current = connection.execute(
                "SELECT 1 FROM paper_gate_funnel WHERE symbol = ? AND timeframe = ? AND bar_at = ?",
                (symbol, timeframe, bar_at),
            ).fetchone()
            if current:
                return False
            connection.execute(
                """
                INSERT INTO paper_gate_funnel (symbol, timeframe, bar_at, payload, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (symbol, timeframe, bar_at, encoded, utc_now().isoformat()),
            )
        return True

    def list_paper_gate_funnel(
        self,
        since: datetime | None = None,
        symbol: str | None = None,
        limit: int = 10000,
    ) -> list[dict]:
        query = "SELECT payload FROM paper_gate_funnel"
        clauses: list[str] = []
        params: list[str | int] = []
        if since is not None:
            clauses.append("bar_at >= ?")
            params.append(_aware_dt(since).isoformat())
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY bar_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def add_judgment(self, judgment: JudgmentLedgerEntry) -> JudgmentLedgerEntry:
        with self._connect() as connection:
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
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM judgment_ledger WHERE position_id = ? ORDER BY as_of DESC LIMIT ?",
                (str(position_id), limit),
            ).fetchall()
        return [JudgmentLedgerEntry.model_validate_json(row["payload"]) for row in rows]

    def add_judgment_score(self, score: JudgmentScore) -> JudgmentScore:
        with self._connect() as connection:
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

    def list_judgment_scores(
        self,
        position_id: UUID | None = None,
        trade_id: UUID | None = None,
        limit: int = 500,
    ) -> list[JudgmentScore]:
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
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [JudgmentScore.model_validate_json(row["payload"]) for row in rows]

    def add_calibration_suggestion(self, suggestion: CalibrationSuggestion) -> CalibrationSuggestion:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO calibration_suggestions
                    (id, status, created_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(suggestion.id),
                    suggestion.status,
                    suggestion.created_at.isoformat(),
                    _dump_model(suggestion),
                ),
            )
        return suggestion

    def get_calibration_suggestion(self, suggestion_id: UUID) -> CalibrationSuggestion | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM calibration_suggestions WHERE id = ?",
                (str(suggestion_id),),
            ).fetchone()
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
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [CalibrationSuggestion.model_validate_json(row["payload"]) for row in rows]

    def add_engine_param_version(self, version: EngineParamVersion) -> EngineParamVersion:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, payload FROM engine_params WHERE param = ? AND status = 'active'",
                (version.param,),
            ).fetchall()
            for row in rows:
                existing = EngineParamVersion.model_validate_json(row["payload"])
                if existing.id == version.id:
                    continue
                superseded = existing.model_copy(update={"status": "superseded"})
                connection.execute(
                    "UPDATE engine_params SET status = ?, payload = ? WHERE id = ?",
                    (superseded.status, _dump_model(superseded), str(superseded.id)),
                )
            connection.execute(
                """
                INSERT OR REPLACE INTO engine_params
                    (id, param, status, approved_at, suggestion_id, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(version.id),
                    version.param,
                    version.status,
                    version.approved_at.isoformat(),
                    str(version.suggestion_id) if version.suggestion_id else None,
                    _dump_model(version),
                ),
            )
        return version

    def latest_engine_param(self, param: str) -> EngineParamVersion | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM engine_params WHERE param = ? AND status = 'active' ORDER BY approved_at DESC LIMIT 1",
                (param,),
            ).fetchone()
        return EngineParamVersion.model_validate_json(row["payload"]) if row else None

    def list_engine_params(self, limit: int = 100) -> list[EngineParamVersion]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM engine_params ORDER BY approved_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [EngineParamVersion.model_validate_json(row["payload"]) for row in rows]

    def add_autonomy_log(self, log: AutonomyLog) -> AutonomyLog:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO autonomy_log
                    (id, signature_key, new_state, transition, autonomous, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(log.id),
                    log.signature_key,
                    log.new_state,
                    log.transition,
                    1 if log.autonomous else 0,
                    log.created_at.isoformat(),
                    _dump_model(log),
                ),
            )
        return log

    def list_autonomy_logs(
        self,
        signature_key: str | None = None,
        new_state: str | None = None,
        since: datetime | None = None,
        limit: int = 200,
    ) -> list[AutonomyLog]:
        query = "SELECT payload FROM autonomy_log"
        clauses: list[str] = []
        params: list[str | int] = []
        if signature_key:
            clauses.append("signature_key = ?")
            params.append(signature_key)
        if new_state:
            clauses.append("new_state = ?")
            params.append(new_state)
        if since is not None:
            clauses.append("created_at >= ?")
            params.append(_aware_dt(since).isoformat())
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [AutonomyLog.model_validate_json(row["payload"]) for row in rows]

    def latest_autonomy_states(self) -> dict[str, str]:
        # SQLite bare-column + MAX 집계: 각 그룹에서 MAX(created_at) 행의 컬럼을 반환.
        with self._connect() as connection:
            rows = connection.execute("SELECT signature_key, new_state, MAX(created_at) FROM autonomy_log GROUP BY signature_key").fetchall()
        return {str(row["signature_key"]): str(row["new_state"]) for row in rows}

    def add_market_snapshot(self, snapshot: MarketSnapshotRecord) -> MarketSnapshotRecord:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO market_snapshots
                    (id, symbol, timeframe, provider, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(snapshot.id),
                    snapshot.symbol.upper(),
                    snapshot.timeframe,
                    snapshot.provider,
                    snapshot.created_at.isoformat(),
                    _dump_model(snapshot),
                ),
            )
        return snapshot

    def add_derivative_snapshot(self, snapshot: DerivativeDataSnapshot) -> DerivativeDataSnapshot:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO derivative_snapshots
                    (id, symbol, provider, tier, as_of, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(snapshot.id),
                    snapshot.symbol.upper(),
                    snapshot.provider,
                    snapshot.tier,
                    snapshot.as_of.isoformat(),
                    snapshot.created_at.isoformat(),
                    _dump_model(snapshot),
                ),
            )
        return snapshot

    def list_derivative_snapshots(self, symbol: str | None = None, provider: str | None = None, limit: int = 100) -> list[DerivativeDataSnapshot]:
        query = "SELECT payload FROM derivative_snapshots"
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if provider:
            clauses.append("provider = ?")
            params.append(provider)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [DerivativeDataSnapshot.model_validate_json(row["payload"]) for row in rows]

    def latest_derivative_snapshot(self, symbol: str, provider: str | None = None) -> DerivativeDataSnapshot | None:
        snapshots = self.list_derivative_snapshots(symbol=symbol, provider=provider, limit=1)
        return snapshots[0] if snapshots else None

    def delete_derivative_snapshots_before(self, cutoff: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM derivative_snapshots WHERE as_of < ?",
                (cutoff.isoformat(),),
            )
            return int(cursor.rowcount or 0)

    def add_derivative_metric(self, metric: DerivativeMetric) -> DerivativeMetric:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO deriv_metrics
                    (id, symbol, source, tier, as_of, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(metric.id),
                    metric.symbol.upper(),
                    metric.source,
                    metric.tier,
                    metric.as_of.isoformat(),
                    metric.created_at.isoformat(),
                    _dump_model(metric),
                ),
            )
        return metric

    def list_derivative_metrics(self, symbol: str | None = None, source: str | None = None, limit: int = 100) -> list[DerivativeMetric]:
        query = "SELECT payload FROM deriv_metrics"
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if source:
            clauses.append("source = ?")
            params.append(source)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY as_of DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [DerivativeMetric.model_validate_json(row["payload"]) for row in rows]

    def latest_derivative_metric(self, symbol: str, source: str | None = None) -> DerivativeMetric | None:
        metrics = self.list_derivative_metrics(symbol=symbol, source=source, limit=1)
        return metrics[0] if metrics else None

    def delete_derivative_metrics_before(self, cutoff: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM deriv_metrics WHERE as_of < ?", (cutoff.isoformat(),))
            return int(cursor.rowcount or 0)

    def add_liquidation_event(self, event: LiquidationEvent) -> LiquidationEvent:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO liquidation_events
                    (id, symbol, source, interval, bucket_start, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.id),
                    event.symbol.upper(),
                    event.source,
                    event.interval,
                    event.bucket_start.isoformat(),
                    event.created_at.isoformat(),
                    _dump_model(event),
                ),
            )
        return event

    def list_liquidation_events(self, symbol: str | None = None, source: str | None = None, limit: int = 100) -> list[LiquidationEvent]:
        query = "SELECT payload FROM liquidation_events"
        clauses: list[str] = []
        params: list[str | int] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if source:
            clauses.append("source = ?")
            params.append(source)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY bucket_start DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [LiquidationEvent.model_validate_json(row["payload"]) for row in rows]

    def delete_liquidation_events_before(self, cutoff: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM liquidation_events WHERE bucket_start < ?",
                (cutoff.isoformat(),),
            )
            return int(cursor.rowcount or 0)

    def add_database_maintenance_event(self, event: DatabaseMaintenanceEvent) -> DatabaseMaintenanceEvent:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO database_maintenance_events
                    (id, event_type, status, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(event.id),
                    event.event_type,
                    event.status,
                    event.created_at.isoformat(),
                    _dump_model(event),
                ),
            )
        return event

    def list_database_maintenance_events(self, event_type: str | None = None, limit: int = 50) -> list[DatabaseMaintenanceEvent]:
        query = "SELECT payload FROM database_maintenance_events"
        params: tuple[str | int, ...]
        if event_type:
            query += " WHERE event_type = ?"
            params = (event_type, limit)
        else:
            params = (limit,)
        query += " ORDER BY created_at DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [DatabaseMaintenanceEvent.model_validate_json(row["payload"]) for row in rows]

    def add_research_run(self, run: ResearchRun) -> ResearchRun:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO research_runs
                    (id, symbol, timeframe, report_id, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run.id),
                    run.symbol.upper(),
                    run.timeframe,
                    str(run.report_id),
                    run.created_at.isoformat(),
                    _dump_model(run),
                ),
            )
        return run

    def get_research_run(self, run_id: UUID) -> ResearchRun | None:
        with self._connect() as connection:
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
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [ResearchRun.model_validate_json(row["payload"]) for row in rows]

    def add_agent_output(self, output: AgentOutput) -> AgentOutput:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO agent_outputs
                    (id, research_run_id, agent_name, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(output.id),
                    str(output.research_run_id),
                    output.agent_name,
                    output.created_at.isoformat(),
                    _dump_model(output),
                ),
            )
        return output

    def list_agent_outputs(self, research_run_id: UUID) -> list[AgentOutput]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM agent_outputs WHERE research_run_id = ? ORDER BY created_at ASC",
                (str(research_run_id),),
            ).fetchall()
        return [AgentOutput.model_validate_json(row["payload"]) for row in rows]

    def add_shadow_profile(self, profile: ShadowProfile) -> ShadowProfile:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO shadow_profiles (shadow_id, created_at, payload) VALUES (?, ?, ?)",
                (
                    profile.shadow_id,
                    profile.created_at.isoformat(),
                    _dump_model(profile),
                ),
            )
        return profile

    def get_shadow_profile(self, shadow_id: str) -> ShadowProfile | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM shadow_profiles WHERE shadow_id = ?", (shadow_id,)).fetchone()
        return ShadowProfile.model_validate_json(row["payload"]) if row else None

    def list_shadow_profiles(self, limit: int = 20) -> list[ShadowProfile]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM shadow_profiles ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [ShadowProfile.model_validate_json(row["payload"]) for row in rows]

    def add_decision_memory(self, memory: DecisionMemory) -> DecisionMemory:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO decision_memories
                    (id, symbol, memory_type, created_at, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(memory.id),
                    memory.symbol.upper() if memory.symbol else None,
                    memory.memory_type,
                    memory.created_at.isoformat(),
                    _dump_model(memory),
                ),
            )
        return memory

    def list_decision_memories(self, symbol: str | None = None, limit: int = 20) -> list[DecisionMemory]:
        if symbol:
            query = "SELECT payload FROM decision_memories WHERE symbol = ? OR symbol IS NULL ORDER BY created_at DESC LIMIT ?"
            params = (symbol.upper(), limit)
        else:
            query = "SELECT payload FROM decision_memories ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [DecisionMemory.model_validate_json(row["payload"]) for row in rows]

    def add_validation_run(self, run: ValidationRun) -> ValidationRun:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO validation_runs
                    (id, symbol, timeframe, strategy_type, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run.id),
                    run.symbol.upper(),
                    run.timeframe,
                    run.strategy_type,
                    run.created_at.isoformat(),
                    _dump_model(run),
                ),
            )
        return run

    def get_validation_run(self, run_id: UUID) -> ValidationRun | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM validation_runs WHERE id = ?", (str(run_id),)).fetchone()
        return ValidationRun.model_validate_json(row["payload"]) if row else None

    def list_validation_runs(self, limit: int = 20) -> list[ValidationRun]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM validation_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [ValidationRun.model_validate_json(row["payload"]) for row in rows]

    def list_watchlist(self) -> list[WatchlistItem]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload FROM watchlist ORDER BY added_at DESC").fetchall()
        return [WatchlistItem.model_validate_json(row["payload"]) for row in rows]

    def upsert_watchlist_item(self, item: WatchlistItem) -> WatchlistItem:
        normalized = item.symbol.upper()
        stored = item.model_copy(update={"symbol": normalized})
        with self._connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO watchlist (symbol, added_at, asset_class, payload) VALUES (?, ?, ?, ?)",
                (normalized, stored.added_at.isoformat(), stored.asset_class, _dump_model(stored)),
            )
        return stored

    def remove_watchlist_item(self, symbol: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),))
        return cursor.rowcount > 0

    def replace_symbol_catalog(self, symbols: list[CatalogSymbol]) -> int:
        with self._connect() as connection:
            connection.execute("DELETE FROM symbol_catalog")
            connection.executemany(
                "INSERT OR REPLACE INTO symbol_catalog (symbol, updated_at, asset_class, payload) VALUES (?, ?, ?, ?)",
                [
                    (
                        item.symbol.upper(),
                        item.updated_at.isoformat(),
                        item.asset_class,
                        _dump_model(item),
                    )
                    for item in symbols
                ],
            )
        return len(symbols)

    def search_symbols(self, query: str, limit: int = 20) -> list[CatalogSymbol]:
        needle = query.strip().upper()
        with self._connect() as connection:
            if needle:
                rows = connection.execute(
                    """
                    SELECT payload
                    FROM symbol_catalog
                    WHERE symbol LIKE ? OR asset_class LIKE ? OR payload LIKE ?
                    ORDER BY length(symbol), symbol
                    LIMIT ?
                    """,
                    (f"%{needle}%", f"%{needle.lower()}%", f"%{needle}%", limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload FROM symbol_catalog ORDER BY length(symbol), symbol LIMIT ?",
                    (limit,),
                ).fetchall()
        return [CatalogSymbol.model_validate_json(row["payload"]) for row in rows]

    def symbol_catalog_updated_at(self) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute("SELECT MAX(updated_at) AS updated_at FROM symbol_catalog").fetchone()
        value = row["updated_at"] if row else None
        return datetime.fromisoformat(value) if value else None

    def get_calibration_report_cache(self, report_key: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload, computed_at FROM calibration_report_cache WHERE report_key = ?",
                (report_key,),
            ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            return None
        return {"payload": payload, "computed_at": row["computed_at"]} if isinstance(payload, dict) else None

    def upsert_calibration_report_cache(self, report_key: str, payload: dict) -> dict:
        computed_at = utc_now().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO calibration_report_cache (report_key, payload, computed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(report_key) DO UPDATE SET
                    payload = excluded.payload,
                    computed_at = excluded.computed_at
                """,
                (
                    report_key,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, default=_json_cache_default),
                    computed_at,
                ),
            )
        return {"payload": dict(payload), "computed_at": computed_at}

    def add_entry_scenario(self, scenario: EntryScenario) -> EntryScenario:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO entry_scenarios
                    (id, symbol, direction, linked_position_id, created_at, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(scenario.id),
                    scenario.symbol.upper(),
                    scenario.direction.value,
                    str(scenario.linked_position_id) if scenario.linked_position_id else None,
                    scenario.created_at.isoformat(),
                    _dump_model(scenario),
                ),
            )
        return scenario

    def get_entry_scenario(self, scenario_id: UUID) -> EntryScenario | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM entry_scenarios WHERE id = ?", (str(scenario_id),)).fetchone()
        return EntryScenario.model_validate_json(row["payload"]) if row else None

    def list_entry_scenarios(self, symbol: str | None = None, limit: int = 50) -> list[EntryScenario]:
        with self._connect() as connection:
            if symbol:
                rows = connection.execute(
                    "SELECT payload FROM entry_scenarios WHERE symbol = ? ORDER BY created_at DESC LIMIT ?",
                    (symbol.upper(), limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload FROM entry_scenarios ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [EntryScenario.model_validate_json(row["payload"]) for row in rows]

    def find_matching_scenario(self, symbol: str, direction: str, within_hours: int = 72) -> EntryScenario | None:
        from datetime import timedelta

        cutoff = (utc_now() - timedelta(hours=within_hours)).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM entry_scenarios
                WHERE symbol = ? AND direction = ? AND linked_position_id IS NULL AND created_at >= ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (symbol.upper(), direction, cutoff),
            ).fetchone()
        return EntryScenario.model_validate_json(row["payload"]) if row else None

    def link_scenario_position(self, scenario_id: UUID, position_id: UUID) -> EntryScenario | None:
        scenario = self.get_entry_scenario(scenario_id)
        if scenario is None:
            return None
        updated = scenario.model_copy(update={"linked_position_id": position_id})
        return self.add_entry_scenario(updated)

    def upsert_whale_wallet(self, wallet: WhaleWallet) -> WhaleWallet:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO whale_wallets (address, active, added_at, payload)
                VALUES (?, ?, ?, ?)
                """,
                (wallet.address.lower(), int(wallet.active), wallet.added_at.isoformat(), _dump_model(wallet)),
            )
        return wallet

    def get_whale_wallet(self, address: str) -> WhaleWallet | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM whale_wallets WHERE address = ?",
                (address.lower(),),
            ).fetchone()
        return WhaleWallet.model_validate_json(row["payload"]) if row else None

    def list_whale_wallets(self, active: bool | None = None, limit: int = 20) -> list[WhaleWallet]:
        with self._connect() as connection:
            if active is None:
                rows = connection.execute(
                    "SELECT payload FROM whale_wallets ORDER BY added_at ASC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload FROM whale_wallets WHERE active = ? ORDER BY added_at ASC LIMIT ?",
                    (int(active), limit),
                ).fetchall()
        return [WhaleWallet.model_validate_json(row["payload"]) for row in rows]

    def remove_whale_wallet(self, address: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM whale_wallets WHERE address = ?", (address.lower(),))
        return cursor.rowcount > 0

    def add_whale_event(self, event: WhaleEvent) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO whale_events
                    (id, wallet_address, symbol, event_type, event_at, size_usd, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.id),
                    event.wallet_address.lower(),
                    event.symbol.upper(),
                    event.event,
                    event.event_at.isoformat(),
                    event.size_usd,
                    _dump_model(event),
                ),
            )
        return cursor.rowcount > 0

    def list_whale_events(
        self,
        symbol: str | None = None,
        wallet_address: str | None = None,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[WhaleEvent]:
        clauses: list[str] = []
        params: list[object] = []
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol.upper())
        if wallet_address:
            clauses.append("wallet_address = ?")
            params.append(wallet_address.lower())
        if since:
            clauses.append("event_at >= ?")
            params.append(since.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT payload FROM whale_events{where} ORDER BY event_at DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [WhaleEvent.model_validate_json(row["payload"]) for row in rows]

    def get_whale_position_state(self, wallet_address: str, coin: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM whale_position_states WHERE wallet_address = ? AND coin = ?",
                (wallet_address.lower(), coin.upper()),
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    def list_whale_position_states(self, wallet_address: str | None = None, limit: int = 500) -> list[dict]:
        with self._connect() as connection:
            if wallet_address:
                rows = connection.execute(
                    "SELECT payload FROM whale_position_states WHERE wallet_address = ? ORDER BY coin LIMIT ?",
                    (wallet_address.lower(), limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT payload FROM whale_position_states ORDER BY wallet_address, coin LIMIT ?",
                    (limit,),
                ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def upsert_whale_position_state(self, wallet_address: str, coin: str, state: dict) -> bool:
        payload = json.dumps(state, ensure_ascii=False, default=_json_cache_default)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO whale_position_states (wallet_address, coin, payload)
                VALUES (?, ?, ?)
                """,
                (wallet_address.lower(), coin.upper(), payload),
            )
        return True

    def delete_whale_position_state(self, wallet_address: str, coin: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM whale_position_states WHERE wallet_address = ? AND coin = ?",
                (wallet_address.lower(), coin.upper()),
            )
        return cursor.rowcount > 0

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


def create_repository(database_url: str) -> Repository:
    if database_url == "memory://":
        return MemoryRepository()
    if database_url.startswith("sqlite:///"):
        return SQLiteRepository(database_url.removeprefix("sqlite:///"))
    raise ValueError(f"Unsupported database URL: {database_url}")


def _dump_model(model) -> str:
    return json.dumps(model.model_dump(mode="json"), ensure_ascii=False)


def _json_cache_default(value) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _same_directional_bar(current: dict | None, candidate: dict) -> bool:
    """Only a newly confirmed candle may mutate a live hysteresis state."""
    if not isinstance(current, dict) or not isinstance(candidate, dict):
        return False
    current_bar = current.get("last_bar_at")
    candidate_bar = candidate.get("last_bar_at")
    return bool(current_bar and candidate_bar and current_bar == candidate_bar)


def _aware_dt(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _timestamp_or_min(value: object) -> datetime:
    if isinstance(value, datetime):
        return _aware_dt(value)
    if isinstance(value, str):
        try:
            return _aware_dt(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)
