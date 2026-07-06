def test_research_run_creation_stores_seven_agent_outputs(client) -> None:
    response = client.post(
        "/api/research-runs",
        json={"symbol": "BTCUSDT", "timeframe": "4h", "mode": "entry_review"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["symbol"] == "BTCUSDT"
    assert len(payload["checklists"]) == 7
    assert "rule_score" in payload["checklists"][0]
    assert "confidence" not in payload["checklists"][0]

    detail = client.get(f"/api/research-runs/{payload['research_run_id']}")
    assert detail.status_code == 200
    assert detail.json()["raw_output"]["checklists"]


def test_research_run_compare(client) -> None:
    client.post("/api/research-runs", json={"symbol": "ETHUSDT", "timeframe": "4h"})
    response = client.get("/api/research-runs/compare?symbol=ETHUSDT&limit=5")

    assert response.status_code == 200
    assert response.json()["runs"][0]["symbol"] == "ETHUSDT"
