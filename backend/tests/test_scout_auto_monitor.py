from __future__ import annotations

from datetime import timedelta

from app.core.config import Settings
from app.db.models import EntryIntent, ScoutSnapshot, utc_now
from app.db.repository import MemoryRepository
from app.scout.monitor import (
    SCOUT_SENTINEL_POSITION_ID,
    process_scout_scan,
    scout_rate_budget,
    score_entry_intents,
    score_scout_setups,
)


def _settings(**overrides) -> Settings:
    defaults = {
        "telegram_alerts_enabled": False,
        "scout_auto_arm_enabled": True,
        "scout_max_armed_setups_per_symbol": 3,
        "worker_scout_scan_enabled": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _scan_payload(symbol: str, mark: float, trigger: float, *, direction: str = "short") -> dict:
    distance = ((trigger - mark) / mark) * 100
    return {
        "rows": [
            {
                "symbol": symbol,
                "timeframe": "4h",
                "as_of": utc_now().isoformat(),
                "mark_price": mark,
                "setup_proximity_pct": abs(distance),
                "setup_candidates": [
                    {
                        "setup_type": "structure_level",
                        "direction": direction,
                        "trigger_price": trigger,
                        "trigger_label": "구조 저항" if direction == "short" else "구조 지지",
                        "trigger_condition": "레벨 근접 시 반응 확인",
                        "distance_pct": round(distance, 2),
                        "confidence": 78,
                        "basis": "저항 · 터치 4 · 점수 78",
                        "preview": {
                            "rr_ratio": 2.4,
                            "checklist_passed": 5,
                            "checklist_total": 6,
                        },
                    }
                ],
            }
        ],
        "scanned_at": utc_now().isoformat(),
        "count": 1,
    }


def _intent_scan_payload(symbol: str, mark: float, *, analysis: dict | None = None) -> dict:
    return {
        "rows": [
            {
                "symbol": symbol,
                "timeframe": "4h",
                "as_of": utc_now().isoformat(),
                "mark_price": mark,
                "analysis": analysis or {},
                "setup_candidates": [],
            }
        ],
        "scanned_at": utc_now().isoformat(),
        "count": 1,
    }


def test_scout_auto_arm_setup_near_then_triggered_alert_chain() -> None:
    repo = MemoryRepository()
    settings = _settings()

    near = process_scout_scan(
        repo,
        settings,
        _scan_payload("PENGUUSDT", mark=100, trigger=101, direction="short"),
    )
    assert len(near["armed_setups"]) == 1
    assert near["alert_candidates"][0]["setup_type"] == "structure_level"
    assert repo.list_armed_setups(symbol="PENGUUSDT", status="armed")[0].setup_near_alerted_at is not None

    triggered = process_scout_scan(
        repo,
        settings,
        _scan_payload("PENGUUSDT", mark=101.1, trigger=101, direction="short"),
    )
    assert triggered["alert_candidates"][0]["setup_id"] == str(near["armed_setups"][0]["id"])
    assert repo.list_armed_setups(symbol="PENGUUSDT", status="triggered")


def test_scout_setup_scores_without_position_after_price_path() -> None:
    repo = MemoryRepository()
    settings = _settings(scout_setup_score_after_hours=1)
    process_scout_scan(
        repo,
        settings,
        _scan_payload("BASEDUSDT", mark=100, trigger=101, direction="short"),
    )
    setup = repo.list_armed_setups(symbol="BASEDUSDT", status="armed")[0]
    old_time = utc_now() - timedelta(hours=2)
    repo.upsert_armed_setup(
        setup.model_copy(
            update={
                "status": "triggered",
                "triggered_at": old_time,
                "updated_at": old_time,
            }
        )
    )
    repo.add_scout_snapshot(
        ScoutSnapshot(
            symbol="BASEDUSDT",
            timeframe="4h",
            as_of=old_time + timedelta(minutes=30),
            mark_price=99.0,
        )
    )
    repo.add_scout_snapshot(
        ScoutSnapshot(
            symbol="BASEDUSDT",
            timeframe="4h",
            as_of=old_time + timedelta(minutes=45),
            mark_price=98.5,
        )
    )

    result = score_scout_setups(repo, settings)

    assert result["scores"] == 1
    scores = repo.list_judgment_scores(position_id=SCOUT_SENTINEL_POSITION_ID)
    assert scores[0].judgment_type == "scout_setup"
    assert scores[0].outcome == "correct"
    assert scores[0].claim["symbol"] == "BASEDUSDT"


def test_entry_intent_partial_then_triggered_alert_chain() -> None:
    repo = MemoryRepository()
    settings = _settings(scout_auto_arm_enabled=False)
    now = utc_now()
    repo.upsert_entry_intent(
        EntryIntent(
            symbol="TSLAUSDT",
            direction="long",
            zone_lower=240,
            zone_upper=250,
            conditions=["price_in_zone", "sweep_confirmed"],
            tolerance="normal",
            tolerance_pct=1.5,
            judgment_id="entry_intent:tsla",
            expires_at=now + timedelta(days=14),
        )
    )

    partial = process_scout_scan(repo, settings, _intent_scan_payload("TSLAUSDT", 245))

    assert partial["alert_candidates"][0]["rule_id"] == "intent_zone_entered_partial"
    active = repo.list_entry_intents(symbol="TSLAUSDT", status="active")[0]
    assert active.partial_alerted_at is not None
    assert active.condition_state["price_in_zone"]["met"] is True
    assert active.condition_state["sweep_confirmed"]["met"] is False

    triggered = process_scout_scan(
        repo,
        settings,
        _intent_scan_payload(
            "TSLAUSDT",
            246,
            analysis={
                "liquidity": {
                    "sweeps": [
                        {
                            "confirmed": True,
                            "grade": "Strong",
                            "price": 244.5,
                        }
                    ]
                }
            },
        ),
    )

    assert triggered["alert_candidates"][0]["rule_id"] == "intent_zone_entered"
    assert repo.list_entry_intents(symbol="TSLAUSDT", status="triggered")
    judgments = repo.list_judgments(position_id=SCOUT_SENTINEL_POSITION_ID)
    assert any(judgment.type == "entry_intent" for judgment in judgments)


def test_entry_intent_scoring_after_triggered_path() -> None:
    repo = MemoryRepository()
    settings = _settings(scout_auto_arm_enabled=False, entry_intent_score_after_hours=1)
    old_time = utc_now() - timedelta(hours=2)
    intent = repo.upsert_entry_intent(
        EntryIntent(
            symbol="TSLAUSDT",
            direction="long",
            zone_lower=240,
            zone_upper=250,
            status="triggered",
            triggered_at=old_time,
            updated_at=old_time,
            judgment_id="entry_intent:tsla-score",
            expires_at=old_time + timedelta(days=14),
        )
    )
    repo.add_scout_snapshot(
        ScoutSnapshot(
            symbol="TSLAUSDT",
            timeframe="4h",
            as_of=old_time + timedelta(minutes=30),
            mark_price=252,
        )
    )
    repo.add_scout_snapshot(
        ScoutSnapshot(
            symbol="TSLAUSDT",
            timeframe="4h",
            as_of=old_time + timedelta(minutes=45),
            mark_price=258,
        )
    )

    result = score_entry_intents(repo, settings)

    assert result["scores"] == 1
    scores = repo.list_judgment_scores(position_id=SCOUT_SENTINEL_POSITION_ID)
    assert scores[0].judgment_id == intent.judgment_id
    assert scores[0].judgment_type == "entry_intent"
    assert scores[0].outcome == "correct"


def test_scout_rate_budget_documents_30_symbol_limit() -> None:
    budget = scout_rate_budget(_settings(), 31)

    assert budget["bitget_requests_per_symbol"] == 3
    assert budget["max_symbols_per_tick"] == 30
    assert budget["round_robin_required"] is True
    assert "candles 1 + ticker 1 + derivatives 1" in budget["formula"]
