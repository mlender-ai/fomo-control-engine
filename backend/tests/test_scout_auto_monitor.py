from __future__ import annotations

from datetime import timedelta

from app.core.config import Settings
from app.db.models import AlertRecord, EntryIntent, ScoutSnapshot, utc_now
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


def _scan_payload(
    symbol: str,
    mark: float,
    trigger: float,
    *,
    direction: str = "short",
    analysis: dict | None = None,
    preview: dict | None = None,
) -> dict:
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
                            **(preview or {}),
                        },
                    }
                ],
                "analysis": analysis or {},
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


def test_scout_setup_alert_distinguishes_overall_stance_from_trigger_direction() -> None:
    repo = MemoryRepository()
    settings = _settings()
    analysis = {
        "one_liners": {
            "overall_stance": "상방",
            "summary": "상방 3 · 하방 1 · 중립 2 · 판단불가 1",
        }
    }
    preview = {
        "briefing_summary": "브리핑: 숏 우위 · 롱 26.14 / 숏 35.13",
        "invalidation_distance_pct": -3.2,
    }

    process_scout_scan(
        repo,
        settings,
        _scan_payload("SOXLUSDT", mark=100, trigger=101, direction="short", analysis=analysis, preview=preview),
    )
    triggered = process_scout_scan(
        repo,
        settings,
        _scan_payload("SOXLUSDT", mark=101.1, trigger=101, direction="short", analysis=analysis, preview=preview),
    )

    message = triggered["_alert_candidate_objects"][0].message
    assert "셋업 방향: 숏 · 현재 종합: 상방 근거 우세 · 충돌" in message
    assert "프리뷰(숏 10x 가정)" in message
    assert "종합 브리핑: 숏 우위" in message
    assert "브리핑: 브리핑" not in message
    assert "short 10x" not in message


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


def test_watch_intent_is_refreshed_without_zone_alerts() -> None:
    repo = MemoryRepository()
    settings = _settings(scout_auto_arm_enabled=False)
    now = utc_now()
    intent = repo.upsert_entry_intent(
        EntryIntent(
            symbol="SOXLUSDT",
            kind="watch",
            direction=None,
            zone_lower=None,
            zone_upper=None,
            conditions=[],
            expires_at=now + timedelta(days=14),
        )
    )

    result = process_scout_scan(repo, settings, _intent_scan_payload("SOXLUSDT", 190))

    assert not [item for item in result["alert_candidates"] if item["rule_id"].startswith("intent_")]
    refreshed = repo.get_entry_intent(intent.id)
    assert refreshed is not None
    assert refreshed.last_seen_at >= intent.last_seen_at


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


def test_full_alignment_shares_universe_daily_limit_and_symbol_cooldown() -> None:
    repo = MemoryRepository()
    settings = _settings(
        alert_enabled_rules="full_alignment,universe_discovery",
        universe_daily_alert_limit=3,
        universe_symbol_cooldown_hours=48,
        scout_auto_arm_enabled=False,
    )
    row = {
        "symbol": "SOXLUSDT",
        "timeframe": "4h",
        "as_of": utc_now().isoformat(),
        "mark_price": 100,
        "tracked": True,
        "setup_candidates": [],
        "full_alignment": {
            "unanimous": True,
            "bar_at": utc_now().isoformat(),
            "direction": "long",
            "agreeing": 5,
            "dissenting": 0,
            "score": 72,
            "sample_label": "표본 축적 중",
        },
    }

    first = process_scout_scan(repo, settings, {"rows": [row], "scanned_at": utc_now().isoformat(), "count": 1})
    assert [item.rule_id for item in first["_alert_candidate_objects"]] == ["full_alignment"]

    repo.add_alert(
        AlertRecord(
            rule_id="universe_discovery",
            symbol="SOXLUSDT",
            severity="info",
            fired_at=utc_now() - timedelta(hours=2),
        )
    )
    row["full_alignment"] = {**row["full_alignment"], "bar_at": (utc_now() + timedelta(hours=4)).isoformat()}
    blocked = process_scout_scan(repo, settings, {"rows": [row], "scanned_at": utc_now().isoformat(), "count": 1})

    assert blocked["_alert_candidate_objects"] == []
