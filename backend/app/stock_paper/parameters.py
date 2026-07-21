from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


DEFAULT_PARAMETERS_PATH = Path(__file__).with_name("params") / "stock-v2.json"


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
    max_open_positions: int
    daily_loss_limit_pct: float
    position_capital_fraction: float
    long_only: bool


def load_stock_parameters(path: Path = DEFAULT_PARAMETERS_PATH) -> StockPaperParameters:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.setdefault("min_entry_score", 0)
    payload.setdefault("signature_gate_mode", "required")
    payload.setdefault("earnings_gate_mode", "required")
    parameters = StockPaperParameters(**payload)
    if not 0 < parameters.position_capital_fraction <= 0.1:
        raise ValueError("stock position capital fraction must be in (0, 0.1]")
    if not parameters.long_only:
        raise ValueError("stock paper must stay long-only")
    if parameters.version == "stock-v2" and parameters.signature_gate_mode != "record_only":
        raise ValueError("stock signature status must remain record-only")
    if parameters.version == "stock-v2" and parameters.earnings_gate_mode != "not_evaluable":
        raise ValueError("earnings must remain explicitly not_evaluable until a source is connected")
    return parameters
