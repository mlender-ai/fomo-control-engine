from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Candle(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float | None = None


class FundingRate(BaseModel):
    symbol: str
    funding_rate: float
    funding_rate_interval_hours: int | None = None
    next_update: datetime | None = None
    min_funding_rate: float | None = None
    max_funding_rate: float | None = None


class OpenInterest(BaseModel):
    symbol: str
    size: float
    timestamp: datetime | None = None


class BitgetPosition(BaseModel):
    symbol: str
    hold_side: Literal["long", "short"]
    margin_coin: str
    total: float
    available: float | None = None
    locked: float | None = None
    leverage: float | None = None
    open_price_avg: float
    mark_price: float | None = None
    unrealized_pl: float | None = None
    liquidation_price: float | None = None
    margin_mode: str | None = None
    position_mode: str | None = None
    margin_ratio: float | None = None
    break_even_price: float | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

