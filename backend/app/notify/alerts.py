from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from html import escape
from typing import Any, Callable
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import Settings
from app.db.models import AlertRecord
from app.notify.bot.formatters import (
    alert_keyboard,
    format_positions_summary,
    format_weekly_calibration,
    lifecycle_alert_keyboard,
    setup_alert_keyboard,
)
from app.notify.lifecycle import (
    closed_candidate,
    opened_candidate,
    pulse_candidate,
    transition_candidates,
)
from app.notify.rules import (
    RULE_LABELS,
    AlertCandidate,
    cooldown_seconds,
    evaluate_data_stall,
    evaluate_derivative_alerts,
    evaluate_performance_alerts,
    evaluate_position_alerts,
    morning_summary_due,
    quiet_hours_active,
    rearm_signals,
)
from app.notify.performance_report import format_weekly_performance
from app.notify.state import AlertRuleState, NotificationState
from app.structure.context import build_structure_context, detect_structure_transitions, transition_state_key
from app.notify.telegram import TelegramSender, inline_keyboard
from app.services import runtime as service

logger = logging.getLogger(__name__)


class AlertEngine:
    def __init__(
        self,
        settings: Settings,
        sender: TelegramSender,
        state: NotificationState,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.sender = sender
        self.state = state
        self._now_provider = now_provider

    async def evaluate_positions(self, payloads: list[dict[str, Any]]) -> int:
        if not self.settings.telegram_alerts_enabled or self.state.is_muted():
            return 0
        sent = 0
        for payload in payloads:
            context = await self._alert_context(payload)
            candidates = evaluate_position_alerts(context, self.settings)
            self._rearm(context, candidates)
            for candidate in candidates:
                sent += await self._fire_if_allowed(candidate)
        return sent

    async def evaluate_position_structure(self, payloads: list[dict[str, Any]]) -> int:
        """보유 포지션의 구조 관계 전이를 알린다 (WO-FCE-STRUCTURE-CONTEXT-01).

        **보유 포지션이 있을 때만 발화한다.** 시장 전체 와이코프 이벤트는 기존
        `wyckoff_event`가 담당하며 이 WO에서 변경하지 않는다. 전이가 없으면 0건이므로
        같은 구조 상태가 100틱 반복돼도 스팸이 생기지 않는다(C6).
        """
        if not self.settings.telegram_alerts_enabled or self.state.is_muted():
            return 0
        if "position_structure_event" not in self.settings.alert_enabled_rule_set:
            return 0
        sent = 0
        for payload in payloads:
            position: dict[str, Any] = _as_dict(payload.get("position")) or payload
            symbol = str(position.get("symbol") or "").upper()
            if not symbol:
                continue
            state: dict[str, Any] = _as_dict(payload.get("state"))
            analysis: dict[str, Any] = _as_dict(state.get("analysis"))
            if not analysis:
                continue
            levels: dict[str, Any] = _as_dict(analysis.get("price_levels"))
            zones = analysis.get("order_block_zones")
            previous: dict[str, Any] = _as_dict(self.state.structure_contexts.get(symbol))
            context = build_structure_context(
                analysis=analysis,
                entry_price=_float(position.get("entry_price")),
                mark_price=_float(analysis.get("mark_price") or position.get("mark_price")),
                invalidation_price=_float(levels.get("invalidation")),
                order_block_zones=[item for item in zones if isinstance(item, dict)] if isinstance(zones, list) else [],
                pnf_objective=_as_dict(analysis.get("pnf_measured_objective")) or None,
                previous_phase=str(_as_dict(previous.get("wyckoff")).get("phase") or "") or None,
            )
            events = detect_structure_transitions(previous or None, context)
            self.state.structure_contexts[symbol] = context
            for event in events:
                candidate = AlertCandidate(
                    rule_id="position_structure_event",
                    severity="info",
                    position_id=str(position.get("id") or "") or None,
                    symbol=symbol,
                    identity=transition_state_key(symbol, event, context),
                    title=RULE_LABELS["position_structure_event"],
                    message=format_structure_event(symbol, position, event, context),
                    payload={"event": event, "context": context},
                )
                sent += await self._fire_if_allowed(candidate)
        self._persist()
        return sent

    async def evaluate_lifecycle(self, sync_payload: dict[str, Any]) -> int:
        """WO-44: 진입/종료/판정 전이/스탠스 반전/근거 부족 알림."""
        if not self.settings.telegram_alerts_enabled or self.state.is_muted():
            return 0
        enabled = self.settings.alert_enabled_rule_set
        sent = 0

        if "position_opened" in enabled:
            for position_id in sync_payload.get("created_position_ids", []) or []:
                try:
                    context = await asyncio.to_thread(service.live_position_alert_context, UUID(str(position_id)))
                except Exception as exc:
                    logger.warning("opened alert context failed position_id=%s error=%s", position_id, exc)
                    continue
                sent += await self._fire_if_allowed(opened_candidate(context))

        if "position_closed" in enabled:
            for item in sync_payload.get("closed_positions", []) or []:
                if not isinstance(item, dict):
                    continue
                position = item.get("position") if isinstance(item.get("position"), dict) else {}
                trade = item.get("trade") if isinstance(item.get("trade"), dict) else None
                sent += await self._fire_if_allowed(closed_candidate(position, trade))
                self.state.lifecycle_positions.pop(str(position.get("id") or ""), None)

        transition_rules = {"verdict_changed", "stance_flipped", "evidence_insufficient"} & enabled
        if transition_rules:
            for payload in sync_payload.get("positions", []) or []:
                context = await self._alert_context(payload)
                # 실서비스 페이로드의 position은 Pydantic 모델일 수 있다 — dict로 정규화.
                position = _as_dict(context.get("position"))
                position_id = str(position.get("id") or "")
                if not position_id:
                    continue
                tracker = self.state.lifecycle_positions.get(position_id, {})
                candidates, updated = transition_candidates(context, tracker, self.settings, now=self._now())
                for candidate in candidates:
                    if candidate.rule_id in transition_rules:
                        sent += await self._fire_if_allowed(candidate)
                self.state.lifecycle_positions[position_id] = updated
        self._persist()
        return sent

    async def maybe_send_pulse(self, sync_payload: dict[str, Any]) -> int:
        """WO-44: 보유 중 정기 펄스 — "전부 정상"도 발송 (침묵과 고장의 구분). 미도달분 병합."""
        if not self.settings.telegram_alerts_enabled or self.state.is_muted():
            return 0
        if "periodic_pulse" not in self.settings.alert_enabled_rule_set:
            return 0
        now = self._now()
        interval_hours = max(0.25, float(getattr(self.settings, "alert_pulse_interval_hours", 4.0)))
        if self.state.last_pulse_at is not None and (now - self.state.last_pulse_at).total_seconds() < interval_hours * 3600:
            return 0
        contexts = []
        for payload in sync_payload.get("positions", []) or []:
            contexts.append(await self._alert_context(payload))
        tracked: list[dict[str, Any]] = []
        try:
            scout_payload = await asyncio.to_thread(service.scout_scan, 100)
            tracked = [item for item in scout_payload.get("tracked", []) if isinstance(item, dict)]
        except Exception:
            logger.exception("notify.periodic_pulse.tracked_load_failed")
        paper: dict[str, Any] | None = None
        try:
            paper = await asyncio.to_thread(service.paper_pulse_summary)
        except Exception:
            logger.exception("notify.periodic_pulse.paper_load_failed")
        candidate = pulse_candidate(contexts, tracked=tracked, paper=paper, pending_redelivery=self.state.pending_redelivery)
        if candidate is None:
            return 0
        if quiet_hours_active(self.settings, now):
            # 무음 준수 — 억제분은 아침 요약에 병합되고, 펄스 주기는 다음 창에서 재개.
            self._suppress(candidate)
            self._record(candidate, delivered=False, fired_at=now)
            self.state.last_pulse_at = now
            self._persist()
            return 0
        delivered_count = await self.sender.send_to_all(candidate.message)
        self._record(candidate, delivered=delivered_count > 0, fired_at=now)
        self.state.last_pulse_at = now
        if delivered_count > 0:
            self.state.pending_redelivery.clear()
        self._persist()
        return delivered_count

    async def evaluate_worker_status(self, worker_status: dict[str, Any]) -> int:
        if not self.settings.telegram_alerts_enabled or self.state.is_muted():
            return 0
        sent = 0
        for candidate in evaluate_data_stall(worker_status, self.settings):
            sent += await self._fire_if_allowed(candidate)
        return sent

    async def evaluate_liveness_alerts(self, candidates: list[AlertCandidate]) -> int:
        """WO-FCE-ENGINE-LIVENESS-01 (C2): 생존/사망 신호는 **뮤트를 관통한다.**

        뮤트는 *조건 알림*을 끄는 장치지 심장박동을 끄는 장치가 아니다. 뮤트가 사망 경보까지
        침묵시키면 "조용함"이 정상인지 고장인지 영원히 구분할 수 없다(이번 사고의 구조적 원인).
        쿨다운·중복 억제는 그대로 적용되어 스팸을 막는다.
        """
        if not self.settings.telegram_alerts_enabled:
            return 0
        sent = 0
        for candidate in candidates:
            sent += await self._fire_if_allowed(candidate)
        return sent

    async def evaluate_derivatives(self, snapshots: list[dict[str, Any]]) -> int:
        if not self.settings.telegram_alerts_enabled or self.state.is_muted():
            return 0
        candidates = evaluate_derivative_alerts(snapshots, self.settings)
        self._rearm_derivatives(snapshots, candidates)
        sent = 0
        for candidate in candidates:
            sent += await self._fire_if_allowed(candidate)
        return sent

    async def evaluate_performance(self, payload: dict[str, Any]) -> int:
        if not self.settings.telegram_alerts_enabled or self.state.is_muted():
            return 0
        sent = 0
        for candidate in evaluate_performance_alerts(payload, self.settings):
            sent += await self._fire_if_allowed(candidate)
        return sent

    async def evaluate_scout_setups(self, candidates: list[AlertCandidate]) -> int:
        if not self.settings.telegram_alerts_enabled or self.state.is_muted():
            return 0
        sent = 0
        for candidate in candidates:
            sent += await self._fire_if_allowed(candidate)
        return sent

    async def evaluate_whale_events(self, events: list[dict[str, Any]], dashboard: dict[str, Any]) -> int:
        if not self.settings.telegram_alerts_enabled or self.state.is_muted() or "whale_entry" not in self.settings.alert_enabled_rule_set:
            return 0
        wallets_value = dashboard.get("wallets")
        wallets: list[dict[str, Any]] = [item for item in wallets_value if isinstance(item, dict)] if isinstance(wallets_value, list) else []
        wallets_by_address = {str(item.get("address") or "").lower(): item for item in wallets if str(item.get("address") or "").strip()}
        now = self._now()
        window_seconds = max(30, int(self.settings.hyperliquid_whale_alert_batch_window_seconds))
        _queue_whale_alert_events(self.state, events, received_at=now)
        sent = 0
        for address in sorted(list(self.state.whale_alert_events)):
            batches, pending = _ready_whale_alert_batches(
                self.state.whale_alert_events.get(address, []),
                now=now,
                window_seconds=window_seconds,
            )
            if pending:
                self.state.whale_alert_events[address] = pending
            else:
                self.state.whale_alert_events.pop(address, None)
            wallet = wallets_by_address.get(address, {})
            for batch in batches:
                # 한 건뿐인 체결은 사용자 요구에 따라 조용히 폐기한다. 원시 원장은 유지된다.
                if len(batch) < 2:
                    continue
                sent += await self._fire_if_allowed(_whale_batch_candidate(batch, wallet, window_seconds=window_seconds))
        self._persist()
        return sent

    async def maybe_send_daily_summary(
        self,
        payload: dict[str, Any],
        liveness_lines: list[str] | None = None,
        performance_lines: list[str] | None = None,
    ) -> int:
        """WO-FCE-ENGINE-LIVENESS-01 작업 5: 트랙 생존 라인은 뮤트를 관통한다(C2).

        뮤트 중이면 조건 알림 다이제스트는 생략하고 **생존 라인만** 보낸다 — 뮤트가 "살아있음"의
        증거까지 지우면 침묵이 다시 스스로를 은폐한다.
        """
        if not self.settings.telegram_alerts_enabled:
            return 0
        muted = self.state.is_muted()
        if muted and not liveness_lines:
            return 0
        due, date_key = morning_summary_due(self.settings, self.state.last_summary_date, self._now())
        if not due:
            return 0
        lines: list[str] = []
        if not muted:
            suppressed = self.state.suppressed_alerts
            lines.append("<b>밤새 알림 요약</b>")
            if suppressed:
                for item in suppressed[:20]:
                    lines.append(f"• {item.get('emoji', '🟡')} <b>{item.get('symbol', '-')}</b> — {item.get('title', '-')}")
                    if item.get("summary"):
                        lines.append(f"  {item['summary']}")
            else:
                lines.append("억제된 알림은 없습니다.")
            lines.append("")
            lines.append(format_positions_summary(payload))
            # WO-FCE-PERFORMANCE-REPORT-01 §2-1: 페이퍼 4트랙 성과. 진입이 0인 트랙도
            # 반드시 등장한다 — 이벤트가 없어서 침묵하던 구조를 여기서 제거한다.
            # 실계좌 포지션 요약(format_positions_summary)과 별도 블록으로 둔다(C4).
            if performance_lines:
                lines.append("")
                lines.extend(performance_lines)
        else:
            lines.append("<b>생존 신호</b> (뮤트 중 — 조건 알림은 억제됨)")
        if liveness_lines:
            lines.append("")
            lines.extend(liveness_lines)
        count = await self.sender.send_to_all("\n".join(lines))
        if count:
            self.state.suppressed_alerts.clear()
            self.state.last_summary_date = date_key
        return count

    async def maybe_send_weekly_performance_report(self) -> int:
        """WO-FCE-PERFORMANCE-REPORT-01 §2-2: 주간 성과 + 4주 검증 진행도.

        N>=30 도달 불가 판정은 **매주 반복 보고**한다 — 문제를 잊지 않게 하는 것이 목적이다.
        """
        if not self.settings.telegram_alerts_enabled or self.state.is_muted():
            return 0
        due, date_key = _weekly_calibration_due(self.settings, self.state.last_weekly_performance_date, self._now())
        if not due:
            return 0
        payload = await asyncio.to_thread(service.paper_performance)
        count = await self.sender.send_to_all(format_weekly_performance(payload))
        if count:
            self.state.last_weekly_performance_date = date_key
            self._persist()
        return count

    async def maybe_send_weekly_calibration_report(self) -> int:
        if not self.settings.telegram_alerts_enabled or self.state.is_muted() or not self.settings.telegram_weekly_calibration_enabled:
            return 0
        due, date_key = _weekly_calibration_due(self.settings, self.state.last_weekly_calibration_date, self._now())
        if not due:
            return 0
        payload = await asyncio.to_thread(service.weekly_calibration_report)
        count = await self.sender.send_to_all(format_weekly_calibration(payload))
        if count:
            self.state.last_weekly_calibration_date = date_key
        return count

    async def _alert_context(self, payload: dict[str, Any]) -> dict[str, Any]:
        # chart_analysis까지 있어야 완전한 컨텍스트다. action_plan만 보고 조기 반환하면
        # wyckoff_event 규칙과 스탠스 추적이 조용히 죽는다 (발화 0건의 실원인 — AlertAudit 참조).
        if payload.get("action_plan") and payload.get("chart_analysis"):
            return payload
        position = _as_dict(payload.get("position"))
        position_id = position.get("id")
        if not position_id:
            return payload
        try:
            return await asyncio.to_thread(service.live_position_alert_context, UUID(str(position_id)))
        except Exception as exc:
            logger.warning("alert context fallback position_id=%s error=%s", position_id, exc)
            return payload

    def _rearm(self, payload: dict[str, Any], candidates: list[AlertCandidate]) -> None:
        active_keys = {candidate.state_key for candidate in candidates}
        signals = rearm_signals(payload, self.settings)
        now = self._now()
        for key, rule_state in list(self.state.alert_rule_states.items()):
            if key in active_keys:
                continue
            if not _same_position_key(key, payload):
                continue
            cooldown_done = rule_state.cooldown_until is None or now >= rule_state.cooldown_until
            if cooldown_done and signals.get(key, _generic_rearm_allowed(key)):
                rule_state.status = "armed"

    def _rearm_derivatives(self, snapshots: list[dict[str, Any]], candidates: list[AlertCandidate]) -> None:
        active_keys = {candidate.state_key for candidate in candidates}
        symbols = {str(snapshot.get("symbol") or "").upper() for snapshot in snapshots if snapshot.get("symbol")}
        threshold = abs(self.settings.alert_funding_extreme_abs_rate)
        now = self._now()
        for key, rule_state in list(self.state.alert_rule_states.items()):
            if not key.startswith("funding_extreme:"):
                continue
            if key in active_keys:
                continue
            parts = key.split(":", 2)
            identity = parts[2] if len(parts) == 3 else ""
            symbol = identity.split(":", 1)[0].upper()
            if not symbol or symbol not in symbols:
                continue
            cooldown_done = rule_state.cooldown_until is None or now >= rule_state.cooldown_until
            if not cooldown_done:
                continue
            matching = next(
                (snapshot for snapshot in snapshots if str(snapshot.get("symbol") or "").upper() == symbol),
                None,
            )
            funding = _float(matching.get("funding_rate")) if isinstance(matching, dict) else None
            if funding is None or abs(funding) < threshold:
                rule_state.status = "armed"

    async def _fire_if_allowed(self, candidate: AlertCandidate) -> int:
        now = self._now()
        rule_state = self.state.alert_rule_states.setdefault(candidate.state_key, AlertRuleState())
        if candidate.rule_id.startswith("setup_") and rule_state.cooldown_until is not None and now >= rule_state.cooldown_until:
            rule_state.status = "armed"
        if rule_state.status != "armed":
            return 0
        if rule_state.cooldown_until is not None and now < rule_state.cooldown_until:
            return 0

        cooldown = cooldown_seconds(candidate.severity, self.settings)
        rule_state.status = "cooldown"
        rule_state.last_fired_at = now
        rule_state.cooldown_until = now + _seconds(cooldown)
        rule_state.last_payload = candidate.payload

        enriched = self._with_response_history(candidate)

        if quiet_hours_active(self.settings, now) and enriched.severity != "critical":
            self._suppress(enriched)
            self._record(enriched, delivered=False, fired_at=now)
            return 0

        delivered_count = await self.sender.send_to_all(enriched.message, reply_markup=self._reply_markup(enriched))
        self._record(enriched, delivered=delivered_count > 0, fired_at=now)
        if delivered_count == 0 and self.sender.enabled:
            # WO-44 Part C: 발송 실패분은 다음 pulse에 병합해 도달을 보증한다.
            self.state.pending_redelivery.append(
                {
                    "rule_id": enriched.rule_id,
                    "symbol": enriched.symbol,
                    "title": enriched.title,
                    "fired_at": now.isoformat(),
                }
            )
            self.state.pending_redelivery = self.state.pending_redelivery[-50:]
        self._persist()
        return delivered_count

    def _persist(self) -> None:
        path = str(getattr(self.settings, "notification_state_path", "") or "")
        if path:
            self.state.save(path)

    def _with_response_history(self, candidate: AlertCandidate) -> AlertCandidate:
        try:
            line = service.alert_response_history_line(candidate.rule_id)
        except Exception as exc:
            logger.debug(
                "alert response history unavailable rule=%s error=%s",
                candidate.rule_id,
                exc,
            )
            line = None
        if not line:
            return candidate
        return AlertCandidate(
            rule_id=candidate.rule_id,
            severity=candidate.severity,
            position_id=candidate.position_id,
            symbol=candidate.symbol,
            identity=candidate.identity,
            title=candidate.title,
            message=f"{candidate.message}\n{line}",
            payload={**candidate.payload, "response_history_line": line},
        )

    def _suppress(self, candidate: AlertCandidate) -> None:
        self.state.suppressed_alerts.append(
            {
                "rule_id": candidate.rule_id,
                "symbol": candidate.symbol,
                "title": candidate.title,
                "severity": candidate.severity,
                "emoji": _severity_emoji(candidate.severity),
                "summary": _summary_line(candidate.payload),
                "payload": candidate.payload,
            }
        )

    def _record(self, candidate: AlertCandidate, *, delivered: bool, fired_at: datetime) -> None:
        record = AlertRecord(
            rule_id=candidate.rule_id,
            position_id=UUID(candidate.position_id) if candidate.position_id else None,
            symbol=candidate.symbol,
            severity=candidate.severity,
            fired_at=fired_at,
            payload={
                **candidate.payload,
                "title": candidate.title,
                "message": candidate.message,
                "state_key": candidate.state_key,
            },
            delivered=delivered,
        )
        try:
            service.record_alert(record)
        except Exception as exc:
            logger.warning(
                "failed to record alert rule=%s symbol=%s error=%s",
                candidate.rule_id,
                candidate.symbol,
                exc,
            )

    def _reply_markup(self, candidate: AlertCandidate) -> dict[str, Any] | None:
        if candidate.payload.get("kind") in {"scout_setup", "entry_intent", "universe_discovery"}:
            return inline_keyboard(setup_alert_keyboard(candidate.symbol, candidate.payload.get("direction")))
        if candidate.payload.get("kind") in {"lifecycle", "lifecycle_pulse"}:
            return inline_keyboard(lifecycle_alert_keyboard(candidate.rule_id, candidate.symbol))
        if not candidate.position_id or candidate.symbol == "SYSTEM":
            return None
        return inline_keyboard(alert_keyboard(candidate.symbol))

    def _now(self) -> datetime:
        now = self._now_provider() if self._now_provider else datetime.now(timezone.utc)
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {}


def _same_position_key(key: str, payload: dict[str, Any]) -> bool:
    position = _as_dict(payload.get("position"))
    position_id = str(position.get("id") or "")
    if not position_id:
        return False
    parts = key.split(":", 2)
    return len(parts) >= 2 and parts[1] == position_id


def _generic_rearm_allowed(key: str) -> bool:
    rule_id = key.split(":", 1)[0]
    return rule_id in {
        "status_worsened",
        "health_drop",
        "wyckoff_event",
        "data_stall",
        "funding_extreme",
        "oi_divergence",
        "liq_cluster_near",
    }


def _seconds(value: int):
    from datetime import timedelta

    return timedelta(seconds=max(1, value))


def _severity_emoji(severity: str) -> str:
    if severity == "critical":
        return "🔴"
    if severity == "warn":
        return "🟠"
    if severity == "action":
        return "🟢"
    return "🟡"


def _summary_line(payload: dict[str, Any]) -> str:
    if payload.get("summary"):
        return str(payload["summary"])
    current = payload.get("current_price")
    trigger = payload.get("trigger_price")
    if trigger is not None and current is not None:
        return f"기준 {trigger} · 현재 {current}"
    if payload.get("health_score") is not None:
        return f"건강도 {payload.get('health_score')} · PnL {payload.get('pnl_percent')}"
    return ""


def _weekly_calibration_due(settings: Settings, last_date_key: str | None, now: datetime) -> tuple[bool, str]:
    try:
        local_timezone = ZoneInfo(settings.telegram_quiet_hours_timezone)
    except ZoneInfoNotFoundError:
        local_timezone = timezone.utc
    current = now.astimezone(local_timezone)
    day = min(max(int(settings.telegram_weekly_calibration_day), 0), 6)
    date_key = current.strftime("%Y-%m-%d")
    if current.weekday() != day or last_date_key == date_key:
        return False, date_key
    target = settings.telegram_weekly_calibration_time.strip()
    return current.strftime("%H:%M") == target, date_key


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compact_usd(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K"
    return f"{value:.0f}"


def _queue_whale_alert_events(state: NotificationState, events: list[dict[str, Any]], *, received_at: datetime) -> None:
    known = {str(item.get("_batch_identity") or "") for pending in state.whale_alert_events.values() for item in pending if isinstance(item, dict)}
    for event in events:
        if not isinstance(event, dict) or event.get("event") not in {"open", "increase", "flip"}:
            continue
        raw_value = event.get("payload")
        raw: dict[str, Any] = raw_value if isinstance(raw_value, dict) else {}
        if raw.get("baseline"):
            continue
        address = str(event.get("wallet_address") or "").strip().lower()
        if not address:
            continue
        identity = _whale_event_identity(event)
        if identity in known:
            continue
        queued = dict(event)
        queued["_batch_identity"] = identity
        queued["_batch_received_at"] = received_at.isoformat()
        state.whale_alert_events.setdefault(address, []).append(queued)
        known.add(identity)


def _ready_whale_alert_batches(
    events: list[dict[str, Any]],
    *,
    now: datetime,
    window_seconds: int,
) -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]]]:
    remaining = sorted(
        [item for item in events if isinstance(item, dict)],
        key=lambda item: (_whale_event_time(item, now), str(item.get("_batch_identity") or "")),
    )
    ready: list[list[dict[str, Any]]] = []
    window = _seconds(window_seconds)
    while remaining:
        first = remaining[0]
        received_at = _parse_whale_datetime(first.get("_batch_received_at"), now)
        event_started_at = _whale_event_time(first, received_at)
        # 체결 시각 기준 3분 창이 닫힌 첫 30초 폴링에서 확정한다. 수집 지연으로
        # 이미 창이 닫힌 이벤트는 관측 즉시 판정하되, 미래 시각으로 앞당기지 않는다.
        if now < max(event_started_at + window, received_at):
            break
        event_deadline = event_started_at + window
        batch = [item for item in remaining if _whale_event_time(item, received_at) <= event_deadline]
        batch_ids = {str(item.get("_batch_identity") or "") for item in batch}
        remaining = [item for item in remaining if str(item.get("_batch_identity") or "") not in batch_ids]
        ready.append(batch)
    return ready, remaining


