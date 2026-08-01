"""포지션 구조 컨텍스트 (WO-FCE-STRUCTURE-CONTEXT-01).

**구조는 관측이지 예측이 아니다.** 이 모듈은 "축적 국면이므로 오른다" 같은 인과 단정을
만들지 않는다. 내 진입가가 어느 레인지·존 안에 있는지를 **서술**할 뿐이다(C4).

**신규 감지기가 아니다**(C1). 재료는 이미 다 있었다 —
- 와이코프 국면·트레이딩 레인지·이벤트: `structure/wyckoff/engine.py::analyze_wyckoff`
- 오더블록 존: `structure/candidates/engine.py::order_block_zones`
- PNF 측정 목표: `structure/pnf`

없던 것은 **연결**이다. 시스템은 "내 진입가가 축적 레인지 안인지, 수요 오더블록 위인지"를
알고 있었지만 사용자에게 보여주지 않았다. 이 모듈이 그 관계를 산출한다.

리페인팅(C3): 와이코프 국면은 본질적으로 사후 재해석된다. 숨기지 않고 이전 판정과 달라졌으면
`repainted=True`와 함께 이전 국면을 남긴다.
"""

from __future__ import annotations

from typing import Any

# 레인지 내부 판정의 허용 오차(경계 근처를 '밖'으로 단정하지 않기 위함).
BOUNDARY_TOLERANCE_PCT = 0.1

RANGE_POSITION_INSIDE = "inside"
RANGE_POSITION_ABOVE = "above"
RANGE_POSITION_BELOW = "below"
RANGE_POSITION_UNKNOWN = "unknown"


def build_structure_context(
    *,
    analysis: dict[str, Any],
    entry_price: float | None,
    mark_price: float | None,
    invalidation_price: float | None = None,
    order_block_zones: list[dict[str, Any]] | None = None,
    pnf_objective: dict[str, Any] | None = None,
    previous_phase: str | None = None,
) -> dict[str, Any]:
    """포지션과 구조의 관계를 산출한다. 판단이 아니라 서술이다.

    ``analysis``는 `build_chart_analysis` 출력(또는 그 하위 집합)이며 `wyckoff_range`·
    `wyckoff_phase`·`wyckoff_markers`를 읽는다. 없으면 해당 항목은 None으로 남긴다 —
    억지로 채우지 않는다.
    """
    wyckoff_range = _dict(analysis.get("wyckoff_range"))
    phase_payload = _dict(analysis.get("wyckoff_phase"))
    phase = str(phase_payload.get("phase") or "undetermined")
    side = str(phase_payload.get("side") or "neutral")

    range_low = _num(_dict(wyckoff_range.get("support")).get("price"))
    range_high = _num(_dict(wyckoff_range.get("resistance")).get("price"))

    zones = [zone for zone in (order_block_zones or []) if isinstance(zone, dict)]
    entry_zone = _overlapping_zone(zones, entry_price)
    nearest_demand = _nearest_zone(zones, mark_price, "demand")
    nearest_supply = _nearest_zone(zones, mark_price, "supply")

    entry_position = _range_position(entry_price, range_low, range_high)
    mark_position = _range_position(mark_price, range_low, range_high)

    repainted = bool(previous_phase and previous_phase != phase and phase != "undetermined")

    context = {
        "wyckoff": {
            "phase": phase,
            "side": side,
            "range_low": range_low,
            "range_high": range_high,
            "range_width_pct": wyckoff_range.get("width_pct"),
            "candles_inside": wyckoff_range.get("candles_inside"),
            "recent_markers": _markers(analysis),
            "accumulation_score": phase_payload.get("accumulation_score") or analysis.get("accumulation_score"),
            "distribution_score": phase_payload.get("distribution_score") or analysis.get("distribution_score"),
            "conflict_note": analysis.get("conflict_note") or phase_payload.get("conflict_note"),
        },
        "order_blocks": {
            "zones": zones,
            "entry_zone": entry_zone,
            "nearest_demand": nearest_demand,
            "nearest_supply": nearest_supply,
        },
        "position_relation": {
            "entry_price": entry_price,
            "mark_price": mark_price,
            "entry_range_position": entry_position,
            "mark_range_position": mark_position,
            "entry_in_order_block": entry_zone is not None,
            "invalidation_price": invalidation_price,
            "invalidation_vs_range_low": _relation(invalidation_price, range_low),
            "invalidation_vs_entry_zone_low": _relation(invalidation_price, _num((entry_zone or {}).get("zone_low"))),
            "pnf_target_price": (pnf_objective or {}).get("target_price"),
            "pnf_remaining_pct": _remaining_pct((pnf_objective or {}).get("target_price"), mark_price),
        },
        "repaint": {
            "repainted": repainted,
            "previous_phase": previous_phase if repainted else None,
            "note": "국면 재해석됨 — 이전 판정과 상이합니다." if repainted else None,
        },
        "disclaimer": "구조는 관측이며 예측이 아닙니다. 도달·방향을 보장하지 않습니다.",
    }
    context["verdict_line"] = build_verdict_line(context)
    return context


