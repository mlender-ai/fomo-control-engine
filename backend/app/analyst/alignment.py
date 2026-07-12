from __future__ import annotations

from typing import Any


MIN_AGREEING_MODULES = 4


def build_full_alignment(
    confluence: dict[str, Any],
    historical_backtest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Describe module unanimity using already calibrated confluence scores.

    Evidence ``score`` already contains the confluence engine weight and the
    calibration CI-lower-bound adjustment. This module only filters and sums
    those values; it does not introduce another weighting formula.
    """

    validated = _validated_engine_directions(historical_backtest)
    module_votes: dict[str, dict[str, Any]] = {}
    for direction, key in (("long", "long_evidence"), ("short", "short_evidence")):
        for item in _list(confluence.get(key)):
            engine = str(item.get("engine") or "")
            if not engine or (engine, direction) not in validated:
                continue
            score = _number(item.get("score"))
            if score <= 0:
                continue
            current = module_votes.get(engine)
            if current is None or score > _number(current.get("score")):
                module_votes[engine] = {**item, "engine": engine, "direction": direction, "score": score}

    long_votes = [item for item in module_votes.values() if item["direction"] == "long"]
    short_votes = [item for item in module_votes.values() if item["direction"] == "short"]
    long_score = sum(_number(item.get("score")) for item in long_votes)
    short_score = sum(_number(item.get("score")) for item in short_votes)
    direction = "long" if long_score > short_score else "short" if short_score > long_score else None
    agreeing = long_votes if direction == "long" else short_votes if direction == "short" else []
    dissenting = short_votes if direction == "long" else long_votes if direction == "short" else [*long_votes, *short_votes]

    htf = confluence.get("htf_context") if isinstance(confluence.get("htf_context"), dict) else {}
    htf_bias = str(htf.get("bias") or "neutral")
    htf_aligned = bool(direction and htf_bias == direction and str(htf.get("alignment") or "") != "conflicting")
    stance_state = confluence.get("stance_state") if isinstance(confluence.get("stance_state"), dict) else {}
    transitioning = bool(stance_state.get("transitioning"))
    unanimous = bool(
        direction
        and len(agreeing) >= MIN_AGREEING_MODULES
        and not dissenting
        and htf_aligned
        and not transitioning
    )
    stat = _alignment_stat(historical_backtest, direction)
    sample_size = int(_number((stat or {}).get("sample_size")))
    ci = (stat or {}).get("win_1r_ci")
    ci_lower = _number(ci[0]) if isinstance(ci, (list, tuple)) and ci else None
    return {
        "unanimous": unanimous,
        "direction": direction,
        "agreeing": len(agreeing),
        "dissenting": len(dissenting),
        "score": round(sum(_number(item.get("score")) for item in agreeing), 2),
        "agreeing_modules": [_module(item) for item in sorted(agreeing, key=lambda item: _number(item.get("score")), reverse=True)],
        "dissenting_modules": [_module(item) for item in sorted(dissenting, key=lambda item: _number(item.get("score")), reverse=True)],
        "htf_aligned": htf_aligned,
        "htf_bias": htf_bias,
        "transitioning": transitioning,
        "candles_in_state": stance_state.get("candles_in_state"),
        "bar_at": stance_state.get("last_bar_at"),
        "sample_size": sample_size,
        "win_1r_pct": (stat or {}).get("win_1r_pct"),
        "win_1r_ci": ci,
        "sample_label": "표본 축적 중" if sample_size < 30 else f"실측 {(stat or {}).get('win_1r_pct')}% (N={sample_size})",
        "predictive_warning": bool(sample_size >= 30 and ci_lower is not None and ci_lower < 50.0),
    }


def _validated_engine_directions(historical: dict[str, Any] | None) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    if not isinstance(historical, dict):
        return result
    for stat in [*_list(historical.get("stats")), *_list(historical.get("event_stats"))]:
        if str(stat.get("lifecycle_state") or "") != "validated":
            continue
        signature = stat.get("signature") if isinstance(stat.get("signature"), dict) else {}
        engine = str(signature.get("engine") or stat.get("engine") or "")
        direction = str(signature.get("direction") or stat.get("direction") or "")
        if engine and direction in {"long", "short"}:
            result.add((engine, direction))
    return result


def _alignment_stat(historical: dict[str, Any] | None, direction: str | None) -> dict[str, Any] | None:
    if not isinstance(historical, dict) or direction is None:
        return None
    for stat in [*_list(historical.get("stats")), *_list(historical.get("event_stats"))]:
        signature = stat.get("signature") if isinstance(stat.get("signature"), dict) else {}
        if str(signature.get("engine") or stat.get("engine") or "") == "full_alignment" and str(
            signature.get("direction") or stat.get("direction") or ""
        ) == direction:
            return stat
    return None


def _module(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "engine": item.get("engine"),
        "claim": item.get("claim"),
        "direction": item.get("direction"),
        "score": round(_number(item.get("score")), 2),
    }


def _list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _number(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
