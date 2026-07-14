from __future__ import annotations

from typing import Any

from app.db.models import MarketCandle
from app.structure.liquidity.pools import MAX_POOLS_PER_TYPE, detect_liquidity_pools
from app.structure.liquidity.structure import dealing_range, detect_structure_shift
from app.structure.liquidity.sweeps import (
    MAX_RETURN_CANDLES,
    VOLUME_CONFIRMATION_RATIO,
    detect_htf_range_sweeps,
    detect_liquidity_sweeps,
)


def analyze_liquidity_structure(
    candles: list[MarketCandle],
    *,
    mark_price: float | None = None,
    levels: dict[str, list[Any]] | None = None,
    wyckoff: dict[str, Any] | None = None,
    trade_flow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    if len(ordered) < 30:
        return _empty("insufficient_candles")
    reference_price = mark_price or ordered[-1].close
    pools = detect_liquidity_pools(ordered)
    sweep_result = detect_liquidity_sweeps(ordered, pools, trade_flow=trade_flow)
    htf_sweeps = detect_htf_range_sweeps(ordered, trade_flow=trade_flow)
    crosscheck = _wyckoff_crosscheck(sweep_result["sweeps"] + htf_sweeps, wyckoff or {})
    return {
        "method": "deterministic_ohlcv_liquidity_v2",
        "as_of": ordered[-1].timestamp.isoformat(),
        "reference_price": round(reference_price, 8),
        "pools": [pool.model_dump() for pool in pools],
        "sweeps": sweep_result["sweeps"],
        "rejected_sweeps": sweep_result["rejected_sweeps"],
        "htf_range_sweeps": htf_sweeps,
        "structure_shift": detect_structure_shift(ordered),
        "dealing_range": dealing_range(ordered, reference_price, wyckoff),
        "wyckoff_crosscheck": crosscheck,
        "limits": {
            "max_pools_per_type": MAX_POOLS_PER_TYPE,
            "max_return_candles": MAX_RETURN_CANDLES,
            "volume_confirmation_ratio": VOLUME_CONFIRMATION_RATIO,
        },
        "notes": [
            "유동성 풀/스윕은 OHLCV와 실체결 보조 데이터 기반 결정론 판정입니다.",
            "상대 거래량 1.5배 미만 스윕은 unconfirmed로 강등하고 판단 원장에 등록하지 않습니다.",
        ],
    }


def attach_liquidity_crosscheck_to_wyckoff(wyckoff: dict[str, Any], liquidity: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(wyckoff, dict):
        return wyckoff
    cross = liquidity.get("wyckoff_crosscheck") if isinstance(liquidity, dict) else None
    if not isinstance(cross, dict):
        return wyckoff
    confirmed_by_type = {item.get("wyckoff_event"): item for item in cross.get("confirmations", []) if isinstance(item, dict) and item.get("wyckoff_event")}
    events = []
    for event in wyckoff.get("events", []) if isinstance(wyckoff.get("events"), list) else []:
        if not isinstance(event, dict):
            events.append(event)
            continue
        marker = confirmed_by_type.get(event.get("type"))
        if marker:
            bonus = _liquidity_bonus(marker.get("sweep_grade"))
            components = {**event.get("components", {}), "liquidity_confirmation": bonus}
            confidence = min(100, int(event.get("confidence", 0)) + bonus)
            events.append(
                {
                    **event,
                    "confidence": confidence,
                    "components": components,
                    "liquidity_crosscheck": marker,
                    "display_label": f"{event.get('label', event.get('type'))} · {confidence} — {marker.get('sweep_grade')} 스윕 확인",
                }
            )
        else:
            events.append({**event, "liquidity_crosscheck": {"confirmed": False}})
    return {
        **wyckoff,
        "liquidity_crosscheck": cross,
        "events": events,
    }


def _wyckoff_crosscheck(sweeps: list[dict[str, Any]], wyckoff: dict[str, Any]) -> dict[str, Any]:
    confirmations = []
    event_types = {event.get("type") for event in wyckoff.get("events", []) if isinstance(event, dict)}
    if wyckoff.get("spring_candidate") or "spring_candidate" in event_types:
        match = next((sweep for sweep in sweeps if sweep.get("wyckoff_equivalent") == "spring_candidate" and sweep.get("confirmed")), None)
        if match:
            confirmations.append(_confirmation("spring_candidate", match))
    if wyckoff.get("utad_candidate") or "utad_candidate" in event_types:
        match = next((sweep for sweep in sweeps if sweep.get("wyckoff_equivalent") == "utad_candidate" and sweep.get("confirmed")), None)
        if match:
            confirmations.append(_confirmation("utad_candidate", match))
    return {
        "confirmed": bool(confirmations),
        "confirmations": confirmations,
        "summary": "스윕과 와이코프 이벤트가 같은 가격 행동을 가리킵니다." if confirmations else "유동성 스윕으로 교차 확인된 와이코프 이벤트가 없습니다.",
    }


def _confirmation(event_type: str, sweep: dict[str, Any]) -> dict[str, Any]:
    return {
        "confirmed": True,
        "wyckoff_event": event_type,
        "sweep_id": sweep.get("id"),
        "sweep_grade": sweep.get("grade"),
        "confidence": sweep.get("confidence"),
        "liquidity_confirmation": _liquidity_bonus(sweep.get("grade")),
    }


def _liquidity_bonus(grade: Any) -> int:
    if grade == "Strong":
        return 15
    if grade == "Mid":
        return 12
    if grade == "Weak":
        return 10
    return 0


def _empty(reason: str) -> dict[str, Any]:
    return {
        "method": "deterministic_ohlcv_liquidity_v2",
        "as_of": None,
        "reference_price": None,
        "pools": [],
        "sweeps": [],
        "rejected_sweeps": [],
        "htf_range_sweeps": [],
        "structure_shift": {"state": reason, "event": None},
        "dealing_range": None,
        "wyckoff_crosscheck": {"confirmed": False, "confirmations": [], "summary": "캔들 데이터가 부족합니다."},
        "limits": {},
        "notes": ["캔들 데이터가 부족해 유동성 구조를 계산하지 않았습니다."],
    }
