from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


MONEY_FLOW_LABELS = {
    "spot_led": "현물 유입 동반 상승",
    "futures_led": "선물 단독 견인 - 레버리지 상승 경계",
    "spot_absorb": "하락 구간 현물 매집 관찰",
    "delever": "레버리지 청산 진행",
    "mixed": "혼조 - 판정 유보",
}


def flow_observation(
    *,
    price_change_pct: float | None,
    spot_flow: dict[str, Any] | None,
    futures_flow: dict[str, Any] | None,
    oi_change_pct: float | None,
    as_of: datetime | None = None,
    confirmed: bool = True,
) -> dict[str, Any]:
    spot_ratio = _window_delta_ratio(spot_flow)
    futures_ratio = _window_delta_ratio(futures_flow)
    spot_available = bool(spot_flow and spot_flow.get("data_available"))
    futures_available = bool(futures_flow and futures_flow.get("data_available"))
    mapping_status = "ok" if spot_available else str((spot_flow or {}).get("status") or "unavailable")
    notes = [str(item) for item in (spot_flow or {}).get("notes", []) if item]
    return {
        "as_of": (as_of or datetime.now(timezone.utc)).isoformat(),
        "price_change_pct": _number(price_change_pct),
        "spot_cvd_delta_ratio": spot_ratio,
        "futures_cvd_delta_ratio": futures_ratio,
        "oi_change_pct": _number(oi_change_pct),
        "confirmed": confirmed,
        "spot_source": str((spot_flow or {}).get("source") or "bitget_spot"),
        "futures_source": str((futures_flow or {}).get("source") or "bitget_futures"),
        "source": "bitget_spot",
        "coverage": {
            "spot_available": spot_available,
            "futures_available": futures_available,
            "spot_mapping": mapping_status,
            "window_buckets": 24,
        },
        "spot_cvd": _compact_cvd(spot_flow),
        "futures_cvd": _compact_cvd(futures_flow),
        "notes": notes,
    }


def classify_money_flow(
    current: dict[str, Any] | None,
    history: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    min_samples: int = 10,
) -> dict[str, Any]:
    if not isinstance(current, dict):
        return _unavailable("아직 자금 흐름 관측치가 없습니다.")
    if current.get("confirmed") is False:
        return {
            **_base(current),
            "state": "mixed",
            "label": MONEY_FLOW_LABELS["mixed"],
            "available": True,
            "provisional": True,
            "sample_size": 0,
            "reason": "현재 캔들이 마감될 때까지 자금 흐름 판정을 보류합니다.",
        }
    coverage = current.get("coverage") if isinstance(current.get("coverage"), dict) else {}
    if not coverage.get("spot_available"):
        reason = next(iter(current.get("notes") or []), "Bitget 현물 마켓 매핑 또는 체결 데이터가 없습니다.")
        return _unavailable(str(reason), current=current)
    if not coverage.get("futures_available"):
        return _unavailable("선물 체결 데이터가 없어 현물/선물 비교를 보류합니다.", current=current)
    if current.get("spot_cvd_delta_ratio") is None or current.get("futures_cvd_delta_ratio") is None:
        return {
            **_base(current),
            "state": "mixed",
            "label": MONEY_FLOW_LABELS["mixed"],
            "available": True,
            "provisional": True,
            "sample_size": 0,
            "required_samples": min_samples,
            "reason": "확정봉 구간의 현물/선물 체결 표본을 확인하는 중입니다.",
        }

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=30)
    samples = [item for item in history if _after(item.get("as_of"), cutoff)]
    samples = [item for item in samples if _complete(item)]
    if len(samples) < min_samples:
        return {
            **_base(current),
            "state": "mixed",
            "label": MONEY_FLOW_LABELS["mixed"],
            "available": True,
            "provisional": True,
            "sample_size": len(samples),
            "required_samples": min_samples,
            "reason": f"30일 분포 표본 축적 중 ({len(samples)}/{min_samples})",
        }

    thresholds = {
        field: _percentile([abs(float(item[field])) for item in samples if item.get(field) is not None], 0.4)
        for field in ("price_change_pct", "spot_cvd_delta_ratio", "futures_cvd_delta_ratio", "oi_change_pct")
    }
    price = _direction(current.get("price_change_pct"), thresholds["price_change_pct"])
    spot = _direction(current.get("spot_cvd_delta_ratio"), thresholds["spot_cvd_delta_ratio"])
    futures = _direction(current.get("futures_cvd_delta_ratio"), thresholds["futures_cvd_delta_ratio"])
    oi = _direction(current.get("oi_change_pct"), thresholds["oi_change_pct"])
    if price == "up" and spot == "up":
        state = "spot_led"
    elif price == "up" and futures == "up" and spot in {"down", "flat"} and oi == "up":
        state = "futures_led"
    elif price in {"down", "flat"} and spot == "up":
        state = "spot_absorb"
    elif price == "down" and oi == "down":
        state = "delever"
    else:
        state = "mixed"
    confidence = _state_confidence(
        state,
        values={
            "price": current.get("price_change_pct"),
            "spot_cvd": current.get("spot_cvd_delta_ratio"),
            "futures_cvd": current.get("futures_cvd_delta_ratio"),
            "oi": current.get("oi_change_pct"),
        },
        thresholds={
            "price": thresholds["price_change_pct"],
            "spot_cvd": thresholds["spot_cvd_delta_ratio"],
            "futures_cvd": thresholds["futures_cvd_delta_ratio"],
            "oi": thresholds["oi_change_pct"],
        },
        directions={"price": price, "spot_cvd": spot, "futures_cvd": futures, "oi": oi},
    )
    return {
        **_base(current),
        "state": state,
        "label": MONEY_FLOW_LABELS[state],
        "available": True,
        "provisional": False,
        "sample_size": len(samples),
        "required_samples": min_samples,
        "directions": {"price": price, "spot_cvd": spot, "futures_cvd": futures, "oi": oi},
        "thresholds": {key: round(value, 8) for key, value in thresholds.items()},
        "confidence": confidence,
        "reason": "최근 30일 관측 분포의 40백분위로 방향을 구분했습니다.",
    }


