from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from app.core.config import Settings

from .broker import PaperBroker, taker_fee_per_share
from .client import PolymarketPublicClient, resolved_outcome
from .estimator import attach_execution_cost, estimate_market_probability, kelly_fraction, quality_at_least
from .models import FillInvariantViolation, OrderBook, PaperOrder, PolyMarket
from .parameters import load_poly_parameters
from .store import PolyPaperStore


class PublicMarketClient(Protocol):
    async def list_markets(self, *, limit: int = 100) -> list[PolyMarket]: ...

    async def get_market(self, market_id: str) -> PolyMarket | None: ...

    async def get_order_book(self, token_id: str) -> OrderBook: ...


async def run_poly_paper_engine(
    settings: Settings,
    market_provider: Any,
    repository: Any,
    *,
    client: PublicMarketClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    parameters = load_poly_parameters()
    store = PolyPaperStore(settings.database_url)
    store.ensure_track(initial_cash=settings.polymarket_initial_usdc, parameter_version=parameters.version, now=now)
    if not settings.polymarket_paper_enabled:
        return {"enabled": False, "reason": "disabled", "live_orders_enabled": False}
    public = client or PolymarketPublicClient(
        gamma_base_url=settings.polymarket_gamma_base_url,
        clob_base_url=settings.polymarket_clob_base_url,
        timeout=settings.polymarket_timeout_seconds,
    )
    broker = PaperBroker(max_observed_ask_fraction=parameters.max_observed_ask_fraction)
    try:
        markets = await public.list_markets(limit=settings.polymarket_market_limit)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        store.record_collection(status="error", observed_at=now, error=message)
        return {"enabled": True, "status": "error", "error": message, "live_orders_enabled": False}
    if markets:
        store.activate_clock(now)
    settled = await _settle_due_markets(store, public, repository, now)
    observed = estimated = entered = excluded = strict_entered = coverage_entered = 0
    for source_market in markets:
        observed += 1
        market = _apply_market_gates(source_market, now=now)
        latest_at = store.latest_estimate_at(market.id)
        retry_unexecuted = (
            store.latest_estimate_needs_execution_retry(market.id)
            and not store.has_open_position(market.id)
            and store.open_position_count() < parameters.max_open_markets
        )
        due = latest_at is None or now - latest_at >= timedelta(minutes=parameters.estimate_min_interval_minutes) or retry_unexecuted
        if not due:
            store.save_market(market)
            continue
        # Bitget's public provider exposes a synchronous snapshot wrapper. Run
        # probability estimation off the active worker event loop so that the
        # wrapper can safely drive its async HTTP client without blocking or
        # leaking an un-awaited coroutine.
        result = await asyncio.to_thread(estimate_market_probability, market, market_provider, now=now)
        if result.estimate is None:
            excluded += 1
            store.save_market(replace(market, trade_eligible=False, exclusion_reason=result.reason or market.exclusion_reason))
            continue
        estimate = result.estimate
        book = None
        token_id = market.yes_token_id if estimate.direction.value == "YES" else market.no_token_id
        if not market.trade_eligible or token_id is None:
            estimate = replace(
                estimate,
                trade_eligible=False,
                exclusion_reason=market.exclusion_reason or "clob_token_missing",
            )
        else:
            try:
                book = await public.get_order_book(token_id)
            except Exception:
                book = None
            best_ask = book.asks[0].price if book and book.asks else None
            quality_allowed = quality_at_least(estimate.quality, parameters.min_estimate_quality)
            if best_ask is not None:
                priced = attach_execution_cost(
                    estimate,
                    effective_price=best_ask + taker_fee_per_share(best_ask, market.taker_fee_rate),
                    minimum_edge=parameters.min_edge,
                    quality_allowed=quality_allowed,
                )
                provisional_notional = max(
                    store.cash() * kelly_fraction(priced, cap=parameters.max_position_fraction),
                    store.cash() * parameters.coverage_position_fraction if parameters.coverage_entry_enabled else 0,
                )
                preview = broker.preview(book, provisional_notional, taker_fee_rate=market.taker_fee_rate) if book and provisional_notional > 0 else None
                estimate = attach_execution_cost(
                    estimate,
                    effective_price=preview.effective_price if preview else None,
                    minimum_edge=parameters.min_edge,
                    quality_allowed=quality_allowed,
                )
            else:
                estimate = attach_execution_cost(
                    estimate,
                    effective_price=None,
                    minimum_edge=parameters.min_edge,
                    quality_allowed=quality_allowed,
                )
        coverage_eligible = bool(
            parameters.coverage_entry_enabled
            and market.trade_eligible
            and estimate.exclusion_reason == "after_cost_edge_low"
            and quality_at_least(estimate.quality, parameters.min_estimate_quality)
            and estimate.effective_price is not None
            and book is not None
            and bool(book.asks)
            and estimate.base_rate
            and estimate.evidence
        )
        estimate = replace(estimate, coverage_eligible=coverage_eligible)
        store.save_market(market)
        store.save_estimate(estimate, repository, parameter_version=parameters.version)
        estimated += 1
        entry_mode = "strict_edge" if estimate.trade_eligible else "coverage_calibration" if coverage_eligible else None
        if entry_mode is None:
            excluded += 1
            continue
        if store.has_open_position(market.id) or store.open_position_count() >= parameters.max_open_markets:
            excluded += 1
            continue
        if book is None or token_id is None:
            excluded += 1
            continue
        if entry_mode == "coverage_calibration" and store.open_position_count("coverage_calibration") >= parameters.coverage_target_open_markets:
            excluded += 1
            continue
        fraction = kelly_fraction(estimate, cap=parameters.max_position_fraction) if entry_mode == "strict_edge" else parameters.coverage_position_fraction
        requested_notional = store.cash() * fraction
        if requested_notional <= 0:
            excluded += 1
            continue
        order = PaperOrder(
            market_id=market.id,
            estimate_id=estimate.id,
            token_id=token_id,
            direction=estimate.direction,
            requested_notional=requested_notional,
            created_at=now,
            entry_mode=entry_mode,
        )
        try:
            execution = broker.place(order, book, taker_fee_rate=market.taker_fee_rate)
        except FillInvariantViolation:
            store.stop_track("fill_price_outside_observed_orderbook", now)
            raise
        store.save_execution(order, status=execution.status, reason=execution.reason, fill=execution.fill)
        if execution.fill is not None:
            entered += 1
            if entry_mode == "strict_edge":
                strict_entered += 1
            else:
                coverage_entered += 1
    store.record_collection(status="observed", observed_at=now)
    return {
        "enabled": True,
        "status": "observed",
        "observed": observed,
        "estimated": estimated,
        "entered": entered,
        "strict_entered": strict_entered,
        "coverage_entered": coverage_entered,
        "excluded": excluded,
        "settled": settled,
        "parameter_version": parameters.version,
        "live_orders_enabled": False,
    }


def poly_paper_dashboard(settings: Settings) -> dict[str, Any]:
    parameters = load_poly_parameters()
    store = PolyPaperStore(settings.database_url)
    store.ensure_track(initial_cash=settings.polymarket_initial_usdc, parameter_version=parameters.version)
    payload = store.dashboard()
    return {
        **payload,
        "enabled": settings.polymarket_paper_enabled,
        "parameter_version": parameters.version,
        "read_only_label": "Public market data · PaperBroker only · 지갑/실주문 없음",
        "performance_gate": "대표 산출물은 수익률이 아니라 만기 Brier score와 calibration입니다.",
        "sample_note": "N<30에서는 캘리브레이션 품질 판정을 유보합니다.",
        "categories": ["crypto", "macro"],
        "live_orders_enabled": False,
    }


def _apply_market_gates(market: PolyMarket, *, now: datetime) -> PolyMarket:
    parameters = load_poly_parameters()
    reason = market.exclusion_reason
    if reason is None and market.liquidity < parameters.min_liquidity:
        reason = "liquidity_below_minimum"
    if reason is None and market.end_at is not None:
        remaining_days = (market.end_at - now).total_seconds() / 86_400
        if remaining_days < parameters.min_days_to_resolution:
            reason = "resolution_too_near"
    if reason is None and not market.active:
        reason = "market_inactive"
    return replace(market, trade_eligible=reason is None, exclusion_reason=reason)


async def _settle_due_markets(
    store: PolyPaperStore,
    client: PublicMarketClient,
    repository: Any,
    now: datetime,
) -> int:
    scored = 0
    for market_id in store.unresolved_market_ids():
        try:
            market = await client.get_market(market_id)
        except Exception:
            continue
        if market is None:
            continue
        store.save_market(market)
        outcome = resolved_outcome(market)
        if outcome is None or not market.resolution_source:
            continue
        scored += store.settle_market(
            market,
            outcome=outcome,
            source=market.resolution_source,
            repository=repository,
            resolved_at=now,
        )
    return scored
