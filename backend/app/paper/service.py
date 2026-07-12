from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from uuid import uuid4

from app.analyst.gauges import build_gauges
from app.analyst.signature_registry import current_state
from app.backtest.signatures import signatures_from_analysis
from app.db.models import (
    Direction,
    JudgmentLedgerEntry,
    JudgmentScore,
    MarketCandle,
    PaperTrade,
    PositionStatus,
    utc_now,
)
from app.paper.policy import (
    PaperPolicy,
    apply_exit_decision,
    evaluate_entry,
    evaluate_exit,
    open_trade,
)


AnalysisLoader = Callable[[str, str], dict[str, Any]]
SimulationLoader = Callable[[str, str, str, float], dict[str, Any]]


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
            funnel_recorded = any(
                str(row.get("timeframe") or "4h") == timeframe and str(row.get("bar_at") or "") == bar_key
                for row in repo.list_paper_gate_funnel(symbol=symbol, limit=10)
            )
            if state.get("last_bar_at") == bar_key and funnel_recorded:
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
                    events.append(_paper_event("partial", updated))
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
                evidence: list[dict[str, Any]] = []
                entry_decision = None
                if direction is not None:
                    simulation = simulation_loader(symbol, timeframe, direction.value, bar.close)
                    signature_gates = _signature_gate_evaluation(repo, settings, analysis, payload, direction)
                    qualified = _dict(signature_gates.get("qualified")) or None
                    action_plan = _dict(simulation.get("action_plan"))
                    invalidation = _price_from(action_plan.get("invalidation") or action_plan.get("engine_invalidation"))
                    take_profit = _first_take_profit(action_plan)
                    evidence = _direction_evidence(confluence, direction)
                    entry_decision = evaluate_entry(
                        stance_state=_stance_state(confluence),
                        direction=direction,
                        evidence_count=len(evidence),
                        checklist_passed=int(simulation.get("checklist_passed") or 0),
                        checklist_total=int(simulation.get("checklist_total") or 0),
                        rr_ratio=_float(simulation.get("rr_ratio")),
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
                    earnings_clear=_earnings_clear(analysis),
                    freshness=_data_fresh(bar, timeframe, now),
                    entry_decision=entry_decision,
                    entered=will_enter,
                    pill_diagnostics=_dict(gauges.get("pill_diagnostics") or gauges.get("event_pill_audit")),
                    event_pill_ids=[str(item.get("id")) for item in _list(gauges.get("event_pills")) if item.get("id")],
                )
                repo.upsert_paper_gate_funnel(gate_record)
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
                            "rr_ratio": simulation.get("rr_ratio"),
                            "survives_to_invalidation": simulation.get("survives_to_invalidation"),
                        },
                        stance_snapshot=_stance_state(confluence),
                        signature_snapshot=qualified or {},
                        policy=policy_from_settings(settings, str(analysis.get("asset_class") or "unknown")),
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
                    "take_profit_pressure_high_streak": high_streak,
                    "updated_at": now.isoformat(),
                },
            )
        except Exception as exc:  # each symbol is isolated; worker continues
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})

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
        "events": events,
        "errors": errors,
    }


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