def _whale_batch_candidate(
    events: list[dict[str, Any]],
    wallet: dict[str, Any],
    *,
    window_seconds: int,
) -> AlertCandidate:
    ordered = sorted(events, key=lambda item: _whale_event_time(item, datetime.min.replace(tzinfo=timezone.utc)))
    first = ordered[0]
    address = str(first.get("wallet_address") or "").lower()
    label = str(first.get("wallet_label") or wallet.get("label") or "고래")
    review_value = wallet.get("review")
    review: dict[str, Any] = review_value if isinstance(review_value, dict) else {}
    validated = review.get("trust_status") == "trusted" or review.get("state") == "validated"
    groups = _summarize_whale_batch(ordered)
    coins = list(dict.fromkeys(str(item.get("coin") or "HL") for item in ordered))
    total_notional = sum(float(item.get("size_usd") or 0.0) for item in ordered)
    minutes = max(1, round(window_seconds / 60))
    lines = [f"🐋 <b>{escape(label)} · {minutes}분 다중체결 {len(ordered)}건 · {len(coins)}종목 · {_compact_usd(total_notional)}</b>"]
    for group in groups[:12]:
        count = int(group["count"])
        count_text = f" {count}건" if count > 1 else ""
        lines.append(
            f"• <b>{escape(str(group['coin']))}</b> {group['action']}{count_text} · "
            f"{_compact_usd(float(group['size_usd']))} @ {_format_whale_price(float(group['entry_px']))}"
        )
    if len(groups) > 12:
        lines.append(f"• 그 외 {len(groups) - 12}개 체결 조합")

    sample_size = int(review.get("sample_size") or 0)
    win_rate = review.get("win_1r_pct")
    accuracy = f"추종 승률 {win_rate}% (N={sample_size})" if win_rate is not None else f"추종 승률 축적 중 (N={sample_size})"
    cumulative_r = float(review.get("cumulative_return_r") or 0.0)
    elapsed_days = int(review.get("validation_days") or 0)
    remaining_days = int(review.get("validation_remaining_days") or max(0, 28 - elapsed_days))
    performance = f"누적 {cumulative_r:+.2f}R · 4주 검증 {elapsed_days}/28일"
    if remaining_days:
        performance += f" ({remaining_days}일 남음)"
    level = "엄선 고래" if validated else "미검증 관측"
    lines.extend(
        [
            f"{level} · {accuracy}",
            performance,
            "3분 창의 다중체결만 묶은 관측 정보이며 따라가기 신호가 아닙니다. 별칭은 사용자 지정 추정입니다.",
        ]
    )
    compact_events = [_compact_whale_event(item) for item in ordered]
    first_identity = str(first.get("_batch_identity") or _whale_event_identity(first))
    last_identity = str(ordered[-1].get("_batch_identity") or _whale_event_identity(ordered[-1]))
    symbol = str(first.get("symbol") or first.get("coin") or "HL") if len(coins) == 1 else "MULTI"
    return AlertCandidate(
        rule_id="whale_entry",
        severity="warn" if validated else "info",
        position_id=None,
        symbol=symbol,
        identity=f"{address}:{first_identity}:{last_identity}:{len(ordered)}",
        title=f"{label} {minutes}분 다중체결 {len(ordered)}건",
        message="\n".join(lines),
        payload={
            "kind": "whale_multi_fill",
            "wallet_address": address,
            "wallet_label": label,
            "validation_state": review.get("state"),
            "window_seconds": window_seconds,
            "fill_count": len(ordered),
            "instrument_count": len(coins),
            "event_ids": [item["id"] for item in compact_events],
            "events": compact_events,
            "summary": f"{minutes}분 다중체결 {len(ordered)}건 · {len(coins)}종목 · {_compact_usd(total_notional)} · {accuracy}",
        },
    )


