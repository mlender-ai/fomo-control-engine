from __future__ import annotations

import asyncio
import logging
import re
from html import escape
from typing import Any, Awaitable, Callable

from app.core.config import Settings
from app.notify.bot.callbacks import encode_callback, parse_callback
from app.notify.bot.formatters import (
    detail_keyboard,
    engine_keyboard,
    format_action_plan,
    format_briefing,
    format_flow,
    format_help,
    format_entry_intents,
    format_engine_scoreboard,
    format_insight,
    format_one_liner_strip,
    format_performance,
    format_position_verdict,
    format_positions_summary,
    format_reviews,
    format_scout_prompt,
    format_scout_quick_answer,
    format_scout_stopped,
    format_scout_tracking,
    format_simulation,
    format_status,
    format_weekly_calibration,
    insight_keyboard,
    main_menu_keyboard,
    positions_keyboard,
    scout_tracking_keyboard,
    split_telegram_text,
)
from app.notify.bot.security import ChatGuard
from app.notify.state import NotificationState
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
            from telegram import Update
            from telegram.ext import (
                Application,
                CallbackQueryHandler,
                CommandHandler,
                ContextTypes,
                MessageHandler,
                filters,
            )
        except ModuleNotFoundError as exc:
            raise RuntimeError("python-telegram-bot is not installed") from exc

        async def guarded(
            update: Update,
            action: Callable[[Update, Any], Awaitable[None]],
            context: ContextTypes.DEFAULT_TYPE,
        ) -> None:
            chat_id = update.effective_chat.id if update.effective_chat else None
            if not self.guard.is_allowed(chat_id):
                logger.warning("telegram ignored unauthorized chat_id=%s", chat_id)
                return
            try:
                await action(update, context)
            except RuntimeError as exc:
                await self._reply(update.effective_message, str(exc))
            except ValueError:
                await self._reply(
                    update.effective_message,
                    "입력값을 확인해주세요. /help에서 사용법을 볼 수 있습니다.",
                )

        async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._help, context)

        async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._positions, context)

        async def positions_full(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._positions_full, context)

        async def plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._plan, context)

        async def insight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._insight, context)

        async def flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._flow, context)

        async def brief(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._brief, context)

        async def scout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._scout, context)

        async def quick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._quick, context)

        async def unscout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._unscout, context)

        async def intents(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._intents, context)

        async def intent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._intent, context)

        async def sim(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._sim, context)

        async def review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._review, context)

        async def calib(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._calib, context)

        async def perf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._perf, context)

        async def engine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._engine, context)

        async def whales(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._whales, context)

        async def whale(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._whale, context)

        async def veto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._veto, context)

        async def experiments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._experiments, context)

        async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._status, context)

        async def mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._mute, context)

        async def unmute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._unmute, context)

        async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._callback, context)

        async def symbol_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            await guarded(update, self._symbol_text, context)

        app = Application.builder().token(self.settings.telegram_bot_token).build()
        app.add_handler(CommandHandler(["start", "help"], start))
        app.add_handler(CommandHandler(["positions", "p"], positions))
        app.add_handler(CommandHandler(["positions_full", "pf"], positions_full))
        app.add_handler(CommandHandler("plan", plan))
        app.add_handler(CommandHandler("insight", insight))
        app.add_handler(CommandHandler("flow", flow))
        app.add_handler(CommandHandler("brief", brief))
        app.add_handler(CommandHandler("scout", scout))
        app.add_handler(CommandHandler(["unscout", "stopscout"], unscout))
        app.add_handler(CommandHandler(["q", "quick"], quick))
        app.add_handler(CommandHandler("intents", intents))
        app.add_handler(CommandHandler("intent", intent))
        app.add_handler(CommandHandler("sim", sim))
        app.add_handler(CommandHandler("review", review))
        app.add_handler(CommandHandler("calib", calib))
        app.add_handler(CommandHandler("perf", perf))
        app.add_handler(CommandHandler(["engine", "paper"], engine))
        app.add_handler(CommandHandler("whales", whales))
        app.add_handler(CommandHandler("whale", whale))
        app.add_handler(CommandHandler("veto", veto))
        app.add_handler(CommandHandler("experiments", experiments))
        app.add_handler(CommandHandler("status", status))
        app.add_handler(CommandHandler("mute", mute))
        app.add_handler(CommandHandler("unmute", unmute))
        app.add_handler(CallbackQueryHandler(callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, symbol_text))

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
        await self._reply(
            update.effective_message,
            format_help(),
            reply_markup=_markup(main_menu_keyboard(), context),
        )

    async def _positions(self, update: Any, context: Any) -> None:
        args = list(context.args or [])
        if args:
            await self._send_detail(update, context, args[0])
            return
        payload = await self._run(service.list_live_positions)
        await self._reply(
            update.effective_message,
            format_positions_summary(payload),
            reply_markup=_markup(positions_keyboard(payload), context),
        )

    async def _positions_full(self, update: Any, context: Any) -> None:
        await self._send_all_position_details(update.effective_message, context)

    async def _plan(self, update: Any, context: Any) -> None:
        symbol = _first_arg(context.args)
        if not symbol:
            await self._reply(update.effective_message, "사용법: /plan BASED")
            return
        await self._send_plan(update, context, symbol)

    async def _insight(self, update: Any, context: Any) -> None:
        symbol = _first_arg(context.args)
        if not symbol:
            await self._reply(update.effective_message, "사용법: /insight BASED")
            return
        await self._send_insight(update, context, symbol)

    async def _flow(self, update: Any, context: Any) -> None:
        symbol = _first_arg(context.args)
        if not symbol:
            await self._reply(update.effective_message, "사용법: /flow BASED")
            return
        await self._send_flow(update, context, symbol)

    async def _brief(self, update: Any, context: Any) -> None:
        symbol = _first_arg(context.args)
        if not symbol:
            await self._reply(update.effective_message, "사용법: /brief BASED")
            return
        await self._send_brief(update, context, symbol)

    async def _scout(self, update: Any, context: Any) -> None:
        symbol = _first_arg(context.args)
        if symbol:
            await self._reply_scout_tracking(update.effective_message, context, symbol)
            return
        payload = await self._run(service.scout_tracking_status)
        await self._reply(
            update.effective_message,
            format_scout_prompt(payload),
            reply_markup=_markup(main_menu_keyboard(), context),
        )

    async def _symbol_text(self, update: Any, context: Any) -> None:
        text = str(getattr(update.effective_message, "text", "") or "").strip()
        if not text:
            return
        if not _looks_like_symbol(text):
            await self._reply(update.effective_message, "티커만 입력해주세요. 예: BTC, ETHUSDT, TSLA")
            return
        await self._reply_scout_tracking(update.effective_message, context, text)

    async def _unscout(self, update: Any, context: Any) -> None:
        symbol = _first_arg(context.args)
        if not symbol:
            await self._reply(update.effective_message, "사용법: /unscout SOL")
            return
        payload = await self._run(service.stop_scout_tracking, symbol)
        await self._reply(update.effective_message, format_scout_stopped(payload), reply_markup=_markup(main_menu_keyboard(), context))

    async def _quick(self, update: Any, context: Any) -> None:
        symbol = _first_arg(context.args)
        if not symbol:
            await self._reply(update.effective_message, "사용법: /q SOL")
            return
        try:
            payload = await self._run(service.scout_quick_answer, symbol)
        except Exception as exc:
            await self._reply(update.effective_message, f"즉답 분석 실패: {exc}")
            return
        await self._reply(update.effective_message, format_scout_quick_answer(payload))

    async def _intents(self, update: Any, context: Any) -> None:
        symbol = _first_arg(context.args)
        payload = await self._run(service.entry_intents, symbol, "active")
        await self._reply(update.effective_message, format_entry_intents(payload))

    async def _intent(self, update: Any, context: Any) -> None:
        args = list(context.args or [])
        if len(args) < 3:
            await self._reply(update.effective_message, "사용법: /intent TSLA long 240-250")
            return
        try:
            payload = await self._run(service.create_entry_intent, args[0], args[1].lower(), args[2])
        except Exception as exc:
            await self._reply(update.effective_message, f"진입 의도 등록 실패: {exc}")
            return
        await self._reply(update.effective_message, format_entry_intents({"intents": [payload["intent"]]}))

    async def _sim(self, update: Any, context: Any) -> None:
        args = list(context.args or [])
        if len(args) < 3:
            await self._reply(update.effective_message, "사용법: /sim BASED long 10 [0.09]")
            return
        entry = float(args[3]) if len(args) >= 4 else None
        result = await self._run(service.simulate_entry, args[0], args[1].lower(), float(args[2]), entry)
        await self._reply(update.effective_message, format_simulation(result))

    async def _review(self, update: Any, context: Any) -> None:
        trades = await self._run(service.recent_reviews)
        await self._reply(update.effective_message, format_reviews(trades))

    async def _calib(self, update: Any, context: Any) -> None:
        payload = await self._run(service.weekly_calibration_report)
        await self._reply(update.effective_message, format_weekly_calibration(payload))

    async def _perf(self, update: Any, context: Any) -> None:
        payload = await self._run(service.performance_summary)
        await self._reply(update.effective_message, format_performance(payload))

    async def _engine(self, update: Any, context: Any) -> None:
        payload = await self._run(service.paper_dashboard)
        await self._reply(
            update.effective_message,
            format_engine_scoreboard(payload),
            reply_markup=_markup(engine_keyboard(), context),
        )

    async def _whales(self, update: Any, context: Any) -> None:
        payload = await self._run(service.whale_dashboard)
        await self._reply(update.effective_message, _format_whales(payload))

    async def _whale(self, update: Any, context: Any) -> None:
        args = list(context.args or [])
        if not args:
            await self._reply(update.effective_message, "사용법: /whale add 0x주소 [추정별칭] · /whale 0x주소")
            return
        if args[0].lower() == "add":
            if len(args) < 2:
                await self._reply(update.effective_message, "사용법: /whale add 0x주소 [추정별칭]")
                return
            wallet = await self._run(service.add_whale_wallet, args[1], " ".join(args[2:]) or None, "bot")
            await self._reply(
                update.effective_message, f"🐋 등록 완료: <b>{escape(str(wallet.get('label') or '-'))}</b> · 별칭은 사용자 추정이며 신원 확정이 아닙니다."
            )
            return
        payload = await self._run(service.whale_dashboard)
        address = args[0].lower()
        wallet = next((item for item in payload.get("wallets", []) if item.get("address") == address), None)
        if wallet is None:
            await self._reply(update.effective_message, "등록된 지갑이 아닙니다. /whale add 0x주소 [추정별칭]")
            return
        await self._reply(update.effective_message, _format_whale(wallet))

    async def _veto(self, update: Any, context: Any) -> None:
        suggestion_id = _first_arg(context.args)
        if not suggestion_id:
            await self._reply(update.effective_message, "사용법: /veto <suggestion_id>")
            return
        try:
            suggestion = await self._run(service.veto_calibration_suggestion, suggestion_id)
        except Exception:
            await self._reply(update.effective_message, "거부권 처리 실패: 제안 ID를 확인해주세요.")
            return
        await self._reply(update.effective_message, f"거부권 처리 완료: {suggestion.get('title', suggestion_id)}")

    async def _experiments(self, update: Any, context: Any) -> None:
        payload = await self._run(service.calibration_experiments)
        await self._reply(update.effective_message, _format_experiments(payload))

    async def _status(self, update: Any, context: Any) -> None:
        payload = get_worker_status()
        try:
            # WO-44 Part C: 최근 24h 발화/발송/실패 노출 — 침묵과 고장의 구분.
            payload["alerts_24h"] = await self._run(service.alert_delivery_stats_24h)
        except Exception:
            payload["alerts_24h"] = None
        await self._reply(update.effective_message, format_status(payload))

    async def _mute(self, update: Any, context: Any) -> None:
        seconds = parse_duration_seconds(_first_arg(context.args) or "2h")
        muted_until = self.state.mute_for(seconds)
        await self._reply(
            update.effective_message,
            f"알림 무음: {muted_until.astimezone().strftime('%H:%M')}까지",
        )

    async def _unmute(self, update: Any, context: Any) -> None:
        self.state.unmute()
        await self._reply(update.effective_message, "알림 무음 해제")

    async def _callback(self, update: Any, context: Any) -> None:
        query = update.callback_query
        parsed = parse_callback(query.data if query else None)
        if query:
            await query.answer()
        if parsed is None:
            return
        if parsed.action == "list":
            payload = await self._run(service.list_live_positions)
            await self._edit(
                query,
                format_positions_summary(payload),
                reply_markup=_markup(positions_keyboard(payload), context),
            )
        elif parsed.action == "all_details":
            if query and query.message:
                await self._send_all_position_details(query.message, context)
        elif parsed.action == "scout":
            if parsed.symbol:
                payload = await self._run(service.start_scout_tracking, parsed.symbol)
                normalized = str(payload.get("symbol") or parsed.symbol).upper()
                tracking = payload.get("tracking") if isinstance(payload.get("tracking"), dict) else {}
                keyboard = detail_keyboard(normalized) if tracking.get("mode") == "position" else scout_tracking_keyboard(normalized)
                await self._edit(
                    query,
                    format_scout_tracking(payload),
                    reply_markup=_markup(keyboard, context),
                )
            else:
                payload = await self._run(service.scout_tracking_status)
                await self._edit(
                    query,
                    format_scout_prompt(payload),
                    reply_markup=_markup(main_menu_keyboard(), context),
                )
        elif parsed.action == "unscout":
            payload = await self._run(service.stop_scout_tracking, parsed.symbol)
            await self._edit(
                query,
                format_scout_stopped(payload),
                reply_markup=_markup(main_menu_keyboard(), context),
            )
        elif parsed.action == "status":
            await self._edit(
                query,
                format_status(get_worker_status()),
                reply_markup=_markup(main_menu_keyboard(), context),
            )
        elif parsed.action == "engine":
            payload = await self._run(service.paper_dashboard)
            await self._edit(
                query,
                format_engine_scoreboard(payload),
                reply_markup=_markup(engine_keyboard(), context),
            )
        elif parsed.action == "review":
            trades = await self._run(service.recent_reviews)
            await self._edit(
                query,
                format_reviews(trades),
                reply_markup=_markup(main_menu_keyboard(), context),
            )
        elif parsed.action == "sim":
            symbol, direction = _symbol_direction(parsed.symbol)
            result = await self._run(service.simulate_entry, symbol, direction, 10.0, None)
            await self._edit(
                query,
                format_simulation(result),
                reply_markup=_markup(main_menu_keyboard(), context),
            )
        elif parsed.action == "detail":
            await self._edit_detail(query, context, parsed.symbol)
        elif parsed.action == "one_liners":
            await self._edit_one_liners(query, context, parsed.symbol)
        elif parsed.action == "chart":
            await self._edit_chart_hint(query, context, parsed.symbol)
        elif parsed.action == "plan":
            await self._edit_plan(query, context, parsed.symbol)
        elif parsed.action == "insight":
            await self._edit_insight(query, context, parsed.symbol)
        elif parsed.action == "flow":
            await self._edit_flow(query, context, parsed.symbol)
        elif parsed.action == "brief":
            await self._edit_brief(query, context, parsed.symbol)
        elif parsed.action == "regen_insight":
            await self._edit_regenerated_insight(query, context, parsed.symbol)
        elif parsed.action == "refresh":
            await self._run(service.sync_and_analyze_positions)
            await self._edit_detail(query, context, parsed.symbol)

    async def _reply_scout_tracking(self, message: Any, context: Any, symbol: str) -> None:
        try:
            payload = await self._run(service.start_scout_tracking, symbol)
        except Exception as exc:
            await self._reply(message, f"스카우트 추적 시작 실패: {exc}")
            return
        tracking = payload.get("tracking") if isinstance(payload.get("tracking"), dict) else {}
        normalized = str(payload.get("symbol") or symbol).upper()
        keyboard = detail_keyboard(normalized) if tracking.get("mode") == "position" else scout_tracking_keyboard(normalized)
        await self._reply(
            message,
            format_scout_tracking(payload),
            reply_markup=_markup(keyboard, context),
        )

    async def _send_detail(self, update: Any, context: Any, symbol: str) -> None:
        payload = await self._detail_payload(symbol)
        if "candidates" in payload:
            await self._reply(
                update.effective_message,
                _candidate_text(payload["candidates"]),
                reply_markup=_markup(_candidate_rows(payload["candidates"]), context),
            )
            return
        await self._reply(
            update.effective_message,
            format_position_verdict(payload),
            reply_markup=_markup(detail_keyboard(payload["position"]["symbol"]), context),
        )

    async def _send_all_position_details(self, message: Any, context: Any) -> None:
        refs = await self._run(service.list_open_position_refs)
        if not refs:
            await self._reply(message, "열린 포지션이 없습니다.")
            return
        for item in refs:
            symbol = str(item.get("symbol") or "-").upper()
            try:
                payload = await self._run(service.live_position_detail, item["id"])
            except (LookupError, RuntimeError) as exc:
                logger.warning("telegram position detail unavailable symbol=%s: %s", symbol, exc)
                await self._reply(message, f"<b>{escape(symbol)}</b> 상세 조회 지연 · 잠시 후 /p {escape(symbol)} 재시도")
                continue
            await self._reply(
                message,
                format_position_verdict(payload),
                reply_markup=_markup(detail_keyboard(payload["position"]["symbol"]), context),
            )

    async def _send_plan(self, update: Any, context: Any, symbol: str) -> None:
        payload = await self._detail_payload(symbol)
        if "candidates" in payload:
            await self._reply(
                update.effective_message,
                _candidate_text(payload["candidates"]),
                reply_markup=_markup(_candidate_rows(payload["candidates"]), context),
            )
            return
        await self._reply(
            update.effective_message,
            format_action_plan(payload),
            reply_markup=_markup(detail_keyboard(payload["position"]["symbol"]), context),
        )

    async def _send_insight(self, update: Any, context: Any, symbol: str) -> None:
        payload = await self._detail_payload(symbol)
        if "candidates" in payload:
            await self._reply(
                update.effective_message,
                _candidate_text(payload["candidates"]),
                reply_markup=_markup(_candidate_rows(payload["candidates"]), context),
            )
            return
        await self._reply(
            update.effective_message,
            format_insight(payload),
            reply_markup=_markup(_insight_keyboard_rows(payload), context),
        )

    async def _send_flow(self, update: Any, context: Any, symbol: str) -> None:
        payload = await self._flow_payload(symbol)
        if "candidates" in payload:
            await self._reply(
                update.effective_message,
                _candidate_text(payload["candidates"]),
                reply_markup=_markup(_candidate_rows(payload["candidates"]), context),
            )
            return
        await self._reply(
            update.effective_message,
            format_flow(payload),
            reply_markup=_markup(detail_keyboard(payload["symbol"]), context),
        )

    async def _send_brief(self, update: Any, context: Any, symbol: str) -> None:
        payload = await self._brief_payload(symbol)
        if "candidates" in payload:
            await self._reply(
                update.effective_message,
                _candidate_text(payload["candidates"]),
                reply_markup=_markup(_candidate_rows(payload["candidates"]), context),
            )
            return
        await self._reply(
            update.effective_message,
            format_briefing(payload),
            reply_markup=_markup(detail_keyboard(payload["symbol"]), context),
        )

    async def _edit_detail(self, query: Any, context: Any, symbol: str) -> None:
        payload = await self._detail_payload(symbol)
        if "candidates" in payload:
            await self._edit(
                query,
                _candidate_text(payload["candidates"]),
                reply_markup=_markup(_candidate_rows(payload["candidates"]), context),
            )
            return
        await self._edit(
            query,
            format_position_verdict(payload),
            reply_markup=_markup(detail_keyboard(payload["position"]["symbol"]), context),
        )

    async def _edit_one_liners(self, query: Any, context: Any, symbol: str) -> None:
        payload = await self._detail_payload(symbol)
        if "candidates" in payload:
            await self._edit(
                query,
                _candidate_text(payload["candidates"]),
                reply_markup=_markup(_candidate_rows(payload["candidates"]), context),
            )
            return
        text = format_one_liner_strip(payload) or "1줄 판정 데이터가 아직 없습니다."
        await self._edit(
            query,
            text,
            reply_markup=_markup(detail_keyboard(payload["position"]["symbol"]), context),
        )

    async def _edit_chart_hint(self, query: Any, context: Any, symbol: str) -> None:
        payload = await self._detail_payload(symbol)
        if "candidates" in payload:
            await self._edit(
                query,
                _candidate_text(payload["candidates"]),
                reply_markup=_markup(_candidate_rows(payload["candidates"]), context),
            )
            return
        position = payload["position"]
        text = "\n".join(
            [
                f"<b>{escape(str(position.get('symbol', symbol)).upper())} 차트</b>",
                "차트는 로컬 대시보드에서 확인합니다.",
                "http://127.0.0.1:8876/",
            ]
        )
        await self._edit(
            query,
            text,
            reply_markup=_markup(detail_keyboard(position["symbol"]), context),
        )

    async def _edit_plan(self, query: Any, context: Any, symbol: str) -> None:
        payload = await self._detail_payload(symbol)
        if "candidates" in payload:
            await self._edit(
                query,
                _candidate_text(payload["candidates"]),
                reply_markup=_markup(_candidate_rows(payload["candidates"]), context),
            )
            return
        await self._edit(
            query,
            format_action_plan(payload),
            reply_markup=_markup(detail_keyboard(payload["position"]["symbol"]), context),
        )

    async def _edit_insight(self, query: Any, context: Any, symbol: str) -> None:
        payload = await self._detail_payload(symbol)
        if "candidates" in payload:
            await self._edit(
                query,
                _candidate_text(payload["candidates"]),
                reply_markup=_markup(_candidate_rows(payload["candidates"]), context),
            )
            return
        await self._edit(
            query,
            format_insight(payload),
            reply_markup=_markup(_insight_keyboard_rows(payload), context),
        )

    async def _edit_flow(self, query: Any, context: Any, symbol: str) -> None:
        payload = await self._flow_payload(symbol)
        if "candidates" in payload:
            await self._edit(
                query,
                _candidate_text(payload["candidates"]),
                reply_markup=_markup(_candidate_rows(payload["candidates"]), context),
            )
            return
        await self._edit(
            query,
            format_flow(payload),
            reply_markup=_markup(detail_keyboard(payload["symbol"]), context),
        )

    async def _edit_brief(self, query: Any, context: Any, symbol: str) -> None:
        payload = await self._brief_payload(symbol)
        if "candidates" in payload:
            await self._edit(
                query,
                _candidate_text(payload["candidates"]),
                reply_markup=_markup(_candidate_rows(payload["candidates"]), context),
            )
            return
        await self._edit(
            query,
            format_briefing(payload),
            reply_markup=_markup(detail_keyboard(payload["symbol"]), context),
        )

    async def _edit_regenerated_insight(self, query: Any, context: Any, symbol: str) -> None:
        payload = await self._regenerate_insight_payload(symbol)
        if "candidates" in payload:
            await self._edit(
                query,
                _candidate_text(payload["candidates"]),
                reply_markup=_markup(_candidate_rows(payload["candidates"]), context),
            )
            return
        await self._edit(
            query,
            format_insight(payload),
            reply_markup=_markup(_insight_keyboard_rows(payload), context),
        )

    async def _detail_payload(self, symbol: str) -> dict[str, Any]:
        match = service.match_position_symbol(symbol)
        if match.position is None:
            return {"candidates": [position.model_dump(mode="json") for position in match.candidates]}
        return await self._run(service.live_position_detail, match.position.id)

    async def _flow_payload(self, symbol: str) -> dict[str, Any]:
        match = service.match_position_symbol(symbol)
        if match.position is None:
            return {"candidates": [position.model_dump(mode="json") for position in match.candidates]}
        return await self._run(service.latest_flow, match.position.symbol)

    async def _brief_payload(self, symbol: str) -> dict[str, Any]:
        return await self._run(service.analyst_briefing, symbol)

    async def _regenerate_insight_payload(self, symbol: str) -> dict[str, Any]:
        match = service.match_position_symbol(symbol)
        if match.position is None:
            return {"candidates": [position.model_dump(mode="json") for position in match.candidates]}
        return await self._run(service.create_position_insight, match.position.id)

    async def _run(self, func: Callable[..., Any], *args: Any) -> Any:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(func, *args),
                timeout=self.settings.telegram_command_timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("계산 중입니다. 잠시 후 다시 시도해주세요.")

    async def _reply(self, message: Any, text: str, *, reply_markup: Any | None = None) -> None:
        chunks = split_telegram_text(text)
        for index, chunk in enumerate(chunks):
            await message.reply_text(
                chunk,
                parse_mode="HTML",
                reply_markup=reply_markup if index == len(chunks) - 1 else None,
            )

    async def _edit(self, query: Any, text: str, *, reply_markup: Any | None = None) -> None:
        chunks = split_telegram_text(text)
        await query.edit_message_text(
            chunks[0],
            parse_mode="HTML",
            reply_markup=reply_markup if len(chunks) == 1 else None,
        )
        if len(chunks) <= 1 or query.message is None:
            return
        for index, chunk in enumerate(chunks[1:], start=1):
            await query.message.reply_text(
                chunk,
                parse_mode="HTML",
                reply_markup=reply_markup if index == len(chunks) - 1 else None,
            )


