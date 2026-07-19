from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path


DEFAULT_PARAMETERS_PATH = Path(__file__).with_name("params") / "stock-v1.json"


@dataclass(frozen=True)
class StockPaperParameters:
    version: str
    min_evidence: int
    min_checklist_passed: int
    min_checklist_total: int
    min_rr: float
    min_signature_ci_low_pct: float
    max_open_positions: int
    daily_loss_limit_pct: float
    position_capital_fraction: float
    long_only: bool


def load_stock_parameters(path: Path = DEFAULT_PARAMETERS_PATH) -> StockPaperParameters:
    payload = json.loads(path.read_text(encoding="utf-8"))
    parameters = StockPaperParameters(**payload)
    if not 0 < parameters.position_capital_fraction <= 0.1:
        raise ValueError("stock position capital fraction must be in (0, 0.1]")
    if not parameters.long_only:
        raise ValueError("stock paper v1 must stay long-only")
    return parameters
