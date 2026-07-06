import asyncio

from app.api.deps import configure_runtime
from app.db.models import ExitRequest, PositionStatus
from app.db.repository import MemoryRepository
from app.demo.provider import FakeBitgetProvider
from app.notify.alerts import AlertEngine
from app.notify.state import NotificationState
from app.notify.telegram import TelegramSender
from app.services import http_handlers as handlers
from app.services import runtime as service


def test_demo_mode_full_cycle(monkeypatch):
    repo = MemoryRepository()
    provider = FakeBitgetProvider()
    configure_runtime(repo=repo, provider=provider)
    monkeypatch.setattr(handlers.settings, "demo_mode", True)
    monkeypatch.setattr(handlers.settings, "telegram_alerts_enabled", True)
    monkeypatch.setattr(handlers.settings, "telegram_quiet_hours_enabled", False)

    seeded = service.seed_demo_data()
    assert seeded["enabled"] is True
    assert seeded["positions"] == 3

    for _ in range(3):
        payload = service.sync_and_analyze_positions()
        assert payload["open_count"] == 3
        service.refresh_derivative_data()

    positions = repo.list_positions(PositionStatus.open)
    critical = next(position for position in positions if position.symbol == "ETHUSDT")
    detail = service.live_position_detail(critical.id)
    assert detail["action_plan"]["invalidation"]["price"] == critical.planned_stop_price

    insight = service.create_position_insight(critical.id, auto_generated=True)
    assert insight["latest_insight"]["insight_source"] in {"template", "fallback_template", "llm"}

    alert_engine = AlertEngine(handlers.settings, TelegramSender(handlers.settings), NotificationState())
    sent = asyncio.run(alert_engine.evaluate_positions([service.live_position_alert_context(critical.id)]))
    assert sent >= 1
    assert repo.list_alerts(position_id=critical.id)

    trade = handlers.record_live_position_exit(
        critical.id,
        ExitRequest(
            exit_price=critical.mark_price or critical.current_price or critical.entry_price,
            exit_reason="DEMO full-cycle close injection",
            memo="WO-FCE-27 full-cycle test",
        ),
    )
    assert trade.review_v2
    assert repo.list_judgment_scores(position_id=critical.id, trade_id=trade.id)

    calibration = service.calibration_snapshot()
    assert calibration["totals"]["total"] >= 1
