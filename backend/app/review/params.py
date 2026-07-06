from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.db.models import CalibrationSuggestion, EngineParamVersion, utc_now


def engine_param_snapshot(repository) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for version in repository.list_engine_params(limit=200):
        if version.status != "active" or version.param in snapshot:
            continue
        snapshot[version.param] = {
            "id": str(version.id),
            "value": version.new_value,
            "approved_at": version.approved_at.isoformat(),
            "suggestion_id": str(version.suggestion_id) if version.suggestion_id else None,
        }
    return snapshot


def apply_engine_param_overrides(settings, repository) -> dict[str, Any]:
    applied: dict[str, Any] = {}
    for param, payload in engine_param_snapshot(repository).items():
        if not hasattr(settings, param):
            continue
        value = _coerce_like(getattr(settings, param), payload["value"])
        setattr(settings, param, value)
        applied[param] = value
    return applied


def engine_param_from_suggestion(settings, suggestion: CalibrationSuggestion) -> EngineParamVersion | None:
    proposed = suggestion.proposed_change or {}
    param = proposed.get("parameter")
    if not isinstance(param, str) or "to" not in proposed:
        return None
    old_value = getattr(settings, param, proposed.get("from"))
    new_value = _coerce_like(old_value, proposed["to"])
    return EngineParamVersion(
        id=uuid5(NAMESPACE_URL, f"fce:engine-param:{param}:{suggestion.id}"),
        param=param,
        old_value=old_value,
        new_value=new_value,
        suggestion_id=UUID(str(suggestion.id)),
        approved_at=utc_now(),
    )


def _coerce_like(reference: Any, value: Any) -> Any:
    if isinstance(reference, bool):
        return bool(value)
    if isinstance(reference, int) and not isinstance(reference, bool):
        return int(float(value))
    if isinstance(reference, float):
        return float(value)
    return value
