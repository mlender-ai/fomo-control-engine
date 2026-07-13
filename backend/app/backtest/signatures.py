from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SetupSignature:
    engine: str
    event_type: str
    strength_class: str
    direction: str
    asset_class: str
    timeframe: str

    @property
    def key(self) -> str:
        return signature_key(self.model_dump())

    def model_dump(self) -> dict[str, str]:
        return {
            "engine": self.engine,
            "event_type": self.event_type,
            "strength_class": self.strength_class,
            "direction": self.direction,
            "asset_class": self.asset_class,
            "timeframe": self.timeframe,
        }


def signature_key(signature: dict[str, Any] | SetupSignature) -> str:
    payload = signature.model_dump() if isinstance(signature, SetupSignature) else signature
    parts = [
        str(payload.get("engine") or "-"),
        str(payload.get("event_type") or "-"),
        str(payload.get("strength_class") or "-"),
        str(payload.get("direction") or "-"),
        str(payload.get("asset_class") or "*"),
        str(payload.get("timeframe") or "*"),
    ]
    return ":".join(part.lower().replace(" ", "_") for part in parts)


def signatures_from_analysis(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert active live analysis signals to the same signatures used by replay."""

    timeframe = str(analysis.get("timeframe") or "4h")
    asset_class = str(analysis.get("asset_class") or "unknown")
    signatures: list[dict[str, Any]] = []
    signatures.extend(_liquidity_signatures(analysis, asset_class, timeframe))
    signatures.extend(_wyckoff_signatures(analysis, asset_class, timeframe))
    signatures.extend(_harmonic_signatures(analysis, asset_class, timeframe))
    signatures.extend(_level_signatures(analysis, asset_class, timeframe))
    signatures.extend(_full_alignment_signatures(analysis, asset_class, timeframe))
    signatures.extend(_money_flow_signatures(analysis, asset_class, timeframe))
    return _dedupe(signatures)


def _money_flow_signatures(analysis: dict[str, Any], asset_class: str, timeframe: str) -> list[dict[str, Any]]:
    derivatives = analysis.get("derivatives") if isinstance(analysis.get("derivatives"), dict) else {}
    signals = derivatives.get("signals") if isinstance(derivatives.get("signals"), dict) else {}
    flow = signals.get("money_flow") if isinstance(signals.get("money_flow"), dict) else {}
    if flow.get("state") != "futures_led" or flow.get("provisional") or not flow.get("available"):
        return []
    return [_signature("money_flow", "futures_led_rally", "candidate", "short", asset_class, timeframe)]


def _full_alignment_signatures(analysis: dict[str, Any], asset_class: str, timeframe: str) -> list[dict[str, Any]]:
    alignment = analysis.get("full_alignment") if isinstance(analysis.get("full_alignment"), dict) else {}
    if not alignment.get("unanimous"):
        return []
    direction = str(alignment.get("direction") or "neutral")
    agreeing = int(_num(alignment.get("agreeing"), 0))
    return [_signature("full_alignment", "unanimous", f"{agreeing}_modules", direction, asset_class, timeframe)]


def signature_from_setup_candidate(candidate: dict[str, Any], *, asset_class: str = "unknown", timeframe: str = "4h") -> dict[str, Any] | None:
    setup_type = str(candidate.get("setup_type") or "")
    direction = str(candidate.get("direction") or "neutral")
    confidence = _num(candidate.get("confidence"), 0)
    if "harmonic" in setup_type or "prz" in setup_type:
        return _signature("harmonic", "prz_touch", _confidence_bucket(confidence), direction, asset_class, timeframe)
    if "wyckoff" in setup_type:
        return _signature("wyckoff", "event_confirmed", _confidence_bucket(confidence), direction, asset_class, timeframe)
    if "liquidity" in setup_type or "pool" in setup_type:
        event = "sweep_low" if direction == "long" else "sweep_high" if direction == "short" else "pool_near"
        return _signature("liquidity", event, _confidence_bucket(confidence), direction, asset_class, timeframe)
    if "level" in setup_type or candidate.get("trigger_price") is not None:
        return _signature("levels", "level_touch", _score_bucket(confidence), direction, asset_class, timeframe)
    return None


def signature_label(signature: dict[str, Any]) -> str:
    engine = str(signature.get("engine") or "-")
    event = str(signature.get("event_type") or "-")
    strength = str(signature.get("strength_class") or "-")
    direction = str(signature.get("direction") or "-")
    engine_label = {
        "liquidity": "유동성",
        "wyckoff": "와이코프",
        "harmonic": "하모닉",
        "levels": "레벨",
        "fvg": "미충전 갭",
        "order_block": "매물 존",
        "vcp": "변동성 수축",
        "stage2_template": "2단계 상승 조건",
        "full_alignment": "만장일치 정렬",
        "money_flow": "자금 흐름",
    }.get(engine, engine)
    event_label = {
        "sweep_low": "저점 스윕",
        "sweep_high": "고점 스윕",
        "htf_sweep_low": "상위 저점 스윕",
        "htf_sweep_high": "상위 고점 스윕",
        "spring_confirmed": "Spring 확인",
        "utad_confirmed": "UTAD 확인",
        "prz_touch": "PRZ 터치",
        "level_touch": "레벨 반응",
        "gap_formed": "형성",
        "retest": "재시험",
        "contraction": "수축 확인",
        "stage2_active": "조건 진입",
        "unanimous": "확인",
        "futures_led_rally": "선물 단독 견인",
    }.get(event, event)
    direction_label = "롱" if direction == "long" else "숏" if direction == "short" else "중립"
    return f"{engine_label} {event_label} · {strength} · {direction_label}"


def _liquidity_signatures(analysis: dict[str, Any], asset_class: str, timeframe: str) -> list[dict[str, Any]]:
    liquidity = analysis.get("liquidity") if isinstance(analysis.get("liquidity"), dict) else {}
    result: list[dict[str, Any]] = []
    for sweep in _list(liquidity.get("sweeps")) + _list(liquidity.get("htf_range_sweeps")):
        if not sweep.get("confirmed"):
            continue
        direction = "long" if sweep.get("side") == "sell_side" else "short" if sweep.get("side") == "buy_side" else "neutral"
        prefix = "htf_sweep" if sweep.get("type") == "htf_range_sweep" else "sweep"
        suffix = "low" if direction == "long" else "high" if direction == "short" else "neutral"
        result.append(_signature("liquidity", f"{prefix}_{suffix}", str(sweep.get("grade") or "unknown"), direction, asset_class, timeframe))
    return result


def _wyckoff_signatures(analysis: dict[str, Any], asset_class: str, timeframe: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for marker in _list(analysis.get("wyckoff_markers")):
        label = str(marker.get("label") or marker.get("type") or "").lower()
        confidence = _num(marker.get("confidence"), 0)
        if "spring" in label or "sos" in label or "lps" in label:
            event = "spring_confirmed" if "spring" in label else "wyckoff_long_event"
            result.append(_signature("wyckoff", event, _confidence_bucket(confidence), "long", asset_class, timeframe))
        elif "utad" in label or "sow" in label or "lpsy" in label:
            event = "utad_confirmed" if "utad" in label else "wyckoff_short_event"
            result.append(_signature("wyckoff", event, _confidence_bucket(confidence), "short", asset_class, timeframe))
    return result


def _harmonic_signatures(analysis: dict[str, Any], asset_class: str, timeframe: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for pattern in _list(analysis.get("harmonic_patterns")):
        direction = "long" if pattern.get("direction") == "bullish" else "short" if pattern.get("direction") == "bearish" else "neutral"
        result.append(
            _signature(
                "harmonic",
                "prz_touch",
                _confidence_bucket(_num(pattern.get("confidence"), 0)),
                direction,
                asset_class,
                timeframe,
            )
        )
    return result


def _level_signatures(analysis: dict[str, Any], asset_class: str, timeframe: str) -> list[dict[str, Any]]:
    levels = analysis.get("price_levels") if isinstance(analysis.get("price_levels"), dict) else {}
    mark = _num(analysis.get("mark_price"), 0)
    result: list[dict[str, Any]] = []
    if mark <= 0:
        return result
    for level in _list(levels.get("support")):
        if _near_level(mark, level.get("price")):
            result.append(_signature("levels", "level_touch", _score_bucket(_num(level.get("score"), 0)), "long", asset_class, timeframe))
            break
    for level in _list(levels.get("resistance")):
        if _near_level(mark, level.get("price")):
            result.append(_signature("levels", "level_touch", _score_bucket(_num(level.get("score"), 0)), "short", asset_class, timeframe))
            break
    return result


def _signature(engine: str, event_type: str, strength_class: str, direction: str, asset_class: str, timeframe: str) -> dict[str, Any]:
    signature = SetupSignature(
        engine=engine,
        event_type=event_type,
        strength_class=strength_class,
        direction=direction,
        asset_class=asset_class,
        timeframe=timeframe,
    ).model_dump()
    signature["key"] = signature_key(signature)
    signature["label"] = signature_label(signature)
    return signature


def _confidence_bucket(confidence: float) -> str:
    if confidence >= 80:
        return "conf>=80"
    if confidence >= 70:
        return "conf>=70"
    if confidence >= 55:
        return "conf>=55"
    return "conf<55"


def _score_bucket(score: float) -> str:
    if score >= 80:
        return "score>=80"
    if score >= 70:
        return "score>=70"
    if score >= 55:
        return "score>=55"
    return "score<55"


def _near_level(mark: float, price: Any, threshold_pct: float = 1.5) -> bool:
    price_num = _num(price, 0)
    return price_num > 0 and abs((mark - price_num) / mark) * 100 <= threshold_pct


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for item in items:
        by_key.setdefault(str(item.get("key")), item)
    return list(by_key.values())


def _list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
