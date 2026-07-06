from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from typing import Any, Literal

from app.core.config import Settings

AlertSeverity = Literal["info", "action", "warn", "critical"]


@dataclass(frozen=True)
class AlertCandidate:
    rule_id: str
    severity: AlertSeverity
    position_id: str | None
    symbol: str
    identity: str
    title: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def state_key(self) -> str:
        return f"{self.rule_id}:{self.position_id or 'system'}:{self.identity}"


RULE_LABELS: dict[str, str] = {
    "trigger_near": "트리거 근접",
    "invalidation_breach": "무효화 이탈",
    "take_profit_hit": "익절 후보 도달",
    "status_worsened": "상태 악화",
    "health_drop": "건강도 급락",
    "liq_proximity": "청산 접근",
    "liq_unknown_high_lev": "청산가 미수신",
    "wyckoff_event": "와이코프 이벤트",
    "data_stall": "데이터 동기화 지연",
    "funding_extreme": "펀딩 과열",
    "oi_divergence": "OI 역행",
    "liq_cluster_near": "청산 밀집대 근접",
    "setup_near": "셋업 접근",
    "setup_triggered": "셋업 트리거",
    "setup_invalidated": "셋업 무효화",
}

RULE_SEVERITY: dict[str, AlertSeverity] = {
    "trigger_near": "info",
    "invalidation_breach": "critical",
    "take_profit_hit": "action",
    "status_worsened": "warn",
    "health_drop": "warn",
    "liq_proximity": "critical",
    "liq_unknown_high_lev": "warn",
    "wyckoff_event": "info",
    "data_stall": "warn",
    "funding_extreme": "warn",
    "oi_divergence": "info",
    "liq_cluster_near": "warn",
    "setup_near": "info",
    "setup_triggered": "action",
    "setup_invalidated": "info",
}


def rule_catalog(settings: Settings) -> list[dict[str, Any]]:
    enabled = settings.alert_enabled_rule_set
    return [
        {
            "id": rule_id,
            "label": RULE_LABELS[rule_id],
            "severity": RULE_SEVERITY[rule_id],
            "enabled": rule_id in enabled,
            "threshold": _rule_threshold(rule_id, settings),
            "cooldown_minutes": _cooldown_minutes(RULE_SEVERITY[rule_id], settings),
        }
        for rule_id in RULE_LABELS
    ]


def evaluate_position_alerts(payload: dict[str, Any], settings: Settings) -> list[AlertCandidate]:
    candidates: list[AlertCandidate] = []
    enabled = settings.alert_enabled_rule_set
    candidates.extend(_trigger_candidates(payload, settings) if "trigger_near" in enabled else [])
    candidates.extend(_invalidation_candidates(payload) if "invalidation_breach" in enabled else [])
    candidates.extend(_take_profit_candidates(payload) if "take_profit_hit" in enabled else [])
    candidates.extend(_status_worsened_candidates(payload) if "status_worsened" in enabled else [])
    candidates.extend(_health_drop_candidates(payload, settings) if "health_drop" in enabled else [])
    candidates.extend(_liq_proximity_candidates(payload, settings) if "liq_proximity" in enabled else [])
    candidates.extend(_liq_unknown_candidates(payload, settings) if "liq_unknown_high_lev" in enabled else [])
    candidates.extend(_wyckoff_candidates(payload, settings) if "wyckoff_event" in enabled else [])
    candidates.extend(_derivative_position_candidates(payload, settings))
    return candidates


