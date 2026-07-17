from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.analyst.gauges import build_gauges
from app.analyst.signature_registry import current_state
from app.backtest.candidate_scoring import CANDIDATE_ENGINES
from app.backtest.outcomes import atr
from app.backtest.statistics import bootstrap_ci_from_counts
from app.backtest.signatures import SetupSignature, signature_key, signatures_from_analysis
from app.db.models import (
    Direction,
    JudgmentLedgerEntry,
    JudgmentScore,
    MarketCandle,
    PaperTrade,
    PositionStatus,
    utc_now,
)
from app.exchange.bitget.trades import timeframe_seconds
from app.paper.policy import (
    PaperPolicy,
    apply_exit_decision,
    evaluate_entry,
    evaluate_exit,
    open_trade,
)
from app.paper.user_fills import (
    USER_FILL_SYNC_SYMBOL,
    USER_FILL_SYNC_TIMEFRAME,
    sync_user_fills as _sync_user_fills,
)


AnalysisLoader = Callable[[str, str], dict[str, Any]]
SimulationLoader = Callable[[str, str, str, float], dict[str, Any]]

VALIDATION_BOOTSTRAP_MAX_POSITIONS = 2
VALIDATION_BOOTSTRAP_MIN_EVIDENCE = 3
VALIDATION_BOOTSTRAP_MIN_CHECKLIST_PASSED = 3
VALIDATION_BOOTSTRAP_MIN_RR = 1.0
ENTRY_GATE_VERSION = "pooled-signature-v1"


def policy_from_settings(settings: Any, asset_class: str = "crypto") -> PaperPolicy:
    slippage = {
        "stock": float(settings.backtest_slippage_stock_pct),
        "index": float(settings.backtest_slippage_index_pct),
    }.get(asset_class, float(settings.backtest_slippage_crypto_pct))
    return PaperPolicy(
        margin_usdt=float(settings.paper_margin_usdt),
        leverage=float(settings.paper_leverage),
        max_open_positions=int(settings.paper_max_open_positions),
        min_evidence=int(settings.paper_min_evidence),
        min_checklist_passed=int(settings.paper_min_checklist_passed),
        min_rr=float(settings.paper_min_rr),
        min_signature_ci_low_pct=float(settings.universe_backtest_min_ci_low_pct),
        max_holding_bars=int(settings.paper_max_holding_bars),
        take_profit_atr_k1=float(getattr(settings, "paper_take_profit_atr_k1", 1.0)),
        take_profit_atr_k2=float(getattr(settings, "paper_take_profit_atr_k2", 2.0)),
        taker_fee_pct=float(settings.backtest_taker_fee_pct),
        slippage_pct=slippage,
    )


def paper_universe(repo: Any) -> list[tuple[str, str]]:
    pairs: set[tuple[str, str]] = {(item.symbol.upper(), item.default_timeframe or "4h") for item in repo.list_watchlist()}
    pairs.update((position.symbol.upper(), "4h") for position in repo.list_positions(PositionStatus.open))
    pairs.update((trade.symbol.upper(), trade.timeframe) for trade in repo.list_paper_trades(status="open"))
    pairs.update((intent.symbol.upper(), intent.timeframe or "4h") for intent in repo.list_entry_intents(status="active", limit=1000))
    for discovery in repo.list_universe_discoveries(limit=500):
        if discovery.gate_passed:
            pairs.add((discovery.symbol.upper(), discovery.timeframe or "4h"))
    return sorted(pairs)


def run_paper_engine(
    repo: Any,
    settings: Any,
    *,
    analysis_loader: AnalysisLoader,
    simulation_loader: SimulationLoader,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not bool(settings.paper_engine_enabled):
        return {"enabled": False, "evaluated": 0, "opened": 0, "partial": 0, "closed": 0, "errors": []}
    now = now or utc_now()
    suppressed_duplicates = _suppress_duplicate_bootstrap_trades(repo, now=now)
    opened = partial = closed = evaluated = 0
    skipped_same_bar = 0
    errors: list[dict[str, str]] = []
    events: list[dict[str, Any]] = []

    for symbol, timeframe in paper_universe(repo):
        try:
            payload = analysis_loader(symbol, timeframe)
            analysis = _dict(payload.get("analysis"))
            briefing = _dict(payload.get("analyst_briefing"))
            confluence = _dict(briefing.get("confluence"))
            gauges = _dict(payload.get("gauges"))
            bar = _confirmed_bar(analysis, gauges)
            if bar is None:
                continue
            state = repo.get_paper_engine_state(symbol, timeframe) or {}
            bar_key = bar.timestamp.isoformat()
            prior_funnel = next(
                (
                    row
                    for row in repo.list_paper_gate_funnel(symbol=symbol, limit=10)
                    if str(row.get("timeframe") or "4h") == timeframe and str(row.get("bar_at") or "") == bar_key
                ),
                None,
            )
            gate_upgrade_pending = bool(
                prior_funnel and prior_funnel.get("rejected_at") == "signature_gate" and state.get("entry_gate_version") != ENTRY_GATE_VERSION
            )
            if state.get("last_bar_at") == bar_key and prior_funnel is not None and not gate_upgrade_pending:
                skipped_same_bar += 1
                continue
            evaluated += 1

            open_rows = repo.list_paper_trades(status="open", symbol=symbol, limit=5)
            open_trade_row = next((item for item in open_rows if item.timeframe == timeframe), None)
            high_streak = int(state.get("take_profit_pressure_high_streak") or 0)
            if open_trade_row is not None:
                position_gauges = build_gauges(
                    analysis=analysis,
                    confluence=confluence,
                    historical_backtest=_dict(payload.get("historical_backtest")),
                    position={"direction": open_trade_row.direction.value},
                    now=now,
                    timeframe=timeframe,
                )
                pressure = _normalize_pressure(_dict(position_gauges.get("take_profit")).get("level"))
                exit_decision = evaluate_exit(
                    open_trade_row,
                    bar=bar,
                    stance_state=_stance_state(confluence),
                    take_profit_pressure=pressure,
                    prior_high_pressure_streak=high_streak,
                    policy=policy_from_settings(settings, open_trade_row.asset_class),
                )
                updated = apply_exit_decision(
                    open_trade_row,
                    decision=exit_decision,
                    bar=bar,
                    policy=policy_from_settings(settings, open_trade_row.asset_class),
                )
                if exit_decision.action == "partial":
                    partial += 1
                    events.append(_paper_event("partial", updated, reason=exit_decision.reason))
                elif exit_decision.action == "close":
                    closed += 1
                    updated = _finalize_closed_trade(repo, updated, analysis)
                    events.append(_paper_event("closed", updated))
                repo.upsert_paper_trade(updated)
                high_streak = exit_decision.high_pressure_streak

            if open_trade_row is None:
                direction = _stance_direction(confluence)
                simulation: dict[str, Any] = {}
                qualified: dict[str, Any] | None = None
                signature_gates = {"signature_gate": False, "regime_gate": False}
                action_plan: dict[str, Any] = {}
                invalidation = take_profit = None
                target_plan: dict[str, Any] = {}
                evidence: list[dict[str, Any]] = []
                entry_decision = None
                if direction is not None:
                    simulation = simulation_loader(symbol, timeframe, direction.value, bar.close)
                    signature_gates = _signature_gate_evaluation(repo, settings, analysis, payload, direction, now=now)
                    qualified = _dict(signature_gates.get("qualified")) or None
                    action_plan = _dict(simulation.get("action_plan"))
                    invalidation = _price_from(action_plan.get("invalidation") or action_plan.get("engine_invalidation"))
                    target_plan = _paper_target_plan(
                        analysis,
                        gauges,
                        bar=bar,
                        direction=direction,
                        invalidation_price=invalidation,
                        action_plan=action_plan,
                        policy=policy_from_settings(settings, str(analysis.get("asset_class") or "unknown")),
                    )
                    invalidation = _float(target_plan.get("execution_invalidation"))
                    take_profit = _float(target_plan.get("take_profit_1"))
                    evidence = _direction_evidence(confluence, direction)
                    simulation = _paper_simulation_contract(simulation, target_plan)
                    entry_decision = evaluate_entry(
                        stance_state=_stance_state(confluence),
                        direction=direction,
                        evidence_count=len(evidence),
                        checklist_passed=int(simulation.get("checklist_passed") or 0),
                        checklist_total=int(simulation.get("checklist_total") or 0),
                        rr_ratio=_float(target_plan.get("rr_ratio")),
                        invalidation_hygiene=simulation.get("invalidation_too_close") is not True
                        and target_plan.get("execution_invalidation_too_close") is not True,
                        survives_to_invalidation=simulation.get("survives_to_invalidation") is True,
                        validated_signature=bool(signature_gates["signature_gate"]),
                        signature_ci_low_pct=(float(settings.universe_backtest_min_ci_low_pct) if signature_gates["regime_gate"] else None),
                        earnings_clear=_earnings_clear(analysis),
                        data_fresh=_data_fresh(bar, timeframe, now),
                        confirmed_bar=True,
                        policy=policy_from_settings(settings, str(analysis.get("asset_class") or "unknown")),
                    )
                capacity_available = len(repo.list_paper_trades(status="open", limit=100)) < int(settings.paper_max_open_positions)
                will_enter = bool(
                    capacity_available and entry_decision is not None and entry_decision.enter and invalidation is not None and take_profit is not None
                )
                gate_record = _gate_funnel_record(
                    symbol=symbol,
                    timeframe=timeframe,
                    bar=bar,
                    now=now,
                    direction=direction,
                    evidence_count=len(evidence),
                    simulation=simulation,
                    signature_gates=signature_gates,
                    action_levels=bool(invalidation is not None and take_profit is not None),
                    capacity_available=capacity_available,
                    rr_ratio=_float(target_plan.get("rr_ratio")),
                    earnings_clear=_earnings_clear(analysis),
                    freshness=_data_fresh(bar, timeframe, now),
                    entry_decision=entry_decision,
                    entered=will_enter,
                    pill_diagnostics=_dict(gauges.get("pill_diagnostics") or gauges.get("event_pill_audit")),
                    event_pill_ids=[str(item.get("id")) for item in _list(gauges.get("event_pills")) if item.get("id")],
                )
                repo.upsert_paper_gate_funnel(gate_record)
                _record_entry_block_logs(repo, gate_record, now=now)
                if (
                    will_enter
                    and direction is not None
                    and entry_decision is not None
                    and entry_decision.enter
                    and invalidation is not None
                    and take_profit is not None
                ):
                    paper_trade = open_trade(
                        trade_id=uuid4(),
                        symbol=symbol,
                        timeframe=timeframe,
                        asset_class=str(analysis.get("asset_class") or "unknown"),
                        direction=direction,
                        bar=bar,
                        invalidation_price=invalidation,
                        take_profit_price=take_profit,
                        evidence={"items": evidence, "gates": entry_decision.gates},
                        checklist={
                            "items": simulation.get("checklist") or [],
                            "passed": simulation.get("checklist_passed"),
                            "total": simulation.get("checklist_total"),
                            "rr_ratio": target_plan.get("rr_ratio"),
                            "simulation_rr_ratio": simulation.get("rr_ratio"),
                            "survives_to_invalidation": simulation.get("survives_to_invalidation"),
                        },
                        stance_snapshot=_stance_state(confluence),
                        signature_snapshot=qualified or {},
                        policy=policy_from_settings(settings, str(analysis.get("asset_class") or "unknown")),
                        take_profit_2_price=_float(target_plan.get("take_profit_2")),
                        entry_atr=_float(target_plan.get("atr")),
                        target_plan=target_plan,
                    )
                    paper_trade = paper_trade.model_copy(
                        update={
                            "entry_evidence": {
                                **paper_trade.entry_evidence,
                                "signature_gate_mode": signature_gates.get("gate_mode"),
                                "candidate_bootstrap": str(signature_gates.get("gate_mode") or "").startswith("candidate_bootstrap"),
                                "bootstrap_relaxed": signature_gates.get("gate_mode") == "candidate_bootstrap_relaxed",
                                "exit_policy": "ATR TP1 부분익절 · TP2 전량익절 · 시간 만료 시 stance 재검토",
                            }
                        }
                    )
                    repo.upsert_paper_trade(paper_trade)
                    _record_entry_judgment(repo, paper_trade)
                    opened += 1
                    events.append(_paper_event("opened", paper_trade))

            repo.upsert_paper_engine_state(
                symbol,
                timeframe,
                {
                    "last_bar_at": bar_key,
                    "entry_gate_version": ENTRY_GATE_VERSION,
                    "last_price": bar.close,
                    "stance_state": _stance_state(confluence),
                    "take_profit_pressure_high_streak": high_streak,
                    "updated_at": now.isoformat(),
                },
            )
        except Exception as exc:  # each symbol is isolated; worker continues
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})

    bootstrap = _bootstrap_validation_positions(
        repo,
        settings,
        analysis_loader=analysis_loader,
        simulation_loader=simulation_loader,
        now=now,
    )
    opened += int(bootstrap.get("opened") or 0)
    events.extend(bootstrap.get("events") or [])
    errors.extend(bootstrap.get("errors") or [])

    diagnostic = _gate_diagnostic_event(repo, now=now)
    if diagnostic is not None:
        events.append(diagnostic)
    return {
        "enabled": True,
        "evaluated": evaluated,
        "opened": opened,
        "partial": partial,
        "closed": closed,
        "skipped_same_bar": skipped_same_bar,
        "open_count": len(repo.list_paper_trades(status="open", limit=100)),
        "suppressed_duplicates": suppressed_duplicates,
        "events": events,
        "errors": errors,
    }


