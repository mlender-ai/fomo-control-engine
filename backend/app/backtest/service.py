from __future__ import annotations

from typing import Any

from app.backtest.replay import aggregate_by_signature, replay_candles
from app.backtest.signatures import signature_key, signatures_from_analysis
from app.core.config import Settings
from app.db.models import BacktestStat, MarketCandle, utc_now

DISCLAIMER = "과거 통계 · 미래 보장 아님 · 수수료/슬리피지 미반영"


def historical_context_for_analysis(
    repo: Any,
    settings: Settings,
    *,
    symbol: str,
    timeframe: str,
    analysis: dict[str, Any],
    candles: list[MarketCandle],
    force: bool = False,
) -> dict[str, Any]:
    active_signatures = signatures_from_analysis(analysis)
    if not getattr(settings, "backtest_enabled", True):
        return _empty(symbol, timeframe, active_signatures, "disabled")

    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    min_window = max(30, int(getattr(settings, "backtest_min_window_candles", 60)))
    lookahead = max(5, int(getattr(settings, "backtest_lookahead_bars", 48)))
    if len(ordered) < min_window + 10:
        return _empty(symbol, timeframe, active_signatures, "insufficient_candles")

    cached = _cached_matching_stats(repo, symbol, active_signatures)
    if cached and not force:
        return _payload(symbol, timeframe, analysis, active_signatures, cached, source="cache")

    cases = replay_candles(
        symbol,
        timeframe,
        ordered,
        asset_class=str(analysis.get("asset_class") or "unknown"),
        min_window=min_window,
        lookahead_bars=lookahead,
    )
    stats = aggregate_by_signature(cases)
    saved = [_save_stat(repo, symbol, timeframe, analysis, stat) for stat in stats]
    selected = _matching_stats(saved, active_signatures) or saved[:3]
    return _payload(symbol, timeframe, analysis, active_signatures, selected, source="replay", case_count=len(cases))


def backtest_line(context: dict[str, Any] | None) -> str | None:
    if not isinstance(context, dict):
        return None
    stats = context.get("stats") if isinstance(context.get("stats"), list) else []
    if not stats:
        return None
    stat = stats[0]
    n = int(stat.get("sample_size") or 0)
    if n <= 0:
        return None
    win = stat.get("win_1r_pct")
    rr = stat.get("median_rr")
    label = stat.get("label") or "동일 시그니처"
    if n < 10:
        return f"백테스트: {label} 과거 {n}회 · 표본 부족 — 결론 유보"
    return f"백테스트: {label} 과거 {n}회 · 1R {win}% · 중앙 {rr}R"


def _cached_matching_stats(repo: Any, symbol: str, active_signatures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = [str(item.get("key") or signature_key(item)) for item in active_signatures]
    stats: list[BacktestStat] = []
    for key in keys:
        stats.extend(repo.list_backtest_stats(symbol=symbol, signature_key=key, limit=1))
    return [stat.model_dump(mode="json") for stat in stats]


def _matching_stats(stats: list[dict[str, Any]], active_signatures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_keys = {str(item.get("key") or signature_key(item)) for item in active_signatures}
    return [stat for stat in stats if stat.get("signature_key") in active_keys]


def _save_stat(repo: Any, symbol: str, timeframe: str, analysis: dict[str, Any], stat: dict[str, Any]) -> dict[str, Any]:
    signature = stat.get("signature") if isinstance(stat.get("signature"), dict) else {}
    model = BacktestStat(
        signature_key=str(stat.get("signature_key") or signature_key(signature)),
        symbol=symbol.upper(),
        timeframe=timeframe,
        asset_class=str(analysis.get("asset_class") or signature.get("asset_class") or "unknown"),
        scope=str(stat.get("scope") or "symbol"),  # type: ignore[arg-type]
        engine=str(signature.get("engine") or "unknown"),
        event_type=str(signature.get("event_type") or "unknown"),
        strength_class=str(signature.get("strength_class") or "unknown"),
        direction=str(signature.get("direction") or "neutral"),
        sample_size=int(stat.get("sample_size") or 0),
        win_1r_pct=stat.get("win_1r_pct"),
        win_2r_pct=stat.get("win_2r_pct"),
        median_rr=stat.get("median_rr"),
        avg_mfe_r=stat.get("avg_mfe_r"),
        avg_mae_r=stat.get("avg_mae_r"),
        avg_resolution_bars=stat.get("avg_resolution_bars"),
        cases=stat.get("cases") if isinstance(stat.get("cases"), list) else [],
        payload={key: value for key, value in stat.items() if key not in {"cases"}},
        generated_at=utc_now(),
    )
    saved = repo.upsert_backtest_stat(model)
    payload = saved.model_dump(mode="json")
    payload["label"] = stat.get("label") or signature.get("label")
    payload["signature"] = signature
    payload["sample_warning"] = stat.get("sample_warning")
    return payload


def _payload(
    symbol: str,
    timeframe: str,
    analysis: dict[str, Any],
    active_signatures: list[dict[str, Any]],
    stats: list[dict[str, Any]],
    *,
    source: str,
    case_count: int | None = None,
) -> dict[str, Any]:
    sample_floor = 10
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "asset_class": analysis.get("asset_class") or "unknown",
        "generated_at": utc_now().isoformat(),
        "source": source,
        "disclaimer": DISCLAIMER,
        "sample_floor": sample_floor,
        "active_signatures": active_signatures,
        "stats": stats,
        "case_count": case_count,
        "notes": [
            "가상 진입은 이벤트 확정 캔들 종가 기준입니다.",
            "룩어헤드 방지를 위해 감지기는 각 시점까지의 캔들만 사용합니다.",
        ],
    }


def _empty(symbol: str, timeframe: str, active_signatures: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "generated_at": utc_now().isoformat(),
        "source": reason,
        "disclaimer": DISCLAIMER,
        "sample_floor": 10,
        "active_signatures": active_signatures,
        "stats": [],
        "case_count": 0,
        "notes": ["백테스트 통계를 계산하지 않았습니다.", f"reason={reason}"],
    }

