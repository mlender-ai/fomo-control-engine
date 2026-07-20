"""실제 Bitget 히스토리로 방향 엔진 v2를 재판정한다 (WO-FCE-88)."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from statistics import mean, median
from typing import Any

from app.analyst.briefing import hysteresis_params_from_settings
from app.analyst.stance_history import replay_confirmed_stance_points
from app.backtest.costs import roundtrip_cost_pct
from app.backtest.statistics import DISCLAIMER_NET, bootstrap_win_ci, format_stat_line
from app.db.models import BacktestStat, MarketCandle, utc_now
from app.exchange.bitget.schemas import Candle
from app.exchange.bitget.trades import timeframe_seconds
from app.positions.chart_analysis import MIN_CHART_CANDLES


SIGNATURE_KEY_V1 = "directional_v1_real_history_24h"
SIGNATURE_KEY = "directional_v2_real_history_24h"
SIGNATURE_KEYS = {False: SIGNATURE_KEY_V1, True: SIGNATURE_KEY}
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOXLUSDT")
LOGGER = logging.getLogger(__name__)
METHODOLOGY = {
    "engine": "directional_v1_vs_v2_same_history",
    "source": "Bitget public history-candles",
    "timeframe": "4h",
    "horizon_bars": 6,
    "horizon_label": "T+24h",
    "stride_bars": 6,
    "analysis_window_bars": 200,
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
    history_bars: int = 2_196,
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
            fetched = [_market_candle(candle) for candle in raw]
            repo.upsert_stance_history_candles(
                symbol,
                timeframe,
                fetched,
                "bitget_public_history-candles",
                generated_at,
            )
            candles = repo.list_stance_history_candles(symbol, timeframe, limit=5_000)
            variants = {
                version: evaluate_stance_history(
                    symbol=symbol,
                    timeframe=timeframe,
                    candles=candles,
                    settings=settings,
                    horizon_bars=horizon_bars,
                    generated_at=generated_at,
                    directional_v2=directional_v2,
                )
                for directional_v2, version in ((False, "v1"), (True, "v2"))
            }
            for result in variants.values():
                repo.upsert_backtest_stat(_as_stat(result, candles))
            collection = {
                "symbol": symbol,
                "requested_bars": history_bars,
                "fetched_bars": len(fetched),
                "cached_bars": len(candles),
                "estimated_pages": (len(fetched) + 199) // 200,
                "endpoint": "/api/v2/mix/market/history-candles",
                "schedule": "daily_low_priority",
                "private_rate_budget_used": False,
            }
            LOGGER.info("stance history collection audit: %s", collection)
            refreshed.append({"symbol": symbol, "collection": collection, **variants})
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
    payload = stance_backtest_dashboard(repo, symbols=requested)
    payload["refresh"] = {
        "requested": requested,
        "refreshed": refreshed,
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
    directional_v2: bool = True,
) -> dict[str, Any]:
    ordered = sorted(candles, key=lambda item: item.timestamp)
    quality = _data_quality(ordered, timeframe)
    quality_floor = int(getattr(settings, "backtest_data_quality_floor", 70))
    usable = _quality_filtered_segment(ordered, timeframe)
    if len(usable) < MIN_CHART_CANDLES + horizon_bars:
        raise ValueError(f"confirmed history requires at least {MIN_CHART_CANDLES + horizon_bars} candles")
    ordered = usable
    horizon = max(1, int(horizon_bars))
    points = replay_confirmed_stance_points(
        symbol=symbol,
        timeframe=timeframe,
        candles=ordered,
        hysteresis_params=hysteresis_params_from_settings(settings),
        directional_v2=directional_v2,
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
    expected_replay_points = max(1, len(ordered) - MIN_CHART_CANDLES + 1)
    replay_coverage_pct = round(len(points) / expected_replay_points * 100, 1)
    sample_floor = int(getattr(settings, "stance_backtest_sample_floor", 30))
    sample_sufficient = sample_size >= sample_floor
    quality_sufficient = quality["score"] >= quality_floor
    publishable = sample_sufficient and quality_sufficient and ci is not None
    period = {
        "from": ordered[0].timestamp.isoformat(),
        "to": ordered[-1].timestamp.isoformat(),
        "label": f"{ordered[0].timestamp.date()}~{ordered[-1].timestamp.date()}",
    }
    engine_version = "v2" if directional_v2 else "v1"
    statement = format_stat_line(
        {
            "sample_size": sample_size,
            "win_1r_pct": hit_rate,
            "win_1r_ci": list(ci) if ci else None,
            "period": period,
        },
        sample_floor=sample_floor,
        label=f"{symbol.upper()} {engine_version} T+24h",
        metric_label="net 방향 적중",
    )
    if not quality_sufficient:
        statement = f"{symbol.upper()} 데이터 품질 {quality['score']}/100 — 통계 발행 보류"
    net_returns = [float(case["net_directional_return_pct"]) for case in cases]
    return {
        "signature_key": SIGNATURE_KEYS[directional_v2],
        "engine_version": engine_version,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "source": "bitget_real_history",
        "real_history": True,
        "generated_at": (generated_at or utc_now()).isoformat(),
        "period": period,
        "candle_count": len(ordered),
        "raw_candle_count": len(candles),
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
        "methodology": {
            **METHODOLOGY,
            "engine": f"directional_{engine_version}",
            "timeframe": timeframe,
            "horizon_bars": horizon,
            "stride_bars": horizon,
        },
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
        v1_rows = repo.list_backtest_stats(symbol=symbol, signature_key=SIGNATURE_KEY_V1, limit=1)
        v2_rows = repo.list_backtest_stats(symbol=symbol, signature_key=SIGNATURE_KEY, limit=1)
        if v2_rows:
            v2 = _dashboard_item(v2_rows[0].payload)
            v1 = _dashboard_item(v1_rows[0].payload) if v1_rows else None
            items.append({**v2, "v1": v1, "v2": v2, "comparison": _compare_variants(v1, v2)})
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
                    "v1": None,
                    "v2": None,
                    "comparison": {"ci_nonoverlap": False, "v2_improvement_proven": False, "claim": "동일 표본 비교 대기"},
                }
            )
    available = sum(1 for item in items if item.get("generated_at"))
    return {
        "status": "ok" if available == len(requested) else "pending" if available == 0 else "partial",
        "signature_key": SIGNATURE_KEY,
        "comparison_signature_key": SIGNATURE_KEY_V1,
        "synthetic_result_combined": False,
        "items": items,
        "available": available,
        "expected": len(requested),
        "methodology": METHODOLOGY,
        "disclaimer": DISCLAIMER_NET,
    }


def _as_stat(result: dict[str, Any], candles: list[MarketCandle]) -> BacktestStat:
    return BacktestStat(
        signature_key=str(result["signature_key"]),
        symbol=str(result["symbol"]),
        timeframe=str(result["timeframe"]),
        asset_class="stock" if result["symbol"] == "SOXLUSDT" else "crypto",
        scope="symbol",
        engine=f"directional_{result['engine_version']}",
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


def _compare_variants(v1: dict[str, Any] | None, v2: dict[str, Any]) -> dict[str, Any]:
    v1_ci = v1.get("directional_hit_ci") if v1 else None
    v2_ci = v2.get("directional_hit_ci")
    if not (isinstance(v1_ci, list) and len(v1_ci) == 2 and isinstance(v2_ci, list) and len(v2_ci) == 2):
        return {"ci_nonoverlap": False, "v2_improvement_proven": False, "claim": "CI 비교 불가"}
    nonoverlap = float(v2_ci[0]) > float(v1_ci[1]) or float(v1_ci[0]) > float(v2_ci[1])
    improved = float(v2_ci[0]) > float(v1_ci[1])
    return {
        "ci_nonoverlap": nonoverlap,
        "v2_improvement_proven": improved,
        "claim": "v2 개선 입증" if improved else "v1/v2 차이 유의하지 않음" if not nonoverlap else "v2 열위 관측",
    }


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
        "excluded_reasons": [
            *([f"timeframe_gap:{gaps}"] if gaps else []),
            *([f"invalid_ohlc:{invalid_ohlc}"] if invalid_ohlc else []),
        ],
        "confirmed_only": True,
    }


def _quality_filtered_segment(candles: list[MarketCandle], timeframe: str) -> list[MarketCandle]:
    """Use the longest valid, contiguous segment; never bridge corrupt history."""

    expected = timeframe_seconds(timeframe)
    segments: list[list[MarketCandle]] = []
    current: list[MarketCandle] = []
    for candle in candles:
        valid = candle.low <= min(candle.open, candle.close) and candle.high >= max(candle.open, candle.close) and candle.low <= candle.high
        contiguous = not current or int((candle.timestamp - current[-1].timestamp).total_seconds()) == expected
        if not valid or not contiguous:
            if current:
                segments.append(current)
            current = []
        if valid:
            current.append(candle)
    if current:
        segments.append(current)
    return max(segments, key=len, default=[])


def _candle_hash(candles: list[MarketCandle]) -> str:
    compact = [[int(item.timestamp.timestamp()), item.open, item.high, item.low, item.close, item.volume] for item in candles]
    return hashlib.sha256(json.dumps(compact, separators=(",", ":")).encode()).hexdigest()
