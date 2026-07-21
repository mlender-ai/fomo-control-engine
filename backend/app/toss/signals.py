from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable


HARD_EXCLUDE_WARNING_KEYS = {"liquidation", "liquidation_trading", "investment_risk", "정리매매", "투자위험"}
BADGE_WARNING_KEYS = {
    "investment_warning",
    "short_term_overheat",
    "overheated",
    "vi",
    "vi_static",
    "vi_dynamic",
    "vi_static_and_dynamic",
    "투자경고",
    "단기과열",
    "변동성완화장치",
}


def warning_gate(warnings: Iterable[str]) -> tuple[bool, list[str]]:
    normalized = {str(item).strip().lower() for item in warnings if str(item).strip()}
    excluded = bool(normalized & HARD_EXCLUDE_WARNING_KEYS)
    badges = sorted(normalized & BADGE_WARNING_KEYS)
    return excluded, badges


def attention_gap_signal(market_rank: int | None, retail_rank: int | None) -> dict[str, Any] | None:
    if market_rank is None or retail_rank is None:
        return None
    gap = retail_rank - market_rank
    if market_rank <= 30 and gap >= 20:
        return {"type": "attention_gap", "label": "리테일 미주목 · 자금 유입 동시 관측", "gap": gap, "tone": "candidate"}
    if retail_rank <= 10 and market_rank - retail_rank >= 20:
        return {"type": "retail_overheat", "label": "리테일 주목 급증 · 시장 순위 정체", "gap": gap, "tone": "risk"}
    return None


def momentum_signal(candles: list[dict[str, Any]]) -> dict[str, Any] | None:
    if len(candles) < 2:
        return None
    first = float(candles[0]["close"])
    last = float(candles[-1]["close"])
    if first <= 0:
        return None
    change_pct = (last / first - 1) * 100
    if abs(change_pct) < 2:
        return None
    direction = "상승" if change_pct > 0 else "하락"
    return {
        "type": "momentum",
        "label": f"확정 1분봉 구간 {direction} {abs(change_pct):.2f}% 관측",
        "change_pct": change_pct,
        "tone": "candidate" if change_pct > 0 else "risk",
    }


def orderbook_change_signal(previous: float | None, current: float | None) -> dict[str, Any] | None:
    if previous is None or current is None:
        return None
    change = current - previous
    if abs(change) < 0.1:
        return None
    direction = "매수 잔량 비중 증가" if change > 0 else "매도 잔량 비중 증가"
    return {
        "type": "orderbook_change",
        "label": f"호가 잔량 변화 · {direction} {abs(change) * 100:.1f}%p",
        "change_ratio": change,
        "tone": "candidate" if change > 0 else "risk",
    }


def price_limit_signal(price: float | None, upper_limit: float | None) -> dict[str, Any] | None:
    if price is None or upper_limit is None or upper_limit <= 0:
        return None
    distance_pct = (upper_limit / price - 1) * 100
    if not 0 <= distance_pct <= 5:
        return None
    return {
        "type": "price_limit_risk",
        "label": f"상한가까지 {distance_pct:.2f}% · 추격 위험",
        "distance_pct": distance_pct,
        "tone": "risk",
    }


def investor_flow_signal(payloads: list[dict[str, Any]], market_rank: int | None) -> dict[str, Any] | None:
    if market_rank is None or market_rank > 30:
        return None
    net_amount = 0
    observed = False
    for payload in payloads:
        result = payload.get("result") or {}
        records = result.get("records") or [] if isinstance(result, dict) else []
        if not records or not isinstance(records[0], dict):
            continue
        record = records[0]
        for kind in ("foreigner", "institution"):
            flow = record.get(kind) or {}
            try:
                net_amount += int(flow.get("buyAmount") or 0) - int(flow.get("sellAmount") or 0)
                observed = True
            except (TypeError, ValueError):
                continue
    if not observed or net_amount == 0:
        return None
    direction = "순매수" if net_amount > 0 else "순매도"
    return {
        "type": "investor_flow",
        "label": f"시장 외국인·기관 {direction}과 거래대금 상위 동시 관측",
        "net_amount_krw": net_amount,
        "tone": "candidate" if net_amount > 0 else "risk",
    }


def build_candidate(
    *,
    market: str,
    symbol: str,
    name: str,
    price: float | None,
    observed_at: str,
    market_rank: int | None,
    retail_rank: int | None,
    warnings: Iterable[str],
    tradable: bool = False,
    role: str = "observation_only",
    extra_signals: Iterable[dict[str, Any]] = (),
) -> dict[str, Any] | None:
    excluded, badges = warning_gate(warnings)
    if excluded:
        return None
    signals = [signal for signal in [attention_gap_signal(market_rank, retail_rank), *extra_signals] if signal]
    if not signals:
        return None
    return {
        "market": market,
        "entity_type": "stock_kr" if market == "KR" else "stock_us",
        "symbol": symbol,
        "name": name or symbol,
        "price": price,
        "observed_at": observed_at,
        "source": "Toss Securities Open API",
        "warning_badges": badges,
        "signals": signals,
        "market_rank": market_rank,
        "retail_rank": retail_rank,
        "tradable": tradable,
        "role": role,
        "trade_exclusion_reason": None if tradable else role,
    }


def group_candidates(candidates: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        for signal in candidate.get("signals", []):
            grouped[str(signal.get("type") or "unknown")].append(candidate)
    return dict(grouped)


def resample_candles(rows: list[dict[str, Any]], minutes: int) -> list[dict[str, Any]]:
    if minutes not in {5, 15, 60, 240}:
        raise ValueError("supported resample minutes are 5, 15, 60, 240")
    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        timestamp = _timestamp(row["opened_at"])
        bucket = int(timestamp.timestamp()) // (minutes * 60) * (minutes * 60)
        buckets[bucket].append(row)
    result = []
    for bucket, items in sorted(buckets.items()):
        ordered = sorted(items, key=lambda item: _timestamp(item["opened_at"]))
        result.append(
            {
                "opened_at": datetime.fromtimestamp(bucket, tz=timezone.utc).isoformat(),
                "open": float(ordered[0]["open"]),
                "high": max(float(item["high"]) for item in ordered),
                "low": min(float(item["low"]) for item in ordered),
                "close": float(ordered[-1]["close"]),
                "volume": sum(float(item.get("volume") or 0) for item in ordered),
            }
        )
    return result


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
