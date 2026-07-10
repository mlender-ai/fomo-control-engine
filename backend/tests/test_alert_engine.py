from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.api.deps import configure_runtime
from app.core.config import Settings
from app.db.repository import MemoryRepository
from app.exchange.mock import MockMarketDataProvider
from app.notify.alerts import AlertEngine
from app.notify.state import NotificationState
from app.notify.telegram import TelegramSender


class FakeTelegramSender:
    enabled = True

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_to_all(self, text: str, *, reply_markup=None) -> int:
        self.messages.append(text)
        return 1


def _settings(**overrides) -> Settings:
    return Settings(
        database_url="memory://",
        telegram_bot_token="token",
        telegram_chat_id="123",
        alert_default_cooldown_minutes=1,
        **overrides,
    )


def _payload(
    *,
    position_id=None,
    current: float = 100,
    invalidation: float = 95,
    take_profit: float = 110,
    direction: str = "long",
    entry: float = 100,
    leverage: float = 3,
    health: int = 70,
    previous_health: int = 72,
    severity: int = 1,
    previous_severity: int = 1,
    derivatives: dict | None = None,
) -> dict:
    now = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
    position_id = position_id or uuid4()
    latest_snapshot = {
        "id": str(uuid4()),
        "position_id": str(position_id),
        "symbol": "BASEDUSDT",
        "as_of": now.isoformat(),
        "mark_price": current,
        "pnl_percent": -12.5,
        "pnl_source": "exchange",
        "liquidation_price": 50,
        "liquidation_distance_pct": 42,
        "health_score": health,
        "severity_rank": severity,
        "status_label": "관찰 필요",
        "risk_score": 30,
        "score_json": {},
        "analysis": {"derivatives": derivatives or {}},
        "analysis_json": {},
        "created_at": now.isoformat(),
    }
    previous_snapshot = {
        **latest_snapshot,
        "id": str(uuid4()),
        "mark_price": current * 1.01,
        "health_score": previous_health,
        "severity_rank": previous_severity,
        "created_at": (now - timedelta(minutes=2)).isoformat(),
    }
    return {
        "position": {
            "id": str(position_id),
            "symbol": "BASEDUSDT",
            "direction": direction,
            "entry_price": entry,
            "quantity": 10,
            "leverage": leverage,
            "status": "open",
            "liquidation_price": 50,
            "opened_at": (now - timedelta(hours=2)).isoformat(),
        },
        "state": latest_snapshot,
        "latest_snapshot": latest_snapshot,
        "snapshots": [latest_snapshot, previous_snapshot],
        "action_plan": {
            "as_of": now.isoformat(),
            "mark_price": current,
            "invalidation": {
                "price": invalidation,
                "basis": "구조 지지 S1",
                "action": "이탈 시 손절 검토",
            },
            "take_profit": [{"price": take_profit, "basis": "저항 R1", "action": "부분 익절 검토"}],
            "watch_triggers": [],
            "headline_action": "무효화와 익절 트리거를 확인하세요.",
        },
    }


def _derivatives_payload(
    *,
    funding_state: str = "neutral",
    funding_rate: float = 0.0,
    divergence_state: str = "price_up_oi_up",
    coinglass_status: str = "locked",
    cluster_price: float | None = None,
) -> dict:
    clusters = []
    if cluster_price is not None:
        clusters.append({"price": cluster_price, "sources": ["liq_cluster"], "score": 74})
    return {
        "as_of": "2026-07-05T12:00:00+00:00",
        "latest": {
            "provider": "bitget",
            "as_of": "2026-07-05T12:00:00+00:00",
            "funding_rate": funding_rate,
            "open_interest_change_pct": 4.2,
            "long_short_ratio": 1.4,
        },
        "coinglass": {"source_status": coinglass_status, "notes": []},
        "signals": {
            "funding_state": {
                "state": funding_state,
                "label": "펀딩 극단",
                "funding": funding_rate,
                "percentile": 95,
            },
            "oi_price_divergence": {
                "state": divergence_state,
                "label": "가격 하락 + OI 증가",
                "price_change_pct": -2.0,
                "oi_change_pct": 4.2,
            },
            "crowding_score": {"score": 82, "label": "쏠림"},
            "liquidation_clusters": clusters,
        },
    }