def _bootstrap_validation_positions(
    repo: Any,
    settings: Any,
    *,
    analysis_loader: AnalysisLoader,
    simulation_loader: SimulationLoader,
    now: datetime,
) -> dict[str, Any]:
    """Seed the 4-week paper benchmark once without weakening normal flip entries."""
    benchmark = paper_benchmark(repo)
    started_at = _parse_datetime(benchmark.get("started_at"))
    if started_at is None:
        return {"opened": 0, "events": [], "errors": []}
    all_trades = repo.list_paper_trades(limit=5000)
    current_seeds = [trade for trade in all_trades if _is_current_benchmark_seed(trade, started_at)]
    for trade in current_seeds:
        if trade.entry_at >= started_at and _dict(trade.entry_evidence).get("benchmark_started_at"):
            continue
        repo.upsert_paper_trade(
            trade.model_copy(
                update={
                    "entry_at": max(trade.entry_at, started_at),
                    "created_at": max(trade.created_at, started_at),
                    "updated_at": max(trade.updated_at, started_at),
                    "entry_evidence": {
                        **trade.entry_evidence,
                        "benchmark_started_at": started_at.isoformat(),
                    },
                }
            )
        )
    benchmark_trades = [trade for trade in all_trades if trade.entry_at >= started_at or trade in current_seeds]
    if benchmark_trades:
        return {"opened": 0, "events": [], "errors": []}

    capacity = max(0, int(settings.paper_max_open_positions) - len(repo.list_paper_trades(status="open", limit=100)))
    target = min(capacity, VALIDATION_BOOTSTRAP_MAX_POSITIONS)
    if target <= 0:
        return {"opened": 0, "events": [], "errors": []}

    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for symbol, timeframe in paper_universe(repo):
        try:
            if any(trade.timeframe == timeframe for trade in repo.list_paper_trades(status="open", symbol=symbol, limit=10)):
                continue
            payload = analysis_loader(symbol, timeframe)
            analysis = _dict(payload.get("analysis"))
            confluence = _dict(_dict(payload.get("analyst_briefing")).get("confluence"))
            gauges = _dict(payload.get("gauges"))
            bar = _confirmed_bar(analysis, gauges)
            direction = _stance_direction(confluence)
            stance_state = _stance_state(confluence)
            if bar is None or direction is None or stance_state.get("transitioning") is True:
                continue

            evidence = _direction_evidence(confluence, direction)
            simulation = simulation_loader(symbol, timeframe, direction.value, bar.close)
            action_plan = _dict(simulation.get("action_plan"))
            invalidation = _price_from(action_plan.get("invalidation") or action_plan.get("engine_invalidation"))
            target_plan = _paper_target_plan(
                analysis,
                gauges,
                bar=bar,
                direction=direction,
                invalidation_price=invalidation,
                action_plan=action_plan,
                policy=policy_from_settings(settings, str(analysis.get("asset_class") or "unknown")),
            )
            invalidation = _float(target_plan.get("execution_invalidation"))
            take_profit = _float(target_plan.get("take_profit_1"))
            simulation = _paper_simulation_contract(simulation, target_plan)
            checklist_passed = int(simulation.get("checklist_passed") or 0)
            checklist_total = int(simulation.get("checklist_total") or 0)
            rr_ratio = _float(target_plan.get("rr_ratio"))
            signature_gates = _signature_gate_evaluation(repo, settings, analysis, payload, direction, now=now)
            gates = {
                "confirmed_stance": True,
                "not_transitioning": True,
                "evidence": len(evidence) >= VALIDATION_BOOTSTRAP_MIN_EVIDENCE,
                "checklist": checklist_total > 0 and checklist_passed >= VALIDATION_BOOTSTRAP_MIN_CHECKLIST_PASSED,
                "invalidation_hygiene": simulation.get("invalidation_too_close") is not True
                and target_plan.get("execution_invalidation_too_close") is not True,
                "risk_reward": rr_ratio is not None and rr_ratio >= VALIDATION_BOOTSTRAP_MIN_RR,
                "liquidation_safety": simulation.get("survives_to_invalidation") is True,
                "action_levels": invalidation is not None and take_profit is not None,
                "event_window": _earnings_clear(analysis),
                "freshness": _data_fresh(bar, timeframe, now),
                "signature_gate": bool(signature_gates.get("signature_gate")),
                "regime_gate": bool(signature_gates.get("regime_gate")),
            }
            if not all(gates.values()) or invalidation is None or take_profit is None or rr_ratio is None:
                continue
            candidates.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "analysis": analysis,
                    "bar": bar,
                    "direction": direction,
                    "evidence": evidence,
                    "simulation": simulation,
                    "invalidation": invalidation,
                    "take_profit": take_profit,
                    "target_plan": target_plan,
                    "stance_state": stance_state,
                    "signature_gates": signature_gates,
                    "gates": gates,
                    "rank": (checklist_passed / checklist_total, rr_ratio, len(evidence)),
                }
            )
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})

    opened: list[PaperTrade] = []
    for candidate in sorted(candidates, key=lambda item: item["rank"], reverse=True)[:target]:
        simulation = candidate["simulation"]
        signature_gates = candidate["signature_gates"]
        trade = open_trade(
            trade_id=uuid5(
                NAMESPACE_URL,
                f"fce:paper:validation-bootstrap:{started_at.isoformat()}:{candidate['symbol']}:{candidate['timeframe']}",
            ),
            symbol=candidate["symbol"],
            timeframe=candidate["timeframe"],
            asset_class=str(candidate["analysis"].get("asset_class") or "unknown"),
            direction=candidate["direction"],
            bar=candidate["bar"],
            invalidation_price=candidate["invalidation"],
            take_profit_price=candidate["take_profit"],
            evidence={
                "entry_mode": "validation_bootstrap",
                "benchmark_started_at": started_at.isoformat(),
                "items": candidate["evidence"],
                "gates": candidate["gates"],
                "signature_gate_mode": signature_gates.get("gate_mode"),
                "candidate_bootstrap": str(signature_gates.get("gate_mode") or "").startswith("candidate_bootstrap"),
                "bootstrap_relaxed": signature_gates.get("gate_mode") == "candidate_bootstrap_relaxed",
                "note": "4주 대결 최초 표본 수집용 · 성적 시그니처 게이트 통과",
                "exit_policy": "ATR TP1 부분익절 · TP2 전량익절 · 시간 만료 시 stance 재검토",
            },
            checklist={
                "entry_mode": "validation_bootstrap",
                "items": simulation.get("checklist") or [],
                "passed": simulation.get("checklist_passed"),
                "total": simulation.get("checklist_total"),
                "rr_ratio": candidate["target_plan"].get("rr_ratio"),
                "simulation_rr_ratio": simulation.get("rr_ratio"),
                "survives_to_invalidation": simulation.get("survives_to_invalidation"),
            },
            stance_snapshot=candidate["stance_state"],
            signature_snapshot={
                "entry_mode": "validation_bootstrap",
                "signature_gate": bool(signature_gates.get("signature_gate")),
                "regime_gate": bool(signature_gates.get("regime_gate")),
                "gate_mode": signature_gates.get("gate_mode"),
                "bootstrap_relaxed": signature_gates.get("gate_mode") == "candidate_bootstrap_relaxed",
                "qualified": signature_gates.get("qualified"),
            },
            policy=policy_from_settings(settings, str(candidate["analysis"].get("asset_class") or "unknown")),
            take_profit_2_price=_float(candidate["target_plan"].get("take_profit_2")),
            entry_atr=_float(candidate["target_plan"].get("atr")),
            target_plan=candidate["target_plan"],
        )
        trade = trade.model_copy(update={"entry_at": now, "created_at": now, "updated_at": now})
        repo.upsert_paper_trade(trade)
        _record_entry_judgment(repo, trade)
        opened.append(trade)

    return {
        "opened": len(opened),
        "events": [_paper_event("opened", trade) for trade in opened],
        "errors": errors,
    }


