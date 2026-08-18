from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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
    # WO-FCE-CORE-DEFECTS-01 Phase 1. 기본값은 **기존 동작**이며, crypto-v2.json 이
    # 로드될 때만 새 모드가 적용된다(옵트인 — 파일 없으면 회귀 0).
    version: str = "crypto-v1"
    stance_gate_mode: str = "confirmed_flip"
    signature_gate_mode: str = "required"
    # WO-FCE-RISK-SIZING-01 Phase 1. 기본값은 **기존 동작**(고정 명목)이며 옵트인이다.
    #
    # 고정 명목에서는 수량이 스톱 거리와 무관하게 정해진다. 그래서 1R 의 금액가치가
    # 스톱 거리에 비례하고(실측 CV 0.522 · 최대/최소 6.16배), 스톱 거리가 1.28배 큰
    # 지는 거래가 같은 −1R 로도 더 큰 금액을 잃는다. R 회계와 금액 회계가 어긋난다.
    #
    #   수량 = 리스크 예산 / |진입가 − 무효화가|  →  1R 금액 = 리스크 예산 (상수)
    sizing_mode: str = "fixed_notional"
    risk_budget_usdt: float = 2.5
    max_notional_usdt: float = 600.0
    min_notional_usdt: float = 10.0
    # WO-FCE-RISK-SIZING-01 Phase 3. 기본값은 **기존 동작**(잠금 없음)이며 옵트인이다.
    #
    # 같은 확정봉 안에서 청산하고 곧바로 같은 가격·같은 방향으로 다시 들어가는 일이
    # 실측 9건 있었다. 새 판단이 아니라 왕복이다 — gross 우위가 **음수**(−0.914R)이면서
    # 비용만 1.127R 을 낸다. 막으면 우위/비용 비율이 3.93배 → 1.51배가 된다.
    reentry_lock_mode: str = "off"
    reentry_lock_bars: int = 0
    reentry_lock_same_direction_only: bool = True

    @property
    def execution_cost_rate(self) -> float:
        return max(0.0, self.taker_fee_pct + self.slippage_pct) / 100.0


@dataclass(frozen=True)
class EntryDecision:
    enter: bool
    gates: dict[str, bool]
    rejection_reasons: tuple[str, ...]
    # 판정 조건에서 제외했지만 사후 채점을 위해 원장에 남기는 관측치(Phase 1).
    observations: dict[str, Any] = field(default_factory=dict)


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
    # Phase 1: flipped 는 **판정 대상이지 판정 조건이 아니다**.
    #
    # 실측(2026-08-05, 600건): confirmed_flip 통과 6.0%. 원인은 WO 가정("flipped 와
    # transitioning 이 겹치는 창이 없다")과 다르다 — 상태머신은 flip 완료 시
    # build(..., transitioning=False, flipped=True) 로 **둘을 동시에** 세우므로 모순이
    # 아니다. 진짜 제약은 flipped=True 가 **flip 완료 봉 1봉만의 펄스**라는 것이다
    # (이후 봉은 cand==prior_stance → flipped=False, 봉 앵커 동결도 False).
    # 즉 진입 기회가 flip 순간 1봉으로 제한됐고, 그 1봉에서 나머지 게이트까지 동시에
    # 통과해야 했다. 이번 주 flip 14회 → 진입 0회가 그 결과다.
    #
    # stable_direction: 방향 일치 + 안정 상태를 요구한다. 주식 stock-v4
    # (stance_gate_mode=stable_long)와 같은 원리를 롱·숏 대칭으로 이식한 것이며,
    # 품질 임계(evidence·checklist·rr 등)는 그대로 둔다 — 완화가 아니라 정합 수리다.
    if policy.stance_gate_mode == "stable_direction":
        stance_passed = bool(confirmed_bar and stance_direction == direction.value and stance_state.get("transitioning") is not True)
    else:
        stance_passed = bool(
            confirmed_bar and stance_state.get("flipped") is True and stance_state.get("transitioning") is not True and stance_direction == direction.value
        )
    # 시그니처 검증을 진입 조건에서 뺀다(record_only) — 검증 대상을 검증 조건으로 삼으면
    # 표본이 영원히 생기지 않는다. 상태는 아래 signature_status 로 원장에 남는다.
    signature_passed = (
        True
        if policy.signature_gate_mode == "record_only"
        else bool(validated_signature and signature_ci_low_pct is not None and signature_ci_low_pct >= policy.min_signature_ci_low_pct)
    )
    gates = {
        "confirmed_flip": stance_passed,
        "evidence": evidence_count >= policy.min_evidence,
        "checklist": checklist_passed >= policy.min_checklist_passed and checklist_total >= policy.min_checklist_total,
        "invalidation_hygiene": invalidation_hygiene,
        "risk_reward": rr_ratio is not None and rr_ratio >= policy.min_rr,
        "liquidation_safety": survives_to_invalidation,
        "validated_signature": signature_passed,
        "earnings_clear": earnings_clear,
        "data_fresh": data_fresh,
    }
    rejected = tuple(name for name, passed in gates.items() if not passed)
    # 판정 조건에서 뺀 값들을 **관측치로 원장에 남긴다** — "전환 직후 진입 vs 안정 후 진입",
    # "미검증 시그니처 진입"을 사후 채점할 수 있어야 한다.
    observations = {
        "policy_version": policy.version,
        "stance_gate_mode": policy.stance_gate_mode,
        "signature_gate_mode": policy.signature_gate_mode,
        "stance": stance,
        "stance_direction": stance_direction,
        "flipped": stance_state.get("flipped"),
        "transitioning": stance_state.get("transitioning"),
        "validated_signature_observed": bool(validated_signature),
        "signature_ci_low_pct_observed": signature_ci_low_pct,
    }
    return EntryDecision(enter=not rejected, gates=gates, rejection_reasons=rejected, observations=observations)


