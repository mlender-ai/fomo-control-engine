from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.notify.bot.formatters import lifecycle_alert_keyboard
from app.notify.rules import RULE_LABELS, rule_catalog
from app.notify.telegram import TelegramSender

router = APIRouter()


class AlertRuleUpdate(BaseModel):
    enabled: bool | None = None
    threshold: float | None = None


class AlertSettingsUpdate(BaseModel):
    rules: dict[str, AlertRuleUpdate] = Field(default_factory=dict)
    quiet_hours_enabled: bool | None = None
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None
    daily_summary_time: str | None = None
    pulse_interval_hours: float | None = None


class AlertTestRequest(BaseModel):
    rule_id: str | None = None


@router.get("/api/alerts/settings")
def get_alert_settings() -> dict[str, Any]:
    settings = get_settings()
    return _settings_payload(settings)


@router.patch("/api/alerts/settings")
def update_alert_settings(update: AlertSettingsUpdate) -> dict[str, Any]:
    settings = get_settings()
    enabled = set(settings.alert_enabled_rule_set)
    for rule_id, patch in update.rules.items():
        if patch.enabled is True:
            enabled.add(rule_id)
        elif patch.enabled is False:
            enabled.discard(rule_id)
        if patch.threshold is not None:
            _set_threshold(settings, rule_id, patch.threshold)
    settings.alert_rules_enabled = ",".join(rule_id for rule_id in _known_rule_order() if rule_id in enabled)
    if update.quiet_hours_enabled is not None:
        settings.telegram_quiet_hours_enabled = update.quiet_hours_enabled
    if update.quiet_hours_start is not None:
        settings.telegram_quiet_hours_start = update.quiet_hours_start
    if update.quiet_hours_end is not None:
        settings.telegram_quiet_hours_end = update.quiet_hours_end
    if update.daily_summary_time is not None:
        settings.telegram_daily_summary_time = update.daily_summary_time
    if update.pulse_interval_hours is not None:
        settings.alert_pulse_interval_hours = max(0.25, float(update.pulse_interval_hours))
    return _settings_payload(settings)


@router.post("/api/alerts/test")
async def send_test_alert(payload: AlertTestRequest | None = None) -> dict[str, Any]:
    settings = get_settings()
    sender = TelegramSender(settings)
    rule_id = payload.rule_id if payload and payload.rule_id in RULE_LABELS else None
    rule_label = RULE_LABELS.get(rule_id or "", "테스트 알림")
    symbol = "BTCUSDT"
    sent = await sender.send_to_all(
        "\n".join(
            [
                "<b>FOMO Control Engine 테스트 알림</b>",
                f"이벤트: {rule_label}",
                "주문 실행 없음 · 읽기 전용 관제 알림",
            ]
        ),
        reply_markup={"inline_keyboard": lifecycle_alert_keyboard(rule_id, symbol)} if rule_id else None,
    )
    return {"configured": sender.enabled, "sent": sent}


def _settings_payload(settings) -> dict[str, Any]:
    return {
        "telegram": {
            "configured": bool(settings.telegram_bot_token.strip() and settings.telegram_allowed_chat_id_list),
            "alerts_enabled": settings.telegram_alerts_enabled,
            "quiet_hours_enabled": settings.telegram_quiet_hours_enabled,
            "quiet_hours_start": settings.telegram_quiet_hours_start,
            "quiet_hours_end": settings.telegram_quiet_hours_end,
            "quiet_hours_timezone": settings.telegram_quiet_hours_timezone,
            "daily_summary_time": settings.telegram_daily_summary_time,
            "pulse_interval_hours": settings.alert_pulse_interval_hours,
            "chat_ids_configured": len(settings.telegram_allowed_chat_id_list),
        },
        "rules": rule_catalog(settings),
    }


def _set_threshold(settings, rule_id: str, value: float) -> None:
    if rule_id == "trigger_near":
        settings.alert_trigger_near_pct = float(value)
    elif rule_id == "health_drop":
        settings.alert_health_drop_points = int(value)
    elif rule_id == "liq_proximity":
        settings.alert_liq_warn_pct = float(value)
    elif rule_id == "liq_unknown_high_lev":
        settings.alert_liq_unknown_high_lev_hours = float(value)
    elif rule_id == "wyckoff_event":
        settings.alert_wyckoff_min_confidence = int(value)
    elif rule_id == "liq_cluster_near":
        settings.alert_trigger_near_pct = float(value)
    elif rule_id == "intent_approaching":
        settings.entry_intent_normal_tolerance_pct = float(value)


def _known_rule_order() -> list[str]:
    return [
        "trigger_near",
        "invalidation_breach",
        "take_profit_hit",
        "status_worsened",
        "health_drop",
        "liq_proximity",
        "liq_unknown_high_lev",
        "wyckoff_event",
        "data_stall",
        "funding_extreme",
        "oi_divergence",
        "liq_cluster_near",
        "setup_near",
        "setup_triggered",
        "setup_invalidated",
        "intent_approaching",
        "intent_zone_entered",
        "intent_zone_entered_partial",
        "intent_invalidated",
        "universe_discovery",
        "mdd_limit_warn",
        "mdd_limit_critical",
        "position_opened",
        "position_closed",
        "verdict_changed",
        "stance_flipped",
        "evidence_insufficient",
        "periodic_pulse",
    ]
