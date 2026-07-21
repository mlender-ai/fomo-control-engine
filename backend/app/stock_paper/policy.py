from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .parameters import StockPaperParameters


@dataclass(frozen=True)
class StockEntryDecision:
    enter: bool
    gate_results: dict[str, dict[str, Any]]
    rejection_reasons: tuple[str, ...]


def evaluate_stock_entry(analysis: dict[str, Any], *, data_fresh: bool, parameters: StockPaperParameters) -> StockEntryDecision:
    confluence = analysis.get("confluence") if isinstance(analysis.get("confluence"), dict) else {}
    state = confluence.get("stance_state") if isinstance(confluence.get("stance_state"), dict) else {}
    long_evidence = confluence.get("long_evidence") if isinstance(confluence.get("long_evidence"), list) else []
    all_evidence = [
        *long_evidence,
        *(confluence.get("short_evidence") if isinstance(confluence.get("short_evidence"), list) else []),
    ]
    invalidation = analysis.get("invalidation") if isinstance(analysis.get("invalidation"), dict) else None
    rr = _float(analysis.get("rr_ratio"))
    results: dict[str, dict[str, Any]] = {}

    def required(gate: str, measured: Any, threshold: Any, passed: bool) -> None:
        results[gate] = {"status": "passed" if passed else "rejected", "measured_value": measured, "threshold": threshold, "required": True}

    required("analysis_available", analysis.get("status"), "analyzed", analysis.get("status") == "analyzed")
    stance_measurement = {
        "stance": state.get("stance"),
        "flipped": state.get("flipped"),
        "transitioning": state.get("transitioning"),
    }
    if parameters.stance_gate_mode == "stable_long":
        stance_threshold = {"stance": ["long_leaning", "long"], "transitioning": False, "flipped": "record_only"}
        stance_passed = state.get("stance") in {"long_leaning", "long"} and state.get("transitioning") is not True
    else:
        stance_threshold = {"stance": "long_leaning", "flipped": True, "transitioning": False}
        stance_passed = state.get("stance") == "long_leaning" and state.get("flipped") is True and state.get("transitioning") is not True
    required(
        "confirmed_flip",
        stance_measurement,
        stance_threshold,
        stance_passed,
    )
    required("evidence", len(long_evidence), parameters.min_evidence, len(long_evidence) >= parameters.min_evidence)
    required(
        "checklist",
        {"passed": len(long_evidence), "total": len(all_evidence)},
        {"passed": parameters.min_checklist_passed, "total": parameters.min_checklist_total},
        len(long_evidence) >= parameters.min_checklist_passed and len(all_evidence) >= parameters.min_checklist_total,
    )
    required("entry_score", analysis.get("entry_score"), parameters.min_entry_score, int(analysis.get("entry_score") or 0) >= parameters.min_entry_score)
    required("liquidation_safety", invalidation, "observed_structure_level", invalidation is not None and _float(invalidation.get("price")) is not None)
    required("risk_reward", rr, parameters.min_rr, rr is not None and rr >= parameters.min_rr)
    required("data_fresh", data_fresh, True, data_fresh)
    results["validated_signature"] = {
        "status": "recorded",
        "measured_value": analysis.get("signature_status") or "unvalidated",
        "threshold": "record_only",
        "required": False,
    }
    results["earnings_gate"] = {
        "status": "not_evaluable",
        "measured_value": "not_evaluable",
        "threshold": "source_backlog",
        "required": False,
    }
    rejected = tuple(gate for gate, result in results.items() if result["required"] and result["status"] == "rejected")
    return StockEntryDecision(not rejected, results, rejected)


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