def parse_duration_seconds(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+)\s*([hm])?\s*", value or "")
    if not match:
        return 7200
    amount = int(match.group(1))
    unit = match.group(2) or "m"
    return amount * 3600 if unit == "h" else amount * 60


def _first_arg(args: list[str] | tuple[str, ...] | None) -> str | None:
    return args[0] if args else None


def _format_whales(payload: dict[str, Any]) -> str:
    wallets = payload.get("wallets") if isinstance(payload.get("wallets"), list) else []
    lines = [f"🐋 <b>Hyperliquid 고래 관측</b> · {len(wallets)}/{payload.get('max_wallets', 20)}"]
    if not wallets:
        lines.append("등록된 지갑 없음 · /whale add 0x주소 [추정별칭]")
    for wallet in wallets[:20]:
        review = wallet.get("review") if isinstance(wallet.get("review"), dict) else {}
        state = "검증" if review.get("state") == "validated" else "축적"
        positions = wallet.get("positions") if isinstance(wallet.get("positions"), list) else []
        summary = (
            ", ".join(
                f"{item.get('coin')} {'롱' if item.get('side') == 'long' else '숏'} {_compact_whale_size(float(item.get('size_usd') or 0))}"
                for item in positions[:3]
            )
            or "포지션 없음"
        )
        lines.append(f"• <b>{escape(str(wallet.get('label') or '-'))}</b> · {state} N={review.get('sample_size', 0)} · {escape(summary)}")
    lines.append("미검증 지갑은 관측 정보이며 따라가기 신호가 아닙니다. 별칭은 사용자 추정입니다.")
    return "\n".join(lines)


