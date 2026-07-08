from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import Settings
from app.db.models import AlertRecord
from app.notify.bot.formatters import (
    alert_keyboard,
    format_positions_summary,
    format_weekly_calibration,
    setup_alert_keyboard,
)
from app.notify.lifecycle import (
    closed_candidate,
    opened_candidate,
    pulse_candidate,
    transition_candidates,
)
from app.notify.rules import (
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
from app.notify.state import AlertRuleState, NotificationState
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
        candidate = pulse_candidate(contexts, pending_redelivery=self.state.pending_redelivery)
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

    async def maybe_send_daily_summary(self, payload: dict[str, Any]) -> int:
        if not self.settings.telegram_alerts_enabled or self.state.is_muted():
            return 0
        due, date_key = morning_summary_due(self.settings, self.state.last_summary_date, self._now())
        if not due:
            return 0
        suppressed = self.state.suppressed_alerts
        lines = ["<b>밤새 알림 요약</b>"]
        if suppressed:
            for item in suppressed[:20]:
                lines.append(f"• {item.get('emoji', '🟡')} <b>{item.get('symbol', '-')}</b> — {item.get('title', '-')}")
                if item.get("summary"):
                    lines.append(f"  {item['summary']}")
        else:
            lines.append("억제된 알림은 없습니다.")
        lines.append("")
        lines.append(format_positions_summary(payload))
        count = await self.sender.send_to_all("\n".join(lines))
        if count:
            self.state.suppressed_alerts.clear()
            self.state.last_summary_date = date_key
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
        if payload.get("action_plan"):
            return payload
        position = payload.get("position") if isinstance(payload.get("position"), dict) else {}
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