def _is_current_benchmark_seed(trade: PaperTrade, started_at: datetime) -> bool:
    expected_id = uuid5(
        NAMESPACE_URL,
        f"fce:paper:validation-bootstrap:{started_at.isoformat()}:{trade.symbol}:{trade.timeframe}",
    )
    return trade.id == expected_id


def _suppress_duplicate_bootstrap_trades(repo: Any, *, now: datetime) -> int:
    groups: dict[tuple[str, str, datetime], list[PaperTrade]] = {}
    for trade in repo.list_paper_trades(status="open", limit=5000):
        if _dict(trade.entry_evidence).get("entry_mode") != "validation_bootstrap":
            continue
        groups.setdefault((trade.symbol, trade.timeframe, trade.entry_bar_at), []).append(trade)

    suppressed = 0
    for trades in groups.values():
        ordered = sorted(trades, key=lambda trade: (trade.created_at, str(trade.id)))
        for duplicate in ordered[1:]:
            repo.upsert_paper_trade(
                duplicate.model_copy(
                    update={
                        "status": "closed",
                        "remaining_quantity": 0.0,
                        "exit_bar_at": duplicate.entry_bar_at,
                        "exit_at": duplicate.entry_at,
                        "exit_price": duplicate.entry_price,
                        "exit_reason": "duplicate_bootstrap_suppressed",
                        "gross_pnl_usdt": 0.0,
                        "costs_usdt": 0.0,
                        "net_pnl_usdt": 0.0,
                        "net_return_pct": 0.0,
                        "loss_tags": [*duplicate.loss_tags, "duplicate_bootstrap_suppressed"],
                        "updated_at": now,
                    }
                )
            )
            suppressed += 1
    return suppressed


BENCHMARK_SYMBOL = "__SYSTEM__"
BENCHMARK_TIMEFRAME = "benchmark"


def paper_benchmark(repo: Any) -> dict[str, Any]:
    state = repo.get_paper_engine_state(BENCHMARK_SYMBOL, BENCHMARK_TIMEFRAME) or {}
    started_at = _parse_datetime(state.get("started_at"))
    ends_at = _parse_datetime(state.get("ends_at"))
    return {
        "started": started_at is not None,
        "started_at": started_at.isoformat() if started_at else None,
        "ends_at": ends_at.isoformat() if ends_at else None,
        "reset_count": int(state.get("reset_count") or 0),
    }


