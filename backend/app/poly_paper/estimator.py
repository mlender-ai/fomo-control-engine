from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
import re
from typing import Any, Protocol

from .models import EstimateQuality, Evidence, OutcomeSide, PolyMarket, ProbabilityEstimate


SYMBOLS = {
    "bitcoin": "BTCUSDT",
    "btc": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "ether": "ETHUSDT",
    "eth": "ETHUSDT",
    "solana": "SOLUSDT",
    "sol": "SOLUSDT",
    "xrp": "XRPUSDT",
}
ABOVE_TERMS = ("above", "over", "reach", "hit", "touch", "exceed", "higher than", "at least")
BELOW_TERMS = ("below", "under", "dip", "drop", "lower than", "at most")
BARRIER_TERMS = ("reach", "hit", "touch", "dip", "drop")


class SnapshotProvider(Protocol):
    def get_snapshot(self, symbol: str, timeframe: str = "4h") -> Any: ...


@dataclass(frozen=True)
class EstimationResult:
    estimate: ProbabilityEstimate | None
    reason: str | None


def estimate_market_probability(
    market: PolyMarket,
    provider: SnapshotProvider,
    *,
    now: datetime | None = None,
) -> EstimationResult:
    now = now or datetime.now(timezone.utc)
    if market.category.value == "macro":
        return EstimationResult(None, "macro_base_rate_provider_unavailable")
    if market.market_probability is None:
        return EstimationResult(None, "market_probability_missing")
    parsed = _parse_crypto_threshold(market.question)
    if parsed is None:
        return EstimationResult(None, "unsupported_crypto_question")
    symbol, target, event_type = parsed
    if market.end_at is None or market.end_at <= now:
        return EstimationResult(None, "resolution_time_invalid")
    try:
        snapshot = provider.get_snapshot(symbol, "4h")
    except Exception:
        return EstimationResult(None, "crypto_evidence_unavailable")
    candles = list(getattr(snapshot, "candles", []) or [])
    closes = [float(item.close) for item in candles if float(item.close) > 0]
    if len(closes) < 31:
        return EstimationResult(None, "realized_volatility_sample_low")
    spot = float(getattr(snapshot, "price", closes[-1]))
    if spot <= 0 or target <= 0:
        return EstimationResult(None, "crypto_evidence_invalid")
    log_returns = [math.log(current / previous) for previous, current in zip(closes, closes[1:]) if previous > 0 and current > 0]
    sigma_4h = _sample_std(log_returns)
    if sigma_4h <= 0:
        return EstimationResult(None, "realized_volatility_zero")
    annualized_vol = sigma_4h * math.sqrt(6 * 365.25)
    years = (market.end_at - now).total_seconds() / (365.25 * 24 * 3600)
    estimated = _event_probability(spot, target, annualized_vol, years, event_type)
    estimated = _clamp(estimated, 0.01, 0.99)
    data_quality = getattr(snapshot, "data_quality", None)
    confirmed_at = getattr(data_quality, "last_candle_at", None) or getattr(candles[-1], "timestamp", now)
    if confirmed_at.tzinfo is None:
        confirmed_at = confirmed_at.replace(tzinfo=timezone.utc)
    quality = EstimateQuality.HIGH if len(log_returns) >= 90 and not bool(getattr(data_quality, "fallback_used", False)) else EstimateQuality.MEDIUM
    width = max(0.04, min(0.18, 0.7 / math.sqrt(len(log_returns))))
    market_probability = float(market.market_probability)
    direction = OutcomeSide.YES if estimated > market_probability else OutcomeSide.NO
    evidence = (
        Evidence(f"{symbol.removesuffix('USDT')} 현물 {spot:,.6g}", f"{getattr(snapshot, 'provider', 'market_data')}:4h", confirmed_at, spot),
        Evidence(
            f"확정 4시간봉 {len(log_returns)}개 연율 실현 변동성 {annualized_vol:.2%}",
            f"{getattr(snapshot, 'provider', 'market_data')}:4h",
            confirmed_at,
            annualized_vol,
        ),
        Evidence(f"시장 명시 임계값 {target:,.6g}", "polymarket:resolution_question", market.observed_at, target),
        Evidence(f"만기까지 {years * 365.25:.2f}일", "polymarket:market_metadata", market.observed_at, market.end_at.isoformat()),
    )
    estimate = ProbabilityEstimate(
        market_id=market.id,
        observed_at=now,
        market_probability=market_probability,
        estimated_probability=estimated,
        confidence_low=_clamp(estimated - width, 0, 1),
        confidence_high=_clamp(estimated + width, 0, 1),
        quality=quality,
        base_rate={
            "model": "lognormal_zero_drift_v1",
            "spot": spot,
            "target": target,
            "annualized_realized_volatility": annualized_vol,
            "horizon_years": years,
            "event": event_type,
        },
        evidence=evidence,
        reasoning=(
            f"현물·확정 4시간봉 실현 변동성·남은 시간을 0-drift lognormal 베이스레이트에 적용했습니다. 질문의 정산 조건은 {event_type}로 분리해 계산했습니다."
        ),
        direction=direction,
        gross_edge=abs(estimated - market_probability),
    )
    return EstimationResult(estimate, None)


