from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from app.db.models import Direction, MarketCandle, PaperTrade


ExitReason = Literal[
    "invalidation_breach",
    "breakeven_stop",
    "opposite_stance_flip",
    "take_profit_pressure",
    "take_profit_2",
    "time_decay",
    "time_stop",
]


@dataclass(frozen=True)
class PaperPolicy:
    margin_usdt: float = 100.0
    leverage: float = 3.0
    max_open_positions: int = 5
    min_evidence: int = 4
    min_checklist_passed: int = 5
    min_checklist_total: int = 5
    min_rr: float = 1.5
    min_signature_ci_low_pct: float = 50.0
    max_holding_bars: int = 30
    take_profit_atr_k1: float = 1.0
    take_profit_atr_k2: float = 2.0
    take_profit_pressure_bars: int = 2
    taker_fee_pct: float = 0.06
    slippage_pct: float = 0.03

    @property
    def execution_cost_rate(self) -> float:
        return max(0.0, self.taker_fee_pct + self.slippage_pct) / 100.0


@dataclass(frozen=True)
class EntryDecision:
    enter: bool
    gates: dict[str, bool]
    rejection_reasons: tuple[str, ...]


@dataclass(frozen=True)
class ExitDecision:
    action: Literal["hold", "partial", "close"]
    reason: ExitReason | Literal["take_profit_1", "none"] = "none"
    high_pressure_streak: int = 0
    execution_price: float | None = None


def evaluate_entry(
    *,
    stance_state: dict[str, Any],
    direction: Direction,
    evidence_count: int,
    checklist_passed: int,
    checklist_total: int,
    rr_ratio: float | None,
    invalidation_hygiene: bool = True,
    survives_to_invalidation: bool,
    validated_signature: bool,
    signature_ci_low_pct: float | None,
    earnings_clear: bool,
    data_fresh: bool,
    confirmed_bar: bool,
    policy: PaperPolicy,
) -> EntryDecision:
    stance = str(stance_state.get("stance") or "")
    stance_direction = "long" if stance in {"long", "long_leaning"} else "short" if stance in {"short", "short_leaning"} else stance
    gates = {
        "confirmed_flip": bool(
            confirmed_bar and stance_state.get("flipped") is True and stance_state.get("transitioning") is not True and stance_direction == direction.value
        ),
        "evidence": evidence_count >= policy.min_evidence,
        "checklist": checklist_passed >= policy.min_checklist_passed and checklist_total >= policy.min_checklist_total,
        "invalidation_hygiene": invalidation_hygiene,
        "risk_reward": rr_ratio is not None and rr_ratio >= policy.min_rr,
        "liquidation_safety": survives_to_invalidation,
        "validated_signature": validated_signature and signature_ci_low_pct is not None and signature_ci_low_pct >= policy.min_signature_ci_low_pct,
        "earnings_clear": earnings_clear,
        "data_fresh": data_fresh,
    }
    rejected = tuple(name for name, passed in gates.items() if not passed)
    return EntryDecision(enter=not rejected, gates=gates, rejection_reasons=rejected)


def open_trade(
    *,
    trade_id: UUID,
    symbol: str,
    timeframe: str,
    asset_class: str,
    direction: Direction,
    bar: MarketCandle,
    invalidation_price: float,
    take_profit_price: float,
    evidence: dict[str, Any],
    checklist: dict[str, Any],
    stance_snapshot: dict[str, Any],
    signature_snapshot: dict[str, Any],
    policy: PaperPolicy,
    take_profit_2_price: float | None = None,
    entry_atr: float | None = None,
    target_plan: dict[str, Any] | None = None,
) -> PaperTrade:
    notional = policy.margin_usdt * policy.leverage
    quantity = notional / bar.close
    entry_cost = notional * policy.execution_cost_rate
    return PaperTrade(
        id=trade_id,
        symbol=symbol.upper(),
        timeframe=timeframe,
        asset_class=asset_class,
        direction=direction,
        entry_bar_at=bar.timestamp,
        entry_at=bar.timestamp,
        entry_price=bar.close,
        margin_usdt=policy.margin_usdt,
        leverage=policy.leverage,
        quantity=quantity,
        remaining_quantity=quantity,
        invalidation_price=invalidation_price,
        take_profit_price=take_profit_price,
        take_profit_2_price=take_profit_2_price,
        entry_atr=entry_atr,
        target_plan=target_plan or {},
        stop_price=invalidation_price,
        entry_evidence=evidence,
        checklist=checklist,
        stance_snapshot=stance_snapshot,
        signature_snapshot=signature_snapshot,
        costs_usdt=entry_cost,
        net_pnl_usdt=-entry_cost,
        net_return_pct=(-entry_cost / policy.margin_usdt) * 100.0,
        judgment_id=f"paper:{trade_id}:entry",
        created_at=bar.timestamp,
        updated_at=bar.timestamp,
    )


