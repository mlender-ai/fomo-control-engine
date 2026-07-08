from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any

from app.analyst.signature_registry import current_state, state_note
from app.backtest.costs import roundtrip_cost_pct
from app.backtest.data_quality import assess_candles
from app.backtest.overlap import overlap_groups_payload
from app.backtest.regimes import label_regime
from app.backtest.replay import aggregate_by_signature, replay_candles
from app.backtest.signatures import signature_key, signatures_from_analysis
from app.backtest.statistics import DISCLAIMER_NET, format_stat_line
from app.core.config import Settings
from app.db.models import BacktestStat, MarketCandle, utc_now

DISCLAIMER = DISCLAIMER_NET


def historical_context_for_analysis(
    repo: Any,
    settings: Settings,
    *,
    symbol: str,
    timeframe: str,
    analysis: dict[str, Any],
    candles: list[MarketCandle],
    force: bool = False,
    quote_volume_24h: float | None = None,
    market_regime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_signatures = signatures_from_analysis(analysis)
    if not getattr(settings, "backtest_enabled", True):
        return _empty(symbol, timeframe, active_signatures, "disabled")

    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    min_window = max(30, int(getattr(settings, "backtest_min_window_candles", 60)))
    lookahead = max(5, int(getattr(settings, "backtest_lookahead_bars", 48)))
    if len(ordered) < min_window + 10:
        return _empty(symbol, timeframe, active_signatures, "insufficient_candles")

    # WO-36 §1: 리플레이 전 캔들 무결성 검사 — 품질 하한 미달 심볼은 통계 발행 금지.
    quality = assess_candles(ordered, timeframe)
    quality_summary = {"score": quality["score"], "checked": quality["checked"], "violations": quality["violations"]}
    quality_floor = int(getattr(settings, "backtest_data_quality_floor", 70))
    if quality["score"] < quality_floor:
        payload = _empty(symbol, timeframe, active_signatures, "data_quality_below_floor")
        payload["data_quality"] = quality_summary
        payload["notes"].append(f"데이터 품질 {quality['score']}점 < 하한 {quality_floor}점 — 통계 발행 금지")
        return payload
    clean = quality["valid_candles"]
    if len(clean) < min_window + 10:
        payload = _empty(symbol, timeframe, active_signatures, "insufficient_clean_candles")
        payload["data_quality"] = quality_summary
        return payload

    current_regime = label_regime(clean, **_regime_params(settings))
    # WO-36 §5: 크립토는 시장(BTC) 레짐 병기 — 알트 신호의 시장 종속성.
    if market_regime and str(analysis.get("asset_class") or "").startswith("crypto"):
        current_regime["market_regime"] = market_regime.get("regime")
        current_regime["market_regime_label"] = market_regime.get("regime_label")

    cached = _cached_matching_stats(repo, settings, symbol, active_signatures)
    if cached and not force:
        _annotate_states(repo, settings, cached)
        payload = _payload(symbol, timeframe, analysis, active_signatures, cached, source="cache")
        payload["data_quality"] = quality_summary
        payload["current_regime"] = current_regime
        payload["overlap_groups"] = _cached_overlap_groups(cached)
        return payload

    cost_pct = roundtrip_cost_pct(
        settings,
        asset_class=str(analysis.get("asset_class") or "unknown"),
        quote_volume_24h=quote_volume_24h,
    )
    cases = replay_candles(
        symbol,
        timeframe,
        clean,
        asset_class=str(analysis.get("asset_class") or "unknown"),
        min_window=min_window,
        lookahead_bars=lookahead,
        cost_pct=cost_pct,
        regime_params=_regime_params(settings),
    )
    stats = aggregate_by_signature(cases, statistics_params=_statistics_params(settings))
    overlap_groups = overlap_groups_payload(cases, threshold=float(getattr(settings, "backtest_overlap_threshold", 0.7)))
    saved = [_save_stat(repo, symbol, timeframe, analysis, stat, overlap_groups) for stat in stats]
    selected = _matching_stats(saved, active_signatures) or saved[:3]
    _annotate_states(repo, settings, selected)
    payload = _payload(
        symbol,
        timeframe,
        analysis,
        active_signatures,
        selected,
        source="replay",
        case_count=len(cases),
    )
    payload["data_quality"] = quality_summary
    payload["current_regime"] = current_regime
    payload["overlap_groups"] = overlap_groups
    payload["cost_pct"] = cost_pct
    return payload


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
    current = context.get("current_regime") if isinstance(context.get("current_regime"), dict) else {}
    sample_floor = int(context.get("sample_floor") or 10)
    return "백테스트: " + format_stat_line(
        stat,
        sample_floor=sample_floor,
        current_regime=str(current.get("regime")) if current.get("regime") else None,
    )


def _annotate_states(repo: Any, settings: Settings, stats: list[dict[str, Any]]) -> None:
    """WO-37: 각 통계에 시그니처 라이프사이클 상태 + 표기 노트를 부착한다."""
    for stat in stats:
        key = stat.get("signature_key")
        if not key:
            continue
        state = current_state(repo, str(key), stat=stat, settings=settings)
        stat["lifecycle_state"] = state
        note = state_note(state)
        if note:
            stat["lifecycle_note"] = note


def _regime_params(settings: Settings) -> dict[str, Any]:
    return {
        "ma_period": int(getattr(settings, "regime_ma_period", 200)),
        "slope_window": int(getattr(settings, "regime_ma_slope_window", 20)),
        "slope_threshold_pct": float(getattr(settings, "regime_ma_slope_threshold_pct", 1.0)),
        "atr_lookback": int(getattr(settings, "regime_atr_lookback", 120)),
        "atr_high_percentile": float(getattr(settings, "regime_atr_high_percentile", 70.0)),
    }


def _statistics_params(settings: Settings) -> dict[str, Any]:
    return {
        "iterations": int(getattr(settings, "backtest_bootstrap_iterations", 1000)),
        "confidence": float(getattr(settings, "backtest_ci_confidence", 0.95)),
        "validation_ratio": float(getattr(settings, "backtest_oos_validation_ratio", 0.30)),
        "unstable_gap_pct": float(getattr(settings, "backtest_oos_unstable_gap_pct", 15.0)),
        "walk_forward_window_days": int(getattr(settings, "backtest_walk_forward_window_days", 180)),
        "walk_forward_step_days": int(getattr(settings, "backtest_walk_forward_step_days", 60)),
    }


def _cached_matching_stats(repo: Any, settings: Settings, symbol: str, active_signatures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = [str(item.get("key") or signature_key(item)) for item in active_signatures]
    stats: list[BacktestStat] = []
    for key in keys:
        stats.extend(repo.list_backtest_stats(symbol=symbol, signature_key=key, limit=1))
    # 신선도: TTL 초과 캐시는 버리고 재계산 — 영구 동결(첫 계산 값이 최신인 척) 방지.
    # 활성 키 전부의 커버는 요구하지 않는다: 리플레이가 케이스를 못 만드는 시그니처는
    # 영원히 커버되지 않아 매 호출 리플레이를 강제하게 되기 때문 (TTL이 상한을 보장).
    ttl = timedelta(hours=max(1, int(getattr(settings, "backtest_cache_ttl_hours", 24))))
    now = utc_now()
    payloads = []
    for stat in stats:
        generated = stat.generated_at if stat.generated_at.tzinfo else stat.generated_at.replace(tzinfo=timezone.utc)
        if now - generated > ttl:
            return []
        payload = stat.model_dump(mode="json")
        # WO-36 §3: CI 없는 캐시(구버전)는 발행 금지 — 재계산 경로로 보낸다.
        inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        if not inner.get("win_1r_ci") and payload.get("sample_size"):
            return []
        payload.update({key: value for key, value in inner.items() if key not in payload or payload[key] is None})
        payloads.append(payload)
    return payloads


def _cached_overlap_groups(cached: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for stat in cached:
        inner = stat.get("payload") if isinstance(stat.get("payload"), dict) else {}
        groups = inner.get("overlap_groups") or stat.get("overlap_groups")
        if isinstance(groups, list) and groups:
            return groups
    return []


def _matching_stats(stats: list[dict[str, Any]], active_signatures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active_keys = {str(item.get("key") or signature_key(item)) for item in active_signatures}
    return [stat for stat in stats if stat.get("signature_key") in active_keys]


def _save_stat(
    repo: Any,
    symbol: str,
    timeframe: str,
    analysis: dict[str, Any],
    stat: dict[str, Any],
    overlap_groups: list[dict[str, Any]],
) -> dict[str, Any]:
    signature = stat.get("signature") if isinstance(stat.get("signature"), dict) else {}
    payload_fields = {key: value for key, value in stat.items() if key not in {"cases"}}
    payload_fields["overlap_groups"] = overlap_groups
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
        disclaimer=DISCLAIMER_NET,
        payload=payload_fields,
        generated_at=utc_now(),
    )
    saved = repo.upsert_backtest_stat(model)
    payload = saved.model_dump(mode="json")
    payload["label"] = stat.get("label") or signature.get("label")
    payload["signature"] = signature
    payload["sample_warning"] = stat.get("sample_warning")
    for key in ("win_1r_ci", "oos", "unstable", "walk_forward", "regimes", "period"):
        payload[key] = stat.get(key)
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
        "disclaimer": DISCLAIMER_NET,
        "sample_floor": sample_floor,
        "active_signatures": active_signatures,
        "stats": stats,
        "case_count": case_count,
        "notes": [
            "가상 진입은 이벤트 확정 캔들 종가 기준입니다.",
            "룩어헤드 방지를 위해 감지기는 각 시점까지의 캔들만 사용합니다.",
            "승률·RR은 수수료·슬리피지 차감 후 net 기준입니다.",
        ],
    }


def _empty(symbol: str, timeframe: str, active_signatures: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "generated_at": utc_now().isoformat(),
        "source": reason,
        "disclaimer": DISCLAIMER_NET,
        "sample_floor": 10,
        "active_signatures": active_signatures,
        "stats": [],
        "case_count": 0,
        "notes": ["백테스트 통계를 계산하지 않았습니다.", f"reason={reason}"],
    }
