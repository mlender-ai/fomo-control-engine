from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.analyst.briefing import build_analyst_briefing
from app.db.models import DataQuality, MarketCandle, MarketSnapshot
from app.positions.chart_analysis import build_chart_analysis
from app.report.engine import generate_report
from app.toss.store import TossStockStore

from .models import Market


def analyze_stock_candidate(
    store: TossStockStore,
    market: Market,
    symbol: str,
    *,
    current_price: float | None,
    prior_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the existing cross-asset analysis stack over persisted Toss candles.

    No stock-only invalidation or R/R implementation lives here: levels,
    scenarios and confluence are delegated to the same engines used by crypto.
    """
    timeframe = "1d"
    rows = store.latest_candles(market.value, symbol, timeframe, 240)
    if len(rows) < 100:
        return {"status": "insufficient_candles", "timeframe": timeframe, "candles": len(rows)}
    candles = [
        MarketCandle(
            timestamp=_timestamp(row["opened_at"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume") or 0),
        )
        for row in rows
    ]
    price = current_price or candles[-1].close
    change = (candles[-1].close / candles[-2].close - 1) * 100 if len(candles) > 1 and candles[-2].close > 0 else 0.0
    snapshot = MarketSnapshot(
        symbol=symbol.upper(),
        timeframe=timeframe,
        price=price,
        change_24h=change,
        funding_rate=0.0,
        open_interest_change=0.0,
        candles=candles,
        provider="toss_observed",
        data_quality=DataQuality(
            ohlcv_ok=True,
            funding_ok=False,
            open_interest_ok=False,
            min_candles_met=True,
            candles=len(candles),
            last_candle_at=candles[-1].timestamp,
        ),
    )
    chart = build_chart_analysis(snapshot)
    briefing = build_analyst_briefing(
        symbol=symbol,
        timeframe=timeframe,
        analysis=chart,
        prior_state=prior_state,
        context="pre_entry",
    )
    report = generate_report(snapshot)
    scenario = (chart.get("scenarios") or {}).get("long") or {}
    invalidation = scenario.get("invalidation") if isinstance(scenario, dict) else None
    targets = scenario.get("take_profit") if isinstance(scenario, dict) else []
    risk = abs(float(invalidation.get("distance_pct"))) if isinstance(invalidation, dict) and invalidation.get("distance_pct") is not None else None
    reward = float(targets[0].get("distance_pct")) if targets and targets[0].get("distance_pct") is not None else None
    rr = reward / risk if risk and reward is not None and reward > 0 else None
    confluence = briefing["confluence"]
    return {
        "status": "analyzed",
        "source": "toss_observed+shared_chart_analysis+shared_confluence",
        "timeframe": timeframe,
        "chart_analysis": chart,
        "confluence": confluence,
        "entry_score": report.entry_score,
        "invalidation": invalidation,
        "rr_ratio": rr,
        "earnings_gate": "not_evaluable",
        "signature_status": "unvalidated",
    }


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
