from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.analyst.param_registry import get_param_definition, validate_param_change
from app.db.models import CalibrationSuggestion, utc_now
from app.review.params import apply_engine_param_overrides, engine_param_from_suggestion


VETO_WINDOW_HOURS = 48
WEEKLY_AUTONOMY_LIMIT = 3


def process_parameter_autonomy(settings: Any, repository: Any, suggestions: list[CalibrationSuggestion]) -> list[CalibrationSuggestion]:
    if not getattr(settings, "param_autonomy_enabled", True):
        return suggestions
    processed: list[CalibrationSuggestion] = []
    for suggestion in suggestions:
        processed.append(process_one_suggestion(settings, repository, suggestion))
    return processed


def process_one_suggestion(settings: Any, repository: Any, suggestion: CalibrationSuggestion) -> CalibrationSuggestion:
    if suggestion.status not in {"pending", "scheduled", "experiment"}:
        return suggestion
    proposed = suggestion.proposed_change or {}
    parameter = proposed.get("parameter")
    if not isinstance(parameter, str):
        return _save_status(repository, suggestion, "discarded", {"reason": "missing_parameter"})
    definition = get_param_definition(parameter)
    if definition is None:
        return _save_status(repository, suggestion, "discarded", {"reason": "unregistered_parameter"})
    old_value = getattr(settings, parameter, proposed.get("from"))
    new_value = proposed.get("to")
    valid, reason = validate_param_change(parameter, new_value)
    if not valid:
        return _save_status(repository, suggestion, "discarded", {"reason": reason})
    direction = definition.classify(old_value, new_value)
    meta = {
        **suggestion.autonomy,
        "enabled": True,
        "parameter": parameter,
        "change_direction": direction,
        "hard_min": definition.hard_min,
        "hard_max": definition.hard_max,
        "min_dwell_days": definition.min_dwell_days,
        "veto_deadline_at": _veto_deadline(suggestion).isoformat(),
    }
    suggestion.autonomy = meta
    if _dwell_active(repository, parameter, definition.min_dwell_days):
        return _save_status(repository, suggestion, "dwell_blocked", {**meta, "reason": "min_dwell_active"})
    if direction == "tighten":
        if utc_now() < _veto_deadline(suggestion):
            return _save_status(repository, suggestion, "scheduled", meta)
        if _weekly_autonomy_count(repository) >= WEEKLY_AUTONOMY_LIMIT:
            return _save_status(repository, suggestion, "scheduled", {**meta, "reason": "weekly_autonomy_limit"})
        return adopt_suggestion(settings, repository, suggestion, adopted_by="autonomy")
    return _start_shadow_experiment(repository, suggestion, meta)


def adopt_suggestion(settings: Any, repository: Any, suggestion: CalibrationSuggestion, *, adopted_by: str = "autonomy") -> CalibrationSuggestion:
    suggestion.status = "adopted" if adopted_by == "autonomy" else "approved"
    suggestion.updated_at = utc_now()
    suggestion.autonomy = {
        **suggestion.autonomy,
        "adopted_by": adopted_by,
        "adopted_at": suggestion.updated_at.isoformat(),
    }
    saved = repository.add_calibration_suggestion(suggestion)
    version = engine_param_from_suggestion(settings, saved, adopted_by=adopted_by)
    if version is not None:
        repository.add_engine_param_version(version)
        apply_engine_param_overrides(settings, repository)
    return saved


def veto_suggestion(repository: Any, suggestion_id: UUID) -> CalibrationSuggestion:
    suggestion = repository.get_calibration_suggestion(suggestion_id)
    if suggestion is None:
        raise KeyError(str(suggestion_id))
    if suggestion.status not in {"pending", "scheduled", "experiment"}:
        return suggestion
    return _save_status(repository, suggestion, "vetoed", {**suggestion.autonomy, "vetoed_at": utc_now().isoformat()})


def experiments_snapshot(suggestions: list[CalibrationSuggestion]) -> dict[str, Any]:
    experiments = [item for item in suggestions if item.status == "experiment"]
    scheduled = [item for item in suggestions if item.status == "scheduled"]
    adopted = [item for item in suggestions if item.status == "adopted"]
    vetoed = [item for item in suggestions if item.status == "vetoed"]
    return {
        "scheduled": len(scheduled),
        "experiments": len(experiments),
        "autonomy_adopted": len(adopted),
        "vetoed": len(vetoed),
        "weekly_limit": WEEKLY_AUTONOMY_LIMIT,
        "veto_window_hours": VETO_WINDOW_HOURS,
    }


def _start_shadow_experiment(repository: Any, suggestion: CalibrationSuggestion, meta: dict[str, Any]) -> CalibrationSuggestion:
    experiment_id = str(uuid5(NAMESPACE_URL, f"fce:param-experiment:{suggestion.id}"))
    experiment_meta = {
        **meta,
        "shadow_experiment_id": experiment_id,
        "preregistered_criteria": {
            "success": "challenger_ci_low_gt_champion_point_estimate",
            "min_sample_size": 30,
            "max_duration_weeks": 6,
        },
        "started_at": suggestion.autonomy.get("started_at") or utc_now().isoformat(),
    }
    return _save_status(repository, suggestion, "experiment", experiment_meta)


def _save_status(repository: Any, suggestion: CalibrationSuggestion, status: str, autonomy: dict[str, Any]) -> CalibrationSuggestion:
    suggestion.status = status
    suggestion.autonomy = autonomy
    suggestion.updated_at = utc_now()
    return repository.add_calibration_suggestion(suggestion)


def _veto_deadline(suggestion: CalibrationSuggestion) -> datetime:
    return _aware(suggestion.created_at) + timedelta(hours=VETO_WINDOW_HOURS)


def _dwell_active(repository: Any, parameter: str, min_dwell_days: int) -> bool:
    latest = repository.latest_engine_param(parameter)
    if latest is None:
        return False
    return _aware(latest.approved_at) + timedelta(days=min_dwell_days) > utc_now()


def _weekly_autonomy_count(repository: Any) -> int:
    week_ago = utc_now() - timedelta(days=7)
    count = 0
    for version in repository.list_engine_params(limit=200):
        if getattr(version, "adopted_by", "manual") == "autonomy" and _aware(version.approved_at) >= week_ago:
            count += 1
    return count


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
