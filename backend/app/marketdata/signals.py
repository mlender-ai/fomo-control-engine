from __future__ import annotations

from typing import Any

from app.db.models import DerivativeMetric, LiquidationEvent
from app.marketdata.money_flow import classify_money_flow, coinglass_flow_observation, observations_from_metrics

MIN_FUNDING_SAMPLES = 20


def build_derivative_signals(
    metrics: list[DerivativeMetric],
    liquidation_events: list[LiquidationEvent] | None = None,
    liquidation_clusters: list[dict] | None = None,
) -> dict[str, Any]:
    ordered = sorted(metrics, key=lambda item: item.as_of, reverse=True)
    latest = next(
        (metric for metric in ordered if _has_metric_value(metric)),
        ordered[0] if ordered else None,
    )
    if latest is None:
        return {
            "as_of": None,
            "coverage": {
                "metric_samples": 0,
                "liquidation_samples": len(liquidation_events or []),
            },
            "oi_price_divergence": None,
            "funding_state": None,
            "crowding_score": None,
            "liquidation_clusters": liquidation_clusters or [],
            "money_flow": classify_money_flow(None, []),
        }
    observations = observations_from_metrics(ordered)
    current_observation = next(
        (
            observation
            for metric in ordered
            if metric.source == "bitget"
            if isinstance(metric.raw_json, dict)
            if isinstance((observation := metric.raw_json.get("money_flow_observation")), dict)
        ),
        observations[0] if observations else None,
    )
    coinglass_observation = next(
        (observation for metric in ordered if metric.source == "coinglass" if (observation := coinglass_flow_observation(metric)) is not None),
        None,
    )
    if coinglass_observation is not None:
        bitget_observation = current_observation or {}
        current_observation = {
            **bitget_observation,
            **coinglass_observation,
            "price_change_pct": coinglass_observation.get("price_change_pct")
            if coinglass_observation.get("price_change_pct") is not None
            else bitget_observation.get("price_change_pct"),
            "oi_change_pct": coinglass_observation.get("oi_change_pct")
            if coinglass_observation.get("oi_change_pct") is not None
            else bitget_observation.get("oi_change_pct"),
            "coverage": {
                **(bitget_observation.get("coverage") or {}),
                **(coinglass_observation.get("coverage") or {}),
            },
        }
    current_source = current_observation.get("source") if isinstance(current_observation, dict) else None
    source_history = [item for item in observations if item.get("source") == current_source] if current_source else observations
    return {
        "as_of": latest.as_of.isoformat(),
        "coverage": {
            "metric_samples": len(metrics),
            "liquidation_samples": len(liquidation_events or []),
            "sources": sorted({metric.source for metric in metrics}),
        },
        "oi_price_divergence": classify_oi_price_divergence(_price_change_24h(latest), latest.oi_change_pct),
        "funding_state": funding_state(latest, ordered),
        "crowding_score": crowding_score(latest, ordered),
        "liquidation_clusters": liquidation_clusters or [],
        "money_flow": classify_money_flow(current_observation, source_history),
    }


def classify_oi_price_divergence(price_change_pct: float | None, oi_change_pct: float | None) -> dict[str, Any] | None:
    if price_change_pct is None or oi_change_pct is None:
        return None
    price_up = price_change_pct >= 0
    oi_up = oi_change_pct >= 0
    if price_up and oi_up:
        state = "price_up_oi_up"
        label = "가격 상승 + OI 증가"
        meaning = "신규 포지션 유입과 함께 가격이 움직였습니다."
    elif price_up and not oi_up:
        state = "price_up_oi_down"
        label = "가격 상승 + OI 감소"
        meaning = "숏커버성 상승 가능성이 있습니다."
    elif not price_up and oi_up:
        state = "price_down_oi_up"
        label = "가격 하락 + OI 증가"
        meaning = "신규 숏 또는 하락 방향 포지션 축적 가능성이 있습니다."
    else:
        state = "price_down_oi_down"
        label = "가격 하락 + OI 감소"
        meaning = "롱 청산 또는 포지션 축소가 동반됐을 가능성이 있습니다."
    return {
        "state": state,
        "label": label,
        "meaning": meaning,
        "price_change_pct": round(price_change_pct, 4),
        "oi_change_pct": round(oi_change_pct, 4),
    }