def rearm_signals(payload: dict[str, Any], settings: Settings) -> dict[str, bool]:
    signals: dict[str, bool] = {}
    position = _position(payload)
    state = _state(payload)
    position_id = _text(position.get("id"))
    current = _current_price(payload)
    direction = _direction(position)
    plan = _plan(payload)

    for trigger in _plan_triggers(plan):
        price = _float(trigger.get("price"))
        if price is None or current is None:
            continue
        distance = _distance_pct(current, price)
        identity = trigger["identity"]
        signals[f"trigger_near:{position_id}:{identity}"] = abs(distance) >= settings.alert_trigger_rearm_pct
        if trigger["kind"] == "invalidation":
            signals[f"invalidation_breach:{position_id}:{identity}"] = not _breached(direction, current, price)
        elif trigger["kind"] == "take_profit":
            signals[f"take_profit_hit:{position_id}:{identity}"] = not _take_profit_reached(direction, current, price)

    liq_distance = _float(state.get("liquidation_distance_pct") or _snapshot(payload).get("liquidation_distance_pct"))
    if liq_distance is not None:
        signals[f"liq_proximity:{position_id}:distance"] = abs(liq_distance) > settings.alert_liq_warn_pct
    position_liq = _float(position.get("liquidation_price"))
    leverage = _float(position.get("leverage")) or 0
    signals[f"liq_unknown_high_lev:{position_id}:missing"] = bool(position_liq is not None or leverage < 10)
    signals[f"status_worsened:{position_id}:severity"] = True
    signals[f"health_drop:{position_id}:health"] = True
    return signals


def evaluate_data_stall(worker_status: dict[str, Any], settings: Settings) -> list[AlertCandidate]:
    if "data_stall" not in settings.alert_enabled_rule_set:
        return []
    jobs = worker_status.get("jobs", {})
    sync = jobs.get("sync_positions", {})
    status = sync.get("status")
    last_success = _parse_dt(sync.get("last_success_at"))
    now = datetime.now(timezone.utc)
    stalled = status == "error" and (last_success is None or (now - last_success).total_seconds() >= 600)
    if not stalled:
        return []
    failures = int(sync.get("consecutive_failures") or sync.get("failures") or 0)
    message = "\n".join(
        [
            "🟠 <b>데이터 동기화 지연</b>",
            f"sync_positions가 10분 이상 성공하지 못했습니다. 연속 실패 {failures}회",
            "→ 포지션 숫자가 최신이 아닐 수 있습니다. Bitget 연결과 워커 로그를 확인하세요.",
        ]
    )
    return [
        AlertCandidate(
            rule_id="data_stall",
            severity="warn",
            position_id=None,
            symbol="SYSTEM",
            identity="sync_positions",
            title=RULE_LABELS["data_stall"],
            message=message,
            payload={
                "job": "sync_positions",
                "status": status,
                "failures": failures,
                "number_sources": [
                    {
                        "label": "failures",
                        "value": failures,
                        "source": "worker_heartbeat",
                    }
                ],
            },
        )
    ]


def evaluate_derivative_alerts(snapshots: list[dict[str, Any]], settings: Settings) -> list[AlertCandidate]:
    # WO-FCE-21 derivative alerts require position direction. The worker still calls
    # this hook after collection, but position-aware candidates are generated from
    # evaluate_position_alerts() so watchlist-only symbols do not produce trade alerts.
    return []


def cooldown_seconds(severity: AlertSeverity, settings: Settings) -> int:
    return int(_cooldown_minutes(severity, settings) * 60)


def quiet_hours_active(settings: Settings, now: datetime | None = None) -> bool:
    if not settings.telegram_quiet_hours_enabled:
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(_quiet_timezone(settings))
    start = _parse_hhmm(settings.telegram_quiet_hours_start)
    end = _parse_hhmm(settings.telegram_quiet_hours_end)
    if start is None or end is None:
        return False
    current_minutes = current.hour * 60 + current.minute
    start_minutes = start[0] * 60 + start[1]
    end_minutes = end[0] * 60 + end[1]
    if start_minutes <= end_minutes:
        return start_minutes <= current_minutes < end_minutes
    return current_minutes >= start_minutes or current_minutes < end_minutes


def morning_summary_due(settings: Settings, last_summary_date: str | None, now: datetime | None = None) -> tuple[bool, str]:
    current = (now or datetime.now(timezone.utc)).astimezone(_quiet_timezone(settings))
    target = settings.telegram_daily_summary_time.strip()
    date_key = current.strftime("%Y-%m-%d")
    return current.strftime("%H:%M") == target and last_summary_date != date_key, date_key


