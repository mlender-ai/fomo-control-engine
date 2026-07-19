from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from app.core.config import Settings
from app.db.models import Direction
from app.paper.policy import PaperPolicy, evaluate_entry
from app.toss.store import TossStockStore

from .accounting import FeeSchedule
from .broker import PaperBroker, create_broker
from .execution import ExecutionPolicy
from .models import Currency, Market, MarketObservation, OrderStatus, Side, StockOrder
from .parameters import StockPaperParameters, load_stock_parameters
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
    evaluated = 0
    rejected = 0
    for market_name in ("KR", "US"):
        payload = payloads.get(market_name) or {}
        market = Market(market_name)
        candidates = _unique_candidates(payload.get("groups"))
        for candidate in candidates:
            account = store.dashboard()
            symbol = str(candidate.get("symbol") or "").upper()
            track = next((item for item in account["tracks"] if item["market"] == market.value), None)
            open_count = sum(1 for item in account["positions"] if item["market"] == market.value)
            if open_count >= parameters.max_open_positions:
                store.record_event_if_stale(market, "entry_gate_rejected", symbol=symbol, reason="max_open_positions")
                rejected += 1
                continue
            if track and track.get("engine_return_pct") is not None and float(track["engine_return_pct"]) <= -parameters.daily_loss_limit_pct:
                store.record_event_if_stale(market, "entry_gate_rejected", symbol=symbol, reason="daily_loss_limit")
                rejected += 1
                continue
            warnings = tuple(str(item) for item in candidate.get("warning_badges") or [])
            allowed, universe_reason = universe.entry_allowed(market, symbol, warnings)
            if not allowed:
                store.record_event_if_stale(market, "entry_gate_rejected", symbol=symbol, reason=universe_reason or "universe_entry_blocked")
                rejected += 1
                continue
            decision = _shared_entry_decision(candidate, parameters)
            evaluated += 1
            if not decision.enter:
                reason = decision.rejection_reasons[0] if decision.rejection_reasons else "entry_gate"
                store.record_event_if_stale(
                    market,
                    "entry_gate_rejected",
                    symbol=symbol,
                    reason=reason,
                    payload={"all_reasons": list(decision.rejection_reasons), "source": "shared_paper_policy"},
                )
                rejected += 1
                continue
            # A stock order is created only after every shared gate supplies real
            # inputs. The current Toss signal payload intentionally leaves RR,
            # earnings and validation blank, so no optimistic fallback reaches here.
            price = _optional_float(candidate.get("price"))
            if price is None:
                store.record_event_if_stale(market, "entry_gate_rejected", symbol=symbol, reason="market_data_missing")
                rejected += 1
                continue
            capital = settings.stock_paper_initial_krw if market == Market.KR else settings.stock_paper_initial_usd
            quantity = max(1, math.floor(capital * parameters.position_capital_fraction / price))
            order = StockOrder(
                symbol=symbol,
                market=market,
                currency=Currency.KRW if market == Market.KR else Currency.USD,
                side=Side.BUY,
                quantity=quantity,
                signal_at=_timestamp(candidate.get("observed_at")),
                signal_price=price,
                evidence={"candidate": candidate, "decision_gates": decision.gates},
            )
            observation = _observation(toss_store, market, symbol, payload.get("market_state") == "open")
            broker.place(order, observation)
    return {
        "enabled": True,
        "evaluated": evaluated,
        "rejected": rejected,
        "pending_processed": processed,
        "universe_version": universe.version,
        "parameter_version": parameters.version,
        "live_orders_enabled": False,
    }


def stock_paper_dashboard(settings: Settings) -> dict[str, Any]:
    universe = load_universe()
    parameters = load_stock_parameters()
    store = StockPaperStore(settings.database_url)
    ready = _ready_to_start(settings)
    if ready:
        store.ensure_tracks(
            universe_version=universe.version,
            initial_krw=settings.stock_paper_initial_krw,
            initial_usd=settings.stock_paper_initial_usd,
        )
    payload = store.dashboard() if store.enabled else {"tracks": [], "positions": [], "recent_fills": []}
    return {
        **payload,
        "enabled": settings.stock_paper_engine_enabled,
        "ready_to_start": ready,
        "start_block_reason": None if ready else "Toss 관측 수집과 인증이 준비된 뒤 독립 4주 시계를 시작합니다.",
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


def universe_payload() -> dict[str, Any]:
    universe = load_universe()
    return {
        "version": universe.version,
        "effective_at": universe.effective_at,
        "count": len(universe.instruments),
        "symbols": {market.value: [item.symbol for item in universe.for_market(market)] for market in Market},
        "sources": universe.sources,
    }


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


def _shared_entry_decision(candidate: dict[str, Any], parameters: StockPaperParameters):
    signals = [item for item in candidate.get("signals") or [] if isinstance(item, dict)]
    positive = [item for item in signals if item.get("tone") == "candidate"]
    momentum = next((item for item in positive if item.get("type") == "momentum" and _optional_float(item.get("change_pct")) is not None), None)
    rr = next((_optional_float(item.get("rr_ratio")) for item in signals if item.get("rr_ratio") is not None), None)
    has_invalidation = any(item.get("invalidation_price") is not None for item in signals)
    validated = any(item.get("validation_status") == "validated" for item in signals)
    ci_low = next((_optional_float(item.get("ci_low_pct")) for item in signals if item.get("ci_low_pct") is not None), None)
    earnings_clear = any(item.get("earnings_clear") is True for item in signals)
    return evaluate_entry(
        stance_state={"stance": "long", "flipped": momentum is not None, "transitioning": False},
        direction=Direction.long,
        evidence_count=len(positive),
        checklist_passed=len(positive),
        checklist_total=max(5, len(signals)),
        rr_ratio=rr,
        survives_to_invalidation=has_invalidation,
        validated_signature=validated,
        signature_ci_low_pct=ci_low,
        earnings_clear=earnings_clear,
        data_fresh=_is_fresh(candidate.get("observed_at")),
        confirmed_bar=momentum is not None,
        policy=PaperPolicy(
            margin_usdt=1,
            leverage=1,
            max_open_positions=parameters.max_open_positions,
            min_evidence=parameters.min_evidence,
            min_checklist_passed=parameters.min_checklist_passed,
            min_checklist_total=parameters.min_checklist_total,
            min_rr=parameters.min_rr,
            min_signature_ci_low_pct=parameters.min_signature_ci_low_pct,
        ),
    )


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