def funding_state(latest: DerivativeMetric, history: list[DerivativeMetric]) -> dict[str, Any] | None:
    funding = latest.funding if latest.funding is not None else latest.oi_weighted_funding
    if funding is None:
        return None
    interval = latest.funding_interval_hours
    samples = [
        abs(metric.funding if metric.funding is not None else metric.oi_weighted_funding)
        for metric in history
        if (metric.funding is not None or metric.oi_weighted_funding is not None)
        and (interval is None or metric.funding_interval_hours == interval or metric.funding_interval_hours is None)
    ]
    if len(samples) < MIN_FUNDING_SAMPLES:
        return {
            "state": None,
            "label": "표본 부족",
            "funding": funding,
            "sample_size": len(samples),
            "required_samples": MIN_FUNDING_SAMPLES,
            "funding_interval_hours": interval,
        }
    percentile = percentile_rank(abs(funding), samples)
    if percentile >= 90:
        state = "extreme"
        label = "펀딩 극단"
    elif percentile >= 70:
        state = "overheated"
        label = "펀딩 과열"
    else:
        state = "neutral"
        label = "펀딩 중립"
    return {
        "state": state,
        "label": label,
        "funding": funding,
        "abs_percentile_30d": percentile,
        "sample_size": len(samples),
        "funding_interval_hours": interval,
    }


def crowding_score(latest: DerivativeMetric, history: list[DerivativeMetric]) -> dict[str, Any] | None:
    funding = funding_state(latest, history)
    if funding is None or funding.get("state") is None:
        return None
    components = {
        "funding_percentile": min(100.0, float(funding["abs_percentile_30d"])),
        "long_short_pressure": _ratio_pressure(latest.taker_ls or latest.top_ls),
        "oi_pressure": min(100.0, abs(latest.oi_change_pct or 0.0) * 10),
    }
    score = round(
        components["funding_percentile"] * 0.4 + components["long_short_pressure"] * 0.35 + components["oi_pressure"] * 0.25,
        2,
    )
    return {
        "score": score,
        "components": components,
        "label": "쏠림 높음" if score >= 70 else "쏠림 보통" if score >= 40 else "쏠림 낮음",
        "formula": "funding_percentile*0.40 + long_short_pressure*0.35 + oi_pressure*0.25",
    }


def percentile_rank(value: float, samples: list[float]) -> float:
    if not samples:
        return 0.0
    below_or_equal = sum(1 for sample in samples if sample <= value)
    return round((below_or_equal / len(samples)) * 100, 2)


def _ratio_pressure(ratio: float | None) -> float:
    if ratio is None or ratio <= 0:
        return 0.0
    if ratio >= 2.0 or ratio <= 0.5:
        return 100.0
    if ratio >= 1.0:
        return round((ratio - 1.0) / 1.0 * 100, 2)
    return round((1.0 - ratio) / 0.5 * 100, 2)


def _price_change_24h(metric: DerivativeMetric) -> float | None:
    value = metric.raw_json.get("price_change_pct_24h") if isinstance(metric.raw_json, dict) else None
    if value is None:
        value = metric.raw_json.get("ticker", {}).get("priceChangePercent") if isinstance(metric.raw_json.get("ticker"), dict) else None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_metric_value(metric: DerivativeMetric) -> bool:
    return any(
        value is not None
        for value in (
            metric.open_interest,
            metric.open_interest_value,
            metric.oi_change_pct,
            metric.funding,
            metric.taker_ls,
            metric.top_ls,
            metric.oi_weighted_funding,
        )
    )
