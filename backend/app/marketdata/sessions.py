from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db.models import MarketCandle
from app.marketdata.assets import AssetClass, classify_asset_class

US_EASTERN = ZoneInfo("America/New_York")

US_MARKET_HOLIDAYS = {
    date(2026, 1, 1),
    date(2026, 1, 19),
    date(2026, 2, 16),
    date(2026, 4, 3),
    date(2026, 5, 25),
    date(2026, 6, 19),
    date(2026, 7, 3),
    date(2026, 9, 7),
    date(2026, 11, 26),
    date(2026, 12, 25),
}


@dataclass(frozen=True)
class SessionInfo:
    asset_class: AssetClass
    state: str
    label: str
    timezone: str
    is_trading_session: bool
    next_open_at: datetime | None = None
    seconds_until_open: int | None = None

    def as_dict(self) -> dict:
        return {
            "asset_class": self.asset_class,
            "state": self.state,
            "label": self.label,
            "timezone": self.timezone,
            "is_trading_session": self.is_trading_session,
            "next_open_at": self.next_open_at.isoformat() if self.next_open_at else None,
            "seconds_until_open": self.seconds_until_open,
        }


def session_info_for_symbol(symbol: str, asset_class: AssetClass | None = None, at: datetime | None = None) -> SessionInfo:
    current_class = asset_class or classify_asset_class(symbol)
    now = _aware_utc(at or datetime.now(timezone.utc))
    if current_class == "crypto":
        return SessionInfo(
            asset_class="crypto",
            state="continuous",
            label="24시간 거래",
            timezone="UTC",
            is_trading_session=True,
        )
    if current_class not in {"stock", "index"}:
        return SessionInfo(
            asset_class=current_class,
            state="unknown",
            label="세션 미분류",
            timezone="America/New_York",
            is_trading_session=True,
        )

    local = now.astimezone(US_EASTERN)
    if _is_market_closed_day(local.date()):
        next_open = _next_regular_open(local)
        return _closed_info(current_class, now, next_open)
    current_time = local.time()
    if time(9, 30) <= current_time < time(16, 0):
        return SessionInfo(
            asset_class=current_class,
            state="regular",
            label="본장",
            timezone="America/New_York",
            is_trading_session=True,
        )
    return SessionInfo(
        asset_class=current_class,
        state="extended",
        label="확장 세션",
        timezone="America/New_York",
        is_trading_session=True,
        next_open_at=_next_regular_open(local).astimezone(timezone.utc),
        seconds_until_open=max(0, int((_next_regular_open(local) - local).total_seconds())),
    )


def tag_candles_with_sessions(candles: list[MarketCandle], asset_class: AssetClass) -> list[MarketCandle]:
    tagged: list[MarketCandle] = []
    for candle in candles:
        info = _session_for_candle(candle.timestamp, asset_class)
        tagged.append(candle.model_copy(update={"session": info.state, "is_regular_session": info.state == "regular"}))
    return tagged


def filter_analysis_candles(candles: list[MarketCandle], asset_class: AssetClass) -> tuple[list[MarketCandle], int]:
    tagged = tag_candles_with_sessions(candles, asset_class)
    if asset_class not in {"stock", "index"}:
        return tagged, 0
    filtered = [candle for candle in tagged if candle.session != "closed"]
    return filtered, len(tagged) - len(filtered)


def _session_for_candle(timestamp: datetime, asset_class: AssetClass) -> SessionInfo:
    if asset_class not in {"stock", "index"}:
        return session_info_for_symbol("", asset_class, timestamp)
    local = _aware_utc(timestamp).astimezone(US_EASTERN)
    if _is_market_closed_day(local.date()):
        next_open = _next_regular_open(local)
        return _closed_info(asset_class, _aware_utc(timestamp), next_open)
    if time(9, 30) <= local.time() < time(16, 0):
        return SessionInfo(asset_class=asset_class, state="regular", label="본장", timezone="America/New_York", is_trading_session=True)
    return SessionInfo(asset_class=asset_class, state="extended", label="확장 세션", timezone="America/New_York", is_trading_session=True)


def _closed_info(asset_class: AssetClass, now_utc: datetime, next_open_local: datetime) -> SessionInfo:
    next_open_utc = next_open_local.astimezone(timezone.utc)
    return SessionInfo(
        asset_class=asset_class,
        state="closed",
        label="휴장",
        timezone="America/New_York",
        is_trading_session=False,
        next_open_at=next_open_utc,
        seconds_until_open=max(0, int((next_open_utc - now_utc).total_seconds())),
    )


def _next_regular_open(local: datetime) -> datetime:
    candidate_date = local.date()
    candidate_open = datetime.combine(candidate_date, time(9, 30), tzinfo=US_EASTERN)
    if local >= candidate_open or _is_market_closed_day(candidate_date):
        candidate_date += timedelta(days=1)
    while _is_market_closed_day(candidate_date):
        candidate_date += timedelta(days=1)
    return datetime.combine(candidate_date, time(9, 30), tzinfo=US_EASTERN)


def _is_market_closed_day(value: date) -> bool:
    return value.weekday() >= 5 or value in US_MARKET_HOLIDAYS


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