def paper_scoreboard(repo: Any, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    benchmark = paper_benchmark(repo)
    started_at = _parse_datetime(benchmark.get("started_at"))
    all_paper = repo.list_paper_trades(limit=5000)
    effective_start = started_at or min((item.entry_at for item in all_paper), default=now)
    paper_closed = [item for item in all_paper if item.status == "closed" and item.entry_at >= effective_start]
    rolling_start = max(now - timedelta(days=28), effective_start)
    user_trades = [trade for trade in repo.list_trades() if trade.created_at >= effective_start]
    rolling_paper = [trade for trade in paper_closed if (trade.exit_at or trade.updated_at) >= rolling_start]
    rolling_user = [trade for trade in user_trades if trade.created_at >= rolling_start]
    paper_metrics = _paper_metrics(paper_closed)
    user_metrics = _user_metrics(user_trades)
    rolling_engine = _paper_metrics(rolling_paper)
    rolling_user_metrics = _user_metrics(rolling_user)
    engine_leading = bool(
        rolling_engine["trade_count"] > 0
        and rolling_user_metrics["trade_count"] > 0
        and rolling_engine["net_return_pct"] > rolling_user_metrics["net_return_pct"]
        and rolling_engine["mdd_pct"] <= rolling_user_metrics["mdd_pct"]
    )
    poor = bool(paper_metrics["trade_count"] > 0 and (paper_metrics["win_rate_pct"] < 45.0 or paper_metrics["mdd_pct"] > float(settings.paper_poor_mdd_pct)))
    autonomy = repo.list_autonomy_logs(since=effective_start, limit=100)
    return {
        "as_of": now.isoformat(),
        "started_at": started_at.isoformat() if started_at else None,
        "benchmark": benchmark,
        "engine": paper_metrics,
        "user": user_metrics,
        "equity_curve": {
            "engine": _paper_equity_curve(paper_closed),
            "user": _user_equity_curve(user_trades),
        },
        "rolling_4w": {
            "engine": rolling_engine,
            "user": rolling_user_metrics,
            "engine_leading": engine_leading,
            "verdict": "engine_leading" if engine_leading else "no_engine_advantage",
        },
        "poor_performance": poor,
        "autonomy_actions": [item.model_dump(mode="json") for item in autonomy],
        "fairness_note": "조건 상이 — 절대 금액이 아닌 방향·타이밍 판단력의 비율 비교",
        "live_orders_enabled": False,
    }


def paper_dashboard(repo: Any, settings: Any, *, calibration: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read-only projection for the engine trading workspace."""
    from app.analyst.signature_registry import state_map

    scoreboard = paper_scoreboard(repo, settings)
    open_trades = repo.list_paper_trades(status="open", limit=100)
    closed_trades = repo.list_paper_trades(status="closed", limit=500)
    states = state_map(repo)
    state_counts = {"validated": 0, "degraded": 0, "quarantined": 0, "candidate": 0}
    for state in states.values():
        state_counts[state] = state_counts.get(state, 0) + 1
    calibration = calibration or {}
    weekly_report = calibration.get("weekly_report") or {}
    poor = bool(scoreboard.get("poor_performance"))
    actions = scoreboard.get("autonomy_actions") or []
    funnel_24h = paper_gate_funnel(repo, days=1)
    activation = _paper_activation(repo, settings, funnel_24h)
    return {
        "scoreboard": scoreboard,
        "open_trades": [item.model_dump(mode="json") for item in open_trades],
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
        },
        "performance_action": {
            "poor": poor,
            "summary": _poor_performance_summary(scoreboard),
            "actions": actions[:8] if poor else [],
        },
        "gate_funnel": paper_gate_funnel(repo),
        "activation": activation,
        "live_orders_enabled": False,
    }


def _paper_activation(repo: Any, settings: Any, funnel_24h: dict[str, Any]) -> dict[str, Any]:
    from app.worker.runtime import get_worker_status

    worker = get_worker_status()
    sync_job = (worker.get("jobs") or {}).get("sync_positions") or {}
    worker_ok = worker.get("status") == "running" and int(sync_job.get("consecutive_failures") or 0) == 0
    target_count = len(paper_universe(repo))
    evaluations = int(funnel_24h.get("evaluations") or 0)
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
    return {"running": all(item["ok"] for item in items), "items": items, "target_count": target_count, "evaluations_24h": evaluations}


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
    "risk_reward",
    "liquidation_safety",
    "action_levels",
    "signature_gate",
    "regime_gate",
    "event_window",
    "freshness",
)

GATE_STAGE_LABELS = {
    "confirmed_flip": "스탠스 전환 통과",
    "evidence": "근거 수 통과",
    "checklist": "체크리스트 통과",
    "risk_reward": "R:R 통과",
    "liquidation_safety": "청산 안전거리 통과",
    "action_levels": "행동 가격 확보",
    "signature_gate": "검증 시그니처 통과",
    "regime_gate": "현재 레짐 성적 통과",
    "event_window": "실적 이벤트 통과",
    "freshness": "데이터 신선도 통과",
}

GATE_REJECTION_LABELS = {
    "confirmed_flip": "스탠스 전환 미확정",
    "evidence": "근거 수 부족",
    "checklist": "체크리스트 미달",
    "risk_reward": "R:R 미달",
    "liquidation_safety": "청산 안전거리 미달",
    "action_levels": "무효화·익절가 부재",
    "signature_gate": "검증 시그니처 부재",
    "regime_gate": "현재 레짐 성적 미달",
    "event_window": "실적 이벤트 구간",
    "freshness": "데이터 신선도 미달",
}


def paper_gate_funnel(repo: Any, *, days: int = 7, now: datetime | None = None) -> dict[str, Any]:
    now = now or utc_now()
    since = now - timedelta(days=days)
    rows = repo.list_paper_gate_funnel(since=since, limit=50000)
    stages: list[dict[str, Any]] = [{"id": "evaluated", "label": "평가", "count": len(rows)}]
    survivors = rows
    for gate in GATE_ORDER:
        survivors = [row for row in survivors if bool(_dict(row.get("gates")).get(gate))]
        stages.append({"id": gate, "label": GATE_STAGE_LABELS[gate], "count": len(survivors)})
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
    return {
        "period_days": days,
        "as_of": now.isoformat(),
        "evaluations": len(rows),
        "entered": entered,
        "stages": [*stages, {"id": "entered", "label": "진입", "count": entered}],
        "top_rejection": ({"id": top_gate, "label": GATE_REJECTION_LABELS.get(top_gate, top_gate), "count": rejection_counts[top_gate]} if top_gate else None),
        "rejection_counts": rejection_counts,
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
        "risk_reward": bool(decision_gates.get("risk_reward")),
        "liquidation_safety": bool(decision_gates.get("liquidation_safety")),
        "action_levels": action_levels,
        "signature_gate": bool(signature_gates.get("signature_gate")),
        "regime_gate": bool(signature_gates.get("regime_gate")),
        "event_window": earnings_clear,
        "freshness": freshness,
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
        "rr_ratio": _float(simulation.get("rr_ratio")),
        "gates": gates,
        "entered": entered,
        "rejected_at": rejected_at,
        "rejection_reasons": [gate for gate in GATE_ORDER if not gates[gate]],
        "pill_diagnostics": pill_diagnostics or {},
        "event_pill_ids": event_pill_ids or [],
    }


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
    loss_tags: list[str] = []
    if trade.net_pnl_usdt < 0:
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
            outcome="correct" if trade.net_pnl_usdt > 0 else "wrong" if trade.net_pnl_usdt < 0 else "untested",
            detail=f"paper trade closed by {trade.exit_reason}; net={trade.net_pnl_usdt:.4f} USDT",
            metrics={"net_pnl_usdt": trade.net_pnl_usdt, "net_return_pct": trade.net_return_pct},
        )
    )
    return result


def _record_entry_judgment(repo: Any, trade: PaperTrade) -> None:
    repo.add_judgment(
        JudgmentLedgerEntry(
            judgment_id=trade.judgment_id or f"paper:{trade.id}:entry",
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
) -> dict[str, Any]:
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
    for signature in signatures_from_analysis(analysis):
        if signature.get("direction") != direction.value:
            continue
        key = str(signature.get("key") or "")
        stat = by_key.get(key)
        if not stat:
            continue
        if current_state(repo, key, stat=stat, settings=settings) != "validated":
            continue
        signature_gate = True
        ci_low = _ci_low(stat)
        if ci_low is None or ci_low < float(settings.universe_backtest_min_ci_low_pct):
            continue
        regime_gate = True
        qualified = {"signature_key": key, "signature": signature, "stat": stat, "ci_low": ci_low}
        break
    return {"qualified": qualified, "signature_gate": signature_gate, "regime_gate": regime_gate}


def _qualified_signature(repo: Any, settings: Any, analysis: dict[str, Any], payload: dict[str, Any], direction: Direction) -> dict[str, Any] | None:
    return _dict(_signature_gate_evaluation(repo, settings, analysis, payload, direction).get("qualified")) or None


def _paper_metrics(trades: Iterable[PaperTrade]) -> dict[str, Any]:
    rows = list(trades)
    returns = [trade.net_return_pct for trade in sorted(rows, key=lambda item: item.exit_at or item.updated_at)]
    wins = [trade for trade in rows if trade.net_pnl_usdt > 0]
    gross_profit = sum(max(0.0, trade.net_pnl_usdt) for trade in rows)
    gross_loss = abs(sum(min(0.0, trade.net_pnl_usdt) for trade in rows))
    return _metric_payload(returns, len(wins), gross_profit, gross_loss)


def _user_metrics(trades: Iterable[Any]) -> dict[str, Any]:
    rows = list(trades)
    returns = [float(trade.pnl_percent) for trade in sorted(rows, key=lambda item: item.created_at)]
    wins = [trade for trade in rows if float(trade.pnl_amount) > 0]
    gross_profit = sum(max(0.0, float(trade.pnl_amount)) for trade in rows)
    gross_loss = abs(sum(min(0.0, float(trade.pnl_amount)) for trade in rows))
    return _metric_payload(returns, len(wins), gross_profit, gross_loss)


def _metric_payload(returns: list[float], wins: int, gross_profit: float, gross_loss: float) -> dict[str, Any]:
    equity = peak = 0.0
    mdd = 0.0
    for value in returns:
        equity += value
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)
    count = len(returns)
    return {
        "net_return_pct": round(sum(returns), 4),
        "win_rate_pct": round((wins / count) * 100.0, 2) if count else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        "mdd_pct": round(mdd, 4),
        "trade_count": count,
    }


def _paper_equity_curve(trades: Iterable[PaperTrade]) -> list[dict[str, Any]]:
    return _return_curve(((trade.exit_at or trade.updated_at, float(trade.net_return_pct)) for trade in trades))


def _user_equity_curve(trades: Iterable[Any]) -> list[dict[str, Any]]:
    return _return_curve(((trade.created_at, float(trade.pnl_percent)) for trade in trades))


def _return_curve(points: Iterable[tuple[datetime, float]]) -> list[dict[str, Any]]:
    total = 0.0
    curve: list[dict[str, Any]] = []
    for timestamp, value in sorted(points, key=lambda item: item[0]):
        total += value
        curve.append({"ts": timestamp.isoformat(), "return_pct": round(total, 4)})
    return curve


def _paper_event(kind: str, trade: PaperTrade) -> dict[str, Any]:
    return {
        "kind": kind,
        "trade": trade.model_dump(mode="json"),
    }


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