def _trigger_candidates(payload: dict[str, Any], settings: Settings) -> list[AlertCandidate]:
    current = _current_price(payload)
    if current is None:
        return []
    candidates: list[AlertCandidate] = []
    for trigger in _plan_triggers(_plan(payload)):
        price = _float(trigger.get("price"))
        if price is None:
            continue
        distance = trigger.get("distance_pct")
        distance = _float(distance) if distance is not None else _distance_pct(current, price)
        if abs(distance) > settings.alert_trigger_near_pct:
            continue
        label = "무효화" if trigger["kind"] == "invalidation" else "익절"
        position, state = _position(payload), _state(payload)
        title = f"{label} 트리거 근접"
        message = "\n".join(
            [
                _headline(payload, "🟡", title),
                f"{label} {_price(price)}까지 {_signed_pct(distance)} 남았습니다. 현재 {_price(current)}",
                f"→ {escape(str(trigger.get('action') or '조건 확인'))}. 근거: {escape(str(trigger.get('basis') or '액션 플랜'))}",
                _snapshot_line(state),
            ]
        )
        candidates.append(
            _candidate(
                "trigger_near",
                "info",
                position,
                state,
                trigger["identity"],
                title,
                message,
                {
                    "trigger_kind": trigger["kind"],
                    "trigger_price": price,
                    "current_price": current,
                    "distance_pct": distance,
                    "basis": trigger.get("basis"),
                    "action": trigger.get("action"),
                    "number_sources": _number_sources(
                        ("trigger_price", price, "action_plan"),
                        ("current_price", current, "snapshot.mark_price"),
                        ("distance_pct", distance, "action_plan_or_computed"),
                    ),
                },
            )
        )
    return candidates


def _invalidation_candidates(payload: dict[str, Any]) -> list[AlertCandidate]:
    current = _current_price(payload)
    direction = _direction(_position(payload))
    if current is None:
        return []
    candidates: list[AlertCandidate] = []
    for trigger in _plan_triggers(_plan(payload)):
        if trigger["kind"] != "invalidation":
            continue
        price = _float(trigger.get("price"))
        if price is None or not _breached(direction, current, price):
            continue
        distance = _distance_pct(price, current)
        position, state = _position(payload), _state(payload)
        message = "\n".join(
            [
                _headline(payload, "🔴", "무효화 이탈"),
                f"{_price(price)} 기준을 종가 이탈했습니다. 현재 {_price(current)} · {_signed_pct(distance)}",
                "→ 손절 검토. 이탈 시 진입 논리 약화 판정.",
                _snapshot_line(state),
            ]
        )
        candidates.append(
            _candidate(
                "invalidation_breach",
                "critical",
                position,
                state,
                trigger["identity"],
                "무효화 이탈",
                message,
                {
                    "trigger_price": price,
                    "current_price": current,
                    "distance_pct": distance,
                    "basis": trigger.get("basis"),
                    "action": "손절 검토",
                    "number_sources": _number_sources(
                        ("trigger_price", price, "action_plan.invalidation.price"),
                        ("current_price", current, "snapshot.mark_price_or_last_close"),
                        ("distance_pct", distance, "computed_from_snapshot"),
                    ),
                },
            )
        )
    return candidates


def _take_profit_candidates(payload: dict[str, Any]) -> list[AlertCandidate]:
    current = _current_price(payload)
    direction = _direction(_position(payload))
    if current is None:
        return []
    candidates: list[AlertCandidate] = []
    for trigger in _plan_triggers(_plan(payload)):
        if trigger["kind"] != "take_profit":
            continue
        price = _float(trigger.get("price"))
        if price is None or not _take_profit_reached(direction, current, price):
            continue
        distance = _distance_pct(price, current)
        position, state = _position(payload), _state(payload)
        title = f"{trigger.get('label', '익절')} 도달"
        message = "\n".join(
            [
                _headline(payload, "🟢", title),
                f"{escape(str(trigger.get('label', '익절')))} {_price(price)}에 도달했습니다. 현재 {_price(current)} · {_signed_pct(distance)}",
                f"→ {escape(str(trigger.get('action') or '부분 익절 검토'))}. 근거: {escape(str(trigger.get('basis') or '액션 플랜'))}",
                _snapshot_line(state),
            ]
        )
        candidates.append(
            _candidate(
                "take_profit_hit",
                "action",
                position,
                state,
                trigger["identity"],
                title,
                message,
                {
                    "trigger_price": price,
                    "current_price": current,
                    "distance_pct": distance,
                    "basis": trigger.get("basis"),
                    "action": trigger.get("action") or "부분 익절 검토",
                    "number_sources": _number_sources(
                        ("trigger_price", price, "action_plan.take_profit.price"),
                        ("current_price", current, "snapshot.mark_price_or_last_close"),
                        ("distance_pct", distance, "computed_from_snapshot"),
                    ),
                },
            )
        )
    return candidates


