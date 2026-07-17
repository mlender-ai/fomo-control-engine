from fastapi.testclient import TestClient

from app.main import app
from app.services import runtime


def test_paper_scoreboard_api(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime,
        "paper_scoreboard",
        lambda: {"engine": {"trade_count": 2}, "user": {"trade_count": 1}, "live_orders_enabled": False},
    )
    response = TestClient(app).get("/api/paper/scoreboard")
    assert response.status_code == 200
    assert response.json()["live_orders_enabled"] is False


def test_paper_dashboard_api(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime,
        "paper_dashboard",
        lambda: {
            "scoreboard": {"engine": {"trade_count": 1}, "user": {"trade_count": 2}},
            "open_trades": [],
            "closed_trades": [],
            "calibration": {"signature_state_counts": {"validated": 3}},
            "performance_action": {"poor": False, "actions": []},
            "live_orders_enabled": False,
        },
    )
    response = TestClient(app).get("/api/paper/dashboard")
    assert response.status_code == 200
    assert response.json()["calibration"]["signature_state_counts"]["validated"] == 3
    assert response.json()["live_orders_enabled"] is False


def test_paper_dashboard_service_uses_worker_snapshot_without_market_fetch(monkeypatch) -> None:
    expected = {
        "open_trades": [
            {
                "symbol": "BTCUSDT",
                "timeframe": "4h",
                "exit_monitor": {"mark_price": 101.0},
            }
        ]
    }

    monkeypatch.setattr(runtime, "calibration_snapshot", lambda: {"cache_status": "ready"})
    monkeypatch.setattr(runtime, "_paper_dashboard", lambda *_args, **_kwargs: expected)

    def fail_market_fetch(*_args, **_kwargs):
        raise AssertionError("read-only paper dashboard fetched an external market snapshot")

    monkeypatch.setattr(runtime.runtime.market_provider, "get_snapshot", fail_market_fetch)

    assert runtime.paper_dashboard() == expected
