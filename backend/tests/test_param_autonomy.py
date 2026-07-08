from datetime import timedelta
from uuid import uuid4

from app.core.config import Settings
from app.db.models import CalibrationSuggestion, utc_now
from app.db.repository import MemoryRepository
from app.review.autonomy import process_one_suggestion, process_parameter_autonomy, veto_suggestion


def test_tighten_suggestion_enters_veto_window_before_autonomy_adoption() -> None:
    repo = MemoryRepository()
    settings = Settings()
    suggestion = _suggestion("min_invalidation_level_score", 40, 55)

    saved = process_one_suggestion(settings, repo, suggestion)

    assert saved.status == "scheduled"
    assert saved.autonomy["change_direction"] == "tighten"
    assert "veto_deadline_at" in saved.autonomy
    assert repo.list_engine_params() == []


def test_tighten_suggestion_is_adopted_after_veto_window() -> None:
    repo = MemoryRepository()
    settings = Settings()
    suggestion = _suggestion("min_invalidation_level_score", 40, 55)
    suggestion.created_at = utc_now() - timedelta(hours=49)

    saved = process_one_suggestion(settings, repo, suggestion)

    assert saved.status == "adopted"
    version = repo.latest_engine_param("min_invalidation_level_score")
    assert version is not None
    assert version.new_value == 55
    assert version.adopted_by == "autonomy"
    assert settings.min_invalidation_level_score == 55


def test_veto_blocks_scheduled_suggestion() -> None:
    repo = MemoryRepository()
    settings = Settings()
    scheduled = process_one_suggestion(settings, repo, _suggestion("wyckoff_event_min_confidence", 55, 70))

    vetoed = veto_suggestion(repo, scheduled.id)

    assert vetoed.status == "vetoed"
    assert repo.latest_engine_param("wyckoff_event_min_confidence") is None


def test_loosen_or_neutral_suggestion_enters_shadow_experiment() -> None:
    repo = MemoryRepository()
    settings = Settings()
    suggestion = _suggestion("alert_trigger_near_pct", 1.5, 2.0)

    saved = process_one_suggestion(settings, repo, suggestion)

    assert saved.status == "experiment"
    assert saved.autonomy["change_direction"] == "loosen"
    assert saved.autonomy["preregistered_criteria"]["min_sample_size"] == 30


def test_unregistered_or_out_of_bound_parameter_is_discarded() -> None:
    repo = MemoryRepository()
    settings = Settings()
    suggestion = _suggestion("harmonic_min_confidence", 55, 110)

    saved = process_parameter_autonomy(settings, repo, [suggestion])[0]

    assert saved.status == "discarded"
    assert saved.autonomy["reason"] == "above_hard_max:95"


def _suggestion(parameter: str, old_value: float | int, new_value: float | int) -> CalibrationSuggestion:
    return CalibrationSuggestion(
        id=uuid4(),
        suggestion_type="test",
        title=f"{parameter} change",
        rationale="test fixture",
        proposed_change={"parameter": parameter, "from": old_value, "to": new_value},
        sample_size=22,
    )
