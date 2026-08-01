from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.analyst.briefing import build_analyst_briefing
from app.db.models import DataQuality, MarketCandle, MarketSnapshot
from app.positions.chart_analysis import build_chart_analysis
from app.report.engine import generate_report
from app.structure.pnf import measured_objective
from app.toss.store import TossStockStore

from .models import Market
from .risk_reward import risk_reward
from .targets import merge_pnf_target


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

    # 작업 2·3: 와이코프가 이미 식별한 축적 구간에 PNF 수평 카운트를 적용해 측정 목표를 얻고,
    # 기존 구조 레벨 목표보다 멀 때만 상위 TP로 편입한다(PNF가 항상 이기지 않는다).
    # 입력은 저장된 확정 일봉뿐이며 미래 봉을 참조하지 않는다(C4).
    objective = measured_objective(candles, trading_range=_trading_range(chart), direction="long")
    targets, target_source, pnf_payload = merge_pnf_target(list(targets or []), objective, price, direction="long")

    # 작업 1: 보상 분자를 분할 청산 가중 기대값으로 정합화한다. 기존 TP1 단독 RR도 병기해
    # 어느 정의로 게이트를 통과했는지 추적 가능하게 한다(임계 min_rr은 불변 — C1).
    rr_payload = risk_reward(targets, risk)
    rr = rr_payload["rr_weighted"]
    # 작업 4 A/B: PNF 목표를 제외한 구조 레벨만의 RR도 함께 기록해 두 정의의 예측력을
    # 사후 비교할 수 있게 한다. 진입은 하나지만 기록은 둘이다.
    structure_only = [item for item in targets if item.get("source") != "pnf_measured_objective"]
    rr_structure_only = risk_reward(structure_only, risk)
    confluence = briefing["confluence"]
    return {
        "status": "analyzed",
        "source": "toss_observed+shared_chart_analysis+shared_confluence",
        "asset_class": "equity",
        "signal_availability": {
            "ohlcv": {"available": True, "used_by_evidence": True},
            "funding_rate": {"available": False, "used_by_evidence": False},
            "open_interest": {"available": False, "used_by_evidence": False},
        },
        "timeframe": timeframe,
        "chart_analysis": chart,
        "confluence": confluence,
        "entry_score": report.entry_score,
        "invalidation": invalidation,
        "rr_ratio": rr,
        # 병기(작업 1): 게이트가 쓰는 가중 RR과 기존 TP1 기준 RR을 함께 남긴다.
        "rr_definition": rr_payload["definition"],
        "rr_ratio_first_target": rr_payload["rr_first_target"],
        "rr_detail": rr_payload,
        # A/B(작업 4): PNF 편입 전후 두 정의를 동시에 저장한다.
        "rr_ab": {
            "with_pnf": rr_payload["rr_weighted"],
            "structure_only": rr_structure_only["rr_weighted"],
            "first_target_only": rr_payload["rr_first_target"],
        },
        "target_source": target_source,
        "pnf_measured_objective": pnf_payload,
        "take_profit_targets": targets,
        "earnings_gate": "not_evaluable",
        "signature_status": "unvalidated",
    }


def _trading_range(chart: dict[str, Any]) -> dict[str, Any] | None:
    """와이코프 엔진이 이미 식별한 축적/분산 구간을 꺼낸다.

    PNF는 구간을 스스로 판정하지 않는다 — 억지 판정 금지(C5)이자, 새 감지기가 아니라
    기존 국면의 목표 계산기라는 경계(C2)를 유지하기 위함이다.
    """
    value = chart.get("wyckoff_range") if isinstance(chart, dict) else None
    return value if isinstance(value, dict) else None


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
