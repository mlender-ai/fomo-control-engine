from __future__ import annotations

from typing import Any

from app.db.models import DerivativeMetric, LiquidationEvent
from app.derivatives.engine import coinglass_status_snapshot, flow_summary
from app.marketdata.signals import build_derivative_signals
from app.validation.candidates import candidate_engine_review


def derivative_context_for_symbol(
    repository: Any,
    settings: Any,
    symbol: str,
    *,
    metric_limit: int = 500,
    event_limit: int = 200,
) -> dict[str, Any]:
    normalized = symbol.upper()
    latest = repository.latest_derivative_snapshot(normalized, provider="bitget")
    coinglass = repository.latest_derivative_snapshot(normalized, provider="coinglass") or coinglass_status_snapshot(normalized, settings)
    metric_history: list[DerivativeMetric] = repository.list_derivative_metrics(symbol=normalized, limit=metric_limit)
    liquidation_history: list[LiquidationEvent] = repository.list_liquidation_events(symbol=normalized, limit=event_limit)
    signals = build_derivative_signals(metric_history, liquidation_history, coinglass.liquidation_clusters)
    money_flow = signals.get("money_flow") if isinstance(signals.get("money_flow"), dict) else None
    if money_flow is not None:
        review = candidate_engine_review(repository, settings, "money_flow") or {}
        money_flow["predictive_warning"] = bool(review.get("predictive_warning"))
        money_flow["candidate_sample_size"] = int(review.get("sample_size") or 0)
        money_flow["candidate_win_1r_ci"] = review.get("win_1r_ci")
    return {
        "symbol": normalized,
        "as_of": signals.get("as_of") or (latest.as_of.isoformat() if latest else None),
        "latest": latest.model_dump(mode="json") if latest else None,
        "summary": flow_summary(latest),
        "coinglass": coinglass.model_dump(mode="json"),
        "signals": signals,
        "metrics": [item.model_dump(mode="json") for item in metric_history[:100]],
        "liquidation_events": [item.model_dump(mode="json") for item in liquidation_history],
        "source": "bitget",
        "source_status": latest.source_status if latest else "missing",
        "number_sources": _number_sources(latest, coinglass, signals),
    }


def derivative_context_for_chart(repository: Any, settings: Any, symbol: str) -> dict[str, Any]:
    context = derivative_context_for_symbol(repository, settings, symbol)
    return {
        "symbol": context["symbol"],
        "as_of": context["as_of"],
        "latest": context["latest"],
        "coinglass": context["coinglass"],
        "signals": context["signals"],
        "metrics": context["metrics"],
        "liquidation_events": context["liquidation_events"],
        "source_status": context["source_status"],
    }


def compact_derivative_context(context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    return {
        "as_of": context.get("as_of"),
        "latest": context.get("latest"),
        "coinglass": context.get("coinglass"),
        "signals": context.get("signals"),
        "source_status": context.get("source_status"),
    }


def _number_sources(latest: Any, coinglass: Any, signals: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    if latest is not None:
        for field, value in (
            ("open_interest", latest.open_interest),
            ("oi_change_pct", latest.open_interest_change_pct),
            ("funding", latest.funding_rate),
            ("long_short_ratio", latest.long_short_ratio),
        ):
            if value is not None:
                sources.append(
                    {
                        "label": field,
                        "value": value,
                        "source": "derivative_snapshots.bitget",
                        "as_of": latest.as_of.isoformat(),
                    }
                )
    if coinglass is not None and coinglass.source_status == "ok":
        for field, value in (
            ("top_long_short_ratio", coinglass.top_long_short_ratio),
            ("oi_weighted_funding_rate", coinglass.oi_weighted_funding_rate),
        ):
            if value is not None:
                sources.append(
                    {
                        "label": field,
                        "value": value,
                        "source": "derivative_snapshots.coinglass",
                        "as_of": coinglass.as_of.isoformat(),
                    }
                )
    crowding = signals.get("crowding_score") if isinstance(signals.get("crowding_score"), dict) else None
    if crowding and crowding.get("score") is not None:
        sources.append(
            {
                "label": "crowding_score",
                "value": crowding["score"],
                "source": "marketdata.signals.crowding_score",
                "as_of": signals.get("as_of"),
            }
        )
    return sources