def reentry_locked(
    *,
    entry_bar_at: datetime,
    direction: Direction,
    last_exit_bar_at: datetime | None,
    last_exit_direction: Direction | None,
    bar_seconds: float,
    policy: PaperPolicy,
) -> str | None:
    """직전 청산 직후 재진입을 막아야 하는가. 막으면 **사유 문자열**을 돌려준다 (C10).

    `off`(기본): 막지 않는다 — 기존 동작.
    `same_bar`: 청산한 그 확정봉 안에서는 다시 들어가지 않는다.
    `bars`: 청산 후 `reentry_lock_bars` 봉이 지나기 전에는 들어가지 않는다.

    **잠금을 넓힐수록 좋아지는 관계가 아니다.** 실측에서 4시간 간격(1봉) 재진입 3건은
    gross 우위가 +1.608R 로 양수였다 — 함께 막으면 성적이 나빠진다. 그래서 채택값은
    `same_bar` 이고, 더 긴 잠금은 표본 33건에서 잡음에 적합된 결과로 본다.
    """
    if policy.reentry_lock_mode == "off" or last_exit_bar_at is None:
        return None
    if policy.reentry_lock_same_direction_only and last_exit_direction is not None and direction != last_exit_direction:
        return None

    if policy.reentry_lock_mode == "same_bar":
        return "reentry_lock:same_bar" if entry_bar_at <= last_exit_bar_at else None

    if policy.reentry_lock_mode == "bars":
        if bar_seconds <= 0:
            return None
        elapsed_bars = (entry_bar_at - last_exit_bar_at).total_seconds() / bar_seconds
        return f"reentry_lock:{policy.reentry_lock_bars}bars" if elapsed_bars <= policy.reentry_lock_bars else None

    return None


