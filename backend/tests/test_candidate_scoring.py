from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.analyst.signature_registry import current_state
from app.backtest.candidate_scoring import (
    CANDIDATE_DEFINITIONS,
    CANDIDATE_SENTINEL_POSITION_ID,
    apply_signature_promotion,
    score_candidates,
    score_live_candidate_judgments,
)
from app.core.config import Settings
from app.db.models import BacktestStat, JudgmentLedgerEntry, JudgmentScore, MarketCandle, utc_now
from app.db.repository import MemoryRepository
from app.review.autonomy import process_one_suggestion


KEY = "fvg:gap_formed:candidate:long:crypto:4h"


def _candidate_stat() -> BacktestStat:
    return BacktestStat(
        signature_key=KEY,
        symbol="BTCUSDT",
        timeframe="4h",
        asset_class="crypto",
        engine="fvg",
        event_type="gap_formed",
        strength_class="candidate",
        direction="long",
        sample_size=40,
        win_1r_pct=90.0,
        payload={
            "label": "FVG",
            "signature": {
                "engine": "fvg",
                "event_type": "gap_formed",
                "strength_class": "candidate",
                "direction": "long",
                "asset_class": "crypto",
                "timeframe": "4h",
            },
            "regimes": {
                "trend": {
                    "sample_size": 40,
                    "win_1r_pct": 90.0,
                }
            },
        },
    )


def _seed_live_win(repo: MemoryRepository) -> None:
    as_of = datetime(2026, 7, 1, tzinfo=timezone.utc)
    repo.add_judgment(
        JudgmentLedgerEntry(
            judgment_id="candidate:fvg:live-1",
            position_id=CANDIDATE_SENTINEL_POSITION_ID,
            source_type="candidate_signature",
            source_id="live-1",
            as_of=as_of,
            type="candidate_signature",
            claim={"engine": "fvg", "event_type": "gap_formed"},
        )
    )
    repo.add_judgment_score(
        JudgmentScore(
            judgment_id="candidate:fvg:live-1",
            position_id=CANDIDATE_SENTINEL_POSITION_ID,
            judgment_type="candidate_signature",
            claim={"engine": "fvg", "event_type": "gap_formed"},
            outcome="correct",
            detail="1R reached after the confirmed event",
        )
    )


def test_score_candidates_records_all_five_and_proposes_without_auto_promotion() -> None:
    repo = MemoryRepository()
    settings = Settings(telegram_alerts_enabled=False)
    repo.upsert_backtest_stat(_candidate_stat())
    _seed_live_win(repo)

    payload = score_candidates(repo, settings)

    assert payload["candidate_count"] == len(CANDIDATE_DEFINITIONS) == 5
    assert set(payload["lookahead_audit"]) == {item[0] for item in CANDIDATE_DEFINITIONS}
    assert all(row["passed"] for row in payload["lookahead_audit"].values())
    fvg = next(item for item in payload["signatures"] if item["engine"] == "fvg")
    assert fvg["sample_size"] == 41
    assert fvg["sources"] == {
        "backtest": {"sample_size": 40, "wins": 36, "wins_2r": 0},
        "live": {"observed": 1, "sample_size": 1, "wins": 1, "wins_2r": 0},
    }
    assert fvg["qualifying_regimes"] == ["trend"]
    assert fvg["promotion_eligible"] is True

    review_rows = [
        stat
        for stat in repo.list_backtest_stats(limit=100)
        if stat.payload.get("candidate_review")
    ]
    assert len(review_rows) == 5
    suggestion = repo.list_calibration_suggestions(status="pending")[0]
    assert suggestion.suggestion_type == "signature_promotion"
    assert process_one_suggestion(settings, repo, suggestion).status == "pending"
    assert current_state(repo, KEY, stat=fvg, settings=settings) == "candidate"
    assert repo.list_autonomy_logs(signature_key=KEY) == []

    transition = apply_signature_promotion(repo, suggestion)

    assert transition.new_state == "validated"
    assert transition.autonomous is False
    assert current_state(repo, KEY, stat=fvg, settings=settings) == "validated"


def test_score_candidates_refuses_to_run_when_lookahead_audit_fails() -> None:
    repo = MemoryRepository()
    repo.upsert_backtest_stat(_candidate_stat())

    with pytest.raises(ValueError, match="vcp"):
        score_candidates(repo, Settings(), audit_overrides={"vcp": False})

    assert not any(
        stat.payload.get("candidate_review")
        for stat in repo.list_backtest_stats(limit=100)
    )


def test_special_candidates_warn_until_predictive_power_is_verified() -> None:
    payload = score_candidates(MemoryRepository(), Settings())
    warnings = {
        item["engine"]: item["prediction_warning"]
        for item in payload["signatures"]
    }

    assert warnings["full_alignment"] == "예측력 미검증"
    assert warnings["money_flow"] == "예측력 미검증"


def test_live_candidate_scores_only_from_later_closed_candles() -> None:
    repo = MemoryRepository()
    start = utc_now() - timedelta(hours=62)
    candles = [
        MarketCandle(
            timestamp=start + timedelta(hours=index),
            open=100 + index * 0.2,
            high=100.3 + index * 0.2,
            low=99.8 + index * 0.2,
            close=100 + index * 0.2,
            volume=1000,
        )
        for index in range(60)
    ]
    observation = candles[5]
    repo.add_judgment(
        JudgmentLedgerEntry(
            judgment_id="candidate:full_alignment:live-closed",
            position_id=CANDIDATE_SENTINEL_POSITION_ID,
            source_type="candidate_signature",
            source_id="live-closed",
            as_of=observation.timestamp,
            type="candidate_signature",
            claim={
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "engine": "full_alignment",
                "event_type": "unanimous",
                "direction": "long",
                "price": observation.close,
            },
        )
    )
    provider = SimpleNamespace(
        get_snapshot=lambda symbol, timeframe: SimpleNamespace(candles=candles)
    )

    result = score_live_candidate_judgments(
        repo,
        provider,
        Settings(backtest_lookahead_bars=48),
    )

    assert result == {"created": 1, "pending": 0, "errors": []}
    score = repo.list_judgment_scores(position_id=CANDIDATE_SENTINEL_POSITION_ID)[0]
    assert score.outcome == "correct"
    assert score.metrics["source"] == "live_validation"
    assert score.metrics["win_1r"] is True
