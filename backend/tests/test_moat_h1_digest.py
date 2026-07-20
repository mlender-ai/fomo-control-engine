from app.db.repository import MemoryRepository
from app.notify.bot.formatters import format_improvement_digest
from app.services import runtime as service_runtime


def test_weekly_digest_publishes_h1_status_from_current_data(monkeypatch) -> None:
    monkeypatch.setattr(service_runtime.runtime, "repository", MemoryRepository())

    digest = service_runtime.improvement_digest(scores=[], suggestions=[])

    h1 = digest["moat_h1"]
    assert h1["real_history"] == {"status": "pending", "available": 0, "expected": 3}
    assert h1["ledger"]["coverage_pct"] == 100.0
    assert h1["fomo"]["eligible_trades"] == 0
    assert h1["fomo"]["sample_sufficient"] is False
    assert h1["routes"] == {
        "status": "consolidated",
        "canonical_page_routes": 9,
        "removed_legacy_pages": 15,
    }


def test_telegram_improvement_card_includes_h1_honesty_line() -> None:
    rendered = format_improvement_digest(
        {
            "totals": {"tested": 0},
            "moat_h1": {
                "batch": "WO-FCE-88~91",
                "real_history": {"available": 1, "expected": 3},
                "ledger": {"coverage_pct": 98.5},
                "fomo": {"eligible_trades": 2, "sample_floor": 10},
                "routes": {"removed_legacy_pages": 15},
                "honesty": "구현 완료와 성과 달성을 구분",
            },
        }
    )

    assert "WO-FCE-88~91 현황" in rendered
    assert "실이력 1/3 · 원장 98.5%" in rendered
    assert "FOMO 진입 표본 2/10 · 레거시 page 15개 제거" in rendered
    assert "구현 완료와 성과 달성을 구분" in rendered
