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
    current_score: int | None = None
    current_price: float | None = None
    mark_price: float | None = None
    pnl_percent: float = 0
    unrealized_pl: float | None = None
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
    thesis_integrity: int
    chart_structure: int
    risk_safety: int
    momentum_volume: int
    liquidity_funding: int


class PositionSnapshot(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    position_id: UUID
    symbol: str
    mark_price: float | None = None
    pnl_percent: float = 0
    pnl_amount: float | None = None
    liquidation_price: float | None = None
    liquidation_distance_pct: float | None = None
    health_score: int
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
    health_score: int
    status_label: str
    input_json: dict
    insight_text: str
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
    memo: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class TradeMemoUpdate(BaseModel):
    memo: str = ""


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


class ValidationRunRequest(BaseModel):
    strategy_type: str = "entry_score_threshold"
    symbol: str = "BTCUSDT"
    timeframe: str = "4h"
    start: datetime | None = None
    end: datetime | None = None
    params: dict = Field(default_factory=lambda: {"entry_score_min": 75, "risk_score_max": 60, "fomo_index_max": 70})
    validation: dict = Field(default_factory=dict)