def _status_worsened_candidates(payload: dict[str, Any]) -> list[AlertCandidate]:
    previous = _previous_snapshot(payload)
    state = _state(payload)
    if not previous:
        return []
    previous_rank = int(_float(previous.get("severity_rank")) or 0)
    current_rank = int(_float(state.get("severity_rank")) or 0)
    if current_rank <= previous_rank:
        return []
    position = _position(payload)
    message = "\n".join(
        [
            _headline(payload, "🟠", "상태 악화"),
            f"심각도 {previous_rank} → {current_rank}. 현재 상태: {escape(str(state.get('status_label') or '-'))}",
            "→ 액션 플랜의 무효화·감시 조건을 다시 확인하세요.",
            _snapshot_line(state),
        ]
    )
    return [
        _candidate(
            "status_worsened",
            "warn",
            position,
            state,
            "severity",
            "상태 악화",
            message,
            {
                "previous_severity_rank": previous_rank,
                "current_severity_rank": current_rank,
                "number_sources": _number_sources(
                    (
                        "previous_severity_rank",
                        previous_rank,
                        "previous_snapshot.severity_rank",
                    ),
                    ("current_severity_rank", current_rank, "snapshot.severity_rank"),
                ),
            },
        )
    ]


def _health_drop_candidates(payload: dict[str, Any], settings: Settings) -> list[AlertCandidate]:
    previous = _previous_snapshot(payload)
    state = _state(payload)
    if not previous:
        return []
    previous_health = _float(previous.get("health_score"))
    current_health = _float(state.get("health_score"))
    if previous_health is None or current_health is None:
        return []
    drop = previous_health - current_health
    if drop < settings.alert_health_drop_points:
        return []
    position = _position(payload)
    message = "\n".join(
        [
            _headline(payload, "🟠", "건강도 급락"),
            f"건강도 {previous_health:.0f} → {current_health:.0f} · {drop:.0f}점 하락",
            "→ 구조 변화, PnL, 청산거리 중 어떤 항목이 악화됐는지 확인하세요.",
            _snapshot_line(state),
        ]
    )
    return [
        _candidate(
            "health_drop",
            "warn",
            position,
            state,
            "health",
            "건강도 급락",
            message,
            {
                "previous_health": previous_health,
                "current_health": current_health,
                "drop_points": drop,
                "number_sources": _number_sources(
                    (
                        "previous_health",
                        previous_health,
                        "previous_snapshot.health_score",
                    ),
                    ("current_health", current_health, "snapshot.health_score"),
                    ("drop_points", drop, "computed_from_snapshots"),
                ),
            },
        )
    ]


def _liq_proximity_candidates(payload: dict[str, Any], settings: Settings) -> list[AlertCandidate]:
    state = _state(payload)
    distance = _float(state.get("liquidation_distance_pct") or _snapshot(payload).get("liquidation_distance_pct"))
    if distance is None or abs(distance) > settings.alert_liq_warn_pct:
        return []
    position = _position(payload)
    title = "청산 접근"
    threshold = settings.alert_liq_critical_pct if abs(distance) <= settings.alert_liq_critical_pct else settings.alert_liq_warn_pct
    message = "\n".join(
        [
            _headline(payload, "🔴", title),
            f"청산가까지 {abs(distance):.2f}% 남았습니다. 기준 단계 {threshold:.1f}%",
            "→ 포지션 유지 전 청산가와 증거금 상태를 수동 확인하세요.",
            _snapshot_line(state),
        ]
    )
    return [
        _candidate(
            "liq_proximity",
            "critical",
            position,
            state,
            "distance",
            title,
            message,
            {
                "liquidation_distance_pct": distance,
                "threshold_pct": threshold,
                "number_sources": _number_sources(
                    (
                        "liquidation_distance_pct",
                        distance,
                        "snapshot.liquidation_distance_pct",
                    ),
                    ("threshold_pct", threshold, "config"),
                ),
            },
        )
    ]


