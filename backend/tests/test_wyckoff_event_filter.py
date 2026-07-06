from app.positions.chart_analysis import split_wyckoff_events


def _event(event_id: str, confidence: int) -> dict:
    return {
        "id": event_id,
        "type": "secondary_test",
        "label": "ST",
        "confidence": confidence,
    }


def test_split_hides_low_confidence_events_from_display() -> None:
    wyckoff = {
        "events": [
            _event("a", 91),
            _event("b", 40),
            _event("c", 72),
            _event("d", 55),
            _event("e", 30),
        ],
        "evidence_event_ids": ["a", "b"],
    }
    result = split_wyckoff_events(wyckoff, 55)

    assert [event["id"] for event in result["events"]] == ["a", "c", "d"]
    assert [event["id"] for event in result["events_low_confidence"]] == ["b", "e"]


def test_split_caps_display_events_to_recent_four() -> None:
    wyckoff = {
        "events": [_event(str(index), 80) for index in range(7)],
        "evidence_event_ids": [],
    }
    result = split_wyckoff_events(wyckoff, 55)

    assert [event["id"] for event in result["events"]] == ["3", "4", "5", "6"]
    assert result["events_low_confidence"] == []


def test_split_keeps_low_confidence_data_intact() -> None:
    """저신뢰 이벤트는 숨김만 하고 삭제하지 않는다 (복기/캘리브레이션용)."""
    wyckoff = {"events": [_event("a", 10)], "evidence_event_ids": ["a"]}
    result = split_wyckoff_events(wyckoff, 55)

    assert result["events"] == []
    assert len(result["events_low_confidence"]) == 1
    assert [event["id"] for event in result["phase_evidence"]] == ["a"]