def evaluate_exit(
    trade: PaperTrade,
    *,
    bar: MarketCandle,
    stance_state: dict[str, Any],
    take_profit_pressure: str | None,
    prior_high_pressure_streak: int,
    policy: PaperPolicy,
) -> ExitDecision:
    next_holding_bars = trade.holding_bars + 1
    if _stop_breached(trade, bar.close):
        reason: ExitReason = "breakeven_stop" if trade.partial_exit_at else "invalidation_breach"
        return ExitDecision("close", reason, 0)

    if trade.partial_exit_at is None and _take_profit_touched(trade, bar):
        return ExitDecision("partial", "take_profit_1", 0, trade.take_profit_price)

    if trade.partial_exit_at is not None and _take_profit_2_touched(trade, bar):
        return ExitDecision("close", "take_profit_2", 0, trade.take_profit_2_price)

    if _opposite_confirmed_flip(trade, stance_state):
        return ExitDecision("close", "opposite_stance_flip", 0)

    # Take-profit pressure protects gains on the remainder; it is not a pre-TP
    # stop and must never close a position that has not realized TP1.
    high_streak = prior_high_pressure_streak + 1 if trade.partial_exit_at is not None and take_profit_pressure == "high" else 0
    if trade.partial_exit_at is not None and high_streak >= policy.take_profit_pressure_bars:
        return ExitDecision("close", "take_profit_pressure", high_streak)
    if next_holding_bars >= policy.max_holding_bars:
        if _stance_supports_trade(trade, stance_state):
            return ExitDecision("hold", "none", high_streak)
        return ExitDecision("close", "time_decay", high_streak)
    return ExitDecision("hold", "none", high_streak)


def apply_exit_decision(
    trade: PaperTrade,
    *,
    decision: ExitDecision,
    bar: MarketCandle,
    policy: PaperPolicy,
) -> PaperTrade:
    holding_bars = trade.holding_bars + 1
    event_at = max(bar.timestamp, trade.entry_at)
    if decision.action == "hold":
        return trade.model_copy(update={"holding_bars": holding_bars, "updated_at": event_at})

    execution_price = decision.execution_price or bar.close
    exit_quantity = trade.remaining_quantity * (0.5 if decision.action == "partial" else 1.0)
    gross_increment = _gross_pnl(trade.direction, trade.entry_price, execution_price, exit_quantity)
    exit_cost = execution_price * exit_quantity * policy.execution_cost_rate
    gross = trade.gross_pnl_usdt + gross_increment
    costs = trade.costs_usdt + exit_cost
    net = gross - costs
    common = {
        "gross_pnl_usdt": gross,
        "costs_usdt": costs,
        "net_pnl_usdt": net,
        "net_return_pct": (net / trade.margin_usdt) * 100.0,
        "holding_bars": holding_bars,
        "updated_at": event_at,
    }
    if decision.action == "partial":
        return trade.model_copy(
            update={
                **common,
                "remaining_quantity": trade.remaining_quantity - exit_quantity,
                "partial_exit_at": event_at,
                "partial_exit_price": execution_price,
                "partial_exit_quantity": exit_quantity,
                "stop_price": trade.entry_price,
            }
        )
    return trade.model_copy(
        update={
            **common,
            "status": "closed",
            "remaining_quantity": 0.0,
            "exit_bar_at": bar.timestamp,
            "exit_at": event_at,
            "exit_price": execution_price,
            "exit_reason": decision.reason,
        }
    )


def _stop_breached(trade: PaperTrade, close: float) -> bool:
    if trade.direction == Direction.long:
        return close <= trade.stop_price
    return close >= trade.stop_price


def _take_profit_touched(trade: PaperTrade, bar: MarketCandle) -> bool:
    if trade.direction == Direction.long:
        return bar.high >= trade.take_profit_price
    return bar.low <= trade.take_profit_price


def _take_profit_2_touched(trade: PaperTrade, bar: MarketCandle) -> bool:
    target = trade.take_profit_2_price
    if target is None:
        return False
    if trade.direction == Direction.long:
        return bar.high >= target
    return bar.low <= target


def _opposite_confirmed_flip(trade: PaperTrade, stance_state: dict[str, Any]) -> bool:
    if stance_state.get("flipped") is not True or stance_state.get("transitioning") is True:
        return False
    stance = str(stance_state.get("stance") or "")
    return (trade.direction == Direction.long and stance in {"short", "short_leaning"}) or (
        trade.direction == Direction.short and stance in {"long", "long_leaning"}
    )


def _stance_supports_trade(trade: PaperTrade, stance_state: dict[str, Any]) -> bool:
    if stance_state.get("transitioning") is True:
        return False
    stance = str(stance_state.get("stance") or "")
    if trade.direction == Direction.long:
        return stance in {"long", "long_leaning"}
    return stance in {"short", "short_leaning"}


def _gross_pnl(direction: Direction, entry: float, exit_price: float, quantity: float) -> float:
    sign = 1.0 if direction == Direction.long else -1.0
    return (exit_price - entry) * quantity * sign
