from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.db.models import Direction, UserTrade, utc_now
from app.exchange.bitget.trades import BitgetAccountFill


USER_FILL_SYNC_SYMBOL = "__SYSTEM__"
USER_FILL_SYNC_TIMEFRAME = "user_fill_sync"
MAX_ACCOUNT_FILL_LOOKBACK_DAYS = 89
INCREMENTAL_OVERLAP_HOURS = 24
EPSILON = 1e-12


def sync_user_fills(
    repo: Any,
    provider: Any,
    *,
    benchmark_started_at: datetime | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    previous = repo.get_paper_engine_state(USER_FILL_SYNC_SYMBOL, USER_FILL_SYNC_TIMEFRAME) or {}
    getter = getattr(provider, "get_account_fills", None)
    private_configured = bool(getattr(getattr(provider, "client", None), "private_configured", False))
    if not callable(getter) or not private_configured:
        state = {
            **previous,
            "status": "not_configured",
            "source": "bitget_account_fills",
            "pnl_status": "reconstructed",
            "last_attempt_at": now.isoformat(),
            "note": "Bitget read-only 계정 체결 권한 또는 인증 설정이 필요합니다.",
        }
        repo.upsert_paper_engine_state(USER_FILL_SYNC_SYMBOL, USER_FILL_SYNC_TIMEFRAME, state)
        return state

    floor = now - timedelta(days=MAX_ACCOUNT_FILL_LOOKBACK_DAYS)
    last_fill_at = _datetime(previous.get("last_fill_at"))
    start_at = max(floor, last_fill_at - timedelta(hours=INCREMENTAL_OVERLAP_HOURS)) if last_fill_at else floor
    try:
        fetched: list[BitgetAccountFill] = getter(start_at, now)
        new_count = 0
        for fill in fetched:
            new_count += 1 if repo.upsert_user_account_fill(fill.model_dump(mode="json")) else 0
        stored_payloads = repo.list_user_account_fills(since=floor, limit=50_000)
        stored = [BitgetAccountFill.model_validate(item) for item in stored_payloads]
        trades, diagnostics = reconstruct_user_trades(stored)
        position_getter = getattr(provider, "get_positions", None)
        if callable(position_getter):
            diagnostics["live_position_reconciliation"] = _reconcile_open_positions(
                diagnostics.get("open_position_details") or [],
                position_getter(),
            )
        else:
            diagnostics["live_position_reconciliation"] = {"status": "unavailable"}
        benchmark_trades = [
            trade
            for trade in trades
            if benchmark_started_at is None or trade.exit_at >= benchmark_started_at
        ]
        for trade in trades:
            repo.upsert_user_trade(trade)
        latest_fill = max((fill.timestamp for fill in stored), default=last_fill_at)
        state = {
            "status": "ok",
            "source": "bitget_account_fills",
            "pnl_status": "reconstructed",
            "fetched_fill_count": len(fetched),
            "new_fill_count": new_count,
            "stored_fill_count": len(stored),
            "reconstructed_trade_count": len(trades),
            "benchmark_trade_count": len(benchmark_trades),
            "last_fill_at": latest_fill.isoformat() if latest_fill else None,
            "last_success_at": now.isoformat(),
            "last_attempt_at": now.isoformat(),
            "diagnostics": diagnostics,
            "note": "인증 계정 체결 기반 재구성 · 거래소 확정 손익과 미세 차이 가능",
        }
        repo.upsert_paper_engine_state(USER_FILL_SYNC_SYMBOL, USER_FILL_SYNC_TIMEFRAME, state)
        return state
    except Exception as exc:
        state = {
            **previous,
            "status": "error",
            "source": "bitget_account_fills",
            "pnl_status": "reconstructed",
            "last_attempt_at": now.isoformat(),
            "last_error": f"{type(exc).__name__}: {exc}",
        }
        repo.upsert_paper_engine_state(USER_FILL_SYNC_SYMBOL, USER_FILL_SYNC_TIMEFRAME, state)
        raise


def reconstruct_user_trades(fills: list[BitgetAccountFill]) -> tuple[list[UserTrade], dict[str, Any]]:
    states: dict[tuple[str, str], dict[str, Any]] = {}
    trades: list[UserTrade] = []
    unmatched_closes = 0
    ignored = 0
    for fill in sorted(fills, key=lambda item: (item.timestamp, _trade_id_order(item.trade_id))):
        action = _fill_action(fill, states)
        if action is None:
            ignored += 1
            continue
        kind, direction = action
        if kind == "open":
            _open(states, fill, direction, fill.size, fill.fee_usdt)
            continue
        closed, remainder = _close(states, fill, direction, fill.size, fill.fee_usdt)
        if closed is None:
            unmatched_closes += 1
        elif isinstance(closed, UserTrade):
            trades.append(closed)
        if remainder > EPSILON and fill.trade_side in {"buy_single", "sell_single"}:
            opposite = "long" if fill.side == "buy" else "short"
            ratio = remainder / fill.size if fill.size > 0 else 0.0
            _open(states, fill, opposite, remainder, fill.fee_usdt * ratio)

    open_details = [
        {
            "symbol": str(state.get("symbol") or ""),
            "direction": str(state.get("direction") or ""),
            "quantity": round(float(state.get("quantity") or 0.0), 12),
            "entry_at": state.get("entry_at").isoformat() if isinstance(state.get("entry_at"), datetime) else None,
        }
        for state in states.values()
        if float(state.get("quantity") or 0) > EPSILON
    ]
    return sorted(trades, key=lambda trade: trade.exit_at), {
        "closed_positions": len(trades),
        "open_positions": len(open_details),
        "open_position_details": sorted(open_details, key=lambda item: (item["symbol"], item["direction"])),
        "unmatched_close_fills": unmatched_closes,
        "ignored_fills": ignored,
    }


def _fill_action(fill: BitgetAccountFill, states: dict[tuple[str, str], dict[str, Any]]) -> tuple[str, str] | None:
    trade_side = fill.trade_side
    if trade_side == "open":
        return "open", "long" if fill.side == "buy" else "short"
    if trade_side == "close":
        # In Bitget hedge-mode account fills, side identifies the position side
        # for both open and close records (open buy -> close buy is a long cycle).
        return "close", "long" if fill.side == "buy" else "short"
    if "close_long" in trade_side or trade_side in {"reduce_sell_single", "burst_sell_single", "delivery_sell_single"}:
        return "close", "long"
    if "close_short" in trade_side or trade_side in {"reduce_buy_single", "burst_buy_single", "delivery_buy_single"}:
        return "close", "short"
    if trade_side in {"buy_single", "sell_single"}:
        closing_direction = "short" if fill.side == "buy" else "long"
        state = states.get((fill.symbol, closing_direction))
        if state and float(state.get("quantity") or 0) > EPSILON:
            return "close", closing_direction
        return "open", "long" if fill.side == "buy" else "short"
    if trade_side.startswith("reduce_") or trade_side.startswith("burst_") or trade_side.startswith("delivery_") or trade_side.startswith("dte_sys_adl_"):
        return "close", "long" if fill.side == "sell" else "short"
    if fill.profit is not None and abs(fill.profit) > EPSILON:
        return "close", "long" if fill.side == "sell" else "short"
    return None


def _open(
    states: dict[tuple[str, str], dict[str, Any]],
    fill: BitgetAccountFill,
    direction: str,
    quantity: float,
    fee: float,
) -> None:
    if quantity <= EPSILON:
        return
    key = (fill.symbol, direction)
    state = states.get(key)
    if state is None:
        state = {
            "symbol": fill.symbol,
            "direction": direction,
            "entry_at": fill.timestamp,
            "quantity": 0.0,
            "cost_basis": 0.0,
            "closed_quantity": 0.0,
            "closed_entry_notional": 0.0,
            "exit_notional": 0.0,
            "gross_pnl": 0.0,
            "entry_fees": 0.0,
            "exit_fees": 0.0,
            "exchange_profit": 0.0,
            "entry_fill_ids": [],
            "exit_fill_ids": [],
            "position_modes": set(),
        }
        states[key] = state
    state["quantity"] += quantity
    state["cost_basis"] += fill.price * quantity
    state["entry_fees"] += fee
    state["entry_fill_ids"].append(fill.trade_id)
    if fill.position_mode:
        state["position_modes"].add(fill.position_mode)


def _close(
    states: dict[tuple[str, str], dict[str, Any]],
    fill: BitgetAccountFill,
    direction: str,
    requested_quantity: float,
    fee: float,
) -> tuple[UserTrade | bool | None, float]:
    key = (fill.symbol, direction)
    state = states.get(key)
    available = float(state.get("quantity") or 0.0) if state else 0.0
    if state is None or available <= EPSILON:
        return None, requested_quantity
    close_quantity = min(available, requested_quantity)
    average_entry = float(state["cost_basis"]) / available
    ratio = close_quantity / requested_quantity if requested_quantity > 0 else 0.0
    entry_notional = average_entry * close_quantity
    exit_notional = fill.price * close_quantity
    direction_sign = 1.0 if direction == "long" else -1.0
    state["closed_quantity"] += close_quantity
    state["closed_entry_notional"] += entry_notional
    state["exit_notional"] += exit_notional
    state["gross_pnl"] += (exit_notional - entry_notional) * direction_sign
    state["exit_fees"] += fee * ratio
    state["exchange_profit"] += float(fill.profit or 0.0) * ratio
    state["exit_fill_ids"].append(fill.trade_id)
    state["quantity"] = available - close_quantity
    state["cost_basis"] = max(0.0, float(state["cost_basis"]) - entry_notional)
    if fill.position_mode:
        state["position_modes"].add(fill.position_mode)
    remainder = max(0.0, requested_quantity - close_quantity)
    if state["quantity"] > EPSILON:
        return False, remainder

    closed_quantity = float(state["closed_quantity"])
    entry_basis = float(state["closed_entry_notional"])
    total_fees = float(state["entry_fees"]) + float(state["exit_fees"])
    gross_pnl = float(state["gross_pnl"])
    net_pnl = gross_pnl - total_fees
    trade_id = uuid5(
        NAMESPACE_URL,
        f"fce:user-trade:{fill.symbol}:{direction}:{state['entry_fill_ids'][0]}:{state['exit_fill_ids'][-1]}",
    )
    trade = UserTrade(
        id=trade_id,
        symbol=fill.symbol,
        direction=Direction(direction),
        entry_at=state["entry_at"],
        exit_at=fill.timestamp,
        entry_price=round(entry_basis / closed_quantity, 8),
        exit_price=round(float(state["exit_notional"]) / closed_quantity, 8),
        quantity=round(closed_quantity, 12),
        entry_notional_usdt=round(entry_basis, 8),
        gross_pnl_usdt=round(gross_pnl, 8),
        fees_usdt=round(total_fees, 8),
        net_pnl_usdt=round(net_pnl, 8),
        net_return_pct=round(net_pnl / entry_basis * 100.0, 6) if entry_basis > 0 else 0.0,
        exchange_reported_profit_usdt=round(float(state["exchange_profit"]), 8),
        fill_count=len(set([*state["entry_fill_ids"], *state["exit_fill_ids"]])),
        entry_fill_ids=list(state["entry_fill_ids"]),
        exit_fill_ids=list(state["exit_fill_ids"]),
        payload={
            "position_modes": sorted(state["position_modes"]),
            "return_basis": "entry_notional",
            "fee_scope": "USDT-denominated feeDetail only",
        },
    )
    states.pop(key, None)
    return trade, remainder


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except (TypeError, ValueError):
        return None


def _reconcile_open_positions(reconstructed: list[dict[str, Any]], live_positions: list[Any]) -> dict[str, Any]:
    reconstructed_map = {
        (str(item.get("symbol") or "").upper(), str(item.get("direction") or "")): float(item.get("quantity") or 0.0)
        for item in reconstructed
    }
    live_map = {
        (str(getattr(item, "symbol", "") or "").upper(), str(getattr(item, "hold_side", "") or "")): float(getattr(item, "total", 0.0) or 0.0)
        for item in live_positions
        if float(getattr(item, "total", 0.0) or 0.0) > EPSILON
    }
    keys = sorted(set(reconstructed_map) | set(live_map))
    mismatches = []
    for symbol, direction in keys:
        reconstructed_quantity = reconstructed_map.get((symbol, direction), 0.0)
        live_quantity = live_map.get((symbol, direction), 0.0)
        tolerance = max(1e-8, abs(live_quantity) * 1e-8)
        if abs(reconstructed_quantity - live_quantity) > tolerance:
            mismatches.append(
                {
                    "symbol": symbol,
                    "direction": direction,
                    "reconstructed_quantity": round(reconstructed_quantity, 12),
                    "live_quantity": round(live_quantity, 12),
                }
            )
    return {
        "status": "matched" if not mismatches else "mismatch",
        "reconstructed_open_count": len(reconstructed_map),
        "live_open_count": len(live_map),
        "mismatches": mismatches,
    }


def _trade_id_order(value: str) -> tuple[int, int | str]:
    try:
        return 0, int(value)
    except (TypeError, ValueError):
        return 1, value
