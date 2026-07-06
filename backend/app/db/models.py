from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Direction(str, Enum):
    long = "long"
    short = "short"


class PositionStatus(str, Enum):
    open = "open"
    closed = "closed"
    missing_from_exchange = "missing_from_exchange"
    needs_exit_record = "needs_exit_record"


class ScoreBreakdown(BaseModel):
    structure: int
    volume: int
    liquidity: int
    momentum: int
    risk: int
    fomo: int


class MarketCandle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float | None = None
    session: str | None = None
    is_regular_session: bool | None = None


class DataQuality(BaseModel):
    ohlcv_ok: bool = True
    funding_ok: bool = True
    open_interest_ok: bool = True
    min_candles_met: bool = True
    fallback_used: bool = False
    candles: int = 0
    last_candle_at: datetime | None = None


class MarketSnapshot(BaseModel):
    symbol: str
    timeframe: str = "4h"
    price: float
    change_24h: float
    funding_rate: float
    open_interest_change: float
    candles: list[MarketCandle]
    provider: str = "mock"
    data_quality: DataQuality = Field(default_factory=DataQuality)


class Report(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    timeframe: str = "4h"
    price: float
    change_24h: float
    entry_score: int
    scores: ScoreBreakdown
    state_label: str
    raw_json: dict
    report: str
    provider: str = "mock"
    data_quality: DataQuality = Field(default_factory=DataQuality)
    created_at: datetime = Field(default_factory=utc_now)


class ReportRequest(BaseModel):
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"


class PositionCreate(BaseModel):
    symbol: str
    direction: Direction = Direction.long
    entry_price: float
    quantity: float
    leverage: float = 1
    entry_report_id: UUID | None = None
    memo: str = ""
    entry_memo: str = ""
    planned_stop_price: float | None = None
    planned_take_profit_price: float | None = None
    thesis_text: str = ""
    entry_direction_score: int | None = None


class Position(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    direction: Direction
    entry_price: float
    quantity: float
    leverage: float = 1
    status: PositionStatus = PositionStatus.open
    entry_report_id: UUID | None = None
    entry_score: int | None = None
    entry_direction_score: int | None = None
    current_score: int | None = None
    current_price: float | None = None
    mark_price: float | None = None
    pnl_percent: float = 0
    unrealized_pl: float | None = None
    margin_size: float | None = None
    pnl_source: Literal["exchange", "computed"] = "computed"
    liquidation_price: float | None = None
    margin_mode: str | None = None
    position_mode: str | None = None
    margin_ratio: float | None = None
    break_even_price: float | None = None
    source: str = "manual"
    detected_source: str = "manual"
    synced_at: datetime | None = None
    memo: str = ""
    entry_memo: str = ""
    planned_stop_price: float | None = None
    planned_take_profit_price: float | None = None
    thesis_text: str = ""
    scenario_id: UUID | None = None
    opened_at: datetime = Field(default_factory=utc_now)
    closed_at: datetime | None = None


class MonitoringLog(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    position_id: UUID
    report_id: UUID
    current_price: float
    pnl_percent: float
    score_change: int
    logic_status: str
    report_text: str
    created_at: datetime = Field(default_factory=utc_now)


class PositionHealthComponents(BaseModel):
    survival: int = 50
    pnl_state: int = 50
    thesis_integrity: int
    structure: int = 50
    flow: int = 50
    chart_structure: int
    risk_safety: int
    momentum_volume: int
    liquidity_funding: int
    pnl_protection: int = 50
    liquidation_buffer: int = 50
    direction_alignment: int = 50
    formula_version: str = "health_v2"


class PositionSnapshot(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    position_id: UUID
    symbol: str
    as_of: datetime = Field(default_factory=utc_now)
    mark_price: float | None = None
    pnl_percent: float = 0
    pnl_amount: float | None = None
    pnl_source: Literal["exchange", "computed"] = "computed"
    liquidation_price: float | None = None
    liquidation_distance_pct: float | None = None
    health_score: int
    severity_rank: int = 0
    status_label: str
    risk_score: int
    score_json: dict
    analysis_json: dict
    created_at: datetime = Field(default_factory=utc_now)


class PositionInsight(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    position_id: UUID
    snapshot_id: UUID | None = None
    insight_type: str = "position_state"
    as_of: datetime = Field(default_factory=utc_now)
    health_score: int
    status_label: str
    input_json: dict
    action_plan: dict = Field(default_factory=dict)
    insight_text: str
    insight_source: str = "template"
    fallback_reason: str | None = None
    auto_generated: bool = False
    age_minutes: int | None = None
    is_stale: bool = False
    price_drift_pct: float | None = None
    basis_mark_price: float | None = None
    stale_reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class PositionEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    position_id: UUID
    event_type: str
    severity: str
    title: str
    description: str = ""
    data: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class AlertRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    rule_id: str
    position_id: UUID | None = None
    symbol: str = ""
    severity: Literal["info", "action", "warn", "critical"]
    fired_at: datetime = Field(default_factory=utc_now)
    payload: dict = Field(default_factory=dict)
    delivered: bool = False
    acked: bool = False
    created_at: datetime = Field(default_factory=utc_now)


class AlertResponseRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    alert_id: UUID
    position_id: UUID
    rule_id: str
    symbol: str = ""
    response: Literal["closed_full", "reduced", "added", "held", "stop_moved"]
    detected_at: datetime = Field(default_factory=utc_now)
    price_at_response: float | None = None
    quantity_at_alert: float | None = None
    quantity_at_response: float | None = None
    planned_stop_at_alert: float | None = None
    planned_stop_at_response: float | None = None
    outcome: Literal["response_good", "response_costly", "inconclusive"] = "inconclusive"
    result_detail: str = ""
    metrics: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ScoutSnapshot(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    timeframe: str = "4h"
    as_of: datetime = Field(default_factory=utc_now)
    mark_price: float | None = None
    setup_proximity_pct: float | None = None
    summary: dict = Field(default_factory=dict)
    analysis: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ArmedSetup(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    timeframe: str = "4h"
    source: Literal["auto", "manual"] = "auto"
    setup_type: str
    direction: Literal["long", "short"] | None = None
    trigger_price: float | None = None
    trigger_label: str
    trigger_condition: str
    distance_pct: float | None = None
    confidence: int | None = None
    basis: str = ""
    status: Literal["armed", "triggered", "invalidated", "disarmed"] = "armed"
    preview: dict = Field(default_factory=dict)
    snapshot_id: UUID | None = None
    judgment_id: str | None = None
    linked_scenario_id: UUID | None = None
    setup_near_alerted_at: datetime | None = None
    triggered_at: datetime | None = None
    invalidated_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)


class EntryIntent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    timeframe: str = "4h"
    direction: Literal["long", "short"]
    zone_lower: float
    zone_upper: float
    conditions: list[
        Literal[
            "price_in_zone",
            "sweep_confirmed",
            "wyckoff_event",
            "volume_spike",
            "briefing_aligned",
        ]
    ] = Field(default_factory=lambda: ["price_in_zone"])
    tolerance: Literal["tight", "normal", "loose"] = "normal"
    tolerance_pct: float = 1.5
    status: Literal["active", "triggered", "partial", "invalidated", "expired", "cancelled"] = "active"
    note: str = ""
    preview: dict = Field(default_factory=dict)
    sim_preview_id: UUID | None = None
    judgment_id: str | None = None
    condition_state: dict = Field(default_factory=dict)
    approaching_alerted_at: datetime | None = None
    partial_alerted_at: datetime | None = None
    zone_entered_alerted_at: datetime | None = None
    triggered_at: datetime | None = None
    invalidated_at: datetime | None = None
    expires_at: datetime
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)


class PositionMemoUpdate(BaseModel):
    memo: str | None = None
    entry_memo: str | None = None
    planned_stop_price: float | None = None
    planned_take_profit_price: float | None = None
    thesis_text: str | None = None


class ExitRequest(BaseModel):
    exit_price: float
    exit_reason: str
    memo: str = ""


class Trade(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    position_id: UUID
    symbol: str
    direction: Direction
    entry_price: float
    exit_price: float
    quantity: float
    pnl_percent: float
    pnl_amount: float
    entry_score: int | None
    exit_score: int | None
    holding_minutes: int
    exit_reason: str
    review_text: str
    review_v2: dict = Field(default_factory=dict)
    judgment_scorecard: dict = Field(default_factory=dict)
    memo: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class TradeMemoUpdate(BaseModel):
    memo: str = ""


class JudgmentLedgerEntry(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    judgment_id: str
    position_id: UUID
    source_type: str
    source_id: str | None = None
    as_of: datetime
    type: str
    claim: dict = Field(default_factory=dict)
    confidence: int | None = None
    param_version: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class JudgmentScore(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    judgment_id: str
    position_id: UUID
    trade_id: UUID | None = None
    judgment_type: str
    claim: dict = Field(default_factory=dict)
    confidence: int | None = None
    outcome: Literal["correct", "wrong", "whipsaw", "untested"]
    detail: str
    metrics: dict = Field(default_factory=dict)
    param_version: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class CalibrationSuggestion(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    suggestion_type: str
    title: str
    rationale: str
    proposed_change: dict
    sample_size: int
    status: Literal["pending", "approved", "rejected"] = "pending"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class EngineParamVersion(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    param: str
    old_value: float | int | str | bool | None = None
    new_value: float | int | str | bool
    suggestion_id: UUID | None = None
    status: Literal["active", "superseded"] = "active"
    approved_at: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)


class MarketSnapshotRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    timeframe: str
    provider: str
    candle_count: int
    latest_price: float
    latest_candle_time: datetime | None = None
    data_quality: dict
    indicators: dict
    scores: dict
    reason_codes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)


class DerivativeDataSnapshot(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    provider: str
    tier: Literal["bitget_public", "coinglass"]
    as_of: datetime = Field(default_factory=utc_now)
    open_interest: float | None = None
    open_interest_value: float | None = None
    open_interest_change_pct: float | None = None
    funding_rate: float | None = None
    funding_rate_interval_hours: int | None = None
    next_funding_time: datetime | None = None
    long_short_ratio: float | None = None
    long_account_ratio: float | None = None
    short_account_ratio: float | None = None
    taker_buy_sell_ratio: float | None = None
    top_long_short_ratio: float | None = None
    oi_weighted_funding_rate: float | None = None
    liquidation_clusters: list[dict] = Field(default_factory=list)
    data_quality: dict = Field(default_factory=dict)
    source_status: Literal["ok", "partial", "locked", "error"] = "ok"
    notes: list[str] = Field(default_factory=list)
    raw_json: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class DerivativeMetric(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    source: Literal["bitget", "coinglass"]
    tier: Literal["bitget_public", "coinglass"]
    as_of: datetime = Field(default_factory=utc_now)
    open_interest: float | None = None
    open_interest_value: float | None = None
    oi_change_pct: float | None = None
    funding: float | None = None
    funding_interval_hours: int | None = None
    funding_next: datetime | None = None
    taker_ls: float | None = None
    top_ls: float | None = None
    long_account_ratio: float | None = None
    short_account_ratio: float | None = None
    oi_weighted_funding: float | None = None
    source_status: Literal["ok", "partial", "locked", "error"] = "ok"
    data_quality: dict = Field(default_factory=dict)
    coverage: dict = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    raw_json: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class LiquidationEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    source: Literal["coinglass"]
    interval: str
    bucket_start: datetime
    long_liquidation_usd: float = 0.0
    short_liquidation_usd: float = 0.0
    source_status: Literal["ok", "partial", "locked", "error"] = "ok"
    data_quality: dict = Field(default_factory=dict)
    raw_json: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class DatabaseMaintenanceEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    event_type: Literal["migration", "backup", "retention", "error"]
    status: Literal["ok", "skipped", "error"]
    message: str = ""
    details: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class AgentOutput(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    research_run_id: UUID
    agent_name: str
    confidence: float
    stance: str
    raw_json: dict
    text_output: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class ResearchRun(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    timeframe: str
    report_id: UUID
    snapshot_id: UUID
    entry_score: int
    fomo_index: int
    state_label: str
    final_summary: str
    final_action_label: str
    raw_input: dict
    raw_output: dict
    created_at: datetime = Field(default_factory=utc_now)


class ResearchRunRequest(BaseModel):
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"
    mode: str = "entry_review"


class ShadowRule(BaseModel):
    rule_id: str
    human_text: str
    entry_conditions: dict
    exit_conditions: dict = Field(default_factory=dict)
    support_count: int
    coverage_rate: float
    avg_pnl: float
    avg_holding_minutes: int
    sample_trade_ids: list[str]
    weight: float = 1.0


class ShadowAttribution(BaseModel):
    noise_trades_pnl: float = 0
    early_exit_pnl: float = 0
    late_exit_pnl: float = 0
    overtrading_pnl: float = 0
    missed_signals_pnl: float = 0
    fomo_trades_pnl: float = 0
    counterfactual_trades: list[dict] = Field(default_factory=list)


class ShadowProfile(BaseModel):
    shadow_id: str
    created_at: datetime = Field(default_factory=utc_now)
    total_trades: int
    profitable_trades: int
    losing_trades: int
    date_range: tuple[datetime, datetime] | None = None
    profile_text: str
    rules: list[ShadowRule]
    fomo_patterns: list[dict]
    common_mistakes: list[dict]
    attribution: ShadowAttribution = Field(default_factory=ShadowAttribution)


class ShadowExtractRequest(BaseModel):
    min_trades: int = 10
    min_profitable_trades: int = 5


class DecisionMemory(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str | None = None
    memory_type: str
    source_trade_id: UUID | None = None
    source_research_run_id: UUID | None = None
    summary: str
    evidence: dict
    weight: float = 1.0
    created_at: datetime = Field(default_factory=utc_now)


class LiquidationCluster(BaseModel):
    price: float
    side: Literal["long_liquidation", "short_liquidation"]
    magnitude: float
    distance_pct: float
    priority: Literal["low", "medium", "high"]
    source: str = "oi_funding_proxy"


class LiquidationAnalysis(BaseModel):
    symbol: str
    timeframe: str
    current_price: float
    liquidity_score: int
    upper_clusters: list[LiquidationCluster]
    lower_clusters: list[LiquidationCluster]
    dominant_magnet: str
    asymmetry_score: int
    cascade_risk_up: str
    cascade_risk_down: str
    cascade_risk: str
    created_at: datetime = Field(default_factory=utc_now)


class LiquidityAnalyzeRequest(BaseModel):
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"


class ValidationRun(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    strategy_type: str
    symbol: str
    timeframe: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    params: dict
    summary: dict
    results: dict
    warnings: list[str]
    created_at: datetime = Field(default_factory=utc_now)


class WatchlistItem(BaseModel):
    symbol: str
    added_at: datetime = Field(default_factory=utc_now)
    note: str = ""
    default_timeframe: str = "4h"
    asset_class: Literal["crypto", "stock", "index", "unknown"] = "unknown"


class CatalogSymbol(BaseModel):
    symbol: str
    base_coin: str = ""
    quote_coin: str = ""
    status: str = ""
    asset_class: Literal["crypto", "stock", "index", "unknown"] = "unknown"
    source_category: str = ""
    funding_rate_interval_hours: int | None = None
    raw_metadata: dict = Field(default_factory=dict)
    maintenance_margin_rate: float | None = None
    taker_fee_rate: float | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class EntryChecklistItem(BaseModel):
    key: str
    label: str
    status: Literal["pass", "fail", "na"]
    reason: str = ""


class EntryScenario(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    symbol: str
    direction: Direction
    entry_price: float
    leverage: float
    margin_usdt: float | None = None
    margin_mode: str = "isolated"
    timeframe: str = "4h"
    estimated_liquidation: float | None = None
    action_plan: dict = Field(default_factory=dict)
    checklist: list[EntryChecklistItem] = Field(default_factory=list)
    rr_ratio: float | None = None
    analysis_as_of: datetime | None = None
    note: str = ""
    linked_position_id: UUID | None = None
    created_at: datetime = Field(default_factory=utc_now)


class ValidationRunRequest(BaseModel):
    strategy_type: str = "entry_score_threshold"
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"
    start: datetime | None = None
    end: datetime | None = None
    params: dict = Field(
        default_factory=lambda: {
            "entry_score_min": 75,
            "risk_score_max": 60,
            "fomo_index_max": 70,
        }
    )
    validation: dict = Field(default_factory=dict)
