from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


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

    def is_muted(self) -> bool:
        return self.muted_until is not None and datetime.now(timezone.utc) < self.muted_until

    def mute_for(self, seconds: int) -> datetime:
        self.muted_until = datetime.now(timezone.utc) + timedelta(seconds=max(1, seconds))
        return self.muted_until

    def unmute(self) -> None:
        self.muted_until = None


notification_state = NotificationState()
