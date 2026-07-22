from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AlertRuleState:
    status: str = "armed"
    last_fired_at: datetime | None = None
    cooldown_until: datetime | None = None
    last_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class NotificationState:
    muted_until: datetime | None = None
    sent_alert_keys: dict[str, datetime] = field(default_factory=dict)
    last_summary_date: str | None = None
    last_weekly_calibration_date: str | None = None
    alert_rule_states: dict[str, AlertRuleState] = field(default_factory=dict)
    suppressed_alerts: list[dict[str, Any]] = field(default_factory=list)
    # WO-44: 포지션별 라이프사이클 트래커 (verdict/stance/insufficient) — 영속 대상.
    lifecycle_positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_pulse_at: datetime | None = None
    # WO-44 Part C: 발송 실패분 — 다음 pulse에 병합.
    pending_redelivery: list[dict[str, Any]] = field(default_factory=list)
    # 같은 고래의 짧은 시간 내 진입 체결을 Telegram 한 건으로 묶기 위한 대기열.
    # 원시 체결 원장은 DB에 그대로 남고, 이 상태는 알림 표현만 지연한다.
    whale_alert_events: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def is_muted(self) -> bool:
        return self.muted_until is not None and datetime.now(timezone.utc) < self.muted_until

    def mute_for(self, seconds: int) -> datetime:
        self.muted_until = datetime.now(timezone.utc) + timedelta(seconds=max(1, seconds))
        return self.muted_until

    def unmute(self) -> None:
        self.muted_until = None

    # ── 영속화 (WO-44 Part C) ──────────────────────────────────────
    # 콰이어트 아워 억제분·아침 요약 상태·라이프사이클 트래커가 인메모리라
    # 재시작 시 유실되던 결함의 수리. 실패해도 알림 경로는 계속 동작한다.

    def save(self, path: str) -> None:
        try:
            payload = {
                "muted_until": _iso(self.muted_until),
                "last_summary_date": self.last_summary_date,
                "last_weekly_calibration_date": self.last_weekly_calibration_date,
                "suppressed_alerts": self.suppressed_alerts[-100:],
                "lifecycle_positions": self.lifecycle_positions,
                "last_pulse_at": _iso(self.last_pulse_at),
                "pending_redelivery": self.pending_redelivery[-50:],
                "whale_alert_events": {address: events[-200:] for address, events in self.whale_alert_events.items() if events},
                "alert_rule_states": {
                    key: {
                        "status": rule.status,
                        "last_fired_at": _iso(rule.last_fired_at),
                        "cooldown_until": _iso(rule.cooldown_until),
                    }
                    for key, rule in list(self.alert_rule_states.items())[-500:]
                },
            }
            directory = os.path.dirname(os.path.abspath(path)) or "."
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".notif_state_")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, default=str)
            os.replace(tmp, path)
        except Exception as exc:
            logger.warning("notification state save failed: %s", exc)

    def load(self, path: str) -> None:
        try:
            if not os.path.exists(path):
                return
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            logger.warning("notification state load failed: %s", exc)
            return
        self.muted_until = _parse_dt(payload.get("muted_until"))
        self.last_summary_date = payload.get("last_summary_date")
        self.last_weekly_calibration_date = payload.get("last_weekly_calibration_date")
        self.suppressed_alerts = [item for item in payload.get("suppressed_alerts", []) if isinstance(item, dict)]
        self.lifecycle_positions = {str(key): value for key, value in (payload.get("lifecycle_positions") or {}).items() if isinstance(value, dict)}
        self.last_pulse_at = _parse_dt(payload.get("last_pulse_at"))
        self.pending_redelivery = [item for item in payload.get("pending_redelivery", []) if isinstance(item, dict)]
        self.whale_alert_events = {
            str(address).lower(): [item for item in events if isinstance(item, dict)][-200:]
            for address, events in (payload.get("whale_alert_events") or {}).items()
            if isinstance(events, list)
        }
        for key, raw in (payload.get("alert_rule_states") or {}).items():
            if not isinstance(raw, dict):
                continue
            self.alert_rule_states[str(key)] = AlertRuleState(
                status=str(raw.get("status") or "armed"),
                last_fired_at=_parse_dt(raw.get("last_fired_at")),
                cooldown_until=_parse_dt(raw.get("cooldown_until")),
            )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


notification_state = NotificationState()