def _format_whale(wallet: dict[str, Any]) -> str:
    review = wallet.get("review") if isinstance(wallet.get("review"), dict) else {}
    positions = wallet.get("positions") if isinstance(wallet.get("positions"), list) else []
    lines = [
        f"🐋 <b>{escape(str(wallet.get('label') or '-'))}</b>",
        f"<code>{escape(str(wallet.get('address') or '-'))}</code>",
        f"채점 {escape(str(review.get('state') or 'candidate'))} · N={review.get('sample_size', 0)} · 1R {review.get('win_1r_pct') if review.get('win_1r_pct') is not None else '대기'}",
    ]
    for item in positions:
        lines.append(
            f"• {item.get('coin')} {'롱' if item.get('side') == 'long' else '숏'} {_compact_whale_size(float(item.get('size_usd') or 0))} · 진입 {float(item.get('entry_px') or 0):,.2f}"
        )
    if not positions:
        lines.append("현재 공개 포지션 없음")
    lines.append("관측 정보이며 따라가기 신호가 아닙니다. 별칭은 사용자 추정입니다.")
    return "\n".join(lines)


def _compact_whale_size(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K"
    return f"{value:.0f}"


def _looks_like_symbol(value: str) -> bool:
    raw = (value or "").strip()
    if not raw or raw.startswith("/"):
        return False
    if any(char.isspace() for char in raw):
        return False
    normalized = raw.upper().replace("/", "").replace("-", "")
    return bool(re.fullmatch(r"[A-Z0-9]{2,18}", normalized))


def _symbol_direction(value: str) -> tuple[str, str]:
    symbol, _, direction = value.partition("|")
    normalized = direction.lower() if direction.lower() in {"long", "short"} else "long"
    return symbol, normalized


def _candidate_text(candidates: list[dict[str, Any]]) -> str:
    if not candidates:
        return "일치하는 열린 포지션이 없습니다."
    return "심볼이 모호합니다: " + ", ".join(str(item.get("symbol", "-")) for item in candidates)


def _candidate_rows(candidates: list[dict[str, Any]]) -> list[list[dict[str, str]]]:
    buttons = [
        {
            "text": str(item.get("symbol", "-")),
            "callback_data": encode_callback("detail", str(item.get("symbol", ""))),
        }
        for item in candidates[:8]
    ]
    return [buttons[index : index + 2] for index in range(0, len(buttons), 2)]


def _format_experiments(payload: dict[str, Any]) -> str:
    autonomy = payload.get("autonomy") if isinstance(payload.get("autonomy"), dict) else {}
    suggestions = payload.get("suggestions") if isinstance(payload.get("suggestions"), list) else []
    lines = [
        "<b>파라미터 자율 피드</b>",
        f"예정 {autonomy.get('scheduled', 0)}건 · 실험 {autonomy.get('experiments', 0)}건 · 자율 적용 {autonomy.get('autonomy_adopted', 0)}건",
    ]
    if not suggestions:
        lines.append("현재 거부권 대기 또는 섀도 실험 중인 항목이 없습니다.")
        return "\n".join(lines)
    for item in suggestions[:8]:
        if not isinstance(item, dict):
            continue
        meta = item.get("autonomy") if isinstance(item.get("autonomy"), dict) else {}
        lines.append(
            "• "
            f"{escape(str(item.get('title', '-')))} · {escape(str(item.get('status', '-')))} · "
            f"{escape(str(meta.get('change_direction', '-')))} · /veto {escape(str(item.get('id', '')))}"
        )
    return "\n".join(lines)


def _insight_keyboard_rows(payload: dict[str, Any]) -> list[list[dict[str, str]]]:
    position = payload.get("position") if isinstance(payload.get("position"), dict) else {}
    symbol = str(position.get("symbol") or "")
    status = payload.get("insight_status") if isinstance(payload.get("insight_status"), dict) else {}
    regenerate = not payload.get("latest_insight") or bool(status.get("is_stale"))
    return insight_keyboard(symbol, regenerate=regenerate)


def _markup(rows: list[list[dict[str, str]]], context: Any):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup([[InlineKeyboardButton(button["text"], callback_data=button["callback_data"]) for button in row] for row in rows])
