from datetime import datetime, timezone
from uuid import uuid4

from app.db.models import (
    AlertRecord,
    Direction,
    JudgmentLedgerEntry,
    JudgmentScore,
    Position,
    PositionEvent,
    PositionSnapshot,
)
from app.db.repository import MemoryRepository
from app.review.coverage import judgment_coverage
from app.services import http_handlers, runtime as service_runtime


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


def test_coverage_counts_recorded_pending_and_unscorable_honestly() -> None:
    repo = MemoryRepository()
    position_id = uuid4()
    scored = _judgment(position_id, "candidate_signature", "scored")
    pending = _judgment(position_id, "stance_flipped", "pending")
    repo.add_judgment(scored)
    repo.add_judgment(pending)
    repo.add_judgment(_judgment(position_id, "position_entry_snapshot", "fact"))
    repo.add_judgment_score(
        JudgmentScore(
            judgment_id=scored.judgment_id,
            position_id=position_id,
            judgment_type=scored.type,
            outcome="correct",
            detail="T+1 확인",
            created_at=NOW,
        )
    )

    result = judgment_coverage(repo, now=NOW)

    assert result["status"] == "ok"
    assert result["total"] == 3
    assert result["recorded"] == 2
    assert result["pending"] == 1
    assert result["unscorable"] == 1
    assert result["coverage_pct"] == 100.0
    assert result["unclassified_types"] == []


def test_stance_flip_alert_writes_semantic_judgment(monkeypatch) -> None:
    repo = MemoryRepository()
    position = Position(symbol="BTCUSDT", direction=Direction.long, entry_price=100, quantity=1)
    repo.add_position(position)
    monkeypatch.setattr(service_runtime.runtime, "repository", repo)

    service_runtime.record_alert(
        AlertRecord(
            rule_id="stance_flipped",
            position_id=position.id,
            symbol=position.symbol,
            severity="warn",
            fired_at=NOW,
            payload={"from": "상방", "to": "하방", "mark_price": 99},
        )
    )

    assert {entry.type for entry in repo.list_judgments(position.id)} == {"alert_fired", "stance_flipped"}


def test_position_status_event_writes_snapshot_row(monkeypatch) -> None:
    repo = MemoryRepository()
    monkeypatch.setattr(http_handlers, "repository", repo)
    position = Position(symbol="ETHUSDT", direction=Direction.short, entry_price=1700, quantity=1)
    snapshot = PositionSnapshot(
        position_id=position.id,
        symbol=position.symbol,
        as_of=NOW,
        mark_price=1680,
        health_score=45,
        status_label="위험",
        risk_score=78,
        score_json={},
        analysis_json={},
    )
    event = PositionEvent(
        position_id=position.id,
        event_type="status_change",
        severity="high",
        title="상태 변경",
        data={"previous": "관찰", "current": "위험"},
    )

    http_handlers._record_position_event_judgment(position, snapshot, event)

    judgment = repo.list_judgments(position.id)[0]
    assert judgment.type == "position_status_transition"
    assert judgment.claim["previous"] == "관찰"
    assert judgment.claim["current"] == "위험"


def _judgment(position_id, kind: str, suffix: str) -> JudgmentLedgerEntry:
    return JudgmentLedgerEntry(
        judgment_id=f"{kind}:{suffix}",
        position_id=position_id,
        source_type="test",
        as_of=NOW,
        type=kind,
    )