def observations_from_metrics(metrics: list[Any]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for metric in metrics:
        raw = metric.raw_json if hasattr(metric, "raw_json") else metric.get("raw_json", {})
        if not isinstance(raw, dict):
            continue
        observation = raw.get("money_flow_observation")
        if isinstance(observation, dict):
            observations.append(observation)
        aggregate = raw.get("money_flow_aggregate")
        if isinstance(aggregate, dict):
            observations.append(aggregate)
    return observations


def coinglass_flow_observation(metric: Any) -> dict[str, Any] | None:
    raw = metric.raw_json if hasattr(metric, "raw_json") else {}
    value = raw.get("money_flow_aggregate") if isinstance(raw, dict) else None
    return value if isinstance(value, dict) and value.get("spot_cvd_delta_ratio") is not None else None


def _base(current: dict[str, Any]) -> dict[str, Any]:
    return {
        "as_of": current.get("as_of"),
        "source": current.get("source") or "bitget_spot",
        "source_label": "Coinglass 전거래소 집계" if current.get("source") == "coinglass_agg" else "Bitget 단일 거래소 프록시",
        "spot_cvd_delta_ratio": current.get("spot_cvd_delta_ratio"),
        "futures_cvd_delta_ratio": current.get("futures_cvd_delta_ratio"),
        "price_change_pct": current.get("price_change_pct"),
        "oi_change_pct": current.get("oi_change_pct"),
        "spot_cvd": current.get("spot_cvd") or [],
        "futures_cvd": current.get("futures_cvd") or [],
        "coverage": current.get("coverage") or {},
        "notes": current.get("notes") or [],
    }


def _unavailable(reason: str, current: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        **_base(current or {}),
        "state": "mixed",
        "label": "자금 흐름 판정 불가",
        "available": False,
        "provisional": False,
        "sample_size": 0,
        "reason": reason,
    }


def _window_delta_ratio(flow: dict[str, Any] | None) -> float | None:
    if not flow or not flow.get("data_available"):
        return None
    buckets = [item for item in (flow.get("buckets") or []) if isinstance(item, dict)][-24:]
    total = sum(abs(float(item.get("buy_volume") or 0)) + abs(float(item.get("sell_volume") or 0)) for item in buckets)
    if total <= 0:
        return None
    delta = sum(float(item.get("delta") or 0) for item in buckets)
    return round(delta / total, 8)


def _compact_cvd(flow: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = [item for item in (flow or {}).get("cvd", []) if isinstance(item, dict)][-24:]
    return [{"time": item.get("time"), "value": item.get("value")} for item in rows]


def _complete(item: dict[str, Any]) -> bool:
    return all(item.get(field) is not None for field in ("price_change_pct", "spot_cvd_delta_ratio", "futures_cvd_delta_ratio", "oi_change_pct"))


def _after(value: Any, cutoff: datetime) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed >= cutoff
    except (TypeError, ValueError):
        return False


def _direction(value: Any, threshold: float) -> str:
    number = _number(value)
    if number is None or abs(number) < threshold:
        return "flat"
    return "up" if number > 0 else "down"


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("inf")
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _state_confidence(
    state: str,
    *,
    values: dict[str, Any],
    thresholds: dict[str, float],
    directions: dict[str, str],
) -> int:
    required = {
        "spot_led": ("price", "spot_cvd"),
        "futures_led": ("price", "futures_cvd", "spot_cvd", "oi"),
        "spot_absorb": ("price", "spot_cvd"),
        "delever": ("price", "oi"),
    }.get(state, ())
    if not required:
        return 0
    strengths: list[float] = []
    for key in required:
        threshold = thresholds.get(key, float("inf"))
        value = abs(_number(values.get(key)) or 0.0)
        if threshold <= 0 or threshold == float("inf"):
            strengths.append(0.0)
        elif directions.get(key) == "flat":
            strengths.append(max(0.0, 1.0 - min(1.0, value / threshold)))
        else:
            strengths.append(min(1.0, value / threshold))
    return round(sum(strengths) / len(strengths) * 100)


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
