import pytest
from fastapi.testclient import TestClient

from app.api import routes as runtime
from app.api.scout_routes import reset_scout_cache
from app.db.models import Direction, Position, PositionStatus
from app.main import app


@pytest.fixture(autouse=True)
def clear_cache():
    reset_scout_cache()
    yield
    reset_scout_cache()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_simulate_returns_rr_liquidation_checklist(client: TestClient):
    res = client.post("/api/scout/simulate", json={"symbol": "BTCUSDT", "direction": "long", "leverage": 10, "margin_usdt": 100})
    assert res.status_code == 200
    body = res.json()
    assert body["estimated_liquidation"] is not None
    assert body["estimated_liquidation"] < body["entry_price"]  # long
    assert "checklist" in body and len(body["checklist"]) == 6
    assert body["mmr_source"] in ("exchange", "default")
    assert "추정" in body["liquidation_formula"]
    assert body["verdict_line"].startswith("이 셋업:")


def test_simulate_rejects_bad_direction(client: TestClient):
    res = client.post("/api/scout/simulate", json={"symbol": "BTCUSDT", "direction": "sideways", "leverage": 5})
    assert res.status_code == 422


def test_scenario_save_and_list(client: TestClient):
    save = client.post("/api/scout/scenarios", json={"symbol": "ETHUSDT", "direction": "long", "entry_price": 3400, "leverage": 5, "margin_usdt": 200, "note": "박스 하단 롱"})
    assert save.status_code == 200
    scenario = save.json()["scenario"]
    assert scenario["symbol"] == "ETHUSDT"
    assert scenario["note"] == "박스 하단 롱"
    assert "checklist" in scenario

    listed = client.get("/api/scout/scenarios", params={"symbol": "ETHUSDT"})
    assert listed.status_code == 200
    assert len(listed.json()["scenarios"]) == 1


def test_match_and_link_lifecycle(client: TestClient):
    # 시나리오 저장
    save = client.post("/api/scout/scenarios", json={"symbol": "SOLUSDT", "direction": "long", "entry_price": 150.0, "leverage": 5, "margin_usdt": 100, "note": "SOL 롱 계획"})
    scenario_id = save.json()["scenario"]["id"]

    # 실제 진입 포지션 생성 (같은 심볼+방향)
    position = Position(symbol="SOLUSDT", direction=Direction.long, entry_price=151.0, quantity=1.0, leverage=5, status=PositionStatus.open)
    runtime.repository.add_position(position)

    # 읽기 시점 매칭 → 제안 프리필
    match = client.get(f"/api/scout/match/{position.id}")
    assert match.status_code == 200
    body = match.json()
    assert body["already_linked"] is False
    assert body["scenario"]["id"] == scenario_id
    assert body["suggestion"] is not None
    # 진입가 151 vs 계획 150 → 슬리피지 +0.67%, 1.5% 이하이므로 플래그 없음
    assert body["suggestion"]["slippage_flag"] is False

    # 원클릭 링크 + 프리필 적용
    link = client.post(f"/api/scout/scenarios/{scenario_id}/link", json={"position_id": str(position.id), "apply_prefill": True})
    assert link.status_code == 200
    assert link.json()["linked"] is True
    linked_pos = link.json()["position"]
    assert linked_pos["scenario_id"] == scenario_id
    assert "진입 시나리오" in linked_pos["thesis_text"]

    # 링크 후 재매칭 시 already_linked
    rematch = client.get(f"/api/scout/match/{position.id}")
    assert rematch.json()["already_linked"] is True

    # judgment ledger에 진입 전 판단 등록됨
    judgments = runtime.repository.list_judgments(position.id)
    types = {j.type for j in judgments}
    assert "entry_checklist" in types
    assert "planned_invalidation" in types or "planned_take_profit" in types


def test_slippage_flag_when_entry_far_from_plan(client: TestClient):
    save = client.post("/api/scout/scenarios", json={"symbol": "XRPUSDT", "direction": "long", "entry_price": 2.00, "leverage": 5, "margin_usdt": 100})
    scenario_id = save.json()["scenario"]["id"]
    # 실제 진입 2.10 → +5% 슬리피지 (>1.5%)
    position = Position(symbol="XRPUSDT", direction=Direction.long, entry_price=2.10, quantity=1.0, leverage=5, status=PositionStatus.open)
    runtime.repository.add_position(position)
    match = client.get(f"/api/scout/match/{position.id}")
    assert match.json()["suggestion"]["slippage_flag"] is True

    link = client.post(f"/api/scout/scenarios/{scenario_id}/link", json={"position_id": str(position.id)})
    assert link.json()["slippage_flag"] is True


def test_match_no_scenario_returns_none(client: TestClient):
    position = Position(symbol="DOGEUSDT", direction=Direction.short, entry_price=0.17, quantity=1.0, leverage=3, status=PositionStatus.open)
    runtime.repository.add_position(position)
    match = client.get(f"/api/scout/match/{position.id}")
    assert match.json()["scenario"] is None