def build_verdict_line(context: dict[str, Any]) -> str:
    """관계를 한 줄로 서술한다. 인과 단정 금지(C4) — '따라서 상승' 류의 문장을 만들지 않는다."""
    relation = _dict(context.get("position_relation"))
    wyckoff = _dict(context.get("wyckoff"))
    blocks = _dict(context.get("order_blocks"))
    entry = relation.get("entry_price")
    if entry is None:
        return "진입가 없음 — 구조 관계를 서술할 수 없습니다."

    parts = [f"진입 {_fmt(entry)}"]
    low, high = wyckoff.get("range_low"), wyckoff.get("range_high")
    label = {
        RANGE_POSITION_INSIDE: "내부",
        RANGE_POSITION_ABOVE: "위",
        RANGE_POSITION_BELOW: "아래",
    }.get(str(relation.get("entry_range_position")), None)
    if low is not None and high is not None and label:
        parts.append(f"{_phase_label(wyckoff.get('phase'))} 레인지({_fmt(low)}~{_fmt(high)}) {label}")

    zone = blocks.get("entry_zone")
    if isinstance(zone, dict):
        kind = "수요" if zone.get("kind") == "demand" else "공급"
        parts.append(f"{kind} OB({_fmt(zone.get('zone_low'))}~{_fmt(zone.get('zone_high'))}) 겹침")

    invalidation = relation.get("invalidation_price")
    if invalidation is not None and low is not None:
        where = "아래" if invalidation < low else "위"
        parts.append(f"무효화 {_fmt(invalidation)}는 레인지 하단 {where}")

    if context.get("repaint", {}).get("repainted"):
        parts.append("국면 재해석됨")
    return " · ".join(parts) + "."


