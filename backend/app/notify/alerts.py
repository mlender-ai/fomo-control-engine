from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.core.config import Settings
from app.notify.bot.formatters import alert_keyboard, format_position_verdict, format_positions_summary
from app.notify.state import NotificationState
from app.notify.telegram import TelegramSender, inline_keyboard

logger = logging.getLogger(__name__)


class AlertEngine:
    def __init__(self, settings: Settings, sender: TelegramSender, state: NotificationState) -> None:
        self.settings = settings
        self.sender = sender
        self.state = state

    async def evaluate_positions(self, payloads: list[dict[str, Any]]) -> int:
        if not self.settings.telegram_alerts_enabled or self.state.is_muted():
            return 0
        sent = 0
        now = datetime.now(timezone.utc)
        for payload in payloads:
            state = payload.get("state", {})
            position = payload.get("position", {})
            severity = int(_value(state, "severity_rank", 0) or 0)
            if severity < 4:
                continue
            symbol = str(_value(position, "symbol", ""))
            key = f"critical:{_value(position, 'id', symbol)}:{_value(state, 'status_label', '')}:{_value(state, 'health_score', '')}"
            last_sent = self.state.sent_alert_keys.get(key)
            if last_sent and (now - last_sent).total_seconds() < self.settings.telegram_alert_min_interval_seconds:
                continue
            count = await self.sender.send_to_all(
                format_position_verdict(payload),
                reply_markup=inline_keyboard(alert_keyboard(symbol)),
            )
            if count:
                self.state.sent_alert_keys[key] = now
                sent += count
        return sent

    async def maybe_send_daily_summary(self, payload: dict[str, Any]) -> int:
        if not self.settings.telegram_alerts_enabled or self.state.is_muted():
            return 0
        now = datetime.now().astimezone()
        target = self.settings.telegram_daily_summary_time.strip()
        if now.strftime("%H:%M") != target:
            return 0
        date_key = now.strftime("%Y-%m-%d")
        if self.state.last_summary_date == date_key:
            return 0
        count = await self.sender.send_to_all(format_positions_summary(payload))
        if count:
            self.state.last_summary_date = date_key
        return count


def _value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)
