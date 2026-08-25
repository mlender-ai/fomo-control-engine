"""WO-FCE-WHALE-FOLLOW-01 Phase 6-2 — 고래 추종 페이퍼 트랙.

## 무엇을 재는가 — 승격 심사와 다른 질문이다

| | 재는 것 |
| --- | --- |
| 승격 심사(`onchain/service.py`) | **고래 자신의 승률** |
| 이 트랙 | **이 고래를 신호로 삼고 우리 사이징·출구로 거래하면 버는가** |

고래 승률이 55% 가 아니어도 우리 손익비가 붙으면 벌 수 있고, 승률 70% 고래를 따라가도
지연·비용 때문에 잃을 수 있다. 후자는 이 트랙 없이는 영원히 측정되지 않는다.

그래서 이 트랙의 성과를 **승격 근거로 쓰지 않는다**(C11).

## 변수를 하나만 바꾼다

진입 트리거만 다르고 나머지는 기존 기계를 **수정 없이** 재사용한다(C5). `paper/policy.py`
diff 0줄이며, 이 모듈은 그 함수들을 호출만 한다.

| 요소 | 출처 |
| --- | --- |
| 방향 | **고래 체결**(이 트랙의 유일한 신규 입력) |
| 봉·무효화선·시뮬레이션 | `paper/service.py` 헬퍼 — 고래 방향으로 조회 |
| 사이징 | `policy.plan_position_size` (리스크 기준) |
| 재진입 잠금 | `service._reentry_block_reason` |
| 출구 | `policy.evaluate_exit` · `apply_exit_decision` |

## 적용 게이트 범위 — 결과 확인 전 고정

| 게이트 | 적용 | 근거 |
| --- | --- | --- |
| `freshness` | 유지 | 안전. 낡은 봉으로 진입하면 트랙 무관하게 잘못이다 |
| `liquidation_safety` | 유지 | 안전 |
| `action_levels`(무효화·목표 존재) | 유지 | 사이징이 스톱 거리를 요구한다 |
| `invalidation_hygiene` | 유지 | 스톱이 진입가에 붙으면 즉시 털린다 |
| `reentry_lock` | 유지 | 같은 봉 왕복 차단 |
| `confirmed_stance` · `not_transitioning` | **제외** | 고래 신호가 방향 판단을 대체한다는 것이 이 트랙의 가설이다 |
| `signature_gate` · `regime_gate` | **제외** | 같음 — 시그니처 승격 여부는 고래 신호와 무관하다 |
| `evidence` · `checklist` · `risk_reward` | **제외** | 방향 근거의 품질 게이트다. 근거는 고래 체결이다 |
| `event_window`(실적) | 유지 | 안전 |

방향 판단 게이트를 남기면 고래 신호의 기여를 측정할 수 없고, 안전 게이트를 빼면 품질
통제가 사라진다.

## 무효화선이 없으면 진입하지 않는다

고래 체결에는 스톱이 없다. 구조 레벨에서 산출하되 **산출 불가면 진입하지 않는다** —
사이징이 스톱 거리를 요구하므로 무효화선 없는 진입은 사이징 불가와 같다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.db.models import Direction, MarketCandle, PaperTrade, utc_now
from app.onchain.follow_eligibility import QUALIFICATION_OBSERVATION
from app.paper import policy as paper_policy
from app.paper import service as paper_service

# 진입 트리거가 되는 체결. 감액·청산은 진입 신호가 아니다 — 별도 판단이다(6-2 항목 2).
ENTRY_EVENTS = frozenset({"open", "increase", "flip"})
# 이 시간보다 오래된 고래 체결은 추종하지 않는다. 지연이 커지면 신호가 아니라 소음이다.
MAX_SIGNAL_AGE_MINUTES = 240
# 한 실행에서 여는 최대 건수. 크립토 트랙 실행을 밀어내지 않는다(C9).
MAX_ENTRIES_PER_RUN = 2
# 한 실행에서 **분석을 조회하는** 최대 건수. 진입 상한과 별개다.
#
# 진입 상한만 두면 신호가 전부 거부될 때 분석 조회가 무제한이 된다. 심볼당 ~30초이므로
# 그것이 곧 잡 타임아웃이고, 실제로 DISCOVERY-UNBLOCK-01 이 같은 기전으로 라이브 장애를
# 냈다(유니버스 3→15 확대 × 30초). 조회 자체를 세서 막는다.
MAX_EVALUATIONS_PER_RUN = 3
# 출구 판정도 같은 이유로 상한을 둔다. 열린 포지션 수에 비례해 커지면 안 된다.
MAX_EXIT_EVALUATIONS_PER_RUN = 6
FOLLOW_TIMEFRAME = "4h"


def _direction(side: str) -> Direction | None:
    value = str(side or "").lower()
    if value == "long":
        return Direction.long
    if value == "short":
        return Direction.short
    return None


def entry_signals(events: list[Any], *, eligible: dict[str, str], now: datetime, max_age_minutes: int = MAX_SIGNAL_AGE_MINUTES) -> list[dict[str, Any]]:
    """추종 대상 지갑의 증액 체결만 남긴다. 지갑당 심볼당 최신 1건.

    `eligible` 은 주소 → 자격 종류(`observation`/`promotion`)다. 자격이 없는 지갑은 여기서
    떨어지고, 그 사유는 `follow_eligibility` 가 따로 낸다(C10).
    """
    cutoff = now - timedelta(minutes=max(1, int(max_age_minutes)))
    keyed: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        address = str(getattr(event, "wallet_address", "") or "").lower()
        qualification = eligible.get(address)
        if qualification is None:
            continue
        if str(getattr(event, "event", "") or "").lower() not in ENTRY_EVENTS:
            continue
        direction = _direction(str(getattr(event, "side", "") or ""))
        event_at = getattr(event, "event_at", None)
        symbol = str(getattr(event, "symbol", "") or "").upper()
        if direction is None or event_at is None or not symbol or event_at < cutoff:
            continue
        key = (address, symbol)
        existing = keyed.get(key)
        if existing is None or event_at > existing["event_at"]:
            keyed[key] = {
                "address": address,
                "qualification": qualification,
                "symbol": symbol,
                "direction": direction,
                "event_at": event_at,
                "event": str(getattr(event, "event", "")),
                "size_usd": float(getattr(event, "size_usd", 0.0) or 0.0),
                "wallet_label": str(getattr(event, "wallet_label", "") or ""),
                "signal_age_seconds": max(0.0, (now - event_at).total_seconds()),
            }
    return sorted(keyed.values(), key=lambda item: float(item["size_usd"]), reverse=True)


def _safety_gates(
    *,
    bar: MarketCandle,
    timeframe: str,
    now: datetime,
    invalidation: float | None,
    take_profit: float | None,
    simulation: dict[str, Any],
    target_plan: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, bool]:
    """안전 게이트만. 방향 판단 게이트는 여기 없다 — 그것이 이 트랙의 설계다."""
    return {
        "freshness": paper_service._data_fresh(bar, timeframe, now),
        "action_levels": invalidation is not None and take_profit is not None,
        "invalidation_hygiene": simulation.get("invalidation_too_close") is not True and target_plan.get("execution_invalidation_too_close") is not True,
        "liquidation_safety": simulation.get("survives_to_invalidation") is True,
        "event_window": paper_service._earnings_clear(analysis),
    }


def evaluate_signal(
    repo: Any,
    settings: Any,
    signal: dict[str, Any],
    *,
    analysis_loader: paper_service.AnalysisLoader,
    simulation_loader: paper_service.SimulationLoader,
    now: datetime,
    timeframe: str = FOLLOW_TIMEFRAME,
) -> dict[str, Any]:
    """한 신호를 진입 후보로 판정한다. 거부도 사유를 남긴다(C10)."""
    symbol = str(signal["symbol"])
    direction: Direction = signal["direction"]
    rejected = {"signal": signal, "opened": False}

    payload = analysis_loader(symbol, timeframe)
    analysis = paper_service._dict(payload.get("analysis"))
    gauges = paper_service._dict(payload.get("gauges"))
    bar = paper_service._confirmed_bar(analysis, gauges)
    if bar is None:
        return {**rejected, "reason": "확정 봉 없음 — 진입 기준 가격을 정할 수 없다"}

    asset_class = str(analysis.get("asset_class") or "crypto")
    policy = paper_service.policy_from_settings(settings, asset_class)

    # 고래 **방향으로** 시뮬레이션을 조회한다. 엔진 스탠스가 반대여도 그것을 묻지 않는다.
    simulation = simulation_loader(symbol, timeframe, direction.value, bar.close)
    action_plan = paper_service._dict(simulation.get("action_plan"))
    invalidation = paper_service._price_from(action_plan.get("invalidation") or action_plan.get("engine_invalidation"))
    target_plan = paper_service._paper_target_plan(
        analysis,
        gauges,
        bar=bar,
        direction=direction,
        invalidation_price=invalidation,
        action_plan=action_plan,
        policy=policy,
    )
    invalidation = paper_service._float(target_plan.get("execution_invalidation"))
    take_profit = paper_service._float(target_plan.get("take_profit_1"))
    simulation = paper_service._paper_simulation_contract(simulation, target_plan)

    if invalidation is None:
        # 6-2 항목 4 — 무효화선 없으면 진입하지 않는다. 사이징이 스톱 거리를 요구한다.
        return {**rejected, "reason": "무효화선 산출 불가 — 사이징이 스톱 거리를 요구하므로 진입하지 않는다"}

    gates = _safety_gates(
        bar=bar,
        timeframe=timeframe,
        now=now,
        invalidation=invalidation,
        take_profit=take_profit,
        simulation=simulation,
        target_plan=target_plan,
        analysis=analysis,
    )
    failed = [name for name, passed in gates.items() if not passed]
    if failed:
        return {**rejected, "reason": f"안전 게이트 미통과: {', '.join(failed)}", "gates": gates}

    lock = paper_service._reentry_block_reason(repo, symbol=symbol, timeframe=timeframe, bar=bar, direction=direction, policy=policy)
    if lock is not None:
        return {**rejected, "reason": f"재진입 잠금: {lock}", "gates": gates}

    if any(trade.symbol == symbol and trade.timeframe == timeframe for trade in repo.list_whale_follow_trades(status="open", limit=200)):
        return {**rejected, "reason": "이 심볼에 추종 트랙 포지션이 이미 열려 있다", "gates": gates}

    return {
        "signal": signal,
        "opened": True,
        "reason": "안전 게이트 통과 · 무효화선 확보",
        "gates": gates,
        "bar": bar,
        "analysis": analysis,
        "asset_class": asset_class,
        "policy": policy,
        "invalidation": invalidation,
        "take_profit": take_profit,
        "take_profit_2": paper_service._float(target_plan.get("take_profit_2")),
        "target_plan": target_plan,
        "simulation": simulation,
        "entry_atr": paper_service._float(target_plan.get("atr")),
    }


def open_follow_trade(candidate: dict[str, Any], *, now: datetime) -> PaperTrade:
    """`policy.open_trade` 를 그대로 호출한다 — 신규 사이징 구현 0건(C5).

    자격 종류·고래 식별·체결→진입 지연을 `entry_evidence` 에 박는다. 관찰 자격 진입은
    **그 사실이 원장에 남아야** 승격 자격 진입과 섞이지 않는다(C3·C8).
    """
    signal = candidate["signal"]
    bar: MarketCandle = candidate["bar"]
    # 체결→진입 지연. 이 지연이 추종 성과를 좌우한다(6-2 항목 6).
    latency_seconds = max(0.0, (bar.timestamp - signal["event_at"]).total_seconds())
    qualification = str(signal["qualification"])
    observation = qualification == QUALIFICATION_OBSERVATION
    return paper_policy.open_trade(
        trade_id=uuid5(NAMESPACE_URL, f"fce:whale-follow:{signal['address']}:{signal['symbol']}:{bar.timestamp.isoformat()}:{signal['event_at'].isoformat()}"),
        symbol=str(signal["symbol"]),
        timeframe=str(candidate.get("timeframe") or FOLLOW_TIMEFRAME),
        asset_class=str(candidate["asset_class"]),
        direction=signal["direction"],
        bar=bar,
        invalidation_price=float(candidate["invalidation"]),
        take_profit_price=float(candidate["take_profit"]),
        take_profit_2_price=candidate.get("take_profit_2"),
        entry_atr=candidate.get("entry_atr"),
        target_plan=candidate.get("target_plan"),
        policy=candidate["policy"],
        evidence={
            "entry_mode": "whale_follow",
            "track": "whale_follow",
            "qualification": qualification,
            # C8 — 미검증임을 원장에 명시한다. 화면·알림이 이 값을 그대로 읽는다.
            "unverified": observation,
            "label": "미검증 관찰 자격 진입" if observation else "승격 고래 추종 진입",
            "whale_address": signal["address"],
            "whale_label": signal.get("wallet_label"),
            "whale_event": signal.get("event"),
            "whale_size_usd": signal.get("size_usd"),
            "whale_event_at": signal["event_at"].isoformat(),
            "entry_bar_at": bar.timestamp.isoformat(),
            "signal_to_entry_seconds": latency_seconds,
            "participant_type": signal.get("participant_type"),
            "participant_confidence": signal.get("participant_confidence"),
            "unclassified_flag": signal.get("unclassified_flag"),
            "sample_size": signal.get("sample_size"),
            "ci_low": signal.get("ci_low"),
            "gates": candidate.get("gates"),
            "gate_scope": "안전 게이트만 적용 · 방향 판단 게이트 제외(고래 신호가 방향 판단을 대체한다는 가설)",
            "not_promotion": "관찰 자격은 승격이 아니다. 이 트랙 성과를 승격 근거로 쓰지 않는다(C11).",
            "note": "실주문이 아닌 엔진 가상 거래 기록",
            "opened_at": now.isoformat(),
        },
        checklist={
            "entry_mode": "whale_follow",
            "items": candidate.get("simulation", {}).get("checklist") or [],
            "passed": candidate.get("simulation", {}).get("checklist_passed"),
            "total": candidate.get("simulation", {}).get("checklist_total"),
            "note": "체크리스트는 기록만 한다 — 이 트랙의 진입 조건이 아니다",
        },
        stance_snapshot={"source": "whale_follow", "note": "스탠스는 진입 조건이 아니다 — 방향은 고래 체결에서 온다"},
        signature_snapshot={"source": "whale_follow", "signature_gate": "제외(설계)"},
    )


def run_entries(
    repo: Any,
    settings: Any,
    *,
    eligible: dict[str, str],
    analysis_loader: paper_service.AnalysisLoader,
    simulation_loader: paper_service.SimulationLoader,
    signal_context: dict[str, dict[str, Any]] | None = None,
    now: datetime | None = None,
    max_entries: int = MAX_ENTRIES_PER_RUN,
    max_evaluations: int = MAX_EVALUATIONS_PER_RUN,
    event_limit: int = 200,
) -> dict[str, Any]:
    """추종 진입 1회 실행. 거부 사유를 전부 돌려준다(C10)."""
    moment = now or utc_now()
    if not eligible:
        return {"entries": [], "opened": 0, "rejected": [], "signals": 0, "reason": "관찰·승격 자격 지갑이 없다"}

    events: list[Any] = []
    for address in eligible:
        events.extend(repo.list_whale_events(wallet_address=address, limit=event_limit))
    signals = entry_signals(events, eligible=eligible, now=moment)
    for signal in signals:
        signal.update(signal_context.get(str(signal["address"]), {}) if signal_context else {})

    opened: list[PaperTrade] = []
    rejected: list[dict[str, Any]] = []
    evaluations = 0
    for signal in signals:
        if len(opened) >= max(0, int(max_entries)):
            rejected.append({"address": signal["address"], "symbol": signal["symbol"], "reason": f"진입 상한 {max_entries}건 도달 — 다음 실행으로 넘긴다"})
            continue
        if evaluations >= max(0, int(max_evaluations)):
            # 침묵하지 않는다 — 잘렸다는 사실을 남긴다(C10). 이것이 없으면 "신호가 없었다"로 읽힌다.
            rejected.append(
                {
                    "address": signal["address"],
                    "symbol": signal["symbol"],
                    "reason": f"분석 조회 상한 {max_evaluations}건 도달 — 평가하지 않고 다음 실행으로 넘긴다(C9)",
                }
            )
            continue
        evaluations += 1
        try:
            candidate = evaluate_signal(repo, settings, signal, analysis_loader=analysis_loader, simulation_loader=simulation_loader, now=moment)
        except Exception as exc:  # 분석 조회 실패가 트랙 전체를 멈추면 안 된다
            rejected.append({"address": signal["address"], "symbol": signal["symbol"], "reason": f"{type(exc).__name__}: {exc}"})
            continue
        if not candidate.get("opened"):
            rejected.append({"address": signal["address"], "symbol": signal["symbol"], "reason": candidate.get("reason"), "gates": candidate.get("gates")})
            continue
        trade = open_follow_trade({**candidate, "timeframe": FOLLOW_TIMEFRAME}, now=moment)
        repo.upsert_whale_follow_trade(trade)
        opened.append(trade)

    return {
        "entries": [{"id": str(trade.id), "symbol": trade.symbol, "direction": trade.direction.value} for trade in opened],
        "opened": len(opened),
        "rejected": rejected,
        "signals": len(signals),
        "evaluated": evaluations,
        "evaluation_cap": int(max_evaluations),
        "eligible_wallets": len(eligible),
        "track": "whale_follow",
        "ledger": "whale_follow_trades (paper_trades 와 분리 · C3)",
    }


def run_exits(
    repo: Any,
    settings: Any,
    *,
    analysis_loader: paper_service.AnalysisLoader,
    now: datetime | None = None,
    timeframe: str = FOLLOW_TIMEFRAME,
) -> dict[str, Any]:
    """열린 추종 포지션의 출구를 판정한다. `policy.evaluate_exit` 를 그대로 쓴다(C5).

    진입만 되고 청산이 없으면 표본은 0이다(6-5 항목 4). 그래서 출구는 진입과 같은 잡에서
    돈다 — 한쪽만 도는 상태가 생기지 않게.

    **반대 스탠스 청산은 이 트랙에서 작동하지 않는다.** 스탠스를 진입 조건에서 뺐으므로
    청산 조건에도 넣지 않는다 — 그러면 방향 판단이 뒷문으로 들어온다. 빈 스탠스를 넘겨
    `_opposite_confirmed_flip` 가 발화하지 않게 한다. 손절·익절·시간 만료는 그대로다.
    """
    moment = now or utc_now()
    open_trades = repo.list_whale_follow_trades(status="open", limit=200)
    closed: list[dict[str, Any]] = []
    held = 0
    deferred = 0
    errors: list[dict[str, Any]] = []
    for index, trade in enumerate(open_trades):
        if index >= MAX_EXIT_EVALUATIONS_PER_RUN:
            deferred += 1
            continue
        try:
            payload = analysis_loader(trade.symbol, timeframe)
            analysis = paper_service._dict(payload.get("analysis"))
            gauges = paper_service._dict(payload.get("gauges"))
            bar = paper_service._confirmed_bar(analysis, gauges)
            if bar is None or bar.timestamp <= trade.entry_bar_at:
                held += 1
                continue
            policy = paper_service.policy_from_settings(settings, str(analysis.get("asset_class") or "crypto"))
            decision = paper_policy.evaluate_exit(
                trade,
                bar=bar,
                stance_state={},
                take_profit_pressure=None,
                prior_high_pressure_streak=0,
                policy=policy,
            )
            updated = paper_policy.apply_exit_decision(trade, decision=decision, bar=bar, policy=policy)
            repo.upsert_whale_follow_trade(updated)
            if decision.action == "hold":
                held += 1
            else:
                closed.append(
                    {
                        "id": str(updated.id),
                        "symbol": updated.symbol,
                        "action": decision.action,
                        "reason": decision.reason,
                        "status": updated.status,
                        "net_pnl_usdt": updated.net_pnl_usdt,
                        "qualification": (updated.entry_evidence or {}).get("qualification"),
                    }
                )
        except Exception as exc:
            errors.append({"id": str(trade.id), "symbol": trade.symbol, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "open": len(open_trades),
        "held": held,
        # 상한에 걸려 이번 실행에서 판정하지 않은 건수. 0 이 아니면 다음 실행에서 처리된다.
        "deferred": deferred,
        "evaluation_cap": MAX_EXIT_EVALUATIONS_PER_RUN,
        "closed": closed,
        "closed_count": len(closed),
        "errors": errors,
        "as_of": moment.isoformat(),
    }


def performance_by_qualification(trades: list[PaperTrade]) -> dict[str, Any]:
    """자격 종류별 분리 집계 (6-4 항목 3). 관찰 자격 건과 승격 자격 건을 섞지 않는다.

    R 은 계획 리스크 기준이다 — `planned_risk_usdt` 가 있으면 그것으로 나눈다. 없으면
    R 을 만들지 않는다(추정치를 적지 않는다).
    """
    buckets: dict[str, dict[str, Any]] = {}
    for trade in trades:
        evidence = trade.entry_evidence or {}
        key = str(evidence.get("qualification") or "unknown")
        bucket = buckets.setdefault(
            key,
            {"qualification": key, "entries": 0, "closed": 0, "wins": 0, "net_usdt": 0.0, "net_r": 0.0, "r_samples": 0, "latency_seconds": []},
        )
        bucket["entries"] += 1
        latency = evidence.get("signal_to_entry_seconds")
        if isinstance(latency, (int, float)):
            bucket["latency_seconds"].append(float(latency))
        if trade.status != "closed":
            continue
        bucket["closed"] += 1
        bucket["net_usdt"] += float(trade.net_pnl_usdt or 0.0)
        if float(trade.net_pnl_usdt or 0.0) > 0:
            bucket["wins"] += 1
        planned_risk = float(((trade.target_plan or {}).get("sizing") or {}).get("planned_risk_usdt") or 0.0)
        if planned_risk > 0:
            bucket["net_r"] += float(trade.net_pnl_usdt or 0.0) / planned_risk
            bucket["r_samples"] += 1

    for bucket in buckets.values():
        latencies = sorted(bucket.pop("latency_seconds"))
        bucket["latency"] = (
            {
                "count": len(latencies),
                "min_seconds": round(latencies[0], 1),
                "median_seconds": round(latencies[len(latencies) // 2], 1),
                "max_seconds": round(latencies[-1], 1),
            }
            if latencies
            else {"count": 0, "note": "진입 0건 — 지연을 측정할 대상이 없다"}
        )
        bucket["net_usdt"] = round(bucket["net_usdt"], 4)
        bucket["net_r"] = round(bucket["net_r"], 4) if bucket["r_samples"] else None
        bucket["win_pct"] = round(bucket["wins"] / bucket["closed"] * 100, 1) if bucket["closed"] else None
        # C11 — 이 수치는 승격 근거가 아니다. 집계에 문구를 붙여 다닌다.
        bucket["not_promotion_evidence"] = "추종 트랙 성과는 승격(28일·N>=30·CI 하한 55%) 근거로 쓰지 않는다"
    return {
        "buckets": buckets,
        "note": "관찰 자격 건과 승격 자격 건은 분리 집계한다 — 문턱이 다르므로 섞으면 둘 다 해석 불가가 된다",
    }
