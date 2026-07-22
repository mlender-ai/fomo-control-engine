from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .models import EstimateQuality


DEFAULT_PATH = Path(__file__).with_name("params") / "poly-v2.json"


@dataclass(frozen=True)
class PolyParameters:
    version: str
    min_edge: float
    min_liquidity: float
    min_days_to_resolution: float
    min_estimate_quality: EstimateQuality
    max_position_fraction: float
    max_open_markets: int
    max_observed_ask_fraction: float
    min_resolution_clarity: str
    estimate_min_interval_minutes: int
    coverage_entry_enabled: bool
    coverage_position_fraction: float
    coverage_target_open_markets: int


def load_poly_parameters(path: Path = DEFAULT_PATH) -> PolyParameters:
    payload = json.loads(path.read_text())
    parameters = PolyParameters(
        version=str(payload["version"]),
        min_edge=float(payload["min_edge"]),
        min_liquidity=float(payload["min_liquidity"]),
        min_days_to_resolution=float(payload["min_days_to_resolution"]),
        min_estimate_quality=EstimateQuality(str(payload["min_estimate_quality"])),
        max_position_fraction=float(payload["max_position_fraction"]),
        max_open_markets=int(payload["max_open_markets"]),
        max_observed_ask_fraction=float(payload["max_observed_ask_fraction"]),
        min_resolution_clarity=str(payload["min_resolution_clarity"]),
        estimate_min_interval_minutes=int(payload["estimate_min_interval_minutes"]),
        coverage_entry_enabled=bool(payload.get("coverage_entry_enabled", False)),
        coverage_position_fraction=float(payload.get("coverage_position_fraction", 0.005)),
        coverage_target_open_markets=int(payload.get("coverage_target_open_markets", 0)),
    )
    if not 0 < parameters.coverage_position_fraction <= 0.01:
        raise ValueError("Polymarket coverage fraction must be in (0, 0.01]")
    if parameters.coverage_target_open_markets > parameters.max_open_markets:
        raise ValueError("Polymarket coverage target must not exceed max open markets")
    return parameters
