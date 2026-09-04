from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.notify import delivery_gate
from app.notify.alerts import AlertEngine
from app.notify.rules import RULE_LABELS, RULE_SEVERITY, AlertCandidate, rule_catalog
from app.notify.state import NotificationState
from app.notify.telegram import TelegramSender

router = APIRouter()


class AlertRuleUpdate(BaseModel):
    enabled: bool | None = None
    threshold: float | None = None


class AlertSettingsUpdate(BaseModel):
    rules: dict[str, AlertRuleUpdate] = Field(default_factory=dict)
    daily_summary_time: str | None = None
    pulse_interval_hours: float | None = None
    paper_alerts_enabled: bool | None = None
    scout_auto_arm_enabled: bool | None = None


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
    if update.daily_summary_time is not None:
        settings.telegram_daily_summary_time = update.daily_summary_time
    if update.pulse_interval_hours is not None:
        settings.alert_pulse_interval_hours = max(0.25, float(update.pulse_interval_hours))
    if update.paper_alerts_enabled is not None:
        settings.paper_telegram_alerts_enabled = update.paper_alerts_enabled
    if update.scout_auto_arm_enabled is not None:
        settings.scout_auto_arm_enabled = update.scout_auto_arm_enabled
    return _settings_payload(settings)


@router.post("/api/alerts/test")
async def send_test_alert(payload: AlertTestRequest | None = None) -> dict[str, Any]:
    """실제 발송 경로를 **단계별로** 통과시키고 어디서 막혔는지 돌려준다.

    옛 구현은 `sender.send_to_all` 을 직접 불렀다. 그것은 봇 토큰이 살아있다는 것만
    증명하고 **진입 알림이 온다는 것은 증명하지 않는다** — 관문·룰 활성·뮤트·룰 상태를
    전부 건너뛰기 때문이다. "테스트는 오는데 진입 알림은 안 온다"가 정확히 그 구멍이다.

    그래서 기본값을 `position_opened` 로 두고 진짜 후보를 만들어 `_fire_if_allowed` 에
    태운다. 관문이 막으면 막았다고, 룰이 꺼져 있으면 꺼져 있다고 답한다.

    **운영 상태를 건드리지 않는다.** 뮤트·최근 차단 사유는 저장된 상태에서 **읽기만** 하고,
    발송은 일회용 상태로 태운다 — 진단이 워커의 쿨다운·룰 상태를 덮어쓰면 진단이 사고가 된다.
    """
    settings = get_settings()
    rule_id = (payload.rule_id if payload and payload.rule_id in RULE_LABELS else None) or "position_opened"

    persisted = NotificationState()
    state_path = str(getattr(settings, "notification_state_path", "") or "")
    if state_path:
        persisted.load(state_path)

    stages: list[dict[str, Any]] = []

    def stage(name: str, ok: bool, detail: str) -> bool:
        stages.append({"stage": name, "ok": ok, "detail": detail})
        return ok

    sender = TelegramSender(settings)
    chat_ids = len(settings.telegram_allowed_chat_id_list)
    ok = stage("telegram_configured", sender.enabled, f"토큰 {'있음' if settings.telegram_bot_token.strip() else '없음'} · 대상 {chat_ids}개")
    ok = stage("alerts_enabled", settings.telegram_alerts_enabled, "FCE_TELEGRAM_ALERTS_ENABLED") and ok
    ok = stage("not_muted", not persisted.is_muted(), "뮤트 상태" if persisted.is_muted() else "뮤트 아님") and ok
    enabled_rules = settings.alert_enabled_rule_set
    ok = stage("rule_enabled", rule_id in enabled_rules, f"{rule_id} · 활성 규칙 {len(enabled_rules)}개") and ok
    decision = delivery_gate.evaluate_rule(rule_id)
    ok = stage("delivery_gate", decision.allowed, decision.reason or "허용") and ok

    sent = 0
    if ok:
        # 일회용 상태 + **쓰기 경로를 지운 설정**. 상태 객체만 새로 만드는 것으로는 부족하다 —
        # `_fire_if_allowed` 끝의 `_persist()` 가 워커의 상태 파일을 그 빈 상태로 덮어써
        # 진행 중인 쿨다운·라이프사이클 추적을 전부 날린다. 회귀가 이것을 잡았다.
        #
        # 발송 마커도 비운다. 진단 1건이 데드맨의 침묵 시계를 되돌리면, 상류가 죽어 진짜
        # 알림이 0건인 구간을 "발송되고 있다"로 덮는다 — 진단이 감시를 속이는 셈이 된다.
        probe_settings = settings.model_copy(update={"notification_state_path": "", "alert_delivery_path": ""})
        engine = AlertEngine(probe_settings, sender, NotificationState())
        sent = await engine._fire_if_allowed(_test_candidate(rule_id))
        stage("delivered", sent > 0, f"{sent}개 대상에 발송")

    return {
        "configured": sender.enabled,
        "sent": sent,
        "rule_id": rule_id,
        "verdict": "delivered" if sent > 0 else f"blocked:{next((row['stage'] for row in stages if not row['ok']), 'unknown')}",
        "stages": stages,
        # "왜 안 왔나"에 답하는 최근 이력 — 관문 차단 · 쿨다운 · 룰 상태로 떨어진 것들.
        "recent_blocked": persisted.blocked_alerts[-10:],
    }


def _test_candidate(rule_id: str) -> AlertCandidate:
    """실제 후보와 같은 모양. identity 에 시각을 넣어 운영 state_key 와 절대 겹치지 않게 한다."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return AlertCandidate(
        rule_id=rule_id,
        severity=RULE_SEVERITY.get(rule_id, "action"),
        position_id=None,
        symbol="BTCUSDT",
        identity=f"delivery-test:{stamp}",
        title=RULE_LABELS.get(rule_id, "테스트 알림"),
        message="\n".join(
            [
                "<b>발송 경로 점검</b>",
                f"이벤트: {RULE_LABELS.get(rule_id, '테스트 알림')} ({rule_id})",
                "이 메시지는 실제 알림과 **같은 경로**로 나갔습니다 — 관문·룰 활성·뮤트·룰 상태 통과.",
                "주문 실행 없음 · 읽기 전용 관제 알림",
            ]
        ),
        payload={"kind": "delivery_test"},
    )


def _settings_payload(settings) -> dict[str, Any]:
    return {
        "telegram": {
            "configured": bool(settings.telegram_bot_token.strip() and settings.telegram_allowed_chat_id_list),
            "alerts_enabled": settings.telegram_alerts_enabled,
            "local_timezone": settings.telegram_local_timezone,
            "daily_summary_time": settings.telegram_daily_summary_time,
            "pulse_interval_hours": settings.alert_pulse_interval_hours,
            "paper_alerts_enabled": settings.paper_telegram_alerts_enabled,
            "chat_ids_configured": len(settings.telegram_allowed_chat_id_list),
        },
        "scout": {
            "auto_arm_enabled": settings.scout_auto_arm_enabled,
            "auto_arm_symbol_limit": settings.scout_auto_arm_symbol_limit,
            "manual_tracking_symbol_limit": settings.scout_tracking_symbol_limit,
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
        "flow_divergence",
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
        "full_alignment",
    ]