def attach_execution_cost(
    estimate: ProbabilityEstimate,
    *,
    effective_price: float | None,
    minimum_edge: float,
    quality_allowed: bool,
) -> ProbabilityEstimate:
    if effective_price is None:
        return replace(estimate, effective_price=None, after_cost_edge=None, trade_eligible=False, exclusion_reason="orderbook_unavailable")
    side_probability = estimate.estimated_probability if estimate.direction == OutcomeSide.YES else 1 - estimate.estimated_probability
    after_cost_edge = side_probability - effective_price
    eligible = quality_allowed and after_cost_edge >= minimum_edge
    reason = None if eligible else "estimate_quality_low" if not quality_allowed else "after_cost_edge_low"
    return replace(
        estimate,
        effective_price=effective_price,
        after_cost_edge=after_cost_edge,
        trade_eligible=eligible,
        exclusion_reason=reason,
    )


def kelly_fraction(estimate: ProbabilityEstimate, *, cap: float) -> float:
    if estimate.effective_price is None:
        return 0.0
    probability = estimate.estimated_probability if estimate.direction == OutcomeSide.YES else 1 - estimate.estimated_probability
    denominator = 1 - estimate.effective_price
    if denominator <= 0:
        return 0.0
    return max(0.0, min(cap, (probability - estimate.effective_price) / denominator))


def quality_at_least(value: EstimateQuality, minimum: EstimateQuality) -> bool:
    rank = {EstimateQuality.LOW: 0, EstimateQuality.MEDIUM: 1, EstimateQuality.HIGH: 2}
    return rank[value] >= rank[minimum]


def _parse_crypto_threshold(question: str) -> tuple[str, float, str] | None:
    text = question.lower().replace("$", " $")
    symbol = next((ticker for name, ticker in SYMBOLS.items() if re.search(rf"\b{re.escape(name)}\b", text)), None)
    if symbol is None:
        return None
    event_is_above = True if any(term in text for term in ABOVE_TERMS) else False if any(term in text for term in BELOW_TERMS) else None
    if event_is_above is None:
        return None
    candidates = re.findall(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?\s*[kKmM]?)", text)
    if not candidates:
        directional = "|".join(re.escape(term) for term in (*ABOVE_TERMS, *BELOW_TERMS))
        match = re.search(rf"(?:{directional})[^0-9]{{0,18}}([0-9][0-9,]*(?:\.[0-9]+)?\s*[kKmM]?)", text)
        candidates = [match.group(1)] if match else []
    if not candidates:
        return None
    target = _compact_number(candidates[0])
    if target is None:
        return None
    barrier = any(re.search(rf"\b{re.escape(term)}\b", text) for term in BARRIER_TERMS)
    event_type = ("upper_touch" if event_is_above else "lower_touch") if barrier else ("terminal_above" if event_is_above else "terminal_below")
    return symbol, target, event_type


def _compact_number(value: str) -> float | None:
    normalized = value.replace(",", "").replace(" ", "")
    multiplier = 1.0
    if normalized[-1:].lower() == "k":
        multiplier = 1_000
        normalized = normalized[:-1]
    elif normalized[-1:].lower() == "m":
        multiplier = 1_000_000
        normalized = normalized[:-1]
    try:
        return float(normalized) * multiplier
    except ValueError:
        return None


def _probability_above(spot: float, target: float, annualized_vol: float, years: float) -> float:
    deviation = annualized_vol * math.sqrt(max(years, 1 / (365.25 * 24)))
    if deviation <= 0:
        return 1.0 if spot > target else 0.0
    z = (math.log(target / spot) + 0.5 * annualized_vol * annualized_vol * years) / deviation
    return 1 - 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _event_probability(spot: float, target: float, annualized_vol: float, years: float, event_type: str) -> float:
    if event_type == "terminal_above":
        return _probability_above(spot, target, annualized_vol, years)
    if event_type == "terminal_below":
        return 1 - _probability_above(spot, target, annualized_vol, years)
    if event_type == "upper_touch":
        return _barrier_touch_probability(spot, target, annualized_vol, years, upper=True)
    if event_type == "lower_touch":
        return _barrier_touch_probability(spot, target, annualized_vol, years, upper=False)
    raise ValueError(f"unsupported event type: {event_type}")


def _barrier_touch_probability(spot: float, target: float, annualized_vol: float, years: float, *, upper: bool) -> float:
    if (upper and spot >= target) or (not upper and spot <= target):
        return 1.0
    sigma = annualized_vol
    horizon = max(years, 1 / (365.25 * 24))
    if sigma <= 0 or spot <= 0 or target <= 0:
        return 0.0
    barrier = math.log(target / spot) if upper else math.log(spot / target)
    drift = -0.5 * sigma * sigma
    transformed_drift = drift if upper else -drift
    scale = sigma * math.sqrt(horizon)
    first = _normal_cdf((transformed_drift * horizon - barrier) / scale)
    second = math.exp(2 * transformed_drift * barrier / (sigma * sigma)) * _normal_cdf((-transformed_drift * horizon - barrier) / scale)
    return _clamp(first + second, 0.0, 1.0)


def _normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))
