"""실제 Bitget 히스토리로 방향 엔진 v2를 재판정한다 (WO-FCE-88)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from statistics import mean, median
from typing import Any

from app.analyst.briefing import hysteresis_params_from_settings
from app.analyst.stance_history import replay_confirmed_stance_points
from app.backtest.costs import roundtrip_cost_pct
from app.backtest.statistics import DISCLAIMER_NET, bootstrap_win_ci
from app.db.models import BacktestStat, MarketCandle, utc_now
from app.exchange.bitget.schemas import Candle
from app.exchange.bitget.trades import timeframe_seconds
from app.positions.chart_analysis import MIN_CHART_CANDLES


SIGNATURE_KEY = "directional_v2_real_history_24h"
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOXLUSDT")
METHODOLOGY = {
    "engine": "directional_v2",
    "source": "Bitget public history-candles",
    "timeframe": "4h",
    "horizon_bars": 6,
    "horizon_label": "T+24h",
    "stride_bars": 6,
    "lookahead_policy": "각 판정은 해당 확정봉까지의 prefix만 입력; 미래 가격은 결과 채점에만 사용",
    "overlap_policy": "6봉 간격 비중첩 표본",
    "derivative_history": "not_included",
    "derivative_history_note": "과거 시점의 펀딩·OI·청산 히스토리를 0이나 현재값으로 대체하지 않음",
}


class StanceHistoryUnavailable(RuntimeError):
    pass


def refresh_stance_backtests(
    repo: Any,
    provider: Any,
    settings: Any,
    *,
    symbols: list[str] | tuple[str, ...] | None = None,
    timeframe: str = "4h",
    history_bars: int = 420,
    horizon_bars: int = 6,
    now: datetime | None = None,
) -> dict[str, Any]:
    loader = getattr(provider, "get_history_ohlcv", None)
    if not callable(loader):
        raise StanceHistoryUnavailable("활성 시장 데이터 공급자가 Bitget 실제 히스토리 수집을 지원하지 않습니다.")
    generated_at = now or utc_now()
    requested = [str(symbol).upper() for symbol in (symbols or DEFAULT_SYMBOLS)]
    refreshed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for symbol in requested:
        try:
            raw = loader(symbol, timeframe, history_bars, now=generated_at)
            candles = [_market_candle(candle) for candle in raw]
            result = evaluate_stance_history(
                symbol=symbol,
                timeframe=timeframe,
                candles=candles,
                settings=settings,
                horizon_bars=horizon_bars,
                generated_at=generated_at,
            )
            repo.upsert_backtest_stat(_as_stat(result, candles))
            refreshed.append(result)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
    payload = stance_backtest_dashboard(repo, symbols=requested)
    payload["refresh"] = {
        "requested": requested,
        "refreshed": [item["symbol"] for item in refreshed],
        "errors": errors,
    }
    payload["status"] = "ok" if not errors else "partial" if refreshed else "error"
    return payload


def evaluate_stance_history(
    *,
    symbol: str,
    timeframe: str,
    candles: list[MarketCandle],
    settings: Any,
    horizon_bars: int = 6,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    ordered = sorted(candles, key=lambda item: item.timestamp)
    if len(ordered) < MIN_CHART_CANDLES + horizon_bars:
        raise ValueError(f"confirmed history requires at least {MIN_CHART_CANDLES + horizon_bars} candles")
    horizon = max(1, int(horizon_bars))
    points = replay_confirmed_stance_points(
        symbol=symbol,
        timeframe=timeframe,
        candles=ordered,
        hysteresis_params=hysteresis_params_from_settings(settings),
    )
    points_by_time = {int(point["time"]): point for point in points}
    asset_class = "stock" if symbol.upper() == "SOXLUSDT" else "crypto"
    cost_pct = roundtrip_cost_pct(settings, asset_class=asset_class)
    cases: list[dict[str, Any]] = []
    skipped = {"non_directional": 0, "missing_replay_point": 0}
    for index in range(MIN_CHART_CANDLES - 1, len(ordered) - horizon, horizon):
        entry = ordered[index]
        outcome = ordered[index + horizon]
        point = points_by_time.get(int(entry.timestamp.timestamp()))
        if point is None:
            skipped["missing_replay_point"] += 1
            continue
        stance = str(point.get("stance") or "insufficient")
        if stance not in {"long_leaning", "short_leaning"}:
            skipped["non_directional"] += 1
            continue
        gross_return_pct = (outcome.close / entry.close - 1) * 100
        if stance == "short_leaning":
            gross_return_pct *= -1
        net_return_pct = gross_return_pct - cost_pct
        cases.append(
            {
                "as_of": entry.timestamp.isoformat(),
                "outcome_at": outcome.timestamp.isoformat(),
                "stance": stance,
                "transitioning": bool(point.get("transitioning")),
                "entry_close": entry.close,
                "outcome_close": outcome.close,
                "gross_directional_return_pct": round(gross_return_pct, 4),
                "cost_pct": cost_pct,
                "net_directional_return_pct": round(net_return_pct, 4),
                "win": net_return_pct > 0,
            }
        )

    wins = [bool(case["win"]) for case in cases]
    ci = bootstrap_win_ci(
        wins,
        iterations=int(getattr(settings, "backtest_bootstrap_iterations", 1000)),
        confidence=float(getattr(settings, "backtest_ci_confidence", 0.95)),
    )
    sample_size = len(cases)
    hit_rate = round(sum(wins) / sample_size * 100, 1) if sample_size else None
    quality = _data_quality(ordered, timeframe)
    expected_replay_points = max(1, len(ordered) - MIN_CHART_CANDLES + 1)
    replay_coverage_pct = round(len(points) / expected_replay_points * 100, 1)
    sample_floor = int(getattr(settings, "stance_backtest_sample_floor", 30))
    quality_floor = int(getattr(settings, "backtest_data_quality_floor", 70))
    sample_sufficient = sample_size >= sample_floor
    quality_sufficient = quality["score"] >= quality_floor
    publishable = sample_sufficient and quality_sufficient and ci is not None
    period = {"from": ordered[0].timestamp.isoformat(), "to": ordered[-1].timestamp.isoformat()}
    if hit_rate is None or ci is None:
        statement = f"{symbol.upper()} T+24h net 방향 적중 CI 미산출 (N={sample_size}) — 결론 유보"
    elif not sample_sufficient:
        statement = f"{symbol.upper()} T+24h net 방향 적중 {hit_rate}% (95% CI {ci[0]}~{ci[1]}%, N={sample_size}) · 표본 부족 — 결론 유보"
    elif not quality_sufficient:
        statement = f"{symbol.upper()} 데이터 품질 {quality['score']}/100 — 통계 발행 보류"
    else:
        statement = f"{symbol.upper()} T+24h net 방향 적중 {hit_rate}% (95% CI {ci[0]}~{ci[1]}%, N={sample_size})"
    net_returns = [float(case["net_directional_return_pct"]) for case in cases]
    return {
        "signature_key": SIGNATURE_KEY,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "source": "bitget_real_history",
        "real_history": True,
        "generated_at": (generated_at or utc_now()).isoformat(),
        "period": period,
        "candle_count": len(ordered),
        "candle_sha256": _candle_hash(ordered),
        "horizon_bars": horizon,
        "horizon_label": f"T+{round(horizon * timeframe_seconds(timeframe) / 3600)}h",
        "stride_bars": horizon,
        "cost_pct": cost_pct,
        "directional_hit_pct": hit_rate,
        "directional_hit_ci": list(ci) if ci else None,
        "sample_size": sample_size,
        "sample_floor": sample_floor,
        "sample_sufficient": sample_sufficient,
        "data_quality": quality,
        "replay_coverage_pct": replay_coverage_pct,
        "quality_floor": quality_floor,
        "quality_sufficient": quality_sufficient,
        "publishable": publishable,
        "decision": "published" if publishable else "withheld",
        "statement": statement,
        "mean_net_directional_return_pct": round(mean(net_returns), 4) if net_returns else None,
        "median_net_directional_return_pct": round(median(net_returns), 4) if net_returns else None,
        "skipped": skipped,
        "cases": cases,
        "methodology": {**METHODOLOGY, "timeframe": timeframe, "horizon_bars": horizon, "stride_bars": horizon},
        "limitations": [
            "과거 펀딩·OI·청산 데이터는 포함하지 않음; 없는 값을 0이나 현재값으로 대체하지 않음",
            "Bitget 퍼페추얼 4시간봉 종가 기반이며 장중 경로를 재구성하지 않음",
            "합성 80.8% 성적과 합산하거나 같은 표본으로 취급하지 않음",
        ],
        "disclaimer": DISCLAIMER_NET,
    }


def stance_backtest_dashboard(repo: Any, *, symbols: list[str] | tuple[str, ...] | None = None) -> dict[str, Any]:
    requested = [str(symbol).upper() for symbol in (symbols or DEFAULT_SYMBOLS)]
    items: list[dict[str, Any]] = []
    for symbol in requested:
        rows = repo.list_backtest_stats(symbol=symbol, signature_key=SIGNATURE_KEY, limit=1)
        if rows:
            items.append(_dashboard_item(rows[0].payload))
        else:
            items.append(
                {
                    "signature_key": SIGNATURE_KEY,
                    "symbol": symbol,
                    "timeframe": "4h",
                    "source": "bitget_real_history",
                    "real_history": True,
                    "generated_at": None,
                    "sample_size": 0,
                    "sample_sufficient": False,
                    "publishable": False,
                    "decision": "pending",
                    "statement": "실제 히스토리 검증 대기",
                    "limitations": [],
                }
            )
    available = sum(1 for item in items if item.get("generated_at"))
    return {
        "status": "ok" if available == len(requested) else "pending" if available == 0 else "partial",
        "signature_key": SIGNATURE_KEY,
        "synthetic_result_combined": False,
        "items": items,
        "available": available,
        "expected": len(requested),
        "methodology": METHODOLOGY,
        "disclaimer": DISCLAIMER_NET,
    }


def _as_stat(result: dict[str, Any], candles: list[MarketCandle]) -> BacktestStat:
    return BacktestStat(
        signature_key=SIGNATURE_KEY,
        symbol=str(result["symbol"]),
        timeframe=str(result["timeframe"]),
        asset_class="stock" if result["symbol"] == "SOXLUSDT" else "crypto",
        scope="symbol",
        engine="directional_v2",
        event_type="forward_close_24h",
        strength_class="real_history",
        direction="neutral",
        sample_size=int(result["sample_size"]),
        win_1r_pct=result.get("directional_hit_pct"),
        cases=list(result.get("cases") or []),
        disclaimer=DISCLAIMER_NET,
        payload=result,
        generated_at=datetime.fromisoformat(str(result["generated_at"])),
        created_at=candles[-1].timestamp,
    )


def _dashboard_item(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep per-case audit rows in the ledger, not in the status poll payload."""

    return {key: value for key, value in payload.items() if key not in {"cases", "candle_sha256"}}


def _market_candle(candle: Candle | MarketCandle) -> MarketCandle:
    if isinstance(candle, MarketCandle):
        return candle
    return MarketCandle(**candle.model_dump())


def _data_quality(candles: list[MarketCandle], timeframe: str) -> dict[str, Any]:
    expected = timeframe_seconds(timeframe)
    gaps = sum(1 for previous, current in zip(candles, candles[1:]) if int((current.timestamp - previous.timestamp).total_seconds()) != expected)
    invalid_ohlc = sum(
        1 for candle in candles if candle.low > min(candle.open, candle.close) or candle.high < max(candle.open, candle.close) or candle.low > candle.high
    )
    observations = max(1, len(candles) - 1)
    score = max(0, round(100 - (gaps + invalid_ohlc) / observations * 100))
    return {
        "score": score,
        "gap_count": gaps,
        "invalid_ohlc_count": invalid_ohlc,
        "confirmed_only": True,
    }


def _candle_hash(candles: list[MarketCandle]) -> str:
    compact = [[int(item.timestamp.timestamp()), item.open, item.high, item.low, item.close, item.volume] for item in candles]
    return hashlib.sha256(json.dumps(compact, separators=(",", ":")).encode()).hexdigest()
