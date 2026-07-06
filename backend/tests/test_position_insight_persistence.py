from app.db.models import PositionInsight
from app.db.repository import MemoryRepository


def test_position_insight_persistence_keeps_latest_first() -> None:
    repository = MemoryRepository()
    first = PositionInsight(
        position_id="11111111-1111-4111-8111-111111111111",
        health_score=71,
        status_label="관찰 필요",
        input_json={
            "position": {"symbol": "BTCUSDT"},
            "chart": {"critical_support": 100},
        },
        insight_text="first insight",
    )
    second = PositionInsight(
        position_id=first.position_id,
        health_score=68,
        status_label="진입 논리 약화",
        input_json={
            "position": {"symbol": "BTCUSDT"},
            "chart": {"critical_support": 98},
        },
        insight_text="second insight",
    )

    repository.add_position_insight(first)
    repository.add_position_insight(second)
    insights = repository.list_position_insights(first.position_id, limit=2)

    assert insights[0].id == second.id
    assert insights[0].input_json["chart"]["critical_support"] == 98
    assert insights[1].id == first.id
