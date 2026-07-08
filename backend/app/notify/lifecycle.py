"""포지션 라이프사이클 알림 (WO-FCE-44 Part B).

진입·종료·판정 전이·스탠스 반전·근거 부족·정기 펄스.
판단 문형 가드: 매매를 지시·권유하는 표현 금지 — 관측 사실만 서술한다
("관측 양호: 상방 근거 5/7 · 반대 2" 형식). 금지 목록은 tests/test_lifecycle_alerts.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any

from app.core.config import Settings
from app.notify.rules import AlertCandidate, RULE_LABELS

VERDICT_LABELS = {
    "holding": "관찰 유지",
    "weakening": "근거 약화",
    "danger": "위험 확인 필요",
    "standby": "판단 유보",
    "unknown": "미판정",
}

# 심각도 배정: 위험 방향 전이는 warn, 완화 방향은 info (비대칭 — 나쁜 소식이 더 급하다).
VERDICT_RANK = {"holding": 0, "standby": 0, "weakening": 1, "danger": 2}


def opened_candidate(context: dict[str, Any]) -> AlertCandidate:
    """position_opened — 진입 감지 + 즉시 초기 판정(1줄 판정 엔진 소비) + 시나리오 매칭."""
    position = _position(context)
    state = _state(context)
    one_liners = _one_liners(context)
    scenario_line = _scenario_line(position)
    lines = [
        _headline(position, "🟢", RULE_LABELS["position_opened"]),
        f"진입 {_price(position.get('entry_price'))} · 수량 {_qty(position.get('quantity'))} · {_time(position.get('opened_at') or state.get('as_of'))} 기준",
        _observation_line(position, one_liners),
        _top_module_lines(one_liners, limit=3),
        scenario_line,
        "→ read-only 감지 통보입니다. 판단은 사용자 몫입니다.",
    ]
    return _candidate(
        "position_opened",
        "action",
        position,
        state,
        identity=str(position.get("opened_at") or position.get("id") or "opened"),
        title=RULE_LABELS["position_opened"],
        message="\n".join(line for line in lines if line),
        payload={
            "kind": "lifecycle",
            "one_liners_summary": one_liners.get("summary"),
            "overall_stance": one_liners.get("overall_stance"),
            "scenario_matched": bool(position.get("scenario_id")),
        },
    )


def closed_candidate(position: dict[str, Any], trade: dict[str, Any] | None) -> AlertCandidate:
    """position_closed — 실현 손익 + 자동 복기 요약 2줄 (판단 채점)."""
    trade = trade or {}
    pnl_amount = _float(trade.get("pnl_amount"))
    pnl_percent = _float(trade.get("pnl_percent"))
    pnl_line = (
        f"실현 손익 {pnl_amount:+,.2f} USDT ({pnl_percent:+.2f}%)"
        if pnl_amount is not None and pnl_percent is not None
        else "실현 손익 집계 대기 (체결 기록 확인 필요)"
    )
    lines = [
        _headline(position, "🏁", RULE_LABELS["position_closed"]),
        pnl_line,
        *_review_lines(trade),
        "→ 종료는 2회 연속 sync 부재 확인 후 확정된 기록입니다.",
    ]
    return _candidate(
        "position_closed",
        "action",
        position,
        {},
        identity=str(trade.get("id") or position.get("closed_at") or "closed"),
        title=RULE_LABELS["position_closed"],
        message="\n".join(line for line in lines if line),
        payload={
            "kind": "lifecycle",
            "trade_id": str(trade.get("id")) if trade.get("id") else None,
            "pnl_amount": pnl_amount,
            "pnl_percent": pnl_percent,
        },
    )


def transition_candidates(
    context: dict[str, Any],
    tracker: dict[str, Any],
    settings: Settings,
    *,
    now: datetime | None = None,
) -> tuple[list[AlertCandidate], dict[str, Any]]:
    """verdict_changed · stance_flipped · evidence_insufficient — 포지션별 이전 상태 대비 전이 감지.

    tracker: NotificationState.lifecycle_positions[position_id] (영속) — 반환값으로 갱신본을 돌려준다.
    """
    now = now or datetime.now(timezone.utc)
    position = _position(context)
    state = _state(context)
    one_liners = _one_liners(context)
    plan = context.get("action_plan") if isinstance(context.get("action_plan"), dict) else {}
    candidates: list[AlertCandidate] = []

    verdict = str(plan.get("verdict_state") or "unknown")
    previous_verdict = str(tracker.get("verdict_state") or "")
    if previous_verdict and verdict != previous_verdict and verdict in VERDICT_LABELS and previous_verdict in VERDICT_LABELS:
        worsened = VERDICT_RANK.get(verdict, 0) > VERDICT_RANK.get(previous_verdict, 0)
        reason = str(plan.get("headline_action") or plan.get("headline") or one_liners.get("summary") or "판정 근거 갱신")[:80]
        next_price = _next_watch_price(plan)
        lines = [
            _headline(position, "🟠" if worsened else "🟢", RULE_LABELS["verdict_changed"]),
            f"{VERDICT_LABELS[previous_verdict]} → {VERDICT_LABELS[verdict]}",
            f"사유: {escape(reason)}",
            f"다음 가격: {next_price}" if next_price else "",
            _snapshot_line(state),
        ]
        candidates.append(
            _candidate(
                "verdict_changed",
                "warn" if worsened else "info",
                position,
                state,
                identity=f"{previous_verdict}->{verdict}",
                title=RULE_LABELS["verdict_changed"],
                message="\n".join(line for line in lines if line),
                payload={"kind": "lifecycle", "from": previous_verdict, "to": verdict, "worsened": worsened},
            )
        )

    stance = str(one_liners.get("overall_stance") or "")
    previous_stance = str(tracker.get("overall_stance") or "")
    if previous_stance in {"상방", "하방"} and stance in {"상방", "하방"} and stance != previous_stance:
        top = _strongest_line(one_liners)
        lines = [
            _headline(position, "🔄", RULE_LABELS["stance_flipped"]),
            f"종합 스탠스 {previous_stance} → {stance} · {escape(str(one_liners.get('summary') or ''))}",
            f"반전 근거: {top}" if top else "",
            _snapshot_line(state),
        ]
        candidates.append(
            _candidate(
                "stance_flipped",
                "warn",
                position,
                state,
                identity=f"{previous_stance}->{stance}",
                title=RULE_LABELS["stance_flipped"],
                message="\n".join(line for line in lines if line),
                payload={"kind": "lifecycle", "from": previous_stance, "to": stance},
            )
        )

    # evidence_insufficient: standby 전이 또는 판정 가능 모듈 <3 이 지속.
    judged = _judged_modules(one_liners)
    insufficient_now = verdict == "standby" or (judged is not None and judged < 3)
    insufficient_since = _parse_dt(tracker.get("insufficient_since"))
    window = timedelta(hours=max(0.25, float(getattr(settings, "alert_evidence_insufficient_hours", 2.0))))
    if insufficient_now:
        if insufficient_since is None:
            insufficient_since = now
        elif now - insufficient_since >= window and not tracker.get("insufficient_alerted"):
            lines = [
                _headline(position, "⚪", RULE_LABELS["evidence_insufficient"]),
                f"현재 판단 근거 부족 — 구조 형성 대기 (판정 가능 모듈 {judged if judged is not None else '-'} / 7)",
                _observation_line(position, one_liners),
                _snapshot_line(state),
            ]
            candidates.append(
                _candidate(
                    "evidence_insufficient",
                    "info",
                    position,
                    state,
                    identity="insufficient",
                    title=RULE_LABELS["evidence_insufficient"],
                    message="\n".join(line for line in lines if line),
                    payload={"kind": "lifecycle", "judged_modules": judged, "since": insufficient_since.isoformat()},
                )
            )
            tracker = {**tracker, "insufficient_alerted": True}
    else:
        insufficient_since = None
        tracker = {**tracker, "insufficient_alerted": False}

    updated = {
        **tracker,
        "verdict_state": verdict if verdict in VERDICT_LABELS else tracker.get("verdict_state"),
        "overall_stance": stance or tracker.get("overall_stance"),
        "insufficient_since": insufficient_since.isoformat() if insufficient_since else None,
        "symbol": position.get("symbol"),
    }
    return candidates, updated


def pulse_candidate(
    contexts: list[dict[str, Any]],
    *,
    pending_redelivery: list[dict[str, Any]] | None = None,
) -> AlertCandidate | None:
    """periodic_pulse — 보유 포지션 1줄 상태 묶음 1통. "전부 정상"도 발송 (침묵 ≠ 정상 증명)."""
    lines = [f"📡 <b>{RULE_LABELS['periodic_pulse']}</b>"]
    if not contexts:
        lines.append("보유 포지션 없음 — 감시 정상 동작 중입니다.")
    all_normal = True
    for context in contexts:
        position = _position(context)
        state = _state(context)
        plan = context.get("action_plan") if isinstance(context.get("action_plan"), dict) else {}
        one_liners = _one_liners(context)
        verdict = str(plan.get("verdict_state") or "unknown")
        if verdict in {"weakening", "danger"}:
            all_normal = False
        aligned, opposing, judged = _alignment_counts(position, one_liners)
        lines.append(
            f"• <b>{escape(str(position.get('symbol') or '-'))}</b> {_direction_kr(position)} "
            f"{_signed_pct(state.get('pnl_percent'))} · 판정 {VERDICT_LABELS.get(verdict, verdict)} · "
            f"방향 근거 {aligned}/{judged if judged is not None else '-'} · 반대 {opposing}"
        )
    if contexts and all_normal:
        lines.append("전 포지션 관측 정상 범위입니다.")
    redelivery = [item for item in (pending_redelivery or []) if isinstance(item, dict)]
    if redelivery:
        lines.append(f"⚠ 미도달 알림 {len(redelivery)}건 병합:")
        for item in redelivery[:5]:
            lines.append(f"  - {escape(str(item.get('symbol') or '-'))} {escape(str(item.get('title') or '-'))} ({_time(item.get('fired_at'))})")
    lines.append("→ 정기 상태 통보입니다. 알림 침묵과 시스템 고장을 구분하기 위해 정상 상태도 발송합니다.")
    return AlertCandidate(
        rule_id="periodic_pulse",
        severity="info",
        position_id=None,
        symbol="PULSE",
        identity="pulse",
        title=RULE_LABELS["periodic_pulse"],
        message="\n".join(lines),
        payload={"kind": "lifecycle_pulse", "positions": len(contexts), "merged_redelivery": len(redelivery)},
    )


# ── 관측 문형 (판단 지시 금지) ─────────────────────────────────────

def _observation_line(position: dict[str, Any], one_liners: dict[str, Any]) -> str:
    aligned, opposing, judged = _alignment_counts(position, one_liners)
    if judged is None:
        return "관측 보류: 판정 데이터 없음"
    if judged < 3:
        return f"관측 보류: 판정 가능 모듈 {judged}/7 — 구조 형성 대기"
    if aligned > opposing:
        grade = "관측 양호"
    elif aligned < opposing:
        grade = "관측 주의"
    else:
        grade = "관측 중립"
    direction_word = "상방" if _direction(position) == "long" else "하방"
    return f"{grade}: {direction_word} 근거 {aligned}/{judged} · 반대 {opposing}"


def _alignment_counts(position: dict[str, Any], one_liners: dict[str, Any]) -> tuple[int, int, int | None]:
    counts = one_liners.get("counts") if isinstance(one_liners.get("counts"), dict) else None
    if not counts:
        return 0, 0, None
    direction = _direction(position)
    aligned = int(counts.get("상방" if direction == "long" else "하방") or 0)
    opposing = int(counts.get("하방" if direction == "long" else "상방") or 0)
    judged = _judged_modules(one_liners)
    return aligned, opposing, judged


def _judged_modules(one_liners: dict[str, Any]) -> int | None:
    counts = one_liners.get("counts") if isinstance(one_liners.get("counts"), dict) else None
    if not counts:
        return None
    total = sum(int(value or 0) for value in counts.values())
    return total - int(counts.get("판단불가") or 0)


def _top_module_lines(one_liners: dict[str, Any], *, limit: int = 3) -> str:
    lines = one_liners.get("lines") if isinstance(one_liners.get("lines"), list) else []
    strength = {"강": 0, "중": 1, "약": 2}
    ranked = sorted(
        (line for line in lines if isinstance(line, dict) and line.get("stance") != "판단불가"),
        key=lambda line: strength.get(str(line.get("confidence_class")), 3),
    )[:limit]
    if not ranked:
        return ""
    parts = [f"{line.get('module_label')}: {line.get('phrase')}({line.get('confidence_class')})" for line in ranked]
    return "판정: " + " / ".join(escape(str(part)) for part in parts)


def _strongest_line(one_liners: dict[str, Any]) -> str:
    text = _top_module_lines(one_liners, limit=1)
    return text.removeprefix("판정: ")


def _scenario_line(position: dict[str, Any]) -> str:
    if position.get("scenario_id"):
        return "시나리오: 저장된 진입 시나리오와 매칭됨 — 계획 기준으로 추적합니다."
    return "시나리오: 매칭된 사전 시나리오 없음 — 현재 구조 기준으로 추적합니다."


def _review_lines(trade: dict[str, Any]) -> list[str]:
    scorecard = trade.get("judgment_scorecard") if isinstance(trade.get("judgment_scorecard"), dict) else {}
    correct = int(scorecard.get("correct") or 0)
    wrong = int(scorecard.get("wrong") or 0)
    whipsaw = int(scorecard.get("whipsaw") or 0)
    tested = correct + wrong + whipsaw
    if tested == 0:
        return ["복기: 판단 채점 표본 없음 — 상세 복기는 /trades에서 확인하세요."]
    first = f"복기: 판단 {tested}건 채점 — 적중 {correct} · 오판 {wrong} · 휩쏘 {whipsaw}"
    review = trade.get("review_v2") if isinstance(trade.get("review_v2"), dict) else {}
    realized_r = _float(review.get("realized_r"))
    second = f"실현 R {realized_r:+.2f}" if realized_r is not None else "실현 R 미산출"
    if wrong > correct:
        second += " · 엔진 판단이 결과와 어긋난 거래 — 원인은 복기 화면에서"
    elif correct > wrong:
        second += " · 엔진 판단과 결과가 일치한 거래"
    return [first, second]


# ── 공용 헬퍼 ─────────────────────────────────────────────────────

def _candidate(
    rule_id: str,
    severity: str,
    position: dict[str, Any],
    state: dict[str, Any],
    *,
    identity: str,
    title: str,
    message: str,
    payload: dict[str, Any],
) -> AlertCandidate:
    return AlertCandidate(
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        position_id=str(position.get("id")) if position.get("id") else None,
        symbol=str(position.get("symbol") or "-").upper(),
        identity=identity,
        title=title,
        message=message,
        payload={
            **payload,
            "symbol": position.get("symbol"),
            "direction": _direction(position),
            "pnl_percent": state.get("pnl_percent"),
            "as_of": state.get("as_of"),
        },
    )


def _headline(position: dict[str, Any], emoji: str, title: str) -> str:
    return (
        f"{emoji} <b>{escape(str(position.get('symbol') or '-'))}</b> "
        f"{_direction_kr(position)} {position.get('leverage', '-')}x — {escape(title)}"
    )


def _snapshot_line(state: dict[str, Any]) -> str:
    return f"건강도 {state.get('health_score', '-')} · PnL {_signed_pct(state.get('pnl_percent'))} · {_time(state.get('as_of'))} 기준"


def _one_liners(context: dict[str, Any]) -> dict[str, Any]:
    analysis = context.get("chart_analysis") if isinstance(context.get("chart_analysis"), dict) else {}
    one_liners = analysis.get("one_liners")
    return one_liners if isinstance(one_liners, dict) else {}


def _next_watch_price(plan: dict[str, Any]) -> str | None:
    invalidation = plan.get("invalidation") or plan.get("engine_invalidation")
    if isinstance(invalidation, dict) and _float(invalidation.get("price")) is not None:
        return f"무효화 {_price(invalidation.get('price'))}"
    targets = plan.get("take_profit") if isinstance(plan.get("take_profit"), list) else []
    for target in targets:
        if isinstance(target, dict) and _float(target.get("price")) is not None:
            return f"익절 후보 {_price(target.get('price'))}"
    return None


def _position(payload: dict[str, Any]) -> dict[str, Any]:
    return _dump(payload.get("position", payload))


def _state(payload: dict[str, Any]) -> dict[str, Any]:
    return _dump(payload.get("state", payload))


def _direction(position: dict[str, Any]) -> str:
    value = position.get("direction")
    if hasattr(value, "value"):
        value = value.value
    return "short" if value == "short" else "long"


def _direction_kr(position: dict[str, Any]) -> str:
    return "숏" if _direction(position) == "short" else "롱"


def _dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {}


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _price(value: Any) -> str:
    number = _float(value)
    if number is None:
        return "-"
    if abs(number) >= 100:
        return f"{number:,.2f}"
    if abs(number) >= 1:
        return f"{number:.4f}"
    return f"{number:.6f}"


def _qty(value: Any) -> str:
    number = _float(value)
    if number is None:
        return "-"
    return f"{number:,.4f}".rstrip("0").rstrip(".")


def _signed_pct(value: Any) -> str:
    number = _float(value)
    if number is None:
        return "-"
    return f"{number:+.2f}%"


def _time(value: Any) -> str:
    parsed = _parse_dt(value)
    if parsed is None:
        return "-"
    try:
        from zoneinfo import ZoneInfo

        return parsed.astimezone(ZoneInfo("Asia/Seoul")).strftime("%m-%d %H:%M")
    except Exception:
        return parsed.strftime("%m-%d %H:%M")


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
