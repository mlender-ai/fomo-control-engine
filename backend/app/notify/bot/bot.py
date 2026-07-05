from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.core.config import Settings
from app.notify.bot.callbacks import parse_callback
from app.notify.bot.formatters import (
    detail_keyboard,
    format_action_plan,
    format_calibration,
    format_help,
    format_insight,
    format_position_verdict,
    format_positions_summary,
    format_reviews,
    format_scout,
    format_simulation,
    format_status,
    positions_keyboard,
)
from app.notify.bot.security import ChatGuard
from app.notify.state import NotificationState
from app.notify.telegram import inline_keyboard
from app.services import runtime as service
from app.worker.runtime import get_worker_status

logger = logging.getLogger(__name__)


class TelegramBotSupervisor:
    def __init__(self, settings: Settings, state: NotificationState) -> None:
        self.settings = settings
        self.state = state
        self.guard = ChatGuard(settings.telegram_allowed_chat_id_list)
        self._stop_event = asyncio.Event()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.telegram_bot_enabled and self.settings.telegram_bot_token.strip() and self.settings.telegram_allowed_chat_id_list)

    def stop(self) -> None:
        self._stop_event.set()

    async def run_forever(self, mark_status: Callable[[str, str | None], None]) -> None:
        if not self.enabled:
            mark_status("disabled", "telegram token or allowed chat ids missing")
            return
        while not self._stop_event.is_set():
            try:
                mark_status("running", None)
                await self._run_polling()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("telegram bot polling crashed")
                mark_status("error", f"{type(exc).__name__}: {exc}")
                await asyncio.sleep(15)

    async def _run_polling(self) -> None:
        try:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
            from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
        except ModuleNotFoundError as exc:
            raise RuntimeError("python-telegram-bot is not installed") from exc

        async def guarded(update: Update, action: Callable[[Update, Any], Awaitable[None]], context: ContextTypes.DEFAULT_TYPE) -> None:
            chat_id = update.effective_chat.id if update.effective_chat else None
            if not self.guard.is_allowed(chat_id):
                logger.warning("telegram ignored unauthorized chat_id=%s", chat_id)
                return
            await action(update, context)

        async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._help, context)

        async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._positions, context)

        async def plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._plan, context)

        async def insight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._insight, context)

        async def scout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._scout, context)

        async def sim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._sim, context)

        async def review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._review, context)

        async def calib(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._calib, context)

        async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._status, context)

        async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._mute, context)

        async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._unmute, context)

        async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._callback, context)

        app = Application.builder().token(self.settings.telegram_bot_token).build()
        app.add_handler(CommandHandler(["start", "help"], start))
        app.add_handler(CommandHandler(["positions", "p"], positions))
        app.add_handler(CommandHandler("plan", plan))
        app.add_handler(CommandHandler("insight", insight))
        app.add_handler(CommandHandler("scout", scout))
        app.add_handler(CommandHandler("sim", sim))
        app.add_handler(CommandHandler("review", review))
        app.add_handler(CommandHandler("calib", calib))
        app.add_handler(CommandHandler("status", status))
        app.add_handler(CommandHandler("mute", mute))
        app.add_handler(CommandHandler("unmute", unmute))
        app.add_handler(CallbackQueryHandler(callback))

        await app.initialize()
        await app.start()
        if app.updater is None:
            raise RuntimeError("telegram updater unavailable")
        await app.updater.start_polling()
        try:
            await self._stop_event.wait()
        finally:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()

    async def _help(self, update: Any, context: Any) -> None:
        await update.effective_message.reply_text(
            format_help(),
            parse_mode="HTML",
            reply_markup=_markup([[{"text": "포지션", "callback_data": "v1:list:"}]], context),
        )

    async def _positions(self, update: Any, context: Any) -> None:
        args = list(context.args or [])
        if args:
            await self._send_detail(update, context, args[0])
            return
        payload = await self._run(service.list_live_positions)
        await update.effective_message.reply_text(format_positions_summary(payload), parse_mode="HTML", reply_markup=_markup(positions_keyboard(payload), context))

    async def _plan(self, update: Any, context: Any) -> None:
        symbol = _first_arg(context.args)
        if not symbol:
            await update.effective_message.reply_text("사용법: /plan BASED")
            return
        await self._send_plan(update, context, symbol)

    async def _insight(self, update: Any, context: Any) -> None:
        symbol = _first_arg(context.args)
        if not symbol:
            await update.effective_message.reply_text("사용법: /insight BASED")
            return
        await self._send_insight(update, context, symbol)

    async def _scout(self, update: Any, context: Any) -> None:
        payload = await self._run(service.scout_scan)
        await update.effective_message.reply_text(format_scout(payload), parse_mode="HTML")

    async def _sim(self, update: Any, context: Any) -> None:
        args = list(context.args or [])
        if len(args) < 3:
            await update.effective_message.reply_text("사용법: /sim BASED long 10 [0.09]")
            return
        entry = float(args[3]) if len(args) >= 4 else None
        result = await self._run(service.simulate_entry, args[0], args[1].lower(), float(args[2]), entry)
        await update.effective_message.reply_text(format_simulation(result), parse_mode="HTML")

    async def _review(self, update: Any, context: Any) -> None:
        trades = await self._run(service.recent_reviews)
        await update.effective_message.reply_text(format_reviews(trades), parse_mode="HTML")

    async def _calib(self, update: Any, context: Any) -> None:
        payload = await self._run(service.calibration_snapshot)
        await update.effective_message.reply_text(format_calibration(payload), parse_mode="HTML")

    async def _status(self, update: Any, context: Any) -> None:
        payload = get_worker_status()
        await update.effective_message.reply_text(format_status(payload), parse_mode="HTML")

    async def _mute(self, update: Any, context: Any) -> None:
        seconds = parse_duration_seconds(_first_arg(context.args) or "2h")
        muted_until = self.state.mute_for(seconds)
        await update.effective_message.reply_text(f"알림 무음: {muted_until.astimezone().strftime('%H:%M')}까지")

    async def _unmute(self, update: Any, context: Any) -> None:
        self.state.unmute()
        await update.effective_message.reply_text("알림 무음 해제")

    async def _callback(self, update: Any, context: Any) -> None:
        query = update.callback_query
        parsed = parse_callback(query.data if query else None)
        if query:
            await query.answer()
        if parsed is None:
            return
        if parsed.action == "list":
            payload = await self._run(service.list_live_positions)
            await query.edit_message_text(format_positions_summary(payload), parse_mode="HTML", reply_markup=_markup(positions_keyboard(payload), context))
        elif parsed.action == "detail":
            await self._edit_detail(query, context, parsed.symbol)
        elif parsed.action == "plan":
            await self._edit_plan(query, context, parsed.symbol)
        elif parsed.action == "insight":
            await self._edit_insight(query, context, parsed.symbol)
        elif parsed.action == "refresh":
            await self._run(service.sync_and_analyze_positions)
            await self._edit_detail(query, context, parsed.symbol)

    async def _send_detail(self, update: Any, context: Any, symbol: str) -> None:
        payload = await self._detail_payload(symbol)
        if "candidates" in payload:
            await update.effective_message.reply_text(_candidate_text(payload["candidates"]))
            return
        await update.effective_message.reply_text(format_position_verdict(payload), parse_mode="HTML", reply_markup=_markup(detail_keyboard(payload["position"]["symbol"]), context))

    async def _send_plan(self, update: Any, context: Any, symbol: str) -> None:
        payload = await self._detail_payload(symbol)
        if "candidates" in payload:
            await update.effective_message.reply_text(_candidate_text(payload["candidates"]))
            return
        await update.effective_message.reply_text(format_action_plan(payload), parse_mode="HTML", reply_markup=_markup(detail_keyboard(payload["position"]["symbol"]), context))

    async def _send_insight(self, update: Any, context: Any, symbol: str) -> None:
        payload = await self._detail_payload(symbol)
        if "candidates" in payload:
            await update.effective_message.reply_text(_candidate_text(payload["candidates"]))
            return
        await update.effective_message.reply_text(format_insight(payload), parse_mode="HTML", reply_markup=_markup(detail_keyboard(payload["position"]["symbol"]), context))

    async def _edit_detail(self, query: Any, context: Any, symbol: str) -> None:
        payload = await self._detail_payload(symbol)
        if "candidates" in payload:
            await query.edit_message_text(_candidate_text(payload["candidates"]))
            return
        await query.edit_message_text(format_position_verdict(payload), parse_mode="HTML", reply_markup=_markup(detail_keyboard(payload["position"]["symbol"]), context))

    async def _edit_plan(self, query: Any, context: Any, symbol: str) -> None:
        payload = await self._detail_payload(symbol)
        if "candidates" in payload:
            await query.edit_message_text(_candidate_text(payload["candidates"]))
            return
        await query.edit_message_text(format_action_plan(payload), parse_mode="HTML", reply_markup=_markup(detail_keyboard(payload["position"]["symbol"]), context))

    async def _edit_insight(self, query: Any, context: Any, symbol: str) -> None:
        payload = await self._detail_payload(symbol)
        if "candidates" in payload:
            await query.edit_message_text(_candidate_text(payload["candidates"]))
            return
        await query.edit_message_text(format_insight(payload), parse_mode="HTML", reply_markup=_markup(detail_keyboard(payload["position"]["symbol"]), context))

    async def _detail_payload(self, symbol: str) -> dict[str, Any]:
        match = service.match_position_symbol(symbol)
        if match.position is None:
            return {"candidates": [position.model_dump(mode="json") for position in match.candidates]}
        return await self._run(service.live_position_detail, match.position.id)

    async def _run(self, func: Callable[..., Any], *args: Any) -> Any:
        try:
            return await asyncio.wait_for(asyncio.to_thread(func, *args), timeout=self.settings.telegram_command_timeout_seconds)
        except asyncio.TimeoutError:
            raise RuntimeError("계산 중입니다. 잠시 후 다시 시도해주세요.")


def parse_duration_seconds(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+)\s*([hm])?\s*", value or "")
    if not match:
        return 7200
    amount = int(match.group(1))
    unit = match.group(2) or "m"
    return amount * 3600 if unit == "h" else amount * 60


def _first_arg(args: list[str] | tuple[str, ...] | None) -> str | None:
    return args[0] if args else None


def _candidate_text(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "일치하는 열린 포지션이 없습니다."
    return "심볼이 모호합니다: " + ", ".join(str(item.get("symbol", "-")) for item in candidates)


def _markup(rows: list[list[dict[str, str]]], context: Any):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup([[InlineKeyboardButton(button["text"], callback_data=button["callback_data"]) for button in row] for row in rows])
