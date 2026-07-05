from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class NotificationState:
    muted_until: datetime | None = None
    sent_alert_keys: dict[str, datetime] = field(default_factory=dict)
    last_summary_date: str | None = None

    def is_muted(self) -> bool:
        return self.muted_until is not None and datetime.now(timezone.utc) < self.muted_until

    def mute_for(self, seconds: int) -> datetime:
        self.muted_until = datetime.now(timezone.utc) + timedelta(seconds=max(1, seconds))
        return self.muted_until

    def unmute(self) -> None:
        self.muted_until = None


notification_state = NotificationState()

