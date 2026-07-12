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
