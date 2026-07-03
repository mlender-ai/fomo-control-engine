from datetime import datetime, timezone
from enum import Enum
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
    synced_at: datetime | None = None
    memo: str = ""
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
    created_at: datetime = Field(default_factory=utc_now)