def start_paper_benchmark(repo: Any, *, reset: bool = False, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    current = paper_benchmark(repo)
    if current["started"] and not reset:
        return {**current, "created": False}
    reset_count = int(current.get("reset_count") or 0) + (1 if current["started"] else 0)
    state = {
        "started_at": now.isoformat(),
        "ends_at": (now + timedelta(days=28)).isoformat(),
        "reset_count": reset_count,
        "updated_at": now.isoformat(),
    }
    repo.upsert_paper_engine_state(BENCHMARK_SYMBOL, BENCHMARK_TIMEFRAME, state)
    return {**paper_benchmark(repo), "created": True, "target_count": len(paper_universe(repo))}


def sync_user_fills(repo: Any, provider: Any, *, now: datetime | None = None) -> dict[str, Any]:
    benchmark = paper_benchmark(repo)
    return _sync_user_fills(
        repo,
        provider,
        benchmark_started_at=_parse_datetime(benchmark.get("started_at")),
        now=now,
    )


def paper_scoreboard(repo: Any, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    benchmark = paper_benchmark(repo)
    started_at = _parse_datetime(benchmark.get("started_at"))
    all_paper = repo.list_paper_trades(limit=5000)
    all_paper_closed = [item for item in all_paper if item.status == "closed" and item.exit_reason != "duplicate_bootstrap_suppressed"]
    effective_start = started_at or min((item.entry_at for item in all_paper), default=now)
    recent_start = now - timedelta(days=28)
    comparison_paper = [trade for trade in all_paper_closed if trade.entry_at >= effective_start]
    comparison_user = repo.list_user_trades(since=effective_start, limit=5000)
    recent_paper = [trade for trade in all_paper_closed if (trade.exit_at or trade.updated_at) >= recent_start]
    recent_user = repo.list_user_trades(since=recent_start, limit=5000)
    paper_metrics = _paper_metrics(comparison_paper)
    user_metrics = _user_metrics(comparison_user)
    recent_engine = _paper_metrics(recent_paper)
    recent_user_metrics = _user_metrics(recent_user)
    sample_sufficient = bool(paper_metrics["sample_sufficient"] and user_metrics["sample_sufficient"])
    engine_leading = bool(
        sample_sufficient and paper_metrics["net_return_pct"] > user_metrics["net_return_pct"] and paper_metrics["mdd_pct"] <= user_metrics["mdd_pct"]
    )
    paper_win_rate = _float(paper_metrics.get("win_rate_pct"))
    poor = bool(
        paper_metrics["trade_count"] > 0
        and ((paper_win_rate is not None and paper_win_rate < 45.0) or paper_metrics["mdd_pct"] > float(settings.paper_poor_mdd_pct))
    )
    autonomy = repo.list_autonomy_logs(since=effective_start, limit=100)
    fill_sync = repo.get_paper_engine_state(USER_FILL_SYNC_SYMBOL, USER_FILL_SYNC_TIMEFRAME) or {
        "status": "waiting",
        "stored_fill_count": 0,
        "reconstructed_trade_count": 0,
        "pnl_status": "reconstructed",
    }
    return {
        "as_of": now.isoformat(),
        "started_at": started_at.isoformat() if started_at else None,
        "benchmark": benchmark,
        "engine": paper_metrics,
        "user": user_metrics,
        "user_fill_sync": fill_sync,
        "equity_curve": {
            "engine": _paper_equity_curve(comparison_paper),
            "user": _user_equity_curve(comparison_user),
        },
        "competition": {
            "window": "benchmark_anchor",
            "started_at": effective_start.isoformat(),
            "engine": paper_metrics,
            "user": user_metrics,
            "engine_leading": engine_leading,
            "verdict": "insufficient_samples" if not sample_sufficient else "engine_leading" if engine_leading else "no_engine_advantage",
            "equity_curve": {
                "engine": _paper_equity_curve(comparison_paper),
                "user": _user_equity_curve(comparison_user),
            },
        },
        "recent_28d": {
            "window": "rolling_28d",
            "started_at": recent_start.isoformat(),
            "engine": recent_engine,
            "user": recent_user_metrics,
            "equity_curve": {
                "engine": _paper_equity_curve(recent_paper),
                "user": _user_equity_curve(recent_user),
            },
        },
        "rolling_4w": {
            "engine": paper_metrics,
            "user": user_metrics,
            "engine_leading": engine_leading,
            "verdict": "insufficient_samples" if not sample_sufficient else "engine_leading" if engine_leading else "no_engine_advantage",
        },
        "poor_performance": poor,
        "autonomy_actions": [item.model_dump(mode="json") for item in autonomy],
        "fairness_note": "대결 판정은 시작 앵커 이후만 비교 · 최근 28일은 참고 성과 · 내 실계좌 손익은 인증 체결 기반 재구성",
        "live_orders_enabled": False,
    }


def paper_dashboard(repo: Any, settings: Any, *, calibration: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read-only projection for the engine trading workspace."""
    from app.analyst.signature_registry import state_map

    scoreboard = paper_scoreboard(repo, settings)
    open_trades = repo.list_paper_trades(status="open", limit=100)
    closed_trades = [trade for trade in repo.list_paper_trades(status="closed", limit=500) if trade.exit_reason != "duplicate_bootstrap_suppressed"]
    states = state_map(repo)
    state_counts = {"validated": 0, "degraded": 0, "quarantined": 0, "candidate": 0}
    for state in states.values():
        state_counts[state] = state_counts.get(state, 0) + 1
    calibration = calibration or {}
    candidate_review = calibration.get("candidate_review") if isinstance(calibration.get("candidate_review"), dict) else {}
    reviewed_keys = set(states)
    for item in candidate_review.get("items", []) if isinstance(candidate_review.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        for promoted_key in item.get("promotion_signature_keys", []):
            if promoted_key in reviewed_keys:
                continue
            reviewed_keys.add(promoted_key)
            state = str(item.get("status") or "candidate")
            state_counts[state] = state_counts.get(state, 0) + 1
    weekly_report = calibration.get("weekly_report") or {}
    poor = bool(scoreboard.get("poor_performance"))
    actions = scoreboard.get("autonomy_actions") or []
    funnel_24h = paper_gate_funnel(repo, days=1)
    funnel_7d = paper_gate_funnel(repo)
    activation = _paper_activation(repo, settings, funnel_24h, funnel_7d)
    return {
        "scoreboard": scoreboard,
        "open_trades": [_open_trade_payload(repo, item) for item in open_trades],
        "closed_trades": [item.model_dump(mode="json") for item in closed_trades],
        "calibration": {
            "computed_at": calibration.get("computed_at"),
            "weekly_report": {
                "improvement_digest": weekly_report.get("improvement_digest") or {},
                "highlights": weekly_report.get("highlights") or [],
                "sample_warning": weekly_report.get("sample_warning"),
            },
            "suggestions": calibration.get("suggestions") or [],
            "suggestion_status_counts": calibration.get("suggestion_status_counts") or {},
            "engine_params": calibration.get("engine_params") or [],
            "signature_state_counts": state_counts,
            "candidate_review": candidate_review
            or weekly_report.get("candidate_review")
            or {
                "generated_at": utc_now().isoformat(),
                "policy": "candidate scoring pending",
                "veto_window_hours": 48,
                "pending_promotions": 0,
                "items": [],
            },
        },
        "performance_action": {
            "poor": poor,
            "summary": _poor_performance_summary(scoreboard),
            "actions": actions[:8] if poor else [],
        },
        "gate_funnel": funnel_7d,
        "activation": activation,
        "live_orders_enabled": False,
    }


def _paper_activation(repo: Any, settings: Any, funnel_24h: dict[str, Any], funnel_7d: dict[str, Any]) -> dict[str, Any]:
    from app.worker.runtime import get_worker_status

    worker = get_worker_status()
    sync_job = (worker.get("jobs") or {}).get("sync_positions") or {}
    worker_ok = worker.get("status") == "running" and int(sync_job.get("consecutive_failures") or 0) == 0
    target_count = len(paper_universe(repo))
    evaluations = int(funnel_24h.get("evaluations") or 0)
    flip_count = _stage_count(funnel_7d, "confirmed_flip")
    recent_trade_count = sum(
        1
        for trade in repo.list_paper_trades(limit=5000)
        if trade.entry_at >= utc_now() - timedelta(days=7) and trade.exit_reason != "duplicate_bootstrap_suppressed"
    )
    entry_count = max(int(funnel_7d.get("entered") or 0), recent_trade_count)
    items = [
        {
            "id": "enabled",
            "label": "페이퍼 엔진",
            "ok": bool(settings.paper_engine_enabled),
            "value": "활성" if settings.paper_engine_enabled else "비활성",
            "reason": None if settings.paper_engine_enabled else "FCE_PAPER_ENGINE_ENABLED를 확인하세요.",
        },
        {
            "id": "worker",
            "label": "워커",
            "ok": worker_ok,
            "value": "정상" if worker_ok else "확인 필요",
            "reason": None if worker_ok else "포지션 동기화 워커가 실행 중인지 확인하세요.",
        },
        {
            "id": "targets",
            "label": "대상 심볼",
            "ok": target_count > 0,
            "value": str(target_count),
            "reason": None if target_count > 0 else "수동 추적 또는 관심종목을 추가하세요.",
        },
        {
            "id": "evaluations",
            "label": "24h 평가",
            "ok": evaluations > 0,
            "value": str(evaluations),
            "reason": None if evaluations > 0 else "첫 확정 캔들 평가를 기다리는 중입니다.",
        },
    ]
    return {
        "running": all(item["ok"] for item in items),
        "items": items,
        "target_count": target_count,
        "evaluations_24h": evaluations,
        "flip_count_7d": flip_count,
        "entry_count_7d": entry_count,
        "next_confirmed_bar_minutes": _next_confirmed_bar_minutes(repo, now=utc_now()),
    }


def _stage_count(funnel: dict[str, Any], stage_id: str) -> int:
    stages = funnel.get("stages") if isinstance(funnel.get("stages"), list) else []
    for stage in stages:
        if isinstance(stage, dict) and stage.get("id") == stage_id:
            return int(stage.get("count") or 0)
    return 0


def _next_confirmed_bar_minutes(repo: Any, *, now: datetime) -> int | None:
    deadlines: list[datetime] = []
    for symbol, timeframe in paper_universe(repo):
        state = repo.get_paper_engine_state(symbol, timeframe) or {}
        last_bar = _parse_datetime(state.get("last_bar_at"))
        if last_bar is None:
            continue
        interval = max(60, timeframe_seconds(timeframe))
        next_bar = last_bar + timedelta(seconds=interval)
        while next_bar <= now:
            next_bar += timedelta(seconds=interval)
        deadlines.append(next_bar)
    if not deadlines:
        return None
    return max(0, int(round((min(deadlines) - now).total_seconds() / 60)))


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


GATE_ORDER = (
    "confirmed_flip",
    "evidence",
    "checklist",
    "invalidation_hygiene",
    "risk_reward",
    "liquidation_safety",
    "action_levels",
    "signature_gate",
    "regime_gate",
    "event_window",
    "freshness",
    "capacity",
)

GATE_STAGE_LABELS = {
    "confirmed_flip": "스탠스 전환 통과",
    "evidence": "근거 수 통과",
    "checklist": "체크리스트 통과",
    "invalidation_hygiene": "무효화 거리 통과",
    "risk_reward": "R:R 통과",
    "liquidation_safety": "청산 안전거리 통과",
    "action_levels": "행동 가격 확보",
    "signature_gate": "검증 시그니처 통과",
    "regime_gate": "현재 레짐 성적 통과",
    "event_window": "실적 이벤트 통과",
    "freshness": "데이터 신선도 통과",
    "capacity": "포지션 여유 통과",
}

GATE_REJECTION_LABELS = {
    "confirmed_flip": "스탠스 전환 미확정",
    "evidence": "근거 수 부족",
    "checklist": "체크리스트 미달",
    "invalidation_hygiene": "무효화 과근접",
    "risk_reward": "R:R 미달",
    "liquidation_safety": "청산 안전거리 미달",
    "action_levels": "무효화·익절가 부재",
    "signature_gate": "검증 시그니처 부재",
    "regime_gate": "현재 레짐 성적 미달",
    "event_window": "실적 이벤트 구간",
    "freshness": "데이터 신선도 미달",
    "capacity": "최대 포지션 도달",
}


def paper_gate_funnel(repo: Any, *, days: int = 7, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    since = now - timedelta(days=days)
    rows = repo.list_paper_gate_funnel(since=since, limit=50000)
    block_logs = repo.list_entry_block_logs(since=since, limit=50000)
    stages: list[dict[str, Any]] = [{"id": "evaluated", "label": "평가", "count": len(rows)}]
    survivors = rows
    for gate in GATE_ORDER:
        survivors = [row for row in survivors if bool(_dict(row.get("gates")).get(gate))]
        reason_counts: dict[str, int] = {}
        for log in block_logs:
            if str(log.get("failed_gate") or "") != gate:
                continue
            detail = str(log.get("detail") or GATE_REJECTION_LABELS.get(gate, gate))
            reason_counts[detail] = reason_counts.get(detail, 0) + 1
        top_reasons = sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[:3]
        stages.append(
            {
                "id": gate,
                "label": GATE_STAGE_LABELS[gate],
                "count": len(survivors),
                "rejection_top3": [{"detail": detail, "count": count} for detail, count in top_reasons],
            }
        )
    entered = sum(1 for row in rows if row.get("entered") is True)
    rejection_counts: dict[str, int] = {}
    for row in rows:
        rejected_at = str(row.get("rejected_at") or "")
        if rejected_at:
            rejection_counts[rejected_at] = rejection_counts.get(rejected_at, 0) + 1
    top_gate = max(rejection_counts, key=rejection_counts.get) if rejection_counts else None
    rendered_ids = {str(event_id) for row in rows for event_id in row.get("event_pill_ids", []) if event_id}
    pill_bottlenecks: dict[str, int] = {}
    for row in rows:
        bottleneck = str(_dict(row.get("pill_diagnostics")).get("bottleneck") or "")
        if bottleneck:
            pill_bottlenecks[bottleneck] = pill_bottlenecks.get(bottleneck, 0) + 1
    pill_bottleneck = max(pill_bottlenecks, key=pill_bottlenecks.get) if pill_bottlenecks else None
    validated_count = sum(1 for state in repo.latest_autonomy_states().values() if state == "validated")
    signature_stage = next((stage for stage in stages if stage.get("id") == "signature_gate"), None)
    signature_gate_note = None
    if signature_stage and int(signature_stage.get("count") or 0) == 0:
        signature_gate_note = (
            "검증 시그니처 통과 0 · validated 시그니처 부재 — candidate 일일 채점·승격 심사 진행 중"
            if validated_count == 0
            else "검증 시그니처 통과 0 · 현재 방향과 일치하는 검증 성적 없음"
        )
    return {
        "period_days": days,
        "as_of": now.isoformat(),
        "evaluations": len(rows),
        "entered": entered,
        "stages": [*stages, {"id": "entered", "label": "진입", "count": entered}],
        "top_rejection": ({"id": top_gate, "label": GATE_REJECTION_LABELS.get(top_gate, top_gate), "count": rejection_counts[top_gate]} if top_gate else None),
        "rejection_counts": rejection_counts,
        "entry_block_count": len(block_logs),
        "checklist_pass_rates": _checklist_pass_rates(rows),
        "signature_gate_note": signature_gate_note,
        "pill_diagnostics": {
            "rendered": len(rendered_ids),
            "bottleneck": pill_bottleneck,
            "bottleneck_count": pill_bottlenecks.get(pill_bottleneck, 0) if pill_bottleneck else 0,
        },
    }


def _gate_funnel_record(
    *,
    symbol: str,
    timeframe: str,
    bar: MarketCandle,
    now: datetime,
    direction: Direction | None,
    evidence_count: int,
    simulation: dict[str, Any],
    signature_gates: dict[str, Any],
    action_levels: bool,
    capacity_available: bool,
    rr_ratio: float | None,
    earnings_clear: bool,
    freshness: bool,
    entry_decision: Any,
    entered: bool,
    pill_diagnostics: dict[str, Any] | None = None,
    event_pill_ids: list[str] | None = None,
) -> dict[str, Any]:
    decision_gates = _dict(getattr(entry_decision, "gates", None))
    gates = {
        "confirmed_flip": bool(decision_gates.get("confirmed_flip")),
        "evidence": bool(decision_gates.get("evidence")),
        "checklist": bool(decision_gates.get("checklist")),
        "invalidation_hygiene": bool(decision_gates.get("invalidation_hygiene")),
        "risk_reward": bool(decision_gates.get("risk_reward")),
        "liquidation_safety": bool(decision_gates.get("liquidation_safety")),
        "action_levels": action_levels,
        "signature_gate": bool(signature_gates.get("signature_gate")),
        "regime_gate": bool(signature_gates.get("regime_gate")),
        "event_window": earnings_clear,
        "freshness": freshness,
        "capacity": capacity_available,
    }
    rejected_at = next((gate for gate in GATE_ORDER if not gates[gate]), None)
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "bar_at": bar.timestamp.isoformat(),
        "evaluated_at": now.isoformat(),
        "direction": direction.value if direction is not None else None,
        "evidence_count": evidence_count,
        "checklist_score": {
            "passed": int(simulation.get("checklist_passed") or 0),
            "total": int(simulation.get("checklist_total") or 0),
        },
        "rr_ratio": rr_ratio,
        "checklist_items": [dict(item) for item in _list(simulation.get("checklist"))],
        "gates": gates,
        "entered": entered,
        "rejected_at": rejected_at,
        "rejection_reasons": [gate for gate in GATE_ORDER if not gates[gate]],
        "pill_diagnostics": pill_diagnostics or {},
        "event_pill_ids": event_pill_ids or [],
        "signature_gate_mode": signature_gates.get("gate_mode"),
        "candidate_bootstrap_active": bool(signature_gates.get("bootstrap_active")),
        "bootstrap_relaxed": signature_gates.get("gate_mode") == "candidate_bootstrap_relaxed",
        "candidate_samples": signature_gates.get("candidate_samples") or [],
        "entry_gate_version": ENTRY_GATE_VERSION,
    }


def _record_entry_block_logs(repo: Any, gate_record: dict[str, Any], *, now: datetime) -> int:
    gates = _dict(gate_record.get("gates"))
    if not gates.get("confirmed_flip") or gate_record.get("entered") is True:
        return 0
    created = 0
    for failed_gate in GATE_ORDER:
        if gates.get(failed_gate):
            continue
        record_id = str(
            uuid5(
                NAMESPACE_URL,
                ":".join(
                    (
                        "fce:entry-block",
                        str(gate_record.get("symbol") or ""),
                        str(gate_record.get("timeframe") or "4h"),
                        str(gate_record.get("bar_at") or ""),
                        str(gate_record.get("direction") or "unknown"),
                        failed_gate,
                    )
                ),
            )
        )
        detail = _entry_block_detail(gate_record, failed_gate)
        created += int(
            repo.upsert_entry_block_log(
                {
                    "id": record_id,
                    "bar_at": gate_record.get("bar_at"),
                    "symbol": gate_record.get("symbol"),
                    "timeframe": gate_record.get("timeframe"),
                    "direction": gate_record.get("direction"),
                    "failed_gate": failed_gate,
                    "detail": detail,
                    "bootstrap_relaxed": bool(gate_record.get("bootstrap_relaxed")),
                    "created_at": now.isoformat(),
                }
            )
        )
    return created


def _entry_block_detail(record: dict[str, Any], gate: str) -> str:
    if gate == "checklist":
        score = _dict(record.get("checklist_score"))
        failed = [str(item.get("label") or item.get("key") or "미확인 항목") for item in _list(record.get("checklist_items")) if item.get("status") == "fail"]
        suffix = f" — {' · '.join(failed[:3])}" if failed else ""
        return f"체크리스트 {int(score.get('passed') or 0)}/{int(score.get('total') or 0)}{suffix}"
    if gate == "signature_gate":
        candidates = _list(record.get("candidate_samples"))
        sample_text = ", ".join(
            f"{item.get('engine') or 'candidate'} N={int(item.get('sample_size') or 0)} win={_format_gate_number(item.get('win_1r_pct'))}%"
            for item in candidates[:5]
        )
        return f"validated 시그니처 0 · candidate {len(candidates)}종 {sample_text or '표본 없음'}"
    if gate == "risk_reward":
        return f"R:R {_format_gate_number(record.get('rr_ratio'))}<1.5"
    if gate == "invalidation_hygiene":
        return "무효화 과근접 — 0.8% 미만 노이즈 의심"
    if gate == "evidence":
        return f"방향 근거 {int(record.get('evidence_count') or 0)}개<4"
    return GATE_REJECTION_LABELS.get(gate, gate)


def _checklist_pass_rates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    for row in rows:
        for item in _list(row.get("checklist_items")):
            status = str(item.get("status") or "")
            if status not in {"pass", "fail"}:
                continue
            key = str(item.get("key") or item.get("label") or "unknown")
            bucket = counts.setdefault(key, {"key": key, "label": str(item.get("label") or key), "passed": 0, "evaluated": 0})
            bucket["evaluated"] += 1
            bucket["passed"] += int(status == "pass")
    return [
        {**bucket, "pass_rate_pct": round(bucket["passed"] / bucket["evaluated"] * 100.0, 1)}
        for bucket in sorted(counts.values(), key=lambda item: (item["passed"] / item["evaluated"], item["key"]))
    ]


def _format_gate_number(value: Any) -> str:
    number = _float(value)
    return "-" if number is None else f"{number:.1f}"


def _gate_diagnostic_event(repo: Any, *, now: datetime) -> dict[str, Any] | None:
    state = repo.get_paper_engine_state("__SYSTEM__", "gate_diagnostic") or {}
    if state.get("sent_at"):
        return None
    all_rows = repo.list_paper_gate_funnel(limit=50000)
    if not all_rows:
        return None
    oldest = min((_timestamp(row.get("bar_at")) for row in all_rows), default=None)
    if oldest is None or oldest > now - timedelta(days=7):
        return None
    funnel = paper_gate_funnel(repo, days=7, now=now)
    if int(funnel.get("entered") or 0) > 0:
        return None
    top = _dict(funnel.get("top_rejection"))
    repo.upsert_paper_engine_state(
        "__SYSTEM__",
        "gate_diagnostic",
        {"sent_at": now.isoformat(), "top_rejection": top.get("id")},
    )
    return {"kind": "gate_diagnostic", "funnel": funnel}


def _finalize_closed_trade(repo: Any, trade: PaperTrade, analysis: dict[str, Any]) -> PaperTrade:
    neutral = trade.exit_reason in {"time_stop", "time_decay"}
    loss_tags: list[str] = ["time_decay", f"exit:{trade.exit_reason}"] if neutral else []
    if trade.net_pnl_usdt < 0 and not neutral:
        signature_key = str(trade.signature_snapshot.get("signature_key") or "unknown")
        loss_tags = [f"validated_signature_failed:{signature_key}", f"exit:{trade.exit_reason}"]
        regime = analysis.get("market_regime") or analysis.get("regime")
        if regime:
            loss_tags.append(f"regime:{regime}")
    result = trade.model_copy(update={"loss_tags": loss_tags})
    repo.add_judgment_score(
        JudgmentScore(
            judgment_id=trade.judgment_id or f"paper:{trade.id}:entry",
            position_id=trade.id,
            judgment_type="paper_trade_entry",
            claim={"direction": trade.direction.value, "entry_price": trade.entry_price},
            outcome="untested" if neutral else "correct" if trade.net_pnl_usdt > 0 else "wrong" if trade.net_pnl_usdt < 0 else "untested",
            detail=f"paper trade closed by {trade.exit_reason}; net={trade.net_pnl_usdt:.4f} USDT",
            metrics={
                "net_pnl_usdt": trade.net_pnl_usdt,
                "net_return_pct": trade.net_return_pct,
                "result_class": "neutral" if neutral else "win" if trade.net_pnl_usdt > 0 else "loss" if trade.net_pnl_usdt < 0 else "neutral",
                "time_decay": neutral,
            },
        )
    )
    return result


def _record_entry_judgment(repo: Any, trade: PaperTrade) -> None:
    judgment_id = trade.judgment_id or f"paper:{trade.id}:entry"
    repo.add_judgment(
        JudgmentLedgerEntry(
            id=uuid5(NAMESPACE_URL, f"fce:{judgment_id}"),
            judgment_id=judgment_id,
            position_id=trade.id,
            source_type="paper_engine",
            source_id=str(trade.id),
            as_of=trade.entry_bar_at,
            type="paper_trade_entry",
            claim={
                "direction": trade.direction.value,
                "price": trade.entry_price,
                "invalidation": trade.invalidation_price,
                "take_profit": trade.take_profit_price,
                "evidence": trade.entry_evidence,
                "checklist": trade.checklist,
                "signature": trade.signature_snapshot,
            },
        )
    )


def _confirmed_bar(analysis: dict[str, Any], gauges: dict[str, Any]) -> MarketCandle | None:
    candles = [item for item in analysis.get("candles", []) if isinstance(item, dict)]
    if not candles:
        return None
    provisional = _dict(gauges.get("bar_state")).get("provisional")
    selected = candles[-2] if provisional is True and len(candles) > 1 else candles[-1]
    timestamp = _timestamp(selected.get("time") or selected.get("timestamp"))
    if timestamp is None:
        return None
    try:
        return MarketCandle(
            timestamp=timestamp,
            open=float(selected["open"]),
            high=float(selected["high"]),
            low=float(selected["low"]),
            close=float(selected["close"]),
            volume=float(selected.get("volume") or 0.0),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _signature_gate_evaluation(
    repo: Any,
    settings: Any,
    analysis: dict[str, Any],
    payload: dict[str, Any],
    direction: Direction,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or utc_now()
    stats = [
        item
        for item in [
            *_list(_dict(payload.get("historical_backtest")).get("stats")),
            *_list(_dict(payload.get("historical_backtest")).get("event_stats")),
        ]
        if isinstance(item, dict)
    ]
    by_key = {str(item.get("signature_key") or _dict(item.get("signature")).get("key") or ""): item for item in stats}
    signature_gate = False
    regime_gate = False
    qualified: dict[str, Any] | None = None
    bootstrap_active = _candidate_bootstrap_active(repo, settings, now=now)
    relaxed_active = _candidate_bootstrap_relaxed_active(repo, settings, now=now)
    normal_min_sample = int(getattr(settings, "paper_candidate_bootstrap_min_sample", 15))
    normal_min_win = float(getattr(settings, "paper_candidate_bootstrap_min_win_1r_pct", 50.0))
    relaxed_min_sample = int(getattr(settings, "paper_candidate_bootstrap_relaxed_min_sample", 8))
    relaxed_min_win = float(getattr(settings, "paper_candidate_bootstrap_relaxed_min_win_1r_pct", 45.0))
    candidate_samples: list[dict[str, Any]] = []
    active_signatures = [
        *signatures_from_analysis(analysis),
        *_candidate_signatures_for_gate(repo, analysis, payload, direction),
    ]
    for signature in active_signatures:
        if signature.get("direction") != direction.value:
            continue
        key = str(signature.get("key") or "")
        stat = _pooled_signature_stat(repo, key, fallback=by_key.get(key)) if key else None
        if not stat:
            continue
        state = current_state(repo, key, stat=stat, settings=settings)
        if state == "candidate" and bootstrap_active:
            sample_size = int(stat.get("sample_size") or 0)
            win_1r_pct = _float(stat.get("win_1r_pct"))
            candidate_samples.append(
                {
                    "signature_key": key,
                    "engine": signature.get("engine"),
                    "sample_size": sample_size,
                    "win_1r_pct": win_1r_pct,
                }
            )
            normal_passed = sample_size >= normal_min_sample and win_1r_pct is not None and win_1r_pct >= normal_min_win
            relaxed_passed = relaxed_active and sample_size >= relaxed_min_sample and win_1r_pct is not None and win_1r_pct >= relaxed_min_win
            if normal_passed or relaxed_passed:
                gate_mode = "candidate_bootstrap" if normal_passed else "candidate_bootstrap_relaxed"
                thresholds = (
                    {"min_sample_size": normal_min_sample, "min_win_1r_pct": normal_min_win}
                    if normal_passed
                    else {"min_sample_size": relaxed_min_sample, "min_win_1r_pct": relaxed_min_win}
                )
                signature_gate = True
                regime_gate = True
                qualified = {
                    "signature_key": key,
                    "signature": signature,
                    "stat": stat,
                    "state": state,
                    "gate_mode": gate_mode,
                    "bootstrap_relaxed": gate_mode == "candidate_bootstrap_relaxed",
                    "bootstrap_thresholds": thresholds,
                }
                break
        if state != "validated":
            continue
        signature_gate = True
        ci_low = _ci_low(stat)
        if ci_low is None or ci_low < float(settings.universe_backtest_min_ci_low_pct):
            continue
        regime_gate = True
        qualified = {"signature_key": key, "signature": signature, "stat": stat, "ci_low": ci_low, "state": state, "gate_mode": "validated"}
        break
    return {
        "qualified": qualified,
        "signature_gate": signature_gate,
        "regime_gate": regime_gate,
        "gate_mode": qualified.get("gate_mode") if qualified else None,
        "bootstrap_active": bootstrap_active,
        "bootstrap_relaxed_active": relaxed_active,
        "candidate_samples": candidate_samples,
    }


def _pooled_signature_stat(repo: Any, signature_key_value: str, *, fallback: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Pool the latest symbol-scoped rows for one exact signature key."""
    stored = repo.list_backtest_stats(signature_key=signature_key_value, limit=5000)
    latest_by_source: dict[tuple[str, str, str], Any] = {}
    for item in stored:
        if bool(item.payload.get("candidate_review")) or item.scope != "symbol":
            continue
        source = (item.symbol.upper(), item.timeframe, item.scope)
        latest_by_source.setdefault(source, item)
    rows = list(latest_by_source.values())
    if not rows:
        return dict(fallback) if isinstance(fallback, dict) else None

    sample_size = sum(max(0, int(item.sample_size)) for item in rows)
    wins_1r = sum(_stat_wins(item.sample_size, item.win_1r_pct) for item in rows)
    wins_2r = sum(_stat_wins(item.sample_size, item.win_2r_pct) for item in rows)
    first = rows[0]
    stat = first.model_dump(mode="json")
    stat.update(_dict(first.payload))
    ci = bootstrap_ci_from_counts(wins_1r, sample_size) if sample_size else None
    stat.update(
        {
            "sample_size": sample_size,
            "win_1r_pct": round(wins_1r / sample_size * 100, 1) if sample_size else None,
            "win_2r_pct": round(wins_2r / sample_size * 100, 1) if sample_size else None,
            "win_1r_ci": list(ci) if ci else None,
            "pooled_signature": True,
            "source_stats_count": len(rows),
            "source_symbols": sorted({item.symbol.upper() for item in rows}),
            "sources": {
                "backtest": {
                    "sample_size": sample_size,
                    "wins_1r": wins_1r,
                    "wins_2r": wins_2r,
                    "rows": len(rows),
                },
                "live": {"sample_size": 0},
            },
        }
    )
    return stat


def _stat_wins(sample_size: int, win_pct: float | None) -> int:
    if sample_size <= 0 or win_pct is None:
        return 0
    return max(0, min(sample_size, round(sample_size * float(win_pct) / 100.0)))


def _candidate_bootstrap_active(repo: Any, settings: Any, *, now: datetime | None = None) -> bool:
    if not bool(getattr(settings, "paper_candidate_bootstrap_enabled", True)):
        return False
    benchmark = paper_benchmark(repo)
    ends_at = _parse_datetime(benchmark.get("ends_at"))
    if ends_at is not None and (now or utc_now()) >= ends_at:
        return False
    validated = sum(1 for state in repo.latest_autonomy_states().values() if state == "validated")
    return validated < int(getattr(settings, "paper_candidate_bootstrap_disable_validated_count", 3))


def _candidate_bootstrap_relaxed_active(repo: Any, settings: Any, *, now: datetime | None = None) -> bool:
    current = now or utc_now()
    if not _candidate_bootstrap_active(repo, settings, now=current):
        return False
    benchmark = paper_benchmark(repo)
    started_at = _parse_datetime(benchmark.get("started_at"))
    if started_at is None:
        return False
    validated = sum(1 for state in repo.latest_autonomy_states().values() if state == "validated")
    if validated > 0:
        return False
    relaxed_days = int(getattr(settings, "paper_candidate_bootstrap_relaxed_days", 14))
    return current < started_at + timedelta(days=relaxed_days)


def _candidate_signatures_for_gate(
    repo: Any,
    analysis: dict[str, Any],
    payload: dict[str, Any],
    direction: Direction,
) -> list[dict[str, Any]]:
    briefing = _dict(payload.get("analyst_briefing"))
    confluence = _dict(briefing.get("confluence"))
    target_at = _parse_datetime(_dict(confluence.get("stance_state")).get("last_bar_at"))
    symbol = str(analysis.get("symbol") or payload.get("symbol") or "").upper()
    timeframe = str(analysis.get("timeframe") or payload.get("timeframe") or "4h")
    asset_class = str(analysis.get("asset_class") or "unknown")
    if not symbol:
        return []
    if target_at is None:
        candles = [item for item in analysis.get("candles", []) if isinstance(item, dict)]
        if candles:
            target_at = _timestamp(candles[-1].get("time") or candles[-1].get("timestamp"))
    result: list[dict[str, Any]] = []
    for judgment in repo.list_judgments(UUID(int=0), limit=500):
        claim = judgment.claim
        engine = str(claim.get("engine") or "")
        if (
            engine not in CANDIDATE_ENGINES
            or str(claim.get("symbol") or "").upper() != symbol
            or str(claim.get("timeframe") or "4h") != timeframe
            or str(claim.get("direction") or "") != direction.value
        ):
            continue
        if target_at is None or abs((judgment.as_of - target_at).total_seconds()) > 60:
            continue
        signature = SetupSignature(
            engine=engine,
            event_type=str(claim.get("event_type") or "candidate"),
            strength_class="candidate",
            direction=direction.value,
            asset_class=asset_class,
            timeframe=timeframe,
        ).model_dump()
        signature["key"] = signature_key(signature)
        result.append(signature)
    return list({str(item["key"]): item for item in result}.values())


def _qualified_signature(repo: Any, settings: Any, analysis: dict[str, Any], payload: dict[str, Any], direction: Direction) -> dict[str, Any] | None:
    return _dict(_signature_gate_evaluation(repo, settings, analysis, payload, direction).get("qualified")) or None


def _paper_metrics(trades: Iterable[PaperTrade]) -> dict[str, Any]:
    rows = list(trades)
    returns = [trade.net_return_pct for trade in sorted(rows, key=lambda item: item.exit_at or item.updated_at)]
    scored = [trade for trade in rows if trade.exit_reason not in {"time_stop", "time_decay"}]
    wins = [trade for trade in scored if trade.net_pnl_usdt > 0]
    gross_profit = sum(max(0.0, trade.net_pnl_usdt) for trade in rows)
    gross_loss = abs(sum(min(0.0, trade.net_pnl_usdt) for trade in rows))
    return _metric_payload(returns, len(wins), gross_profit, gross_loss, scored_count=len(scored), neutral_count=len(rows) - len(scored))


def _user_metrics(trades: Iterable[Any]) -> dict[str, Any]:
    rows = list(trades)
    returns = [float(trade.net_return_pct) for trade in sorted(rows, key=lambda item: item.exit_at)]
    wins = [trade for trade in rows if float(trade.net_pnl_usdt) > 0]
    gross_profit = sum(max(0.0, float(trade.net_pnl_usdt)) for trade in rows)
    gross_loss = abs(sum(min(0.0, float(trade.net_pnl_usdt)) for trade in rows))
    return _metric_payload(returns, len(wins), gross_profit, gross_loss, scored_count=len(rows), neutral_count=0)


def _metric_payload(
    returns: list[float],
    wins: int,
    gross_profit: float,
    gross_loss: float,
    *,
    scored_count: int,
    neutral_count: int,
) -> dict[str, Any]:
    equity = peak = 0.0
    mdd = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)
    count = len(returns)
    return {
        "net_return_pct": round(sum(returns), 4),
        "win_rate_pct": round((wins / scored_count) * 100.0, 2) if scored_count else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        "mdd_pct": round(mdd, 4),
        "trade_count": count,
        "scored_trade_count": scored_count,
        "neutral_count": neutral_count,
        "sample_sufficient": scored_count >= 10,
    }


def _paper_equity_curve(trades: Iterable[PaperTrade]) -> list[dict[str, Any]]:
    return _return_curve(((trade.exit_at or trade.updated_at, float(trade.net_return_pct)) for trade in trades))


def _user_equity_curve(trades: Iterable[Any]) -> list[dict[str, Any]]:
    return _return_curve(((trade.exit_at, float(trade.net_return_pct)) for trade in trades))


def _return_curve(points: Iterable[tuple[datetime, float]]) -> list[dict[str, Any]]:
    total = 0.0
    curve: list[dict[str, Any]] = []
    for timestamp, value in sorted(points, key=lambda item: item[0]):
        total += value
        curve.append({"ts": timestamp.isoformat(), "return_pct": round(total, 4)})
    return curve


def _paper_event(kind: str, trade: PaperTrade, *, reason: str | None = None) -> dict[str, Any]:
    return {
        "kind": kind,
        "reason": reason or trade.exit_reason,
        "trade": trade.model_dump(mode="json"),
    }


def paper_exit_monitor(trade: PaperTrade | dict[str, Any], mark_price: float | None) -> dict[str, float] | None:
    payload = trade.model_dump(mode="json") if isinstance(trade, PaperTrade) else trade
    mark = _float(mark_price)
    entry = _float(payload.get("entry_price"))
    stop_price = _float(payload.get("stop_price")) or _float(payload.get("invalidation_price"))
    take_profit = _float(payload.get("take_profit_price"))
    if mark is None or mark <= 0 or entry is None or entry <= 0 or stop_price is None or take_profit is None:
        return None
    direction_sign = 1.0 if str(payload.get("direction")) == "long" else -1.0
    remaining_quantity = float(_float(payload.get("remaining_quantity")) or 0.0)
    unrealized_pnl = (mark - entry) * remaining_quantity * direction_sign
    mark_net_pnl = float(_float(payload.get("gross_pnl_usdt")) or 0.0) + unrealized_pnl - float(_float(payload.get("costs_usdt")) or 0.0)
    margin = _float(payload.get("margin_usdt"))
    monitor = {
        "mark_price": round(mark, 8),
        "unrealized_pnl_usdt": round(unrealized_pnl, 4),
        "mark_net_pnl_usdt": round(mark_net_pnl, 4),
        "mark_net_return_pct": round(mark_net_pnl / margin * 100.0, 4) if margin and margin > 0 else 0.0,
        "invalidation_distance_pct": round(-abs(stop_price / mark - 1.0) * 100.0, 3),
        "take_profit_distance_pct": round(abs(take_profit / mark - 1.0) * 100.0, 3),
    }
    take_profit_2 = _float(payload.get("take_profit_2_price"))
    if take_profit_2 is not None:
        monitor["take_profit_2_distance_pct"] = round(abs(take_profit_2 / mark - 1.0) * 100.0, 3)
    return monitor


def audit_atr_target_reachability(
    candles: list[MarketCandle],
    *,
    atr_multiplier: float = 1.0,
    max_holding_bars: int = 30,
) -> dict[str, Any]:
    ordered = sorted(candles, key=lambda item: item.timestamp)
    samples = reached = 0
    misses_by_direction = {"long": 0, "short": 0}
    if atr_multiplier <= 0 or max_holding_bars <= 0:
        raise ValueError("ATR multiplier and max holding bars must be positive")
    for index in range(14, len(ordered) - max_holding_bars):
        entry = ordered[index].close
        distance = atr(ordered[: index + 1]) * atr_multiplier
        future = ordered[index + 1 : index + 1 + max_holding_bars]
        long_reached = max(item.high for item in future) >= entry + distance
        short_reached = min(item.low for item in future) <= entry - distance
        for direction, touched in (("long", long_reached), ("short", short_reached)):
            samples += 1
            reached += int(touched)
            if not touched:
                misses_by_direction[direction] += 1
    misses = samples - reached
    return {
        "samples": samples,
        "reached": reached,
        "reach_rate_pct": round(reached / samples * 100.0, 2) if samples else None,
        "time_decay_rate_pct": round(misses / samples * 100.0, 2) if samples else None,
        "misses_by_direction": misses_by_direction,
        "atr_multiplier": atr_multiplier,
        "max_holding_bars": max_holding_bars,
        "method": "confirmed_candle_atr_target_replay",
    }


def _open_trade_payload(repo: Any, trade: PaperTrade) -> dict[str, Any]:
    payload = trade.model_dump(mode="json")
    state = repo.get_paper_engine_state(trade.symbol, trade.timeframe) or {}
    if isinstance(state.get("stance_state"), dict):
        payload["current_stance"] = state["stance_state"]
    monitor = paper_exit_monitor(trade, _float(state.get("last_price")))
    if monitor is not None:
        payload["exit_monitor"] = monitor
    return payload


def _poor_performance_summary(scoreboard: dict[str, Any]) -> str:
    if not scoreboard.get("poor_performance"):
        return "부진 기준에 해당하지 않습니다."
    engine = _dict(scoreboard.get("engine"))
    return f"승률 {float(engine.get('win_rate_pct') or 0):.1f}% · MDD {float(engine.get('mdd_pct') or 0):.1f}% — 같은 기간 자율 조치를 함께 표시합니다."


def _stance_state(confluence: dict[str, Any]) -> dict[str, Any]:
    state = dict(_dict(confluence.get("stance_state")))
    state.setdefault("stance", confluence.get("stance"))
    return state


def _stance_direction(confluence: dict[str, Any]) -> Direction | None:
    stance = str(confluence.get("stance") or "")
    if stance in {"long", "long_leaning"}:
        return Direction.long
    if stance in {"short", "short_leaning"}:
        return Direction.short
    return None


def _direction_evidence(confluence: dict[str, Any], direction: Direction) -> list[dict[str, Any]]:
    key = "long_evidence" if direction == Direction.long else "short_evidence"
    return [item for item in _list(confluence.get(key)) if isinstance(item, dict)]


def _earnings_clear(analysis: dict[str, Any]) -> bool:
    if str(analysis.get("asset_class")) not in {"stock", "index"}:
        return True
    earnings = _dict(analysis.get("earnings") or analysis.get("earnings_risk"))
    return bool(earnings) and earnings.get("blocked") is False and earnings.get("days_to_event") not in {-1, 0, 1}


def _data_fresh(bar: MarketCandle, timeframe: str, now: datetime) -> bool:
    hours = {"1h": 1, "4h": 4, "1d": 24}.get(timeframe.lower(), 4)
    return now - bar.timestamp <= timedelta(hours=hours * 2 + 1)


def _first_take_profit(action_plan: dict[str, Any]) -> float | None:
    for item in _list(action_plan.get("take_profit")):
        price = _price_from(item)
        if price is not None:
            return price
    return None


def _paper_target_plan(
    analysis: dict[str, Any],
    gauges: dict[str, Any],
    *,
    bar: MarketCandle,
    direction: Direction,
    invalidation_price: float | None,
    action_plan: dict[str, Any],
    policy: PaperPolicy,
) -> dict[str, Any]:
    candles = _confirmed_candles(analysis, gauges)
    atr_value = atr(candles) if candles else max(bar.close * 0.015, 1e-9)
    sign = 1.0 if direction == Direction.long else -1.0
    tp1_distance = atr_value * policy.take_profit_atr_k1
    tp2_distance = atr_value * policy.take_profit_atr_k2
    structural = _first_take_profit(action_plan)
    structural_distance = (structural - bar.close) * sign if structural is not None else None
    tp2_source = "atr"
    if structural_distance is not None and tp1_distance < structural_distance < tp2_distance:
        tp2_distance = structural_distance
        tp2_source = "action_plan_nearer"
    structural_risk = abs(bar.close - invalidation_price) if invalidation_price is not None else None
    invalidation_directional = bool(
        invalidation_price is not None
        and ((direction == Direction.long and invalidation_price < bar.close) or (direction == Direction.short and invalidation_price > bar.close))
    )
    execution_risk = min(structural_risk, atr_value) if structural_risk and structural_risk > 0 and invalidation_directional else None
    execution_invalidation = bar.close - sign * execution_risk if execution_risk is not None else None
    staged_reward = tp1_distance * 0.5 + tp2_distance * 0.5
    rr_ratio = staged_reward / execution_risk if execution_risk and execution_risk > 0 else None
    execution_distance_pct = abs(execution_risk / bar.close * 100.0) if execution_risk is not None and bar.close else None
    return {
        "method": "atr_multistage",
        "atr": round(atr_value, 8),
        "atr_period": 14,
        "k1": policy.take_profit_atr_k1,
        "k2": policy.take_profit_atr_k2,
        "take_profit_1": round(bar.close + sign * tp1_distance, 8),
        "take_profit_2": round(bar.close + sign * tp2_distance, 8),
        "take_profit_2_source": tp2_source,
        "action_plan_take_profit_1": structural,
        "thesis_invalidation": invalidation_price,
        "structural_risk_distance": round(structural_risk, 8) if structural_risk is not None else None,
        "execution_invalidation": round(execution_invalidation, 8) if execution_invalidation is not None else None,
        "execution_invalidation_source": "atr_risk_cap"
        if execution_risk is not None and structural_risk is not None and execution_risk < structural_risk
        else "structural",
        "execution_invalidation_distance_pct": round(execution_distance_pct, 4) if execution_distance_pct is not None else None,
        "execution_invalidation_too_close": bool(execution_distance_pct is not None and execution_distance_pct < 0.8),
        "risk_distance": round(execution_risk, 8) if execution_risk is not None else None,
        "staged_reward_distance": round(staged_reward, 8),
        "reward_weighting": {"take_profit_1": 0.5, "take_profit_2": 0.5},
        "rr_ratio": round(rr_ratio, 4) if rr_ratio is not None else None,
        "minimum_rr": policy.min_rr,
        "rr_eligible": bool(rr_ratio is not None and rr_ratio >= policy.min_rr),
    }


def _paper_simulation_contract(simulation: dict[str, Any], target_plan: dict[str, Any]) -> dict[str, Any]:
    """Align the paper checklist with its ATR execution targets without changing the shared simulator."""
    rr_ratio = _float(target_plan.get("rr_ratio"))
    minimum_rr = _float(target_plan.get("minimum_rr")) or 1.5
    checklist = []
    for raw in _list(simulation.get("checklist")):
        item = dict(raw)
        if item.get("key") == "rr":
            passed = rr_ratio is not None and rr_ratio >= minimum_rr
            item.update(
                {
                    "status": "pass" if passed else "fail",
                    "reason": (f"단계 익절 기대 R:R {rr_ratio:.2f} — TP1 50%·TP2 50%" if rr_ratio is not None else "단계 익절 R:R 산출 불가"),
                }
            )
        checklist.append(item)
    evaluated = [item for item in checklist if item.get("status") in {"pass", "fail"}]
    return {
        **simulation,
        "checklist": checklist,
        "checklist_passed": sum(1 for item in evaluated if item.get("status") == "pass"),
        "checklist_total": len(evaluated),
        "rr_ratio": rr_ratio,
        "paper_target_plan": target_plan,
    }


def _confirmed_candles(analysis: dict[str, Any], gauges: dict[str, Any]) -> list[MarketCandle]:
    rows = [item for item in analysis.get("candles", []) if isinstance(item, dict)]
    if _dict(gauges.get("bar_state")).get("provisional") is True and rows:
        rows = rows[:-1]
    candles: list[MarketCandle] = []
    for item in rows:
        timestamp = _timestamp(item.get("time") or item.get("timestamp"))
        if timestamp is None:
            continue
        try:
            candles.append(
                MarketCandle(
                    timestamp=timestamp,
                    open=float(item["open"]),
                    high=float(item["high"]),
                    low=float(item["low"]),
                    close=float(item["close"]),
                    volume=float(item.get("volume") or 0.0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return candles


def _price_from(value: Any) -> float | None:
    if isinstance(value, dict):
        return _float(value.get("price"))
    return _float(value)


def _ci_low(stat: dict[str, Any]) -> float | None:
    ci = stat.get("win_1r_ci") or _dict(stat.get("payload")).get("win_1r_ci")
    return _float(ci[0]) if isinstance(ci, (list, tuple)) and len(ci) == 2 else None


def _normalize_pressure(value: Any) -> str | None:
    text = str(value or "").lower()
    return "high" if text in {"high", "높음"} else "mid" if text in {"mid", "middle", "중간"} else "low" if text in {"low", "낮음"} else None


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        seconds = float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