def _summarize_whale_batch(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for event in events:
        coin = str(event.get("coin") or "HL")
        side = "long" if event.get("side") == "long" else "short"
        kind = str(event.get("event") or "open")
        key = (coin, side, kind)
        row = grouped.setdefault(
            key,
            {
                "coin": coin,
                "action": _whale_action(kind, side),
                "count": 0,
                "size_usd": 0.0,
                "size": 0.0,
                "weighted_entry": 0.0,
            },
        )
        size = abs(float(event.get("size") or 0.0))
        entry = float(event.get("entry_px") or 0.0)
        row["count"] += 1
        row["size_usd"] += float(event.get("size_usd") or 0.0)
        row["size"] += size
        row["weighted_entry"] += entry * size
    result = []
    for row in grouped.values():
        size = float(row.pop("size"))
        weighted_entry = float(row.pop("weighted_entry"))
        row["entry_px"] = weighted_entry / size if size > 0 else 0.0
        result.append(row)
    return result


def _whale_action(kind: str, side: str) -> str:
    side_label = "롱" if side == "long" else "숏"
    if kind == "flip":
        return "숏→롱 전환" if side == "long" else "롱→숏 전환"
    if kind == "increase":
        return f"{side_label} 증액"
    return f"{side_label} 신규"


def _compact_whale_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(event.get("id") or event.get("fill_id") or _whale_event_identity(event)),
        "fill_id": event.get("fill_id"),
        "coin": event.get("coin"),
        "symbol": event.get("symbol"),
        "side": event.get("side"),
        "event": event.get("event"),
        "size": event.get("size"),
        "size_usd": event.get("size_usd"),
        "entry_px": event.get("entry_px"),
        "event_at": event.get("event_at"),
    }