def _liq_unknown_candidates(payload: dict[str, Any], settings: Settings) -> list[AlertCandidate]:
    position = _position(payload)
    state = _state(payload)
    leverage = _float(position.get("leverage")) or 0
    if leverage < 10 or _float(position.get("liquidation_price")) is not None:
        return []
    opened_at = _parse_dt(position.get("opened_at"))
    if opened_at is None:
        return []
    elapsed_hours = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
    if elapsed_hours < settings.alert_liq_unknown_high_lev_hours:
        return []
    message = "\n".join(
        [
            _headline(payload, "🟠", "청산가 미수신"),
            f"{leverage:.0f}x 포지션인데 청산가가 {elapsed_hours:.1f}시간째 수신되지 않았습니다.",
            "→ 거래소 화면에서 청산가를 수동 확인하세요.",
            _snapshot_line(state),
        ]
    )
    return [
        _candidate(
            "liq_unknown_high_lev",
            "warn",
            position,
            state,
            "missing",
            "청산가 미수신",
            message,
            {
                "leverage": leverage,
                "elapsed_hours": elapsed_hours,
                "number_sources": _number_sources(
                    ("leverage", leverage, "position.leverage"),
                    ("elapsed_hours", elapsed_hours, "position.opened_at"),
                ),
            },
        )
    ]


def _wyckoff_candidates(payload: dict[str, Any], settings: Settings) -> list[AlertCandidate]:
    position = _position(payload)
    state = _state(payload)
    markers = _wyckoff_markers(payload)
    candidates: list[AlertCandidate] = []
    for marker in markers:
        confidence = _float(marker.get("confidence"))
        if confidence is None or confidence < settings.alert_wyckoff_min_confidence:
            continue
        marker_type = str(marker.get("type") or marker.get("label") or "event")
        label = str(marker.get("label") or marker_type)
        identity = str(marker.get("id") or f"{marker_type}:{marker.get('time') or marker.get('price') or confidence}")
        message = "\n".join(
            [
                _headline(payload, "🟡", "와이코프 이벤트"),
                f"{escape(label)} 신뢰도 {confidence:.0f} 감지",
                "→ 포지션 방향과 구조가 맞는지 액션 플랜에서 조건을 확인하세요.",
                _snapshot_line(state),
            ]
        )
        candidates.append(
            _candidate(
                "wyckoff_event",
                "info",
                position,
                state,
                identity,
                "와이코프 이벤트",
                message,
                {
                    "event": marker,
                    "confidence": confidence,
                    "number_sources": _number_sources(
                        (
                            "confidence",
                            confidence,
                            "chart_analysis.wyckoff_markers.confidence",
                        )
                    ),
                },
            )
        )
    return candidates