@pytest.fixture()
def repo():
    repository = MemoryRepository()
    configure_runtime(repo=repository, provider=MockMarketDataProvider())
    return repository


@pytest.mark.asyncio
async def test_invalidation_breach_sends_once_and_cools_down(repo) -> None:
    sender = FakeTelegramSender()
    engine = AlertEngine(_settings(), sender, NotificationState())
    position_id = uuid4()
    payload = _payload(position_id=position_id, current=90, invalidation=95)

    assert await engine.evaluate_positions([payload]) == 1
    assert await engine.evaluate_positions([payload]) == 0

    alerts = repo.list_alerts(position_id)
    assert len(alerts) == 1
    assert alerts[0].rule_id == "invalidation_breach"
    assert alerts[0].severity == "critical"
    assert alerts[0].delivered is True
    assert "손절 검토" in sender.messages[0]
    assert "매도하세요" not in sender.messages[0]


@pytest.mark.asyncio
async def test_short_take_profit_above_entry_is_not_alerted(repo) -> None:
    sender = FakeTelegramSender()
    engine = AlertEngine(_settings(), sender, NotificationState())
    payload = _payload(
        direction="short",
        entry=158.0,
        current=160.22,
        invalidation=170.0,
        take_profit=160.71,
    )

    assert await engine.evaluate_positions([payload]) == 0
    assert sender.messages == []


@pytest.mark.asyncio
async def test_short_take_profit_alert_uses_directional_progress(repo) -> None:
    sender = FakeTelegramSender()
    clock = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)
    engine = AlertEngine(
        _settings(alert_trigger_near_pct=0.5),
        sender,
        NotificationState(),
        now_provider=lambda: clock,
    )
    payload = _payload(
        direction="short",
        entry=100.0,
        current=89.0,
        invalidation=110.0,
        take_profit=90.0,
    )

    assert await engine.evaluate_positions([payload]) == 1
    assert "익절1 90.0000에 도달했습니다. 현재 89.0000 · 목표 대비 +1.11%" in sender.messages[0]


@pytest.mark.asyncio
async def test_trigger_near_rearms_only_after_exit_and_cooldown(repo) -> None:
    clock = [datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)]
    sender = FakeTelegramSender()
    engine = AlertEngine(_settings(), sender, NotificationState(), now_provider=lambda: clock[0])
    position_id = uuid4()

    near = _payload(position_id=position_id, current=100, invalidation=98.8)
    far = _payload(position_id=position_id, current=103, invalidation=98.8)

    assert await engine.evaluate_positions([near]) == 1
    clock[0] += timedelta(seconds=30)
    assert await engine.evaluate_positions([near]) == 0
    assert await engine.evaluate_positions([far]) == 0
    clock[0] += timedelta(minutes=2)
    assert await engine.evaluate_positions([far]) == 0
    assert await engine.evaluate_positions([near]) == 1

    assert len([alert for alert in repo.list_alerts(position_id, limit=10) if alert.rule_id == "trigger_near"]) == 2


@pytest.mark.asyncio
async def test_quiet_hours_suppresses_warn_and_morning_summary_sends(repo) -> None:
    clock = [datetime(2026, 7, 5, 17, 0, tzinfo=timezone.utc)]  # 02:00 KST
    sender = FakeTelegramSender()
    engine = AlertEngine(
        _settings(telegram_daily_summary_time="08:30"),
        sender,
        NotificationState(),
        now_provider=lambda: clock[0],
    )
    position_id = uuid4()
    payload = _payload(
        position_id=position_id,
        current=100,
        severity=2,
        previous_severity=1,
        leverage=3,
    )

    assert await engine.evaluate_positions([payload]) == 0
    assert len(engine.state.suppressed_alerts) == 1
    assert repo.list_alerts(position_id)[0].delivered is False

    clock[0] = datetime(2026, 7, 5, 23, 30, tzinfo=timezone.utc)  # 08:30 KST
    assert await engine.maybe_send_daily_summary({"positions": [payload], "timestamp": clock[0].isoformat()}) == 1
    assert not engine.state.suppressed_alerts
    assert "상태 악화" in sender.messages[-1]


