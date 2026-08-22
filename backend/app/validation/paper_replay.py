"""WO-FCE-REPLAY-DEPTH-01 4-4 — 페이퍼 엔진 재판정 하네스.

## 왜 이것이 이 WO 의 본 목적인가

    라이브 페이퍼   4시간봉 · 하루 6회 확정 · 최대 12건/일   ← 늘릴 수 없다
    재판정          저장된 히스토리 위에서 즉시 수백 건        ← 지금까지 못 썼다

**파라미터 하나를 바꿀 때마다 4주를 기다리는 것과 즉시 재판정하는 것의 차이다.**
`RISK-SIZING-01` Phase 1~4 의 반사실은 전부 **커밋되지 않은 임시 스크립트**로 산출됐고,
그래서 Phase 3-5 의 기준선을 재현할 수 없게 됐다(오프셋 1.902R, 원인 미확정). 재판정 기반이
없으면 그 사고는 반복된다.

## 기존 하네스를 확장한다 (중복 구현 금지 · 불변 규칙 2)

- `app/validation/directional_replay.py` — **판정** 재현(워크포워드 · 히스테리시스 체이닝)
- `app/validation/risk_sizing_replay.py` — **집계**(R 정의 · PF · MDD · 손절 체결)
- `app/paper/policy.py` — 진입 게이트 9종 · 사이징 · 재진입 잠금 · 출구 사다리

이 모듈은 셋을 잇는다. 판정도 게이트도 여기서 다시 구현하지 않는다 — **호출한다.**
여기서 무언가를 새로 계산하는 순간 재판정은 라이브가 아니라 하네스 자신을 대변한다.

## 룩어헤드 (C6)

각 봉의 판정과 진입 결정은 `candles[:end]` 프리픽스만 입력받는다. 그래서 입력 구간을
잘라도 그 구간의 거래는 전체 입력 시와 **완전히 동일**해야 하며 테스트로 강제한다
(`tests/test_paper_replay.py::test_prefix_invariance_proves_no_lookahead`).

## 손절 체결 반사실 — 봉 중간 터치 vs 종가 (`RISK-SIZING-01` Phase 2 이월)

라이브 `policy._stop_breached` 는 **종가**만 본다. 봉 중간에 무효화가를 관통했다가 되돌아온
봉은 손절되지 않는다. Phase 2 는 이 반사실을 **크립토 봉 미보존**으로 포기했다 — 4-2 가
봉을 저장하면서 비로소 가능해졌다.

`stop_fill="intrabar"` 는 **하네스가** 그 판정을 대신 내린다. `paper/policy.py` 는 한 줄도
바뀌지 않는다(C3) — 반사실은 정책이 아니라 관측이다.

## 이것은 반사실이지 실적이 아니다 (C9)

산출물의 모든 페이로드에 `kind: "replay_counterfactual"` 이 붙는다. 재판정 수치를 라이브
성적으로 인용하면 그 순간 정직한 표본 규율이 무너진다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence
from uuid import NAMESPACE_URL, uuid5

from app.db.models import Direction, MarketCandle, PaperTrade
from app.exchange.bitget.trades import timeframe_seconds
from app.paper import service as paper_service
from app.paper.policy import (
    ExitDecision,
    PaperPolicy,
    apply_exit_decision,
    evaluate_entry,
    evaluate_exit,
    open_trade,
    reentry_locked,
)
from app.positions.chart_analysis import MIN_CHART_CANDLES
from app.positions.simulator import simulate_entry
from app.validation import risk_sizing_replay as rsr
from app.validation.directional_replay import _judge


REPLAY_KIND = "replay_counterfactual"

# 손절 체결 모드. `close` 가 라이브 현행이며 `intrabar` 가 Phase 2 이월 반사실이다.
STOP_FILL_MODES = ("close", "intrabar")


@dataclass(frozen=True)
class ReplayAssumptions:
    """재판정이 **재현할 수 없어 가정으로 채운** 입력. 숨기지 않고 산출물에 싣는다.

    과거 시점의 저장소 상태(시그니처 통계·실적 창)는 되돌릴 수 없다. 그것을 조용히
    "통과"로 채우면 재판정이 라이브보다 관대해지고, 그 사실이 결과 어디에도 안 남는다.
    """

    signature_gate: bool = True
    signature_gate_note: str = "과거 시점의 시그니처 통계는 복원 불가 — 통과로 가정(관측 등급 진입과 같은 취급)"
    # 체크리스트 6항목 중 둘은 **OHLCV 로 복원되지 않는다.**
    #
    #   funding  ← 과거 펀딩률 히스토리가 저장되지 않는다
    #   volume   ← `volume_xray.volume_state` 는 실체결(trade_flow) 이 있어야 판정된다
    #
    # 그래서 재판정에서 평가 가능한 항목은 최대 4개이고, `min_checklist_total=5` 는 **구조적으로
    # 통과 불가**다. 이것을 그대로 두면 재판정 진입이 0건이 되어 하네스가 아무것도 못 잰다.
    #
    # 조용히 통과시키지 않는다. 가정을 이름으로 남기고, 몇 건에 적용됐는지 센다.
    unavailable_checklist_items: tuple[str, ...] = ("funding", "volume")
    unavailable_checklist_policy: str = "count_as_pass"
    checklist_note: str = "funding·volume 은 저장되지 않은 입력이라 평가 불가 — 가정에 따라 통과로 센다"
    earnings_clear: bool | None = None
    earnings_note: str = "실적 창은 분석 페이로드에서 읽는다 — 크립토는 항상 통과"
    take_profit_pressure: str | None = None
    pressure_note: str = "익절 압력 게이지는 라이브 게이지 경로를 그대로 호출한다"
    derivative_history: str = "not_included"
    derivative_note: str = "과거 펀딩·OI·청산 히스토리를 0 이나 현재값으로 대체하지 않는다"

    def as_dict(self) -> dict[str, Any]:
        return {
            "signature_gate": self.signature_gate,
            "signature_gate_note": self.signature_gate_note,
            "unavailable_checklist_items": list(self.unavailable_checklist_items),
            "unavailable_checklist_policy": self.unavailable_checklist_policy,
            "checklist_note": self.checklist_note,
            "earnings_clear": self.earnings_clear,
            "earnings_note": self.earnings_note,
            "take_profit_pressure": self.take_profit_pressure,
            "pressure_note": self.pressure_note,
            "derivative_history": self.derivative_history,
            "derivative_note": self.derivative_note,
        }


@dataclass(frozen=True)
class ReplayResult:
    symbol: str
    timeframe: str
    stop_fill: str
    policy_label: str
    trades: list[PaperTrade] = field(default_factory=list)
    judgment_points: int = 0
    entry_blocks: dict[str, int] = field(default_factory=dict)
    assumptions: ReplayAssumptions = field(default_factory=ReplayAssumptions)

    @property
    def closed_trades(self) -> list[PaperTrade]:
        return [trade for trade in self.trades if trade.status == "closed"]


def replay_paper_engine(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[MarketCandle],
    policy: PaperPolicy,
    stop_fill: str = "close",
    min_candles: int = MIN_CHART_CANDLES,
    hysteresis_params: dict[str, Any] | None = None,
    assumptions: ReplayAssumptions | None = None,
    policy_label: str = "현행",
) -> ReplayResult:
    """저장된 히스토리 전 구간에 대해 페이퍼 엔진을 재실행한다 (4-4 작업 2).

    진입 게이트 9종 · 사이징 · 재진입 잠금 · 출구 사다리 · 손절 체결을 **현행 로직 그대로**
    돌린다. 한 심볼은 한 번에 한 포지션이며, 이는 라이브 `run_paper_engine` 의 동작
    (`open_rows` 에서 같은 타임프레임 건을 찾아 있으면 진입 평가를 건너뜀)과 같다.
    """
    if stop_fill not in STOP_FILL_MODES:
        raise ValueError(f"stop_fill must be one of {STOP_FILL_MODES}: {stop_fill}")

    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    facts = assumptions or ReplayAssumptions()
    bar_seconds = float(timeframe_seconds(timeframe))
    trades: list[PaperTrade] = []
    blocks: dict[str, int] = {}
    prior_state: dict[str, Any] | None = None
    live_trade: PaperTrade | None = None
    high_streak = 0
    last_exit_bar_at: datetime | None = None
    last_exit_direction: Direction | None = None
    judged = 0

    for end in range(max(1, int(min_candles)), len(ordered) + 1):
        prefix = list(ordered[:end])
        try:
            analysis, confluence = _judge(symbol, timeframe, prefix, prior_state, hysteresis_params)
        except ValueError:
            # 캔들 부족 등으로 분석이 성립하지 않는 시점. 없는 판정을 만들어 내지 않는다.
            continue
        state = confluence.get("stance_state") if isinstance(confluence.get("stance_state"), dict) else {}
        prior_state = dict(state) if state else prior_state
        judged += 1
        bar = prefix[-1]

        if live_trade is not None:
            decision, high_streak = _exit_decision(
                live_trade,
                bar=bar,
                analysis=analysis,
                confluence=confluence,
                timeframe=timeframe,
                policy=policy,
                prior_high_pressure_streak=high_streak,
                stop_fill=stop_fill,
            )
            live_trade = apply_exit_decision(live_trade, decision=decision, bar=bar, policy=policy)
            if decision.action == "close":
                trades.append(live_trade)
                last_exit_bar_at, last_exit_direction = live_trade.exit_bar_at, live_trade.direction
                live_trade = None
                high_streak = 0
            continue

        opened, reason = _entry_attempt(
            symbol=symbol,
            timeframe=timeframe,
            bar=bar,
            analysis=analysis,
            confluence=confluence,
            policy=policy,
            assumptions=facts,
            bar_seconds=bar_seconds,
            last_exit_bar_at=last_exit_bar_at,
            last_exit_direction=last_exit_direction,
        )
        if opened is not None:
            live_trade = opened
            high_streak = 0
        elif reason:
            blocks[reason] = blocks.get(reason, 0) + 1

    # 구간 끝에서 열려 있는 건은 **닫지 않는다.** 미실현 손익을 실현으로 적으면 표본이
    # 실제보다 좋아지거나 나빠진다 — 라이브에서도 그것은 아직 결과가 아니다.
    if live_trade is not None:
        trades.append(live_trade)

    return ReplayResult(
        symbol=symbol.upper(),
        timeframe=timeframe,
        stop_fill=stop_fill,
        policy_label=policy_label,
        trades=trades,
        judgment_points=judged,
        entry_blocks=dict(sorted(blocks.items())),
        assumptions=facts,
    )


def _exit_decision(
    trade: PaperTrade,
    *,
    bar: MarketCandle,
    analysis: dict[str, Any],
    confluence: dict[str, Any],
    timeframe: str,
    policy: PaperPolicy,
    prior_high_pressure_streak: int,
    stop_fill: str,
) -> tuple[ExitDecision, int]:
    """출구 판정. `intrabar` 만 하네스가 대신 내린다 (C3 — 정책 파일 diff 0줄).

    봉 중간 터치가 종가 판정보다 **먼저** 일어난다. 그래서 터치가 있으면 나머지 출구 사다리를
    보지 않고 즉시 손절로 확정한다 — 같은 봉 안에서 손절과 익절이 모두 닿았을 때 어느 쪽이
    먼저였는지는 봉 데이터로 알 수 없고, 리스크 관측에서는 나쁜 쪽을 가정하는 것이 정직하다.
    """
    if stop_fill == "intrabar" and _stop_touched_intrabar(trade, bar):
        reason = "breakeven_stop" if trade.partial_exit_at else "invalidation_breach"
        return ExitDecision("close", reason, 0, trade.stop_price), 0

    gauges = paper_service.build_gauges(
        analysis=analysis,
        confluence=confluence,
        historical_backtest={},
        position={"direction": trade.direction.value},
        now=_judged_at(bar, timeframe),
        timeframe=timeframe,
    )
    pressure = paper_service._normalize_pressure(paper_service._dict(gauges.get("take_profit")).get("level"))
    decision = evaluate_exit(
        trade,
        bar=bar,
        stance_state=paper_service._stance_state(confluence),
        take_profit_pressure=pressure,
        prior_high_pressure_streak=prior_high_pressure_streak,
        policy=policy,
    )
    return decision, decision.high_pressure_streak


def _stop_touched_intrabar(trade: PaperTrade, bar: MarketCandle) -> bool:
    if trade.direction == Direction.long:
        return bar.low <= trade.stop_price
    return bar.high >= trade.stop_price


def _entry_attempt(
    *,
    symbol: str,
    timeframe: str,
    bar: MarketCandle,
    analysis: dict[str, Any],
    confluence: dict[str, Any],
    policy: PaperPolicy,
    assumptions: ReplayAssumptions,
    bar_seconds: float,
    last_exit_bar_at: datetime | None,
    last_exit_direction: Direction | None,
) -> tuple[PaperTrade | None, str | None]:
    """진입 게이트 9종 · 재진입 잠금 · 사이징을 **라이브와 같은 순서로** 통과시킨다."""
    direction = paper_service._stance_direction(confluence)
    if direction is None:
        return None, "no_direction"

    simulation = simulate_entry(
        symbol=symbol.upper(),
        direction=direction.value,
        entry_price=bar.close,
        leverage=policy.leverage,
        margin_usdt=policy.margin_usdt,
        margin_mode="cross",
        chart_analysis=analysis,
        mmr=None,
        direction_score=None,
    )
    action_plan = paper_service._dict(simulation.get("action_plan"))
    invalidation = paper_service._price_from(action_plan.get("invalidation") or action_plan.get("engine_invalidation"))
    target_plan = paper_service._paper_target_plan(
        analysis,
        {},
        bar=bar,
        direction=direction,
        invalidation_price=invalidation,
        action_plan=action_plan,
        policy=policy,
    )
    execution_invalidation = paper_service._float(target_plan.get("execution_invalidation"))
    take_profit = paper_service._float(target_plan.get("take_profit_1"))
    if execution_invalidation is None or take_profit is None:
        return None, "no_execution_plan"
    contract = paper_service._paper_simulation_contract(simulation, target_plan)
    evidence = paper_service._direction_evidence(confluence, direction)
    earnings_clear = assumptions.earnings_clear if assumptions.earnings_clear is not None else paper_service._earnings_clear(analysis)
    checklist_passed, checklist_total, assumed = _checklist_counts(contract, assumptions)

    decision = evaluate_entry(
        stance_state=paper_service._stance_state(confluence),
        direction=direction,
        evidence_count=len(evidence),
        checklist_passed=checklist_passed,
        checklist_total=checklist_total,
        rr_ratio=paper_service._float(target_plan.get("rr_ratio")),
        invalidation_hygiene=contract.get("invalidation_too_close") is not True and target_plan.get("execution_invalidation_too_close") is not True,
        survives_to_invalidation=contract.get("survives_to_invalidation") is True,
        validated_signature=assumptions.signature_gate,
        # 시그니처 통계 CI 하한도 복원 불가다. 게이트를 통과시키기로 가정한 이상 임계도
        # 통과값으로 둔다 — 여기서 None 을 주면 가정이 반쪽만 적용돼 결과가 뒤틀린다.
        signature_ci_low_pct=policy.min_signature_ci_low_pct if assumptions.signature_gate else None,
        earnings_clear=earnings_clear,
        data_fresh=True,
        confirmed_bar=True,
        policy=policy,
    )
    if not decision.enter:
        return None, f"gate:{decision.rejection_reasons[0]}"

    lock = reentry_locked(
        entry_bar_at=bar.timestamp,
        direction=direction,
        last_exit_bar_at=last_exit_bar_at,
        last_exit_direction=last_exit_direction,
        bar_seconds=bar_seconds,
        policy=policy,
    )
    if lock is not None:
        return None, lock

    trade = open_trade(
        trade_id=uuid5(NAMESPACE_URL, f"replay:{symbol.upper()}:{timeframe}:{bar.timestamp.isoformat()}"),
        symbol=symbol,
        timeframe=timeframe,
        asset_class=str(analysis.get("asset_class") or "unknown"),
        direction=direction,
        bar=bar,
        invalidation_price=execution_invalidation,
        take_profit_price=take_profit,
        evidence={"items": evidence, "count": len(evidence)},
        checklist={
            "passed": checklist_passed,
            "total": checklist_total,
            "evaluated_passed": contract.get("checklist_passed"),
            "evaluated_total": contract.get("checklist_total"),
            # 가정으로 채운 항목을 건별로 남긴다 — 합계만 보면 재판정이 라이브보다 관대했다는
            # 사실이 사라진다.
            "assumed_items": assumed,
        },
        stance_snapshot=paper_service._stance_state(confluence),
        signature_snapshot={"assumed": assumptions.signature_gate, "kind": REPLAY_KIND},
        policy=policy,
        take_profit_2_price=paper_service._float(target_plan.get("take_profit_2")),
        entry_atr=paper_service._float(target_plan.get("atr")),
        target_plan=target_plan,
    )
    return trade, None


def _checklist_counts(contract: dict[str, Any], assumptions: ReplayAssumptions) -> tuple[int, int, list[str]]:
    """평가 불가 체크리스트 항목을 가정으로 채운다 (기본: 통과로 센다).

    `block` 을 고르면 라이브 게이트를 그대로 두는 대신 재판정 진입이 **구조적으로 0건**이
    된다. 둘 중 무엇을 골랐는지가 결과 해석을 완전히 바꾸므로 산출물에 항상 싣는다.
    """
    passed = int(contract.get("checklist_passed") or 0)
    total = int(contract.get("checklist_total") or 0)
    if assumptions.unavailable_checklist_policy != "count_as_pass":
        return passed, total, []
    statuses = {str(item.get("key")): str(item.get("status")) for item in paper_service._list(contract.get("checklist")) if isinstance(item, dict)}
    assumed = [key for key in assumptions.unavailable_checklist_items if statuses.get(key) == "na"]
    return passed + len(assumed), total + len(assumed), assumed


def _judged_at(bar: MarketCandle, timeframe: str) -> datetime:
    """판정 시각은 그 봉이 마감된 직후. 벽시계를 쓰면 과거 재판정이 전부 stale 로 눌린다."""
    stamp = bar.timestamp if bar.timestamp.tzinfo is not None else bar.timestamp.replace(tzinfo=timezone.utc)
    return stamp + timedelta(seconds=timeframe_seconds(timeframe), milliseconds=1)


# ── 집계 ────────────────────────────────────────────────────────────────
# 지표 정의를 여기서 다시 만들지 않는다 — `risk_sizing_replay` 가 정본이다.


def as_replay_trades(trades: Iterable[PaperTrade]) -> list[rsr.ReplayTrade]:
    """`risk_sizing_replay` 의 집계기가 먹을 수 있는 형태로 옮긴다 (재구현 금지)."""
    rows: list[rsr.ReplayTrade] = []
    for trade in trades:
        if trade.status != "closed" or trade.exit_price is None:
            continue
        rows.append(
            rsr.ReplayTrade(
                trade_id=str(trade.id),
                symbol=trade.symbol,
                timeframe=trade.timeframe,
                direction=trade.direction.value,
                entry_bar_at=trade.entry_bar_at,
                exit_bar_at=trade.exit_bar_at,
                entry_price=trade.entry_price,
                invalidation_price=trade.invalidation_price,
                exit_price=trade.exit_price,
                exit_reason=trade.exit_reason,
                quantity=trade.quantity,
                gross_pnl_usdt=trade.gross_pnl_usdt,
                costs_usdt=trade.costs_usdt,
                net_pnl_usdt=trade.net_pnl_usdt,
            )
        )
    return rows


def replay_metrics(result: ReplayResult) -> dict[str, Any]:
    rows = as_replay_trades(result.trades)
    summary = rsr.metrics(rows)
    return {
        "kind": REPLAY_KIND,
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "stop_fill": result.stop_fill,
        "policy": result.policy_label,
        "judgment_points": result.judgment_points,
        "trades_opened": len(result.trades),
        "trades_closed": len(rows),
        "entry_blocks": result.entry_blocks,
        "sample_size": summary.sample_size,
        "gross_r": round(summary.gross_r, 4),
        "cost_r": round(summary.cost_r, 4),
        "net_r": round(summary.net_r, 4),
        "profit_factor": None if summary.profit_factor is None else round(summary.profit_factor, 4),
        "mdd_usdt": round(summary.mdd_usdt, 4),
        "mean_stop_distance_pct": round(summary.mean_stop_distance_pct, 4),
        "assumptions": result.assumptions.as_dict(),
        "disclaimer": "재판정 반사실이다 — 라이브 실적이 아니다(C9).",
    }


def stop_execution_counterfactual(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[MarketCandle],
    policy: PaperPolicy,
    min_candles: int = MIN_CHART_CANDLES,
    hysteresis_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """봉 중간 터치 vs 종가 (`RISK-SIZING-01` Phase 2 이월 종결 · 4-4 작업 2).

    두 모드는 **같은 캔들·같은 정책**으로 돌린다. 차이는 손절이 언제 체결되는가 하나뿐이며,
    그래서 결과 차이는 전부 그 하나에 귀속된다(교란 없음).
    """
    close_result = replay_paper_engine(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        policy=policy,
        stop_fill="close",
        min_candles=min_candles,
        hysteresis_params=hysteresis_params,
        policy_label="현행(종가 손절)",
    )
    intrabar_result = replay_paper_engine(
        symbol=symbol,
        timeframe=timeframe,
        candles=candles,
        policy=policy,
        stop_fill="intrabar",
        min_candles=min_candles,
        hysteresis_params=hysteresis_params,
        policy_label="반사실(봉 중간 터치)",
    )
    close_metrics = replay_metrics(close_result)
    intrabar_metrics = replay_metrics(intrabar_result)
    close_stops = rsr.stop_executions(as_replay_trades(close_result.trades))
    intrabar_stops = rsr.stop_executions(as_replay_trades(intrabar_result.trades))
    return {
        "kind": REPLAY_KIND,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "close": close_metrics,
        "intrabar": intrabar_metrics,
        "delta": {
            "sample_size": intrabar_metrics["sample_size"] - close_metrics["sample_size"],
            "gross_r": round(intrabar_metrics["gross_r"] - close_metrics["gross_r"], 4),
            "net_r": round(intrabar_metrics["net_r"] - close_metrics["net_r"], 4),
            "mdd_usdt": round(intrabar_metrics["mdd_usdt"] - close_metrics["mdd_usdt"], 4),
        },
        "stop_counts": {"close": len(close_stops), "intrabar": len(intrabar_stops)},
        "mean_execution_risk_r": {
            "close": _mean(item.execution_risk_r for item in close_stops),
            "intrabar": _mean(item.execution_risk_r for item in intrabar_stops),
        },
        "note": ("라이브 `policy._stop_breached` 는 종가만 본다. `intrabar` 은 하네스가 내리는 반사실 판정이며 `paper/policy.py` 는 변경되지 않았다(C3)."),
        "disclaimer": "재판정 반사실이다 — 라이브 실적이 아니다(C9).",
    }


# ── 파라미터 스윕 ────────────────────────────────────────────────────────
# "엔진 고도화"의 실행 수단이다. 스윕은 **후보를 고르는 도구**이지 채택 근거가 아니다.


def policy_variants(base: PaperPolicy, overrides: Sequence[tuple[str, dict[str, Any]]]) -> list[tuple[str, PaperPolicy]]:
    """라벨 + 덮어쓸 필드로 정책 변형을 만든다.

    **한 번에 한 축만 움직이는 행을 반드시 포함하라**(AGENTS.md "승률 개선안은 한 번에 하나씩").
    여러 축을 동시에 움직인 행만 보면 무엇이 효과였는지 영원히 알 수 없다.
    """
    return [(label, replace(base, **fields)) for label, fields in overrides]


def sweep(
    *,
    symbol: str,
    timeframe: str,
    candles: Sequence[MarketCandle],
    variants: Sequence[tuple[str, PaperPolicy]],
    stop_fill: str = "close",
    min_candles: int = MIN_CHART_CANDLES,
    hysteresis_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """파라미터를 바꿔가며 일괄 재판정한다 (4-4 작업 5)."""
    rows = []
    for label, policy in variants:
        result = replay_paper_engine(
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            policy=policy,
            stop_fill=stop_fill,
            min_candles=min_candles,
            hysteresis_params=hysteresis_params,
            policy_label=label,
        )
        rows.append(replay_metrics(result))
    return {
        "kind": REPLAY_KIND,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "stop_fill": stop_fill,
        "rows": rows,
        "overfit_warning": ("스윕은 과거 데이터다. 여기서 고른 설정은 전방 라이브로 확인해야 하며, 그 확인 규칙은 **결과를 보기 전에** 정해야 한다."),
        "disclaimer": "재판정 반사실이다 — 라이브 실적이 아니다(C9).",
    }


def trades_digest(trades: Iterable[PaperTrade]) -> str:
    """거래 목록의 지문. 재판정이 조용히 바뀌지 않았음을 증명하는 데 쓴다."""
    payload = [
        {
            "symbol": trade.symbol,
            "direction": trade.direction.value,
            "entry_bar_at": trade.entry_bar_at.isoformat(),
            "entry_price": round(trade.entry_price, 8),
            "exit_bar_at": trade.exit_bar_at.isoformat() if trade.exit_bar_at else None,
            "exit_price": None if trade.exit_price is None else round(trade.exit_price, 8),
            "exit_reason": trade.exit_reason,
            "net_pnl_usdt": round(trade.net_pnl_usdt, 8),
        }
        for trade in trades
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return round(sum(items) / len(items), 4) if items else None