def _derivative_position_candidates(payload: dict[str, Any], settings: Settings) -> list[AlertCandidate]:
    derivatives = _derivatives(payload)
    if not derivatives:
        return []
    enabled = settings.alert_enabled_rule_set
    position = _position(payload)
    state = _state(payload)
    signals = derivatives.get("signals") if isinstance(derivatives.get("signals"), dict) else {}
    current = _current_price(payload)
    candidates: list[AlertCandidate] = []
    if "funding_extreme" in enabled:
        funding = signals.get("funding_state") if isinstance(signals.get("funding_state"), dict) else None
        funding_value = _float((funding or {}).get("funding"))
        if funding and funding.get("state") == "extreme" and funding_value is not None and _funding_adverse(_direction(position), funding_value):
            title = "펀딩 극단"
            message = "\n".join(
                [
                    _headline(payload, "🟠", title),
                    f"펀딩 {_signed_rate(funding_value)} · {escape(str(funding.get('label') or '극단'))}",
                    "→ 보유 방향 쏠림이 커진 상태입니다. 청산 리스크와 비용 부담을 감시하세요.",
                    _derivative_source_line(derivatives),
                ]
            )
            candidates.append(
                _candidate(
                    "funding_extreme",
                    "warn",
                    position,
                    state,
                    f"funding:{'positive' if funding_value > 0 else 'negative'}",
                    title,
                    message,
                    {
                        "funding_rate": funding_value,
                        "funding_state": funding,
                        "number_sources": _number_sources(("funding_rate", funding_value, "deriv_metrics.funding")),
                    },
                )
            )
    if "oi_divergence" in enabled:
        divergence = signals.get("oi_price_divergence") if isinstance(signals.get("oi_price_divergence"), dict) else None
        if divergence and _divergence_adverse(_direction(position), str(divergence.get("state") or "")):
            title = "OI 역행"
            message = "\n".join(
                [
                    _headline(payload, "🟡", title),
                    f"{escape(str(divergence.get('label') or '가격/OI 역행'))} · OI 24h {_signed_pct(divergence.get('oi_change_pct'))}",
                    "→ 포지션 방향과 수급이 맞는지 4시간봉 기준으로 확인하세요.",
                    _derivative_source_line(derivatives),
                ]
            )
            candidates.append(
                _candidate(
                    "oi_divergence",
                    "info",
                    position,
                    state,
                    str(divergence.get("state") or "divergence"),
                    title,
                    message,
                    {
                        "oi_price_divergence": divergence,
                        "number_sources": _number_sources(
                            (
                                "price_change_pct",
                                divergence.get("price_change_pct"),
                                "deriv_metrics.raw_json.price_change_pct_24h",
                            ),
                            (
                                "oi_change_pct",
                                divergence.get("oi_change_pct"),
                                "deriv_metrics.oi_change_pct",
                            ),
                        ),
                    },
                )
            )
    if "liq_cluster_near" in enabled and current is not None:
        coinglass = derivatives.get("coinglass") if isinstance(derivatives.get("coinglass"), dict) else {}
        if coinglass.get("source_status") == "ok":
            for cluster in signals.get("liquidation_clusters", []) if isinstance(signals.get("liquidation_clusters"), list) else []:
                if not isinstance(cluster, dict):
                    continue
                price = _float(cluster.get("price") or cluster.get("mid") or cluster.get("level"))
                if price is None:
                    continue
                distance = _distance_pct(current, price)
                if abs(distance) > settings.alert_trigger_near_pct:
                    continue
                title = "청산 밀집대 근접"
                message = "\n".join(
                    [
                        _headline(payload, "🟠", title),
                        f"청산 밀집대 추정 {_price(price)}까지 {_signed_pct(distance)} · 현재 {_price(current)}",
                        "→ 도달 시 변동성 확대 가능성을 감시하세요. 추정 모델이며 확정 사실이 아닙니다.",
                        _derivative_source_line(derivatives),
                    ]
                )
                candidates.append(
                    _candidate(
                        "liq_cluster_near",
                        "warn",
                        position,
                        state,
                        f"cluster:{round(price, 8)}",
                        title,
                        message,
                        {
                            "cluster_price": price,
                            "current_price": current,
                            "distance_pct": distance,
                            "cluster": cluster,
                            "number_sources": _number_sources(
                                (
                                    "cluster_price",
                                    price,
                                    "coinglass.liquidation_clusters.estimated",
                                ),
                                (
                                    "current_price",
                                    current,
                                    "snapshot.mark_price_or_last_close",
                                ),
                                ("distance_pct", distance, "computed_from_snapshot"),
                            ),
                        },
                    )
                )
    return candidates


def _candidate(
    rule_id: str,
    severity: AlertSeverity,
    position: dict[str, Any],
    state: dict[str, Any],
    identity: str,
    title: str,
    message: str,
    payload: dict[str, Any],
) -> AlertCandidate:
    payload = {
        **payload,
        "symbol": position.get("symbol"),
        "direction": _direction(position),
        "leverage": position.get("leverage"),
        "health_score": state.get("health_score"),
        "pnl_percent": state.get("pnl_percent"),
        "pnl_source": state.get("pnl_source"),
        "as_of": state.get("as_of"),
        "status_label": state.get("status_label"),
    }
    return AlertCandidate(
        rule_id=rule_id,
        severity=severity,
        position_id=_text(position.get("id")),
        symbol=str(position.get("symbol") or "-").upper(),
        identity=identity,
        title=title,
        message=message,
        payload=payload,
    )