@pytest.mark.asyncio
async def test_telegram_missing_config_warns_once(caplog) -> None:
    caplog.set_level(logging.WARNING)
    sender = TelegramSender(Settings(database_url="memory://", telegram_bot_token="", telegram_chat_id=""))

    assert await sender.send_to_all("test") == 0
    assert await sender.send_to_all("test") == 0

    warnings = [record for record in caplog.records if "telegram sender disabled" in record.message]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_position_funding_extreme_alert_fires_once_and_rearms(repo) -> None:
    clock = [datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)]
    sender = FakeTelegramSender()
    engine = AlertEngine(_settings(), sender, NotificationState(), now_provider=lambda: clock[0])
    position_id = uuid4()
    adverse = _payload(
        position_id=position_id,
        direction="long",
        derivatives=_derivatives_payload(funding_state="extreme", funding_rate=0.0012),
    )
    recovered = _payload(
        position_id=position_id,
        direction="long",
        derivatives=_derivatives_payload(funding_state="neutral", funding_rate=0.0),
    )

    assert await engine.evaluate_positions([adverse]) == 1
    assert await engine.evaluate_positions([adverse]) == 0
    clock[0] += timedelta(minutes=2)
    assert await engine.evaluate_positions([recovered]) == 0
    assert await engine.evaluate_positions([adverse]) == 1

    alerts = repo.list_alerts(position_id, limit=10)
    assert len([alert for alert in alerts if alert.rule_id == "funding_extreme"]) == 2
    assert "펀딩" in sender.messages[0]
    assert "감시" in sender.messages[0]


@pytest.mark.asyncio
async def test_position_oi_divergence_alert_uses_direction(repo) -> None:
    clock = [datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)]
    sender = FakeTelegramSender()
    engine = AlertEngine(_settings(), sender, NotificationState(), now_provider=lambda: clock[0])
    position_id = uuid4()
    payload = _payload(
        position_id=position_id,
        direction="long",
        derivatives=_derivatives_payload(divergence_state="price_down_oi_up"),
    )

    assert await engine.evaluate_positions([payload]) == 1

    alerts = repo.list_alerts(position_id, limit=10)
    assert alerts[0].rule_id == "oi_divergence"
    assert alerts[0].severity == "info"
    assert "4시간봉" in sender.messages[0]


@pytest.mark.asyncio
async def test_liquidation_cluster_alert_requires_tier2_source(repo) -> None:
    clock = [datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)]
    sender = FakeTelegramSender()
    engine = AlertEngine(_settings(), sender, NotificationState(), now_provider=lambda: clock[0])
    position_id = uuid4()
    locked = _payload(
        position_id=position_id,
        current=100,
        derivatives=_derivatives_payload(coinglass_status="locked", cluster_price=100.8),
    )
    active = _payload(
        position_id=position_id,
        current=100,
        derivatives=_derivatives_payload(coinglass_status="ok", cluster_price=100.8),
    )

    assert await engine.evaluate_positions([locked]) == 0
    assert await engine.evaluate_positions([active]) == 1

    alerts = repo.list_alerts(position_id, limit=10)
    assert alerts[0].rule_id == "liq_cluster_near"
    assert alerts[0].severity == "warn"
    assert "추정 모델" in sender.messages[0]


@pytest.mark.asyncio
async def test_alert_payload_records_number_sources(repo) -> None:
    sender = FakeTelegramSender()
    engine = AlertEngine(_settings(), sender, NotificationState())
    position_id = uuid4()

    assert await engine.evaluate_positions([_payload(position_id=position_id, current=90, invalidation=95)]) == 1
    payload = repo.list_alerts(position_id)[0].payload

    sources = {item["source"] for item in payload["number_sources"]}
    assert "action_plan.invalidation.price" in sources
    assert "snapshot.mark_price_or_last_close" in sources