def _whale_event_identity(event: dict[str, Any]) -> str:
    return str(
        event.get("id")
        or event.get("fill_id")
        or ":".join(
            [
                str(event.get("wallet_address") or ""),
                str(event.get("coin") or ""),
                str(event.get("event") or ""),
                str(event.get("event_at") or ""),
                str(event.get("size") or ""),
            ]
        )
    )


def _whale_event_time(event: dict[str, Any], fallback: datetime) -> datetime:
    return _parse_whale_datetime(event.get("event_at"), fallback)


def _parse_whale_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return fallback
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _format_whale_price(value: float) -> str:
    if value < 1:
        return f"{value:,.6f}"
    if value < 100:
        return f"{value:,.4f}"
    return f"{value:,.2f}"


def format_structure_event(symbol: str, position: dict[str, Any], event: dict[str, Any], context: dict[str, Any]) -> str:
    """구조 이벤트 메시지. 관측 서술만 담고 매매 신호를 만들지 않는다(C4)."""
    from html import escape

    direction = "롱" if str(position.get("direction") or "").lower() == "long" else "숏"
    relation = context.get("position_relation") if isinstance(context.get("position_relation"), dict) else {}
    wyckoff = context.get("wyckoff") if isinstance(context.get("wyckoff"), dict) else {}
    headline = {
        "range_exit": "레인지 이탈",
        "range_enter": "레인지 진입",
        "order_block_enter": "오더블록 진입",
        "order_block_exit": "오더블록 이탈",
        "phase_change": "국면 전환",
        "spring_or_lps": "Spring/LPS 확정",
        "pnf_target_reached": "PNF 측정 목표 도달",
    }.get(str(event.get("kind")), "구조 변화")

    lines = [f"<b>🏗 구조 이벤트 · {escape(symbol)} ({direction} 보유)</b>"]
    if event.get("kind") == "phase_change":
        lines.append(f"· 국면 {escape(str(event.get('from')))} → {escape(str(event.get('to')))}")
    elif event.get("kind") in {"range_exit", "range_enter"}:
        bound = wyckoff.get("range_low") if event.get("to") == "below" else wyckoff.get("range_high")
        lines.append(f"· {headline} (경계 {bound})")
    else:
        lines.append(f"· {headline}")
    entry = relation.get("entry_price")
    invalidation = relation.get("invalidation_price")
    if entry is not None:
        tail = f" · 무효화 {invalidation}" if invalidation is not None else ""
        lines.append(f"· 내 진입 {entry}{tail}")
    markers = wyckoff.get("recent_markers") or []
    if markers:
        lines.append(f"· 최근 마커: {escape(str(markers[0].get('label') or '-'))}")
    if (context.get("repaint") or {}).get("repainted"):
        lines.append("· 국면 재해석됨 — 이전 판정과 상이")
    lines.append("관측 정보이며 매매 신호가 아닙니다.")
    return "\n".join(lines)