def _headline(payload: dict[str, Any], emoji: str, title: str) -> str:
    position = _position(payload)
    return f"{emoji} <b>{escape(str(position.get('symbol') or '-'))}</b> {_direction_kr(position)} {position.get('leverage', '-')}x — {escape(title)}"


def _snapshot_line(state: dict[str, Any]) -> str:
    return f"건강도 {state.get('health_score', '-')} · PnL {_signed_pct(state.get('pnl_percent'))} · {_time(state.get('as_of'))} 기준"


def _plan_triggers(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not plan:
        return []
    triggers: list[dict[str, Any]] = []
    invalidation = plan.get("invalidation") or plan.get("engine_invalidation")
    if isinstance(invalidation, dict) and _float(invalidation.get("price")) is not None:
        triggers.append(
            {
                **invalidation,
                "kind": "invalidation",
                "identity": "invalidation",
                "label": "무효화",
            }
        )
    for index, target in enumerate(plan.get("take_profit", []) if isinstance(plan.get("take_profit"), list) else []):
        if isinstance(target, dict) and _float(target.get("price")) is not None:
            triggers.append(
                {
                    **target,
                    "kind": "take_profit",
                    "identity": f"take_profit_{index + 1}",
                    "label": f"익절{index + 1}",
                }
            )
    return triggers


def _plan(payload: dict[str, Any]) -> dict[str, Any] | None:
    plan = payload.get("action_plan") or (payload.get("latest_insight") or {}).get("action_plan")
    return plan if isinstance(plan, dict) else None


def _position(payload: dict[str, Any]) -> dict[str, Any]:
    return _dump(payload.get("position", payload))


def _state(payload: dict[str, Any]) -> dict[str, Any]:
    return _dump(payload.get("state", payload))


def _snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    return _dump(payload.get("latest_snapshot", payload.get("snapshot", {})))


def _derivatives(payload: dict[str, Any]) -> dict[str, Any] | None:
    state = _state(payload)
    analysis = state.get("analysis") if isinstance(state.get("analysis"), dict) else {}
    derivatives = analysis.get("derivatives")
    if isinstance(derivatives, dict):
        return derivatives
    chart_analysis = payload.get("chart_analysis") if isinstance(payload.get("chart_analysis"), dict) else {}
    derivatives = chart_analysis.get("derivatives")
    return derivatives if isinstance(derivatives, dict) else None


def _previous_snapshot(payload: dict[str, Any]) -> dict[str, Any] | None:
    previous = payload.get("previous_snapshot")
    if previous:
        return _dump(previous)
    snapshots = payload.get("snapshots")
    if isinstance(snapshots, list) and len(snapshots) >= 2:
        return _dump(snapshots[1])
    return None


def _wyckoff_markers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    analysis = payload.get("chart_analysis") if isinstance(payload.get("chart_analysis"), dict) else {}
    markers = analysis.get("wyckoff_markers")
    if isinstance(markers, list):
        return [marker for marker in markers if isinstance(marker, dict)]
    low_conf = analysis.get("wyckoff_markers_low_confidence")
    if isinstance(low_conf, list):
        return [marker for marker in low_conf if isinstance(marker, dict)]
    return []


def _current_price(payload: dict[str, Any]) -> float | None:
    last_close = _last_close(payload)
    if last_close is not None:
        return last_close
    state = _state(payload)
    snapshot = _snapshot(payload)
    position = _position(payload)
    return _float(state.get("mark_price") or snapshot.get("mark_price") or position.get("mark_price") or position.get("current_price"))


def _last_close(payload: dict[str, Any]) -> float | None:
    analysis = payload.get("chart_analysis") if isinstance(payload.get("chart_analysis"), dict) else {}
    candles = analysis.get("candles")
    if isinstance(candles, list) and candles:
        return _float(_dump(candles[-1]).get("close"))
    return None


def _direction(position: dict[str, Any]) -> str:
    value = position.get("direction")
    if hasattr(value, "value"):
        value = value.value
    return "short" if value == "short" else "long"


def _direction_kr(position: dict[str, Any]) -> str:
    return "숏" if _direction(position) == "short" else "롱"


def _breached(direction: str, current: float, price: float) -> bool:
    return current <= price if direction == "long" else current >= price


def _take_profit_reached(direction: str, current: float, price: float) -> bool:
    return current >= price if direction == "long" else current <= price


def _distance_pct(base: float, target: float) -> float:
    return ((target - base) / base) * 100 if base else 0.0


def _rule_threshold(rule_id: str, settings: Settings) -> float | int | str | None:
    return {
        "trigger_near": settings.alert_trigger_near_pct,
        "invalidation_breach": None,
        "take_profit_hit": None,
        "status_worsened": None,
        "health_drop": settings.alert_health_drop_points,
        "liq_proximity": settings.alert_liq_warn_pct,
        "liq_unknown_high_lev": settings.alert_liq_unknown_high_lev_hours,
        "wyckoff_event": settings.alert_wyckoff_min_confidence,
        "data_stall": 10,
        "funding_extreme": "p90",
        "oi_divergence": 4,
        "liq_cluster_near": settings.alert_trigger_near_pct,
        "setup_near": settings.scout_setup_near_pct,
        "setup_triggered": None,
        "setup_invalidated": None,
    }.get(rule_id)


def _funding_adverse(direction: str, funding: float) -> bool:
    return (direction == "long" and funding > 0) or (direction == "short" and funding < 0)


def _divergence_adverse(direction: str, state: str) -> bool:
    if direction == "long":
        return state in {"price_down_oi_up", "price_down_oi_down"}
    return state in {"price_up_oi_up", "price_up_oi_down"}


def _derivative_source_line(derivatives: dict[str, Any]) -> str:
    latest = derivatives.get("latest") if isinstance(derivatives.get("latest"), dict) else {}
    coinglass = derivatives.get("coinglass") if isinstance(derivatives.get("coinglass"), dict) else {}
    source = latest.get("provider") or latest.get("source") or "bitget"
    if coinglass.get("source_status") == "ok":
        source = f"{source}+coinglass"
    return f"기준 {_time(derivatives.get('as_of') or latest.get('as_of'))} · 출처 {escape(str(source))}"


def _signed_rate(value: float) -> str:
    return f"{value * 100:+.4f}%"


def _cooldown_minutes(severity: AlertSeverity, settings: Settings) -> int:
    if severity == "critical":
        return max(1, settings.alert_critical_cooldown_minutes)
    return max(1, settings.alert_default_cooldown_minutes)


def _number_sources(*items: tuple[str, Any, str]) -> list[dict[str, Any]]:
    return [{"label": label, "value": value, "source": source} for label, value, source in items]


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


def _text(value: Any) -> str:
    return str(value) if value is not None else ""


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _parse_hhmm(value: str) -> tuple[int, int] | None:
    try:
        hour, minute = value.strip().split(":", 1)
        return int(hour), int(minute)
    except (ValueError, AttributeError):
        return None


def _quiet_timezone(settings: Settings):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(settings.telegram_quiet_hours_timezone)
    except Exception:
        return timezone.utc


def _price(value: Any) -> str:
    number = _float(value)
    if number is None:
        return "-"
    if abs(number) >= 100:
        return f"{number:.2f}"
    if abs(number) >= 1:
        return f"{number:.4f}"
    return f"{number:.6f}"


def _signed_pct(value: Any) -> str:
    number = _float(value)
    if number is None:
        return "-"
    return f"{number:+.2f}%"


def _time(value: Any) -> str:
    parsed = _parse_dt(value)
    if parsed is None:
        return "-"
    return parsed.astimezone(_quiet_timezone_placeholder()).strftime("%H:%M")


def _quiet_timezone_placeholder():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Seoul")
    except Exception:
        return timezone.utc