def plan_position_size(
    *,
    entry_price: float,
    invalidation_price: float,
    policy: PaperPolicy,
) -> dict[str, Any]:
    """수량을 정하고 **그 근거를 남긴다** (WO-FCE-RISK-SIZING-01 Phase 1 · C10).

    `fixed_notional`(기본): 기존 동작. `notional = 증거금 × 레버리지`, 스톱 거리와 무관하다.
    `risk_based`: `수량 = 리스크 예산 / |진입가 − 무효화가|`.

    리스크 기준에서는 1R 의 금액가치가 스톱 거리와 무관하게 **리스크 예산 그 자체**가 된다.
    그래서 R 합계의 부호가 곧 금액 합계의 부호가 된다 — 지금은 둘이 어긋나 있다.

    **R 회계는 건드리지 않는다.** 수량이 s 배가 되면 손익도 비용도 계획 리스크도 같이 s 배가
    되므로 R = 손익 / 계획리스크 는 불변이다. 반사실 산출에서 실측으로 확인했다(불변 −2.747R).

    상한·하한에 걸리면 실제 리스크가 예산과 달라진다. 그 사실을 숨기지 않고 기록한다.
    """
    fallback_notional = policy.margin_usdt * policy.leverage
    stop_distance = abs(entry_price - invalidation_price)

    if policy.sizing_mode != "risk_based" or stop_distance <= 0.0 or entry_price <= 0.0:
        # 무효화가가 없거나 진입가와 같으면 리스크를 나눌 수 없다. 기존 경로로 되돌린다.
        reason = "mode:fixed_notional" if policy.sizing_mode != "risk_based" else "fallback:no_stop_distance"
        return {
            "mode": "fixed_notional",
            "quantity": fallback_notional / entry_price,
            "notional_usdt": fallback_notional,
            "margin_usdt": policy.margin_usdt,
            "stop_distance": stop_distance,
            "stop_distance_pct": (stop_distance / entry_price * 100.0) if entry_price > 0 else None,
            "risk_budget_usdt": None,
            "planned_risk_usdt": stop_distance * (fallback_notional / entry_price),
            "constraint": reason,
        }

    quantity = policy.risk_budget_usdt / stop_distance
    notional = quantity * entry_price
    constraint = "none"

    # 스톱 거리가 극히 작으면 수량이 발산한다. 상한이 없으면 한 건이 계좌를 지운다.
    if notional > policy.max_notional_usdt:
        quantity = policy.max_notional_usdt / entry_price
        notional = policy.max_notional_usdt
        constraint = "max_notional"
    elif notional < policy.min_notional_usdt:
        # 거래소 최소 주문 미만. 리스크가 예산을 **넘게** 되므로 그 사실을 남긴다.
        quantity = policy.min_notional_usdt / entry_price
        notional = policy.min_notional_usdt
        constraint = "min_notional"

    return {
        "mode": "risk_based",
        "quantity": quantity,
        "notional_usdt": notional,
        "margin_usdt": notional / policy.leverage if policy.leverage > 0 else notional,
        "stop_distance": stop_distance,
        "stop_distance_pct": stop_distance / entry_price * 100.0,
        "risk_budget_usdt": policy.risk_budget_usdt,
        # 상한에 걸리면 실제 리스크가 예산과 다르다. 예산이 아니라 **실제**를 적는다.
        "planned_risk_usdt": stop_distance * quantity,
        "constraint": constraint,
    }


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
    sizing = plan_position_size(
        entry_price=bar.close,
        invalidation_price=invalidation_price,
        policy=policy,
    )
    notional = sizing["notional_usdt"]
    quantity = sizing["quantity"]
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
        margin_usdt=sizing["margin_usdt"],
        leverage=policy.leverage,
        quantity=quantity,
        remaining_quantity=quantity,
        invalidation_price=invalidation_price,
        take_profit_price=take_profit_price,
        take_profit_2_price=take_profit_2_price,
        entry_atr=entry_atr,
        target_plan={**(target_plan or {}), "sizing": sizing},
        stop_price=invalidation_price,
        entry_evidence=evidence,
        checklist=checklist,
        stance_snapshot=stance_snapshot,
        signature_snapshot=signature_snapshot,
        costs_usdt=entry_cost,
        net_pnl_usdt=-entry_cost,
        net_return_pct=(-entry_cost / sizing["margin_usdt"]) * 100.0,
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
