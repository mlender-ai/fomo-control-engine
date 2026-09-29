"""그림자 운용 (FOMO LAB ENG-02) — 본 트랙과 같은 신호를 받고 **딱 하나만** 다르게 돈다.

## 무엇인가

- 본 트랙과 **같은 봉 · 같은 분석 · 같은 신호**를 받는다(`run_paper_engine` 이 봉 하나를 끝낸 자리에서 부른다)
- `PaperPolicy` 의 칸 **하나만** 바꾼다 — 나머지는 본 트랙 정책 그대로
- 거래는 `paper_shadow_trades` 에 따로 쌓는다. `paper_trades` · 엔진 상태 · 용량을 **건드리지 않는다**
- 그림자가 실패해도 본 트랙은 계속 돈다(호출하는 쪽이 따로 감싼다)

## 누가 정하나

실험 대장(사전 등록 · 판정 기준 잠금 · 누적 번호 · 판정 · 승인)은 **FOMO LAB** 이 들고 있다. LAB 러너가 돌고 있는 그림자만
`logs/shadows/active.json` 에 적고, 여기서는 그것을 읽어 **실행만** 한다. 여기서도 규칙을 다시 지킨다:

- 동시 최대 {MAX_ACTIVE}개 · 그림자마다 파라미터 1개 · 허용 목록 밖 파라미터 거부 · 같은 파라미터 둘이면 뒤엣것 거부

## 승인 (`/approve`)

그림자는 본 트랙을 바꾸지 않는다 — 아무리 좋아도. 사람이 텔레그램 `/approve N` 을 보내면 `logs/shadows/decisions.jsonl` 에
한 줄이 남고, LAB 이 그 실험이 **승인 대기**인지 확인한 뒤 새 정책 버전 파일(`params/crypto-vN.json`)이 생긴다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, fields, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.paper.liquidation import PositionLossInvariantViolation, with_liquidation
from app.paper.policy import PaperPolicy, apply_exit_decision, evaluate_entry, evaluate_exit, open_trade

logger = logging.getLogger(__name__)

MAX_ACTIVE = 3
# 바꿀 수 있는 칸 — LAB `experiments.ts` `PARAMS` 와 같다. 값의 모양까지 본다.
ALLOWED: dict[str, type] = {"max_entry_cost_r": float, "risk_mode": str, "risk_budget_usdt": float}
RISK_MODES = {"atr_capped", "structural", "nearest_structure"}

SHADOW_DIR = Path(__file__).resolve().parents[3] / "logs" / "shadows"
ACTIVE_FILE = SHADOW_DIR / "active.json"
DECISIONS_FILE = SHADOW_DIR / "decisions.jsonl"


@dataclass(frozen=True)
class Shadow:
    number: int
    param: str
    value: Any


def load_active(path: Path | None = None) -> list[Shadow]:
    """돌 그림자. 규칙을 어기는 줄은 버리고 로그를 남긴다 — 파일이 틀려도 본 트랙은 멀쩡해야 한다."""
    try:
        rows = json.loads((path or ACTIVE_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out: list[Shadow] = []
    seen: set[str] = set()
    for row in sorted((r for r in rows if isinstance(r, dict)), key=lambda r: int(r.get("number") or 0)):
        param = str(row.get("param") or "")
        kind = ALLOWED.get(param)
        # **바꿀 것은 하나** — 칸이 여럿이면(changes 등) 받지 않는다.
        extra = set(row) - {"number", "param", "value", "baseline", "startedAt", "fingerprint"}
        if kind is None or extra or param in seen:
            logger.warning("shadow rejected: %s", row)
            continue
        value = row.get("value")
        if kind is float and (isinstance(value, bool) or not isinstance(value, (int, float))):
            logger.warning("shadow rejected (value): %s", row)
            continue
        if param == "risk_mode" and value not in RISK_MODES:
            logger.warning("shadow rejected (mode): %s", row)
            continue
        if len(out) >= MAX_ACTIVE:
            logger.warning("shadow rejected (limit %s): %s", MAX_ACTIVE, row)
            continue
        seen.add(param)
        out.append(Shadow(number=int(row["number"]), param=param, value=float(value) if kind is float else str(value)))
    return out


def shadow_policy(base: PaperPolicy, shadow: Shadow) -> PaperPolicy:
    """본 트랙 정책에서 칸 **하나만** 바꾼다. 둘 이상 달라지면 거부한다(코드로 강제)."""
    changed = replace(base, **{shadow.param: shadow.value})
    differing = [f.name for f in fields(PaperPolicy) if getattr(changed, f.name) != getattr(base, f.name)]
    if len(differing) > 1:
        raise ValueError(f"shadow #{shadow.number} changes {differing}")
    return changed


def _svc() -> Any:
    from app.paper import service  # 순환 import 를 피한다 — service 가 이 모듈을 부른다

    return service


def run_shadows_for_bar(
    repo: Any,
    settings: Any,
    *,
    symbol: str,
    timeframe: str,
    bar: Any,
    analysis: dict[str, Any],
    gauges: dict[str, Any],
    confluence: dict[str, Any],
    payload: dict[str, Any],
    now: datetime,
    simulation_loader: Any,
    shared: dict[str, Any] | None = None,
    shadows: list[Shadow] | None = None,
) -> list[dict[str, Any]]:
    """이 봉을 그림자마다 한 번 더 판정한다. `shared` 는 본 트랙이 이미 받은 시뮬레이션 · 시그니처(다시 받지 않는다)."""
    active = load_active() if shadows is None else shadows
    if not active:
        return []
    svc = _svc()
    asset_class = str(analysis.get("asset_class") or "crypto")
    base_policy = svc.policy_from_settings(settings, asset_class)
    cache = dict(shared or {})
    events: list[dict[str, Any]] = []
    for shadow in active:
        policy = shadow_policy(base_policy, shadow)
        rows = repo.list_paper_shadow_trades(shadow=shadow.number, symbol=symbol, limit=500)
        open_rows = [t for n, t in rows if t.status == "open" and t.timeframe == timeframe]
        if open_rows:
            trade = open_rows[0]
            if bar.timestamp <= trade.entry_bar_at:
                continue
            position_gauges = svc.build_gauges(
                analysis=analysis,
                confluence=confluence,
                historical_backtest=svc._dict(payload.get("historical_backtest")),
                position={"direction": trade.direction.value},
                now=now,
                timeframe=timeframe,
            )
            pressure = svc._normalize_pressure(svc._dict(position_gauges.get("take_profit")).get("level"))
            trade = with_liquidation(trade, bar=bar)
            streak = int((trade.target_plan or {}).get("shadow_high_streak") or 0)
            decision = evaluate_exit(
                trade,
                bar=bar,
                stance_state=svc._stance_state(confluence),
                take_profit_pressure=pressure,
                prior_high_pressure_streak=streak,
                policy=policy,
                liquidation_price=trade.liquidation_price,
            )
            try:
                updated = apply_exit_decision(trade, decision=decision, bar=bar, policy=policy)
            except PositionLossInvariantViolation as exc:
                # 그림자의 위반은 그림자 기록으로 남긴다 — 본 트랙을 멈추지 않는다.
                events.append({"kind": "shadow_invariant", "shadow": shadow.number, "symbol": symbol, "error": str(exc)})
                continue
            updated = updated.model_copy(update={"target_plan": {**(updated.target_plan or {}), "shadow_high_streak": decision.high_pressure_streak}})
            repo.upsert_paper_shadow_trade(shadow.number, updated)
            if decision.action != "hold":
                events.append({"kind": f"shadow_{decision.action}", "shadow": shadow.number, "symbol": symbol, "reason": decision.reason})
            continue

        direction = svc._stance_direction(confluence)
        if direction is None:
            continue
        # 같은 봉 왕복 금지(본 트랙과 같은 규칙) — 그림자 자기 기록으로 본다.
        if policy.reentry_lock_mode == "same_bar" and any(t.exit_bar_at == bar.timestamp for _, t in rows if t.status == "closed"):
            continue
        open_count = len(repo.list_paper_shadow_trades(shadow=shadow.number, status="open", limit=100))
        if open_count >= int(settings.paper_max_open_positions):
            continue
        key = f"sim:{direction.value}"
        if key not in cache:
            cache[key] = simulation_loader(symbol, timeframe, direction.value, bar.close)
        if "signature_gates" not in cache:
            cache["signature_gates"] = svc._signature_gate_evaluation(repo, settings, analysis, payload, direction, now=now)
        simulation = svc._dict(cache[key])
        signature_gates = cache["signature_gates"]
        action_plan = svc._dict(simulation.get("action_plan"))
        target_plan = svc._paper_target_plan(
            analysis,
            gauges,
            bar=bar,
            direction=direction,
            invalidation_price=svc._price_from(action_plan.get("invalidation") or action_plan.get("engine_invalidation")),
            action_plan=action_plan,
            policy=policy,
        )
        invalidation = svc._float(target_plan.get("execution_invalidation"))
        take_profit = svc._float(target_plan.get("take_profit_1"))
        evidence = svc._direction_evidence(confluence, direction)
        contract = svc._paper_simulation_contract(simulation, target_plan)
        earnings_required = bool(getattr(settings, "paper_earnings_gate_required", False))
        state_of_earnings = svc.earnings_state(analysis, earnings_clear=svc._earnings_clear)
        decision = evaluate_entry(
            stance_state=svc._stance_state(confluence),
            direction=direction,
            evidence_count=len(evidence),
            checklist_passed=int(contract.get("checklist_passed") or 0),
            checklist_total=int(contract.get("checklist_total") or 0),
            rr_ratio=svc._float(target_plan.get("rr_ratio")),
            invalidation_hygiene=contract.get("invalidation_too_close") is not True and target_plan.get("execution_invalidation_too_close") is not True,
            survives_to_invalidation=contract.get("survives_to_invalidation") is True,
            validated_signature=bool(signature_gates["signature_gate"]),
            signature_ci_low_pct=(float(settings.universe_backtest_min_ci_low_pct) if signature_gates["regime_gate"] else None),
            earnings_clear=svc.earnings_gate_passes(state_of_earnings, required=earnings_required),
            data_fresh=svc._data_fresh(bar, timeframe, now),
            confirmed_bar=True,
            policy=policy,
            cost_r=svc._float(target_plan.get("cost_r")),
            stop_atr_multiple=svc._float(target_plan.get("stop_atr_multiple")),
            htf_conflict=contract.get("htf_conflict") is True,
        )
        if not (decision.enter and invalidation is not None and take_profit is not None):
            continue
        trade = open_trade(
            trade_id=uuid4(),
            symbol=symbol,
            timeframe=timeframe,
            asset_class=asset_class,
            direction=direction,
            bar=bar,
            invalidation_price=invalidation,
            take_profit_price=take_profit,
            evidence={"items": evidence, "gates": decision.gates, "shadow": shadow.number, "shadow_param": shadow.param},
            checklist={"passed": contract.get("checklist_passed"), "total": contract.get("checklist_total"), "rr_ratio": target_plan.get("rr_ratio")},
            stance_snapshot=svc._stance_state(confluence),
            signature_snapshot=svc._dict(signature_gates.get("qualified")),
            policy=policy,
            take_profit_2_price=svc._float(target_plan.get("take_profit_2")),
            entry_atr=svc._float(target_plan.get("atr")),
            target_plan=target_plan,
        )
        repo.upsert_paper_shadow_trade(shadow.number, trade)
        events.append({"kind": "shadow_opened", "shadow": shadow.number, "symbol": symbol})
    return events


def shadow_payload(repo: Any) -> dict[str, Any]:
    """`GET /api/paper/shadows` — 도는 그림자 · 거래 전부(열린 것 포함). LAB 업로더가 읽는다."""
    return {
        "active": [{"number": s.number, "param": s.param, "value": s.value} for s in load_active()],
        "trades": [{"shadow": n, **t.model_dump(mode="json")} for n, t in repo.list_paper_shadow_trades(limit=5000)],
    }


def record_decision(action: str, number: int, *, reason: str, chat_id: int | None, at: datetime, path: Path | None = None) -> dict[str, Any]:
    """텔레그램 `/approve` · `/reject` — **기록만** 한다. 반영은 LAB 이 승인 대기인지 확인한 뒤다(자동 승인 없음)."""
    row = {"at": at.isoformat(), "action": action, "number": int(number), "reason": reason[:300], "chat_id": chat_id}
    target = path or DECISIONS_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row