def detect_structure_transitions(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> list[dict[str, Any]]:
    """이전 컨텍스트 대비 **상태 전이만** 반환한다(C6 — 매 틱 발송 금지).

    전이가 없으면 빈 리스트다. 같은 상태가 100틱 반복돼도 이벤트는 0건이다.
    """
    if previous is None:
        return []
    events: list[dict[str, Any]] = []
    previous_relation = _dict(previous.get("position_relation"))
    current_relation = _dict(current.get("position_relation"))
    previous_wyckoff = _dict(previous.get("wyckoff"))
    current_wyckoff = _dict(current.get("wyckoff"))

    before = str(previous_relation.get("mark_range_position") or RANGE_POSITION_UNKNOWN)
    after = str(current_relation.get("mark_range_position") or RANGE_POSITION_UNKNOWN)
    if before != after and RANGE_POSITION_UNKNOWN not in {before, after}:
        events.append(
            {
                "kind": "range_exit" if after != RANGE_POSITION_INSIDE else "range_enter",
                "from": before,
                "to": after,
                "range_low": current_wyckoff.get("range_low"),
                "range_high": current_wyckoff.get("range_high"),
            }
        )

    if bool(previous_relation.get("entry_in_order_block")) != bool(current_relation.get("entry_in_order_block")):
        events.append(
            {
                "kind": "order_block_enter" if current_relation.get("entry_in_order_block") else "order_block_exit",
                "zone": _dict(current.get("order_blocks")).get("entry_zone"),
            }
        )

    previous_phase = str(previous_wyckoff.get("phase") or "undetermined")
    current_phase = str(current_wyckoff.get("phase") or "undetermined")
    if previous_phase != current_phase and "undetermined" not in {previous_phase, current_phase}:
        events.append({"kind": "phase_change", "from": previous_phase, "to": current_phase})

    previous_markers = {item.get("label") for item in previous_wyckoff.get("recent_markers") or []}
    for marker in current_wyckoff.get("recent_markers") or []:
        label = str(marker.get("label") or "")
        if label and label not in previous_markers and any(key in label.lower() for key in ("spring", "lps")):
            events.append({"kind": "spring_or_lps", "label": label, "at": marker.get("as_of")})

    target = current_relation.get("pnf_target_price")
    mark = current_relation.get("mark_price")
    previous_mark = previous_relation.get("mark_price")
    if target is not None and mark is not None and previous_mark is not None and previous_mark < target <= mark:
        events.append({"kind": "pnf_target_reached", "target_price": target})

    return events


def transition_state_key(symbol: str, event: dict[str, Any], context: dict[str, Any]) -> str:
    """전이 억제 키 — {종목, 이벤트유형, 레인지ID}. 같은 상태 반복 발송을 막는다."""
    wyckoff = _dict(context.get("wyckoff"))
    range_id = f"{wyckoff.get('range_low')}-{wyckoff.get('range_high')}"
    return f"{symbol}:{event.get('kind')}:{range_id}"


# ── 내부 헬퍼 ────────────────────────────────────────────────


def _range_position(price: float | None, low: float | None, high: float | None) -> str:
    if price is None or low is None or high is None or high <= low:
        return RANGE_POSITION_UNKNOWN
    tolerance = (high - low) * (BOUNDARY_TOLERANCE_PCT / 100)
    if price < low - tolerance:
        return RANGE_POSITION_BELOW
    if price > high + tolerance:
        return RANGE_POSITION_ABOVE
    return RANGE_POSITION_INSIDE


def _overlapping_zone(zones: list[dict[str, Any]], price: float | None) -> dict[str, Any] | None:
    if price is None:
        return None
    for zone in zones:
        low, high = _num(zone.get("zone_low")), _num(zone.get("zone_high"))
        if low is not None and high is not None and low <= price <= high:
            return zone
    return None


def _nearest_zone(zones: list[dict[str, Any]], price: float | None, kind: str) -> dict[str, Any] | None:
    if price is None:
        return None
    candidates = [zone for zone in zones if zone.get("kind") == kind]
    if not candidates:
        return None
    return min(candidates, key=lambda zone: abs((_num(zone.get("zone_high")) or 0) - price))


def _relation(value: float | None, reference: float | None) -> str | None:
    if value is None or reference is None:
        return None
    if value < reference:
        return "below"
    if value > reference:
        return "above"
    return "equal"


def _remaining_pct(target: float | None, mark: float | None) -> float | None:
    if target is None or mark is None or mark <= 0:
        return None
    return round((target / mark - 1) * 100, 2)


def _markers(analysis: dict[str, Any], limit: int = 4) -> list[dict[str, Any]]:
    markers = analysis.get("wyckoff_markers")
    if not isinstance(markers, list):
        return []
    return [
        {
            "label": item.get("label") or item.get("type"),
            "as_of": item.get("as_of") or item.get("time"),
            "confidence": item.get("confidence"),
        }
        for item in markers[:limit]
        if isinstance(item, dict)
    ]


def _phase_label(phase: Any) -> str:
    return {
        "accumulation": "축적",
        "distribution": "분산",
        "markup": "마크업",
        "markdown": "마크다운",
    }.get(str(phase), "구조")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _num(value: Any) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "-"
    return f"{number:,.2f}".rstrip("0").rstrip(".") if abs(number) < 10000 else f"{number:,.0f}"
