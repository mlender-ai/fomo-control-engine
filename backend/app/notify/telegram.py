from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.core.config import Settings
from app.notify.bot.formatters import split_telegram_text

logger = logging.getLogger(__name__)


class TelegramSender:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._missing_config_warned = False

    @property
    def enabled(self) -> bool:
        if self.settings.demo_mode:
            return True
        return bool(self.settings.telegram_bot_token.strip() and self.settings.telegram_allowed_chat_id_list)

    async def send_to_all(self, text: str, *, reply_markup: dict[str, Any] | None = None) -> int:
        if self.settings.demo_mode:
            logger.info("demo telegram sink message=%s reply_markup=%s", text, reply_markup)
            return 1
        if not self.enabled:
            if not self._missing_config_warned:
                logger.warning("telegram sender disabled: FCE_TELEGRAM_BOT_TOKEN or FCE_TELEGRAM_CHAT_ID is missing")
                self._missing_config_warned = True
            return 0
        sent = 0
        for chat_id in self.settings.telegram_allowed_chat_id_list:
            sent += await self.send_message(chat_id, text, reply_markup=reply_markup)
        return sent

    async def send_message(self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None) -> int:
        if self.settings.demo_mode:
            logger.info("demo telegram sink chat_id=%s message=%s reply_markup=%s", chat_id, text, reply_markup)
            return 1
        if not self.settings.telegram_bot_token.strip():
            return 0
        chunks = split_telegram_text(text)
        sent = 0
        async with httpx.AsyncClient(timeout=10) as client:
            for index, chunk in enumerate(chunks):
                payload: dict[str, Any] = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
                if reply_markup and index == len(chunks) - 1:
                    payload["reply_markup"] = reply_markup
                for attempt in range(3):
                    try:
                        response = await client.post(
                            f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage",
                            json=payload,
                        )
                        response.raise_for_status()
                        sent += 1
                        break
                    except httpx.HTTPError as exc:
                        if attempt == 2:
                            logger.warning(
                                "telegram send failed chat_id=%s chunk=%s error=%s",
                                chat_id,
                                index + 1,
                                exc,
                            )
                            continue
                        await asyncio.sleep(0.5 * (2**attempt))
        return sent


def inline_keyboard(rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    return {"inline_keyboard": rows}
