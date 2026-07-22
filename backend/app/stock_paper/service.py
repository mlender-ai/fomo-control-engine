from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any

from app.core.config import Settings
from app.toss.store import TossStockStore

from .accounting import FeeSchedule
from .broker import PaperBroker, create_broker
from .execution import ExecutionPolicy
from .models import Currency, Market, MarketObservation, OrderStatus, Side, StockOrder
from .parameters import load_stock_parameters
from .analysis import analyze_stock_candidate
from .policy import evaluate_stock_entry
from .store import StockPaperStore
from .universe import load_universe


def run_stock_paper_engine(settings: Settings, market_payloads: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    if not settings.stock_paper_engine_enabled:
        return {"enabled": False, "reason": "disabled"}
    if not _ready_to_start(settings):
        return {"enabled": True, "ready_to_start": False, "reason": "toss_observation_not_configured", "live_orders_enabled": False}
    universe = load_universe()
    parameters = load_stock_parameters()
    store = StockPaperStore(settings.database_url)
    store.ensure_tracks(
        universe_version=universe.version,
        initial_krw=settings.stock_paper_initial_krw,
        initial_usd=settings.stock_paper_initial_usd,
    )
    broker = create_broker(
        live_trading_enabled=settings.stock_live_trading_enabled,
        store=store,
        policy=_execution_policy(settings),
    )
    assert isinstance(broker, PaperBroker)
    toss_store = TossStockStore(settings.database_url)
    payloads = market_payloads or {}
    processed = _process_pending_orders(broker, toss_store, payloads)
    evaluated = rejected = strict_entered = coverage_evaluated = coverage_attempted = coverage_entered = 0
    for market_name in ("KR", "US"):
        payload = payloads.get(market_name) or {}
        market = Market(market_name)
        observed_at = _timestamp(payload.get("observed_at") or datetime.now(timezone.utc))
        store.update_market_state(market, str(payload.get("market_state") or payload.get("status") or "unknown"), observed_at)
        if payload.get("status") == "observed":
            store.activate_clock(
                market,
                parameter_version=parameters.version,
                observed_at=observed_at,
            )
        strict_candidates = _unique_candidates(payload.get("trade_groups") if "trade_groups" in payload else payload.get("groups"))
        for candidate in strict_candidates:
            account = store.dashboard()
            symbol = str(candidate.get("symbol") or "").upper()
            if store.position_quantity(market, symbol) > 0 or store.has_active_order(market, symbol):
                continue
            track = next((item for item in account["tracks"] if item["market"] == market.value), None)
            open_count = sum(1 for item in account["positions"] if item["market"] == market.value)
            if open_count >= parameters.max_open_positions:
                store.record_event_if_stale(market, "entry_gate_rejected", symbol=symbol, reason="max_open_positions")
                store.record_entry_rejection(market, symbol, gate="max_open_positions", measured_value=open_count, threshold=parameters.max_open_positions)
                rejected += 1
                continue
            if track and track.get("engine_return_pct") is not None and float(track["engine_return_pct"]) <= -parameters.daily_loss_limit_pct:
                store.record_event_if_stale(market, "entry_gate_rejected", symbol=symbol, reason="daily_loss_limit")
                store.record_entry_rejection(
                    market,
                    symbol,
                    gate="daily_loss_limit",
                    measured_value=track["engine_return_pct"],
                    threshold=-parameters.daily_loss_limit_pct,
                )
                rejected += 1
                continue
            warnings = tuple(str(item) for item in candidate.get("warning_badges") or [])
            if candidate.get("tradable") is False:
                store.record_entry_rejection(market, symbol, gate="universe_entry_blocked", measured_value=candidate.get("role"), threshold="universe_member")
                rejected += 1
                continue
            allowed, universe_reason = universe.entry_allowed(market, symbol, warnings)
            if not allowed:
                store.record_event_if_stale(market, "entry_gate_rejected", symbol=symbol, reason=universe_reason or "universe_entry_blocked")
                store.record_entry_rejection(
                    market, symbol, gate=universe_reason or "universe_entry_blocked", measured_value=warnings, threshold="entry_allowed"
                )
                rejected += 1
                continue
            previous = store.latest_analysis_snapshot(market, symbol) or {}
            previous_confluence = previous.get("confluence") if isinstance(previous.get("confluence"), dict) else {}
            prior_state = previous_confluence.get("stance_state") if isinstance(previous_confluence.get("stance_state"), dict) else None
            analysis = analyze_stock_candidate(
                toss_store,
                market,
                symbol,
                current_price=_optional_float(candidate.get("price")),
                prior_state=prior_state,
            )
            candidate_at = _timestamp(candidate.get("observed_at"))
            store.save_analysis_snapshot(market, symbol, observed_at=candidate_at, parameter_version=parameters.version, payload=analysis)
            decision = evaluate_stock_entry(analysis, data_fresh=_is_fresh(candidate.get("observed_at")), parameters=parameters)
            evaluated += 1
            if not decision.enter:
                reason = (
                    "evidence" if "evidence" in decision.rejection_reasons else decision.rejection_reasons[0] if decision.rejection_reasons else "entry_gate"
                )
                for gate in decision.rejection_reasons:
                    result = decision.gate_results[gate]
                    store.record_entry_rejection(
                        market,
                        symbol,
                        gate=gate,
                        measured_value=result["measured_value"],
                        threshold=result["threshold"],
                        payload={"parameter_version": parameters.version, "source": analysis.get("source")},
                        observed_at=candidate_at,
                    )
                store.record_event_if_stale(
                    market,
                    "entry_gate_rejected",
                    symbol=symbol,
                    reason=reason,
                    payload={"all_reasons": list(decision.rejection_reasons), "source": f"{parameters.version}-shared-analysis"},
                )
                rejected += 1
                continue
            result = _place_entry(
                broker,
                toss_store,
                settings,
                market,
                payload,
                candidate,
                entry_mode="strict_signal",
                capital_fraction=parameters.position_capital_fraction,
                evidence={
                    "decision_gates": decision.gate_results,
                    "analysis_source": analysis.get("source"),
                    "stance_state": ((analysis.get("confluence") or {}).get("stance_state") if isinstance(analysis.get("confluence"), dict) else None),
                    "signature_status": analysis.get("signature_status"),
                    "earnings_gate": analysis.get("earnings_gate"),
                },
            )
            if result == "filled":
                strict_entered += 1
        coverage_candidates = [item for item in payload.get("coverage_candidates") or [] if isinstance(item, dict)]
        coverage_slots_used = store.mode_position_count(market, "coverage") + store.mode_active_order_count(market, "coverage")
        market_track = next((item for item in store.dashboard()["tracks"] if item["market"] == market.value), None)
        loss_gate_open = not (
            market_track and market_track.get("engine_return_pct") is not None and float(market_track["engine_return_pct"]) <= -parameters.daily_loss_limit_pct
        )
        if parameters.coverage_entry_enabled and loss_gate_open and coverage_slots_used < parameters.coverage_target_open_positions:
            scored: list[tuple[int, int, dict[str, Any], dict[str, Any], Any]] = []
            for candidate in coverage_candidates:
                symbol = str(candidate.get("symbol") or "").upper()
                if not symbol or store.position_quantity(market, symbol) > 0 or store.has_active_order(market, symbol):
                    continue
                warnings = tuple(str(item) for item in candidate.get("warning_badges") or [])
                allowed, _ = universe.entry_allowed(market, symbol, warnings)
                if candidate.get("tradable") is False or not allowed or not _is_fresh(candidate.get("observed_at")):
                    continue
                previous = store.latest_analysis_snapshot(market, symbol) or {}
                prior = (previous.get("confluence") or {}).get("stance_state") if isinstance(previous.get("confluence"), dict) else None
                analysis = analyze_stock_candidate(
                    toss_store,
                    market,
                    symbol,
                    current_price=_optional_float(candidate.get("price")),
                    prior_state=prior,
                )
                candidate_at = _timestamp(candidate.get("observed_at"))
                store.save_analysis_snapshot(market, symbol, observed_at=candidate_at, parameter_version=parameters.version, payload=analysis)
                decision = evaluate_stock_entry(analysis, data_fresh=True, parameters=parameters)
                coverage_evaluated += 1
                long_evidence = ((analysis.get("confluence") or {}).get("long_evidence") if isinstance(analysis.get("confluence"), dict) else []) or []
                short_evidence = ((analysis.get("confluence") or {}).get("short_evidence") if isinstance(analysis.get("confluence"), dict) else []) or []
                scored.append((int(analysis.get("entry_score") or 0), len(long_evidence) - len(short_evidence), candidate, analysis, decision))
            scored.sort(key=lambda item: (item[0], item[1], str(item[2].get("symbol") or "")), reverse=True)
            for _, _, candidate, analysis, decision in scored:
                if coverage_attempted >= parameters.coverage_max_attempts_per_cycle:
                    break
                if (
                    store.mode_position_count(market, "coverage") + store.mode_active_order_count(market, "coverage")
                    >= parameters.coverage_target_open_positions
                ):
                    break
                if sum(1 for item in store.dashboard()["positions"] if item["market"] == market.value) >= parameters.max_open_positions:
                    break
                symbol = str(candidate.get("symbol") or "").upper()
                if store.position_quantity(market, symbol) > 0 or store.has_active_order(market, symbol):
                    continue
                bypassed = [gate for gate in decision.rejection_reasons]
                coverage_attempted += 1
                result = _place_entry(
                    broker,
                    toss_store,
                    settings,
                    market,
                    payload,
                    candidate,
                    entry_mode="coverage",
                    capital_fraction=parameters.coverage_position_capital_fraction,
                    evidence={
                        "decision_gates": decision.gate_results,
                        "bypassed_strict_gates": bypassed,
                        "analysis_status": analysis.get("status"),
                        "analysis_source": analysis.get("source"),
                        "coverage_policy": "execution_sample_only_not_strict_performance",
                    },
                )
                store.record_event(
                    market,
                    "coverage_entry_attempt",
                    symbol=symbol,
                    reason=result,
                    payload={"bypassed_strict_gates": bypassed, "parameter_version": parameters.version},
                    observed_at=_timestamp(candidate.get("observed_at")),
                )
                if result == "filled":
                    coverage_entered += 1
    return {
        "enabled": True,
        "evaluated": evaluated,
        "rejected": rejected,
        "strict_entered": strict_entered,
        "coverage_evaluated": coverage_evaluated,
        "coverage_attempted": coverage_attempted,
        "coverage_entered": coverage_entered,
        "pending_processed": processed,
        "universe_version": universe.version,
        "parameter_version": parameters.version,
        "live_orders_enabled": False,
    }


def _place_entry(
    broker: PaperBroker,
    toss_store: TossStockStore,
    settings: Settings,
    market: Market,
    payload: dict[str, Any],
    candidate: dict[str, Any],
    *,
    entry_mode: str,
    capital_fraction: float,
    evidence: dict[str, Any],
) -> str:
    symbol = str(candidate.get("symbol") or "").upper()
    price = _optional_float(candidate.get("price"))
    if price is None or price <= 0:
        return "market_data_missing"
    capital = settings.stock_paper_initial_krw if market == Market.KR else settings.stock_paper_initial_usd
    quantity = max(1, math.floor(capital * capital_fraction / price))
    order = StockOrder(
        symbol=symbol,
        market=market,
        currency=Currency.KRW if market == Market.KR else Currency.USD,
        side=Side.BUY,
        quantity=quantity,
        signal_at=_timestamp(candidate.get("observed_at")),
        signal_price=price,
        entry_mode=entry_mode,
        evidence={"candidate": candidate, "entry_mode": entry_mode, **evidence},
    )
    observation = _observation(toss_store, market, symbol, payload.get("market_state") == "open")
    execution = broker.place(order, observation)
    return "filled" if execution.fill is not None else str(execution.reason or execution.order.status.value)


def stock_paper_dashboard(settings: Settings) -> dict[str, Any]:
    universe = load_universe()
    parameters = load_stock_parameters()
    store = StockPaperStore(settings.database_url)
    configured = _ready_to_start(settings)
    if configured:
        store.ensure_tracks(
            universe_version=universe.version,
            initial_krw=settings.stock_paper_initial_krw,
            initial_usd=settings.stock_paper_initial_usd,
        )
    payload = store.dashboard() if store.enabled else {"tracks": [], "positions": [], "recent_fills": []}
    ready = configured and len(payload.get("tracks") or []) == 2 and all(bool(track.get("clock_valid")) for track in payload["tracks"])
    return {
        **payload,
        "enabled": settings.stock_paper_engine_enabled,
        "ready_to_start": ready,
        "start_block_reason": None if ready else "Toss 인증 성공 후 KR/US 첫 정상 관측에서 시장별 독립 4주 시계를 시작합니다.",
        "execution_model_complete": True,
        "parameter_version": parameters.version,
        "universe": {
            "version": universe.version,
            "effective_at": universe.effective_at,
            "total": len(universe.instruments),
            "markets": {market.value: len(universe.for_market(market)) for market in Market},
            "sources": universe.sources,
            "refresh_policy": "quarterly_manual",
        },
    }


def stock_paper_entry_chart(settings: Settings, market: Market, symbol: str) -> dict[str, Any]:
    """Join persisted paper fills to observed Toss candles for audit display."""
    stock_store = StockPaperStore(settings.database_url)
    fills = stock_store.list_instrument_fills(market, symbol) if stock_store.enabled else []
    if not fills:
        return {
            "market": market.value,
            "symbol": symbol.upper(),
            "timeframe": None,
            "source": None,
            "candles": [],
            "fills": [],
            "empty_reason": "paper_fill_missing",
        }

    anchor = fills[0].filled_at
    toss_store = TossStockStore(settings.database_url)
    timeframe = "1m"
    candles = toss_store.candles_around(market.value, symbol, timeframe, anchor)
    if len(candles) < 2:
        timeframe = "1d"
        candles = toss_store.candles_around(market.value, symbol, timeframe, anchor, before=60, after=60)
    if len(candles) < 2:
        return {
            "market": market.value,
            "symbol": symbol.upper(),
            "timeframe": None,
            "source": None,
            "candles": [],
            "fills": [fill.payload() for fill in reversed(fills)],
            "empty_reason": "observed_candles_missing",
        }

    first_open = datetime.fromisoformat(str(candles[0]["opened_at"]).replace("Z", "+00:00"))
    last_open = datetime.fromisoformat(str(candles[-1]["opened_at"]).replace("Z", "+00:00"))
    visible_fills = [fill for fill in reversed(fills) if first_open <= fill.filled_at <= last_open + _timeframe_span(timeframe)]
    return {
        "market": market.value,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "source": candles[-1]["source"],
        "candles": candles,
        "fills": [fill.payload() for fill in visible_fills],
        "empty_reason": None,
    }


def universe_payload() -> dict[str, Any]:
    universe = load_universe()
    return {
        "version": universe.version,
        "effective_at": universe.effective_at,
        "count": len(universe.instruments),
        "symbols": {market.value: [item.symbol for item in universe.for_market(market)] for market in Market},
        "sources": universe.sources,
    }


def _timeframe_span(timeframe: str) -> timedelta:
    return timedelta(minutes=1) if timeframe == "1m" else timedelta(days=1)


def _execution_policy(settings: Settings) -> ExecutionPolicy:
    return ExecutionPolicy(
        max_minute_volume_ratio=settings.stock_paper_max_minute_volume_ratio,
        fee_schedule=FeeSchedule(
            kr_commission_rate=settings.stock_paper_kr_commission_rate,
            us_commission_rate=settings.stock_paper_us_commission_rate,
            kr_sell_transaction_tax_rate=settings.stock_paper_kr_sell_tax_rate,
        ),
    )


def _ready_to_start(settings: Settings) -> bool:
    return bool(settings.stock_paper_engine_enabled and settings.toss_stock_scout_enabled and settings.toss_client_id and settings.toss_client_secret)


def _process_pending_orders(broker: PaperBroker, toss_store: TossStockStore, payloads: dict[str, dict[str, Any]]) -> int:
    processed = 0
    for order in broker.store.list_orders((OrderStatus.QUEUED, OrderStatus.PARTIAL)):
        payload = payloads.get(order.market.value) or {}
        observation = _observation(toss_store, order.market, order.symbol, payload.get("market_state") == "open")
        broker.place(order, observation)
        processed += 1
    return processed


def _observation(store: TossStockStore, market: Market, symbol: str, session_open: bool) -> MarketObservation | None:
    value = store.latest_execution_observation(market.value, symbol, session_open=session_open)
    if value is None:
        return None
    return MarketObservation(
        symbol=symbol,
        market=market,
        observed_at=_timestamp(value["observed_at"]),
        session_open=bool(value["session_open"]),
        session_open_price=_optional_float(value.get("session_open_price")),
        minute_open=_optional_float(value.get("minute_open")),
        minute_high=_optional_float(value.get("minute_high")),
        minute_low=_optional_float(value.get("minute_low")),
        minute_close=_optional_float(value.get("minute_close")),
        minute_volume=_optional_float(value.get("minute_volume")),
        bid=_optional_float(value.get("bid")),
        ask=_optional_float(value.get("ask")),
        upper_limit=_optional_float(value.get("upper_limit")),
        lower_limit=_optional_float(value.get("lower_limit")),
        upper_locked=bool(value.get("upper_locked")),
        lower_locked=bool(value.get("lower_locked")),
        vi_active=bool(value.get("vi_active")),
        halted=bool(value.get("halted")),
        warnings=tuple(str(item) for item in value.get("warnings") or []),
        fx_rate_to_krw=_optional_float(value.get("fx_rate_to_krw")),
        fx_observed_at=_timestamp(value["fx_observed_at"]) if value.get("fx_observed_at") else None,
    )


def _unique_candidates(groups: Any) -> list[dict[str, Any]]:
    if not isinstance(groups, dict):
        return []
    values: dict[tuple[str, str], dict[str, Any]] = {}
    for rows in groups.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                values[(str(row.get("market") or ""), str(row.get("symbol") or ""))] = row
    return list(values.values())


def _is_fresh(value: Any) -> bool:
    try:
        return abs((datetime.now(timezone.utc) - _timestamp(value)).total_seconds()) <= 120
    except (TypeError, ValueError):
        return False


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
