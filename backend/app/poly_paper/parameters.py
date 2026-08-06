from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from .models import EstimateQuality


DEFAULT_PATH = Path(__file__).with_name("params") / "poly-v3.json"


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
    # WO-FCE-SAMPLE-VIABILITY-01 PHASE 2 — 만기 상한.
    #
    # v2 까지는 만기 조건이 **아래쪽(min_days_to_resolution)에만** 있었다. 그래서 만기가
    # 5개월 뒤인 시장이 얼마든지 선정됐고, 실측 2026-08-05 보유 9건 중 8건이 2027-01-01
    # 만기였다. 검증 창 안에 정산되는 건이 0이면 유효 관측일을 다 채워도 **채점 가능한
    # 표본이 0**이다. 유니버스에는 창 안 만기 시장이 4,847개 있었으므로 데이터 문제가 아니라
    # 선정 기준 문제였다.
    #
    # None = 상한 없음(v1·v2 하위호환). 기존 임계값은 하나도 바꾸지 않았다 — 이것은 완화가
    # 아니라 **없던 조건의 추가**이며, 후보를 줄이는 방향이다.
    max_days_to_resolution: float | None = None
    # 정산 확정에서 채점까지 필요한 여유. 만기 당일에 정산이 확정되지 않는다
    # (`resolved_outcome` 는 가격이 0.999 이상으로 확정된 뒤에야 참이다).
    # 실측 근거는 `PolyPaperStore.settlement_latency()` 로 다시 잰다.
    settlement_buffer_days: float = 0.0


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
        max_days_to_resolution=(float(payload["max_days_to_resolution"]) if payload.get("max_days_to_resolution") is not None else None),
        settlement_buffer_days=float(payload.get("settlement_buffer_days", 0.0)),
    )
    if not 0 < parameters.coverage_position_fraction <= 0.01:
        raise ValueError("Polymarket coverage fraction must be in (0, 0.01]")
    if parameters.coverage_target_open_markets > parameters.max_open_markets:
        raise ValueError("Polymarket coverage target must not exceed max open markets")
    if parameters.max_days_to_resolution is not None and parameters.max_days_to_resolution <= parameters.min_days_to_resolution:
        raise ValueError("Polymarket resolution window must be wider than the minimum")
    if parameters.settlement_buffer_days < 0:
        raise ValueError("Polymarket settlement buffer must not be negative")
    return parameters
