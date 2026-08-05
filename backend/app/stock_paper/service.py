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
from app.notify.paper_events import track_event

from .analysis import analyze_stock_candidate
from .exit_policy import evaluate_stock_exit, load_exit_parameters
from .policy import evaluate_stock_entry
from .store import StockPaperStore
from .universe import load_universe

TRACK = "stock"
# 청산 경로 부재 구간(WO-FCE-STOCK-EXIT-01 이전)에 진입한 포지션의 제외 사유.
VOID_NO_EXIT_PATH = "void_no_exit_path"


def run_stock_paper_engine(settings: Settings, market_payloads: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    if not settings.stock_paper_engine_enabled:
        # 조용한 스킵 금지 (D3): 비활성도 사유와 함께 관측 가능해야 한다. effective_run=False로
        # 워커가 last_effective_run_at을 갱신하지 않게 하고, skipped 이벤트로 상태를 노출한다.
        return {
            "enabled": False,
            "reason": "disabled",
            "effective_run": False,
            "events": [track_event(TRACK, "skipped", "*", detail={"reason": "disabled"})],
        }
    if not _ready_to_start(settings):
        return {
            "enabled": True,
            "ready_to_start": False,
            "reason": "toss_observation_not_configured",
            "live_orders_enabled": False,
            "effective_run": False,
            "events": [track_event(TRACK, "skipped", "*", detail={"reason": "toss_observation_not_configured"})],
        }
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
    events: list[dict[str, Any]] = []
    reject_gate_counts: dict[str, int] = {}
    # 작업 3-2: 커버리지 레인이 0인 이유를 **추측 없이** 특정하기 위한 단계별 차단 카운터.
    # 진입 0의 원인이 게이트 미달인지 레인 차단인지 잡 미실행인지 실측으로 갈린다.
    coverage_blocks: dict[str, int] = {}

    def _coverage_block(stage: str) -> None:
        coverage_blocks[stage] = coverage_blocks.get(stage, 0) + 1

    exit_parameters = load_exit_parameters()
    # 청산을 진입보다 먼저 처리한다 — 자본과 포지션 슬롯을 먼저 회수해야 진입 한도가 정확해진다.
    exits = _process_exits(broker, store, toss_store, payloads, events)
    for market_name in ("KR", "US"):
        payload = payloads.get(market_name) or {}
        market = Market(market_name)
        observed_at = _timestamp(payload.get("observed_at") or datetime.now(timezone.utc))
        store.update_market_state(market, str(payload.get("market_state") or payload.get("status") or "unknown"), observed_at)
        if payload.get("status") == "observed":
            # 검증 시계는 진입 정책과 청산 정책을 함께 식별한다. 청산 경로가 없던 구간은
            # 출구가 없어 승률·실현 R이 원리적으로 산출 불가였으므로 그 구간의 경과를
            # 그대로 이어 세면 안 된다. 청산 정책이 도입/변경되면 시계가 사유와 함께
            # 재시작된다(validation_clock_restarted).
            store.activate_clock(
                market,
                parameter_version=f"{parameters.version}+{exit_parameters.version}",
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
                reject_gate_counts["max_open_positions"] = reject_gate_counts.get("max_open_positions", 0) + 1
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
                reject_gate_counts["daily_loss_limit"] = reject_gate_counts.get("daily_loss_limit", 0) + 1
                continue
            warnings = tuple(str(item) for item in candidate.get("warning_badges") or [])
            if candidate.get("tradable") is False:
                store.record_entry_rejection(market, symbol, gate="universe_entry_blocked", measured_value=candidate.get("role"), threshold="universe_member")
                rejected += 1
                reject_gate_counts["universe_entry_blocked"] = reject_gate_counts.get("universe_entry_blocked", 0) + 1
                continue
            allowed, universe_reason = universe.entry_allowed(market, symbol, warnings)
            if not allowed:
                store.record_event_if_stale(market, "entry_gate_rejected", symbol=symbol, reason=universe_reason or "universe_entry_blocked")
                store.record_entry_rejection(
                    market, symbol, gate=universe_reason or "universe_entry_blocked", measured_value=warnings, threshold="entry_allowed"
                )
                rejected += 1
                _gate = universe_reason or "universe_entry_blocked"
                reject_gate_counts[_gate] = reject_gate_counts.get(_gate, 0) + 1
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
                reject_gate_counts[reason] = reject_gate_counts.get(reason, 0) + 1
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
                store=store,
                exit_plan=_exit_plan_from_analysis(analysis),
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
                events.append(
                    track_event(
                        TRACK,
                        "opened",
                        symbol,
                        detail={"market": market.value, "entry_mode": "strict_signal", "price": _optional_float(candidate.get("price"))},
                    )
                )
        coverage_candidates = [item for item in payload.get("coverage_candidates") or [] if isinstance(item, dict)]
        coverage_slots_used = store.mode_position_count(market, "coverage") + store.mode_active_order_count(market, "coverage")
        market_track = next((item for item in store.dashboard()["tracks"] if item["market"] == market.value), None)
        loss_gate_open = not (
            market_track and market_track.get("engine_return_pct") is not None and float(market_track["engine_return_pct"]) <= -parameters.daily_loss_limit_pct
        )
        if not parameters.coverage_entry_enabled:
            _coverage_block("lane_disabled")
        elif not loss_gate_open:
            _coverage_block("daily_loss_limit")
        elif coverage_slots_used >= parameters.coverage_target_open_positions:
            _coverage_block("coverage_slots_full")
        if parameters.coverage_entry_enabled and loss_gate_open and coverage_slots_used < parameters.coverage_target_open_positions:
            scored: list[tuple[int, int, dict[str, Any], dict[str, Any], Any]] = []
            for candidate in coverage_candidates:
                symbol = str(candidate.get("symbol") or "").upper()
                if not symbol or store.position_quantity(market, symbol) > 0 or store.has_active_order(market, symbol):
                    _coverage_block("already_held_or_ordered")
                    continue
                warnings = tuple(str(item) for item in candidate.get("warning_badges") or [])
                allowed, _ = universe.entry_allowed(market, symbol, warnings)
                if candidate.get("tradable") is False:
                    _coverage_block("not_tradable")
                    continue
                if not allowed:
                    _coverage_block("universe_entry_blocked")
                    continue
                if not _is_fresh(candidate.get("observed_at")):
                    _coverage_block("observation_stale")
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
                    _coverage_block("max_attempts_per_cycle")
                    break
                if (
                    store.mode_position_count(market, "coverage") + store.mode_active_order_count(market, "coverage")
                    >= parameters.coverage_target_open_positions
                ):
                    _coverage_block("coverage_target_reached")
                    break
                if sum(1 for item in store.dashboard()["positions"] if item["market"] == market.value) >= parameters.max_open_positions:
                    _coverage_block("max_open_positions")
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
                    store=store,
                    exit_plan=_exit_plan_from_analysis(analysis),
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
                    events.append(
                        track_event(
                            TRACK,
                            "opened",
                            symbol,
                            detail={"market": market.value, "entry_mode": "coverage", "price": _optional_float(candidate.get("price"))},
                        )
                    )
    # 거부는 개별 발송하면 스팸(전 종목 거부)이므로 1건의 집계 이벤트로 요약한다 (작업3).
    if rejected > 0:
        top_gate = max(reject_gate_counts.items(), key=lambda kv: kv[1])[0] if reject_gate_counts else None
        events.append(
            track_event(
                TRACK,
                "rejected_summary",
                "*",
                detail={
                    "evaluated": evaluated,
                    "rejected": rejected,
                    "top_reject_gate": top_gate,
                    "gate_counts": reject_gate_counts,
                    "strict_entered": strict_entered,
                    "coverage_entered": coverage_entered,
                },
            )
        )
    return {
        "enabled": True,
        "evaluated": evaluated,
        "rejected": rejected,
        "strict_entered": strict_entered,
        "coverage_evaluated": coverage_evaluated,
        "coverage_attempted": coverage_attempted,
        "coverage_entered": coverage_entered,
        "pending_processed": processed,
        # 작업 3-2: 커버리지 진입 0의 원인을 단계별로 드러낸다(진단 표면·리포트에서 조회).
        "coverage_blocks": coverage_blocks,
        "exits": exits,
        "exit_parameter_version": exit_parameters.version,
        "universe_version": universe.version,
        "parameter_version": parameters.version,
        "live_orders_enabled": False,
        # 정직한 effective run (WO-FCE-TOSS-US-STALL-01): 과거엔 True 하드코딩이라
        # 양 시장이 closed 이고 평가가 0건이어도 last_effective_run_at 이 10초마다 갱신됐다.
        # 그래서 20.8시간 수집 정지 동안에도 "실제 평가 중"으로 보였다 — 바로 이 지표가 잡으라고
        # 만든 고장을 이 지표가 가려준 셈이다. 한 시장이라도 실제 관측(observed)했을 때만 True.
        # 호출부가 페이로드를 주지 않으면(직접 호출·테스트) 판단 근거가 없으므로 기존 동작 유지.
        "effective_run": (
            any(
                str((payloads.get(name) or {}).get("status")) == "observed" or str((payloads.get(name) or {}).get("market_state")) == "open"
                for name in ("KR", "US")
            )
            if payloads
            else True
        ),
        "top_reject_gate": (max(reject_gate_counts.items(), key=lambda kv: kv[1])[0] if reject_gate_counts else None),
        "events": events,
    }


def _process_exits(
    broker: PaperBroker,
    store: StockPaperStore,
    toss_store: TossStockStore,
    payloads: dict[str, dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, int]:
    """보유 포지션의 청산을 판정하고 **매도 주문을 생성**한다.

    이 함수가 WO-FCE-STOCK-EXIT-01 이전에 없던 바로 그 코드다. 이전에는 무효화선을 계산해
    진입 게이트에 쓰면서도 그 선에 도달했을 때 아무 일도 일어나지 않았다.

    체결 가격은 여기서 정하지 않는다 — `broker.place()` → `execute_order()`가 관측 데이터에서
    파생한다. 장외 신호는 `session_closed`로 대기했다가 다음 세션 **시가**로 체결되므로
    무효화 가격 마법 체결이 구조적으로 불가능하다(C1).
    """
    parameters = load_exit_parameters()
    counters = {"evaluated": 0, "triggered": 0, "filled": 0, "unfilled": 0}
    for row in store.open_positions():
        market = Market(str(row["market"]))
        symbol = str(row["symbol"]).upper()
        payload = payloads.get(market.value) or {}
        plan = row.get("exit_plan") if isinstance(row.get("exit_plan"), dict) else {}
        observation = _observation(toss_store, market, symbol, payload.get("market_state") == "open")
        warnings = observation.warnings if observation is not None else ()
        counters["evaluated"] += 1
        decision = evaluate_stock_exit(
            plan=plan,
            close_price=_optional_float(row.get("current_price")),
            average_price=_optional_float(row.get("average_price")),
            holding_days=_holding_days(row.get("opened_at")),
            warnings=tuple(str(item) for item in warnings),
            realized_levels=tuple(int(item) for item in (plan.get("realized_levels") or [])),
            parameters=parameters,
        )
        if not decision.should_exit:
            continue
        counters["triggered"] += 1
        held = int(row["quantity"])
        quantity = held if decision.fraction >= 1.0 else max(1, math.floor(held * decision.fraction))
        quantity = min(quantity, held)
        order = StockOrder(
            symbol=symbol,
            market=market,
            currency=Currency.KRW if market == Market.KR else Currency.USD,
            side=Side.SELL,
            quantity=quantity,
            signal_at=datetime.now(timezone.utc),
            # signal_price는 기록용이다. 체결가로 쓰이지 않는다 — 그게 갭 정직성의 핵심이다.
            signal_price=_optional_float(row.get("current_price")),
            entry_mode=str(row.get("entry_mode") or "strict_signal"),
            evidence={
                "exit_trigger": decision.trigger,
                "exit_level": decision.level,
                "exit_detail": decision.detail,
                "exit_parameter_version": parameters.version,
                "plan": plan,
            },
        )
        execution = broker.place(order, observation)
        store.record_event(
            market,
            "exit_signal",
            symbol=symbol,
            order_id=order.id,
            reason=decision.trigger,
            payload={"decision": decision.detail, "requested_quantity": quantity, "level": decision.level},
        )
        if execution.fill is not None:
            counters["filled"] += 1
            if decision.trigger == "take_profit" and decision.level is not None:
                store.record_realized_level(market, symbol, decision.level)
            events.append(
                track_event(
                    TRACK,
                    "closed",
                    symbol,
                    detail={
                        "market": market.value,
                        "trigger": decision.trigger,
                        "quantity": execution.fill.quantity,
                        "price": execution.fill.price,
                        "entry_mode": order.entry_mode,
                        "excluded_from_stats": bool(row.get("excluded_from_stats")),
                    },
                )
            )
        else:
            # 하한가 잠김·VI·거래정지·장외는 미체결이며 사유가 원장에 남는다.
            # "빠져나올 수 없었다"를 정직하게 기록하는 것이 성과를 좋게 보이게 하는 것보다 중요하다.
            counters["unfilled"] += 1
    return counters


def _holding_days(opened_at: Any) -> int:
    if not opened_at:
        return 0
    try:
        opened = _timestamp(opened_at)
    except (TypeError, ValueError):
        return 0
    return max(0, (datetime.now(timezone.utc) - opened).days)


def _exit_plan_from_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    """진입 시점의 무효화선·TP 목표를 청산 기준으로 고정한다."""
    invalidation = analysis.get("invalidation") if isinstance(analysis.get("invalidation"), dict) else {}
    targets = [item for item in (analysis.get("take_profit_targets") or []) if isinstance(item, dict)]
    return {
        "invalidation_price": invalidation.get("price"),
        "targets": [{"level": index + 1, "price": item.get("price"), "source": item.get("source")} for index, item in enumerate(targets)],
        "target_source": analysis.get("target_source"),
        "realized_levels": [],
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
    store: StockPaperStore | None = None,
    exit_plan: dict[str, Any] | None = None,
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
    if execution.fill is not None and store is not None and exit_plan is not None:
        # 진입 근거로 삼은 무효화선·목표를 청산 기준으로 고정한다. 매 주기 재계산하면
        # 기준선이 움직여 손절이 사후적으로 정당화된다.
        store.set_exit_plan(market, symbol, exit_plan, execution.fill.filled_at)
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
        # WO-FCE-OBSERVATION-INTEGRITY-01 1-3: 코드로 못 고치는 손실은 화면에 상시 올린다.
        # 조용히 유실로만 세면 영원히 안 고쳐진다.
        "manual_actions": _manual_actions(settings),
    }


def _manual_actions(settings: Settings) -> list[dict[str, Any]]:
    """수동 조치 필요 항목(호스트 절전 등). 조회 실패는 빈 목록 — 화면을 죽이지 않는다."""
    try:
        from app.db.maintenance import sqlite_path
        from app.db.sqlite_utils import connect_sqlite
        from app.worker import observation

        path = sqlite_path(settings.database_url)
        if path is None or not path.exists():
            return []
        with connect_sqlite(str(path)) as connection:
            rows = [dict(row) for row in connection.execute("SELECT * FROM observation_coverage")]
        return observation.manual_action_items(rows)
    except Exception:  # 관측 표면이 화면 장애 원인이 되면 안 된다
        return []


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
