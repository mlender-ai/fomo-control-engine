from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


DEFAULT_PARAMETERS_PATH = Path(__file__).with_name("params") / "stock-v4.json"


@dataclass(frozen=True)
class StockPaperParameters:
    version: str
    min_evidence: int
    min_checklist_passed: int
    min_checklist_total: int
    min_rr: float
    min_entry_score: int
    min_signature_ci_low_pct: float
    signature_gate_mode: str
    earnings_gate_mode: str
    stance_gate_mode: str
    max_open_positions: int
    daily_loss_limit_pct: float
    position_capital_fraction: float
    long_only: bool
    coverage_entry_enabled: bool = False
    coverage_target_open_positions: int = 0
    coverage_position_capital_fraction: float = 0.005
    coverage_scan_batch_size: int = 0
    coverage_max_attempts_per_cycle: int = 0


def load_stock_parameters(path: Path = DEFAULT_PARAMETERS_PATH) -> StockPaperParameters:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("min_entry_score", 0)
    payload.setdefault("signature_gate_mode", "required")
    payload.setdefault("earnings_gate_mode", "required")
    payload.setdefault("stance_gate_mode", "confirmed_flip")
    payload.setdefault("coverage_entry_enabled", False)
    payload.setdefault("coverage_target_open_positions", 0)
    payload.setdefault("coverage_position_capital_fraction", 0.005)
    payload.setdefault("coverage_scan_batch_size", 0)
    payload.setdefault("coverage_max_attempts_per_cycle", 0)
    parameters = StockPaperParameters(**payload)
    if not 0 < parameters.position_capital_fraction <= 0.1:
        raise ValueError("stock position capital fraction must be in (0, 0.1]")
    if not parameters.long_only:
        raise ValueError("stock paper must stay long-only")
    if not 0 < parameters.coverage_position_capital_fraction <= 0.01:
        raise ValueError("stock coverage position fraction must be in (0, 0.01]")
    if parameters.coverage_target_open_positions > parameters.max_open_positions:
        raise ValueError("stock coverage target must not exceed maximum open positions")
    if parameters.version in {"stock-v2", "stock-v3", "stock-v4"} and parameters.signature_gate_mode != "record_only":
        raise ValueError("stock signature status must remain record-only")
    if parameters.version in {"stock-v2", "stock-v3", "stock-v4"} and parameters.earnings_gate_mode != "not_evaluable":
        raise ValueError("earnings must remain explicitly not_evaluable until a source is connected")
    if parameters.version in {"stock-v3", "stock-v4"} and parameters.stance_gate_mode != "stable_long":
        raise ValueError("stock-v3+ must require a stable long stance")
    return parameters
