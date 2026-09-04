"""정숙 시간 제거 + 발송 경로 진단 (2026-09-04 사용자 지시).

두 가지를 고정한다:

1. **정숙 시간이 되살아나지 않는다.** 억제 술어도 설정도 사라졌다. 밤에 진입해도 그때 온다.
2. **왜 안 왔는지에 답할 수 있다.** 관문 뒤에서 떨어지던 세 경로(룰 상태·쿨다운·이탈 반복)가
   전부 `return 0` 뿐이었다 — 사용자가 물으면 코드를 읽어야 답할 수 있었고, 그것이
   침묵과 고장이 구분되지 않는 형태다.

그리고 테스트 발송이 **실제 경로를 통과**하는지 본다. 옛 구현은 `sender.send_to_all` 을
직접 불러서 봇 토큰이 살아있다는 것만 증명했다 — "테스트는 오는데 진입 알림은 안 온다"가
정확히 그 구멍이었다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.notify import routes
from app.notify.alerts import AlertEngine
from app.notify.rules import AlertCandidate
from app.notify.state import AlertRuleState, NotificationState


NOW = datetime(2026, 9, 4, 17, 30, tzinfo=timezone.utc)  # 02:30 KST — 옛 무음 창 한가운데


class _Sender:
    def __init__(self, *, enabled: bool = True, delivered: int = 1) -> None:
        self.enabled = enabled
        self.messages: list[str] = []
        self._delivered = delivered

    async def send_to_all(self, message: str, reply_markup=None) -> int:
        self.messages.append(message)
        return self._delivered


POSITION_ID = "11111111-2222-3333-4444-555555555555"


def _settings(**overrides) -> Settings:
    base = {
        "database_url": "memory://",
        "telegram_bot_token": "token",
        "telegram_chat_id": "123",
        "telegram_alerts_enabled": True,
        "notification_state_path": "",
    }
    return Settings(**{**base, **overrides})


def _candidate(rule_id: str = "position_opened", identity: str = "one") -> AlertCandidate:
    return AlertCandidate(
        rule_id=rule_id,
        severity="action",
        position_id=POSITION_ID,
        symbol="BTCUSDT",
        identity=identity,
        title="진입 감지",
        message="진입",
        payload={"kind": "lifecycle"},
    )


def _engine(monkeypatch, *, sender: _Sender | None = None, settings: Settings | None = None):
    sender = sender or _Sender()
    engine = AlertEngine(settings or _settings(), sender, NotificationState(), now_provider=lambda: NOW)
    monkeypatch.setattr("app.notify.alerts.service.record_alert", lambda record: record)
    return engine, sender


# ── 1. 정숙 시간이 사라졌다 ────────────────────────────────────────────────────


def test_quiet_hours_predicate_is_gone() -> None:
    """술어 자체가 없어야 한다. 남아 있으면 누군가 다시 배선한다."""
    import app.notify.rules as rules

    assert not hasattr(rules, "quiet_hours_active")


def test_quiet_hours_settings_are_gone() -> None:
    settings = _settings()
    for field in ("telegram_quiet_hours_enabled", "telegram_quiet_hours_start", "telegram_quiet_hours_end"):
        assert not hasattr(settings, field), field
    # 지역 시간대는 남는다 — 일일 요약 시각 판정과 본문 표기가 쓴다(억제와 무관).
    assert settings.telegram_local_timezone == "Asia/Seoul"


def test_old_timezone_env_name_still_read(monkeypatch) -> None:
    """운영 `.env` 를 고칠 수 없으므로 옛 이름이 계속 읽혀야 한다."""
    monkeypatch.setenv("FCE_TELEGRAM_QUIET_HOURS_TIMEZONE", "America/New_York")
    assert Settings(database_url="memory://").telegram_local_timezone == "America/New_York"


def test_alert_fires_inside_the_old_quiet_window(monkeypatch) -> None:
    """02:30 KST 진입 알림이 **그때** 나간다 — 아침 요약으로 미루지 않는다."""
    engine, sender = _engine(monkeypatch)
    assert asyncio.run(engine._fire_if_allowed(_candidate())) == 1
    assert sender.messages
    assert engine.state.suppressed_alerts == []


# ── 2. 떨어진 알림에 사유가 남는다 ───────────────────────────────────────────


def test_cooldown_drop_records_reason(monkeypatch) -> None:
    engine, sender = _engine(monkeypatch)
    candidate = _candidate()
    assert asyncio.run(engine._fire_if_allowed(candidate)) == 1
    sender.messages.clear()

    # 같은 state_key 로 곧바로 다시 — 쿨다운에 걸린다.
    assert asyncio.run(engine._fire_if_allowed(candidate)) == 0
    assert sender.messages == []
    reasons = [row["reason"] for row in engine.state.blocked_alerts]
    assert any(reason.startswith("cooldown:") or reason.startswith("rule_state:") for reason in reasons), reasons


def test_rule_state_drop_records_reason(monkeypatch) -> None:
    engine, _sender = _engine(monkeypatch)
    candidate = _candidate(identity="two")
    state = AlertRuleState()
    state.status = "cooldown"
    state.cooldown_until = NOW - timedelta(hours=1)  # 쿨다운은 끝났는데 상태가 armed 가 아니다
    engine.state.alert_rule_states[candidate.state_key] = state

    assert asyncio.run(engine._fire_if_allowed(candidate)) == 0
    assert [row["reason"] for row in engine.state.blocked_alerts] == ["rule_state:cooldown"]


def test_dropped_reasons_are_bounded(monkeypatch) -> None:
    """쿨다운은 매 틱 발생할 수 있다 — 큐가 무한히 자라면 그것이 다음 사고다."""
    engine, _sender = _engine(monkeypatch)
    candidate = _candidate(identity="three")
    assert asyncio.run(engine._fire_if_allowed(candidate)) == 1
    for _ in range(260):
        asyncio.run(engine._fire_if_allowed(candidate))
    assert len(engine.state.blocked_alerts) == 200


# ── 3. 테스트 발송이 실제 경로를 지난다 ──────────────────────────────────────


def test_probe_reports_every_stage(monkeypatch) -> None:
    settings = _settings()
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "TelegramSender", lambda _settings: _Sender())
    monkeypatch.setattr("app.notify.alerts.service.record_alert", lambda record: record)

    result = asyncio.run(routes.send_test_alert(None))
    assert result["rule_id"] == "position_opened"
    assert result["verdict"] == "delivered"
    assert result["sent"] == 1
    stages = [row["stage"] for row in result["stages"]]
    # 봇 토큰만 보는 것이 아니라 관문·룰 활성·뮤트까지 지난다는 것이 요점이다.
    assert stages == ["telegram_configured", "alerts_enabled", "not_muted", "rule_enabled", "delivery_gate", "delivered"]


def test_probe_names_the_blocking_stage(monkeypatch) -> None:
    """막히면 **어디서** 막혔는지 말한다. 'sent: 0' 만으로는 아무것도 못 고친다."""
    settings = _settings(telegram_alerts_enabled=False)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "TelegramSender", lambda _settings: _Sender())

    result = asyncio.run(routes.send_test_alert(None))
    assert result["verdict"] == "blocked:alerts_enabled"
    assert result["sent"] == 0
    assert not next(row for row in result["stages"] if row["stage"] == "alerts_enabled")["ok"]


def test_probe_does_not_write_worker_state(monkeypatch, tmp_path) -> None:
    """진단이 운영 쿨다운·룰 상태를 덮으면 진단이 사고가 된다."""
    state_path = tmp_path / "notify.json"
    saved = NotificationState()
    saved.alert_rule_states["position_opened:pos-9:x"] = AlertRuleState()
    saved.save(str(state_path))
    before = state_path.read_text()

    settings = _settings(notification_state_path=str(state_path))
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "TelegramSender", lambda _settings: _Sender())
    monkeypatch.setattr("app.notify.alerts.service.record_alert", lambda record: record)

    assert asyncio.run(routes.send_test_alert(None))["verdict"] == "delivered"
    assert state_path.read_text() == before


def test_probe_does_not_touch_the_silence_marker(monkeypatch, tmp_path) -> None:
    """진단 1건이 데드맨의 침묵 시계를 되돌리면 진단이 감시를 속인다.

    상류(동기화·큐)가 죽어 진짜 알림이 0건인 구간에서 진단만 성공하면, 마커가 갱신되어
    "발송되고 있다"로 읽힌다. 침묵 감지를 워커 밖으로 뺀 이유가 통째로 무너진다.
    """
    marker = tmp_path / "alert_delivery.json"
    settings = _settings(alert_delivery_path=str(marker))
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "TelegramSender", lambda _settings: _Sender())
    monkeypatch.setattr("app.notify.alerts.service.record_alert", lambda record: record)

    assert asyncio.run(routes.send_test_alert(None))["sent"] == 1
    assert not marker.exists()


def test_probe_state_key_cannot_collide_with_a_real_position() -> None:
    """진단 후보가 실제 포지션의 state_key 를 먹으면 그 포지션의 진입 알림이 사라진다."""
    key = routes._test_candidate("position_opened").state_key
    assert key.startswith("position_opened:system:delivery-test:")


@pytest.mark.parametrize("rule_id", ["position_opened", "position_closed"])
def test_probe_accepts_a_named_rule(monkeypatch, rule_id: str) -> None:
    settings = _settings()
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "TelegramSender", lambda _settings: _Sender())
    monkeypatch.setattr("app.notify.alerts.service.record_alert", lambda record: record)

    result = asyncio.run(routes.send_test_alert(routes.AlertTestRequest(rule_id=rule_id)))
    assert result["rule_id"] == rule_id
