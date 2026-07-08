from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.backtest.statistics import bootstrap_ci_from_counts
from app.db.models import JudgmentScore, utc_now

CALIBRATION_SAMPLE_FLOOR = 20
CONFLICT_RATIO = 0.25
MIN_DIRECTIONAL_EVIDENCE = 3

ENGINE_BASE_WEIGHTS: dict[str, float] = {
    "liquidity": 18.0,
    "wyckoff": 18.0,
    "harmonic": 16.0,
    "level": 12.0,
    "derivatives": 11.0,
    "volume": 9.0,
    "mtf": 10.0,
    "structure": 8.0,
}

ENGINE_JUDGMENT_TYPES: dict[str, str] = {
    "liquidity": "liquidity_sweep",
    "wyckoff": "wyckoff_event",
    "harmonic": "harmonic_prz",
    "level": "invalidation",
    "mtf": "wyckoff_event",
}


def build_confluence(
    *,
    symbol: str,
    timeframe: str,
    analysis: dict[str, Any],
    calibration_scores: list[JudgmentScore] | None = None,
    generated_at: datetime | None = None,
    overlap_groups: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize existing engine outputs into long/short evidence stacks.

    This function does not recompute indicators. It only reads the already
    calculated chart-analysis payload and applies documented weighting.
    """

    now = generated_at or utc_now()
    calibration = _calibration_index(calibration_scores or [])
    raw_evidence = _collect_evidence(analysis)
    evidence = [_apply_weight(item, calibration, now) for item in raw_evidence]
    if overlap_groups is None:
        historical = analysis.get("historical_backtest") if isinstance(analysis.get("historical_backtest"), dict) else {}
        overlap_groups = historical.get("overlap_groups") if isinstance(historical.get("overlap_groups"), list) else []
    evidence, overlap_suppressed = _suppress_overlaps(evidence, overlap_groups)
    directional = [item for item in evidence if item["direction"] in {"long", "short"} and item["score"] > 0]
    long_evidence = sorted([item for item in directional if item["direction"] == "long"], key=_evidence_sort_key, reverse=True)
    short_evidence = sorted([item for item in directional if item["direction"] == "short"], key=_evidence_sort_key, reverse=True)
    long_score = round(sum(item["score"] for item in long_evidence), 2)
    short_score = round(sum(item["score"] for item in short_evidence), 2)
    stance = _stance(long_evidence, short_evidence, long_score, short_score)
    stronger = max(long_score, short_score)
    total = long_score + short_score
    composite = round((stronger / total) * 100, 1) if total > 0 else 0.0
    counter = _counter_evidence(stance, long_evidence, short_evidence)

    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "generated_at": now.isoformat(),
        "data_as_of": _analysis_as_of(analysis),
        "max_engine_age_minutes": _max_engine_age_minutes(evidence, now),
        "stance": stance,
        "stance_label": _stance_label(stance),
        "composite_score": composite if stance not in {"insufficient"} else 0.0,
        "long_score": long_score,
        "short_score": short_score,
        "long_evidence": long_evidence,
        "short_evidence": short_evidence,
        "counter_evidence": counter,
        "evidence_count": len(directional),
        "neutral_evidence": [item for item in evidence if item["direction"] == "neutral"],
        "overlap_suppressed": overlap_suppressed,
        "calibration_policy": {
            "sample_floor": CALIBRATION_SAMPLE_FLOOR,
            "formula": "effective_score = base_weight × confidence/100 × calibration_factor",
            "calibration_factor": "N>=20이면 적중률 CI 하한/70을 0.60~1.25 사이로 클램프 (점추정 아님, WO-36)",
            "conflict_ratio": CONFLICT_RATIO,
            "overlap_policy": "같은 overlap_group 증거는 최강 1개만 가중 반영, 나머지는 동근원 확인으로 표기만",
        },
    }


def _collect_evidence(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    as_of = _analysis_as_of(analysis)
    evidence.extend(_level_evidence(analysis, as_of))
    evidence.extend(_wyckoff_evidence(analysis, as_of))
    evidence.extend(_liquidity_evidence(analysis, as_of))
    evidence.extend(_harmonic_evidence(analysis, as_of))
    evidence.extend(_volume_evidence(analysis, as_of))
    evidence.extend(_derivative_evidence(analysis, as_of))
    evidence.extend(_mtf_evidence(analysis, as_of))
    return evidence


def _level_evidence(analysis: dict[str, Any], as_of: str | None) -> list[dict[str, Any]]:
    levels = analysis.get("price_levels") if isinstance(analysis.get("price_levels"), dict) else {}
    result: list[dict[str, Any]] = []
    support = _first_level(levels.get("support"))
    resistance = _first_level(levels.get("resistance"))
    if support:
        result.append(
            _evidence(
                "level",
                f"구조 지지 {support['price']} · 터치 {support.get('touches', support.get('touch_count', '-'))}",
                "long",
                _num(support.get("score"), 55),
                as_of,
                price=support.get("price"),
                source=support,
            )
        )
    if resistance:
        result.append(
            _evidence(
                "level",
                f"구조 저항 {resistance['price']} · 터치 {resistance.get('touches', resistance.get('touch_count', '-'))}",
                "short",
                _num(resistance.get("score"), 55),
                as_of,
                price=resistance.get("price"),
                source=resistance,
            )
        )
    return result


def _wyckoff_evidence(analysis: dict[str, Any], as_of: str | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    phase = analysis.get("wyckoff_phase") if isinstance(analysis.get("wyckoff_phase"), dict) else {}
    side = str(phase.get("side") or "")
    wyckoff = analysis.get("wyckoff") if isinstance(analysis.get("wyckoff"), dict) else {}
    if side == "accumulation":
        confidence = max(_num(wyckoff.get("accumulation_score"), 55), 50)
        result.append(_evidence("wyckoff", "매집 국면 우세", "long", confidence, as_of, source=phase))
    elif side == "distribution":
        confidence = max(_num(wyckoff.get("distribution_score"), 55), 50)
        result.append(_evidence("wyckoff", "분산 국면 우세", "short", confidence, as_of, source=phase))

    for marker in _list(analysis.get("wyckoff_markers")):
        label = str(marker.get("label") or marker.get("type") or "와이코프 이벤트")
        direction = _wyckoff_direction(label)
        if direction is None:
            continue
        confidence = _num(marker.get("confidence"), 50)
        cross = marker.get("liquidity_crosscheck") if isinstance(marker.get("liquidity_crosscheck"), dict) else {}
        suffix = ""
        if cross.get("confirmed"):
            suffix = f" + {cross.get('sweep_grade', '스윕')} 스윕 확인"
        result.append(
            _evidence(
                "wyckoff",
                f"{label} {int(confidence)}{suffix}",
                direction,
                confidence,
                marker.get("time") or as_of,
                price=marker.get("price"),
                source=marker,
            )
        )
    return result


def _liquidity_evidence(analysis: dict[str, Any], as_of: str | None) -> list[dict[str, Any]]:
    liquidity = analysis.get("liquidity") if isinstance(analysis.get("liquidity"), dict) else {}
    result: list[dict[str, Any]] = []
    sweeps = _list(liquidity.get("sweeps")) + _list(liquidity.get("htf_range_sweeps"))
    for sweep in sweeps:
        if not sweep.get("confirmed"):
            continue
        direction = "long" if sweep.get("side") == "sell_side" else "short" if sweep.get("side") == "buy_side" else None
        if direction is None:
            continue
        grade = str(sweep.get("grade") or "-")
        side = "저점" if direction == "long" else "고점"
        result.append(
            _evidence(
                "liquidity",
                f"{side} {grade} 스윕 확인",
                direction,
                _num(sweep.get("confidence"), 55),
                sweep.get("time") or as_of,
                price=sweep.get("price"),
                source=sweep,
            )
        )

    dealing = liquidity.get("dealing_range") if isinstance(liquidity.get("dealing_range"), dict) else {}
    zone = str(dealing.get("zone") or "")
    if "discount" in zone:
        result.append(_evidence("structure", "디스카운트 존 복귀", "long", 58, as_of, source=dealing))
    elif "premium" in zone:
        result.append(_evidence("structure", "프리미엄 존 진입", "short", 58, as_of, source=dealing))

    shift = liquidity.get("structure_shift") if isinstance(liquidity.get("structure_shift"), dict) else {}
    if shift.get("event") in {"BOS", "CHoCH"} and shift.get("direction") in {"up", "down"}:
        direction = "long" if shift["direction"] == "up" else "short"
        confidence = 62 if shift.get("event") == "CHoCH" else 58
        result.append(_evidence("structure", str(shift.get("label") or shift["event"]), direction, confidence, as_of, price=shift.get("level"), source=shift))
    return result


def _harmonic_evidence(analysis: dict[str, Any], as_of: str | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    patterns = _list(analysis.get("harmonic_patterns"))
    for pattern in sorted(patterns, key=lambda item: _num(item.get("confidence"), 0), reverse=True)[:3]:
        direction = "long" if pattern.get("direction") == "bullish" else "short" if pattern.get("direction") == "bearish" else None
        if direction is None:
            continue
        prz = pattern.get("prz") if isinstance(pattern.get("prz"), dict) else {}
        result.append(
            _evidence(
                "harmonic",
                f"{pattern.get('label') or pattern.get('name') or '하모닉'} PRZ 반전 후보",
                direction,
                _num(pattern.get("confidence"), 55),
                as_of,
                price=prz.get("mid"),
                source=pattern,
            )
        )
    return result


def _volume_evidence(analysis: dict[str, Any], as_of: str | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    mark = _num(analysis.get("mark_price"), 0)
    profile = analysis.get("volume_profile") if isinstance(analysis.get("volume_profile"), dict) else {}
    poc = _num(profile.get("poc_price"), 0)
    if mark > 0 and poc > 0:
        direction = "long" if mark >= poc else "short"
        claim = "POC 상방 유지" if direction == "long" else "POC 하방 이탈"
        confidence = 60 if profile.get("method") == "trade_fills" else 54
        result.append(_evidence("volume", claim, direction, confidence, as_of, price=poc, source=profile))

    xray = analysis.get("volume_xray") if isinstance(analysis.get("volume_xray"), dict) else {}
    delta = _optional_float(xray.get("delta_ratio"))
    if delta is not None and abs(delta) >= 0.2:
        direction = "long" if delta > 0 else "short"
        result.append(_evidence("volume", f"체결 델타 {'양수' if delta > 0 else '음수'} 우세", direction, min(75, 50 + abs(delta) * 80), as_of, source=xray))
    elif xray.get("volume_state") == "drying_up":
        result.append(_evidence("volume", "거래량 고갈로 방향 판단 보류", "neutral", 50, as_of, source=xray))
    return result


def _derivative_evidence(analysis: dict[str, Any], as_of: str | None) -> list[dict[str, Any]]:
    derivatives = analysis.get("derivatives") if isinstance(analysis.get("derivatives"), dict) else {}
    signals = derivatives.get("signals") if isinstance(derivatives.get("signals"), dict) else {}
    result: list[dict[str, Any]] = []
    divergence = signals.get("oi_price_divergence") if isinstance(signals.get("oi_price_divergence"), dict) else None
    if divergence:
        state = str(divergence.get("state") or "")
        direction = "long" if state in {"price_up_oi_up", "price_up_oi_down"} else "short" if state in {"price_down_oi_up", "price_down_oi_down"} else None
        if direction:
            confidence = 62 if state in {"price_up_oi_up", "price_down_oi_up"} else 55
            result.append(_evidence("derivatives", str(divergence.get("label") or "OI/가격 변화"), direction, confidence, signals.get("as_of") or as_of, source=divergence))

    funding = signals.get("funding_state") if isinstance(signals.get("funding_state"), dict) else None
    crowding = signals.get("crowding_score") if isinstance(signals.get("crowding_score"), dict) else None
    funding_value = _optional_float(funding.get("funding")) if funding else None
    crowd_score = _optional_float(crowding.get("score")) if crowding else None
    if funding and funding.get("state") == "extreme" and funding_value is not None:
        direction = "short" if funding_value > 0 else "long"
        confidence = min(80, 58 + (crowd_score or 0) * 0.2)
        result.append(_evidence("derivatives", f"{funding.get('label')} + 쏠림 {crowd_score or '-'}", direction, confidence, signals.get("as_of") or as_of, source={"funding": funding, "crowding": crowding}))
    return result


def _mtf_evidence(analysis: dict[str, Any], as_of: str | None) -> list[dict[str, Any]]:
    mtf = analysis.get("wyckoff_mtf") if isinstance(analysis.get("wyckoff_mtf"), dict) else {}
    phase = str(mtf.get("htf_phase") or mtf.get("htf_trend") or "")
    if not phase:
        return []
    if "accumulation" in phase or "bull" in phase:
        return [_evidence("mtf", f"상위 TF {phase}", "long", 60, as_of, source=mtf)]
    if "distribution" in phase or "bear" in phase:
        return [_evidence("mtf", f"상위 TF {phase}", "short", 60, as_of, source=mtf)]
    return []


def _apply_weight(evidence: dict[str, Any], calibration: dict[tuple[str, int], dict[str, Any]], now: datetime) -> dict[str, Any]:
    engine = str(evidence["engine"])
    confidence = _num(evidence.get("confidence"), 50)
    base_weight = ENGINE_BASE_WEIGHTS.get(engine, 8.0)
    judgment_type = ENGINE_JUDGMENT_TYPES.get(engine)
    bucket = _confidence_bucket(confidence)
    calibration_payload = calibration.get((judgment_type, bucket)) if judgment_type else None
    if calibration_payload is None and judgment_type:
        calibration_payload = calibration.get((judgment_type, -1))
    factor = 1.0
    accuracy_ci_low: float | None = None
    if calibration_payload and calibration_payload["tested"] >= CALIBRATION_SAMPLE_FLOOR:
        # WO-36 §3: 가중 보정은 점추정이 아니라 적중률 CI 하한 기준 —
        # 운 좋은 점추정이 가중치를 부풀리는 것을 구조적으로 차단.
        ci = bootstrap_ci_from_counts(int(calibration_payload["correct"]), int(calibration_payload["tested"]))
        accuracy_ci_low = ci[0] if ci else None
        basis = accuracy_ci_low if accuracy_ci_low is not None else float(calibration_payload["accuracy_pct"])
        factor = _clamp(basis / 70.0, 0.60, 1.25)
    calibration_meta = (
        {
            **calibration_payload,
            "accuracy_ci_low_pct": accuracy_ci_low,
            "sample_floor": CALIBRATION_SAMPLE_FLOOR,
            "applied": calibration_payload["tested"] >= CALIBRATION_SAMPLE_FLOOR,
        }
        if calibration_payload
        else None
    )
    score = round(base_weight * (confidence / 100.0) * factor, 2)
    as_of = evidence.get("as_of")
    return {
        **evidence,
        "base_weight": base_weight,
        "calibration_factor": round(factor, 3),
        "score": score,
        "is_stale": _is_stale(as_of, now),
        "calibration": calibration_meta,
    }


def _evidence_family(item: dict[str, Any]) -> tuple[str, str, str] | None:
    """증거를 백테스트 overlap 패밀리 (engine, event_family, direction)로 사상한다."""
    engine = str(item.get("engine") or "")
    direction = str(item.get("direction") or "neutral")
    claim = str(item.get("claim") or "")
    if direction not in {"long", "short"}:
        return None
    if engine == "liquidity":
        return ("liquidity", "sweep", direction)
    if engine == "wyckoff":
        # 국면 증거는 이벤트가 아니다 — 이벤트 마커 증거만 동근원 후보.
        if "국면" in claim:
            return None
        return ("wyckoff", "event", direction)
    if engine == "harmonic":
        return ("harmonic", "prz", direction)
    if engine == "level":
        return ("levels", "level", direction)
    return None


def _suppress_overlaps(
    evidence: list[dict[str, Any]],
    overlap_groups: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """같은 overlap_group 증거는 최강 1개만 가중 반영 — 이중 가점 구조적 차단 (WO-36 §6)."""
    if not overlap_groups:
        return evidence, []
    group_of: dict[tuple[str, str, str], int] = {}
    group_meta: dict[int, dict[str, Any]] = {}
    for group_index, group in enumerate(overlap_groups):
        families = group.get("families") if isinstance(group.get("families"), list) else []
        for family in families:
            if isinstance(family, (list, tuple)) and len(family) == 3:
                group_of[tuple(str(part) for part in family)] = group_index
                group_meta[group_index] = {"group_id": group.get("group_id"), "source": group.get("source")}

    winners: dict[int, dict[str, Any]] = {}
    for item in evidence:
        family = _evidence_family(item)
        if family is None or family not in group_of:
            continue
        group_index = group_of[family]
        current = winners.get(group_index)
        if current is None or float(item.get("score") or 0) > float(current.get("score") or 0):
            winners[group_index] = item

    suppressed: list[dict[str, Any]] = []
    result: list[dict[str, Any]] = []
    for item in evidence:
        family = _evidence_family(item)
        group_index = group_of.get(family) if family else None
        if group_index is not None and winners.get(group_index) is not item:
            winner = winners[group_index]
            demoted = {
                **item,
                "score": 0.0,
                "overlap_note": "동근원 확인",
                "overlap_group": group_meta.get(group_index, {}).get("group_id"),
                "overlap_winner": winner.get("claim"),
            }
            suppressed.append(demoted)
            result.append(demoted)
            continue
        if group_index is not None:
            item = {**item, "overlap_group": group_meta.get(group_index, {}).get("group_id")}
        result.append(item)
    return result, suppressed


def _calibration_index(scores: list[JudgmentScore]) -> dict[tuple[str, int], dict[str, Any]]:
    grouped: dict[tuple[str, int], list[JudgmentScore]] = defaultdict(list)
    typed: dict[str, list[JudgmentScore]] = defaultdict(list)
    for score in scores:
        if score.outcome == "untested" or score.confidence is None:
            continue
        typed[score.judgment_type].append(score)
        grouped[(score.judgment_type, _confidence_bucket(score.confidence))].append(score)
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for key, items in grouped.items():
        result[key] = _calibration_summary(items)
    for judgment_type, items in typed.items():
        result[(judgment_type, -1)] = _calibration_summary(items)
    return result


def _calibration_summary(scores: list[JudgmentScore]) -> dict[str, Any]:
    tested = len(scores)
    correct = len([score for score in scores if score.outcome == "correct"])
    accuracy = round(correct / tested * 100, 1) if tested else None
    return {
        "tested": tested,
        "correct": correct,
        "accuracy_pct": accuracy,
    }


def _stance(long_evidence: list[dict[str, Any]], short_evidence: list[dict[str, Any]], long_score: float, short_score: float) -> str:
    if len(long_evidence) + len(short_evidence) < MIN_DIRECTIONAL_EVIDENCE:
        return "insufficient"
    if not long_evidence or not short_evidence:
        return "insufficient"
    stronger = max(long_score, short_score)
    if stronger <= 0:
        return "insufficient"
    if abs(long_score - short_score) / stronger < CONFLICT_RATIO:
        return "conflicted"
    return "long_leaning" if long_score > short_score else "short_leaning"


def _counter_evidence(stance: str, long_evidence: list[dict[str, Any]], short_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if stance == "long_leaning":
        return short_evidence[:2]
    if stance == "short_leaning":
        return long_evidence[:2]
    if stance == "conflicted":
        return sorted([*long_evidence[:1], *short_evidence[:1]], key=_evidence_sort_key, reverse=True)
    return []


def _evidence(
    engine: str,
    claim: str,
    direction: str,
    confidence: float,
    as_of: Any,
    *,
    price: Any = None,
    source: Any = None,
) -> dict[str, Any]:
    return {
        "engine": engine,
        "claim": claim,
        "direction": direction,
        "confidence": int(_clamp(confidence, 0, 100)),
        "as_of": _iso(as_of),
        "price": price,
        "source": _compact_source(source),
    }


def _first_level(value: Any) -> dict[str, Any] | None:
    items = _list(value)
    if not items:
        return None
    candidates = [item for item in items if isinstance(item.get("price"), (int, float))]
    return sorted(candidates, key=lambda item: _num(item.get("score"), 0), reverse=True)[0] if candidates else None


def _wyckoff_direction(label: str) -> str | None:
    lower = label.lower()
    if any(token in lower for token in ("spring", "sos", "lps")) and "lpsy" not in lower:
        return "long"
    if any(token in lower for token in ("utad", "sow", "lpsy", "distribution")):
        return "short"
    # WO-43: 매집 레인지의 UT(업스러스트) 재명명 라벨 — 방향은 여전히 하락 경계.
    if lower == "ut" or lower.startswith("ut "):
        return "short"
    return None


def _analysis_as_of(analysis: dict[str, Any]) -> str | None:
    quality = analysis.get("data_quality") if isinstance(analysis.get("data_quality"), dict) else {}
    value = quality.get("last_candle_at") or analysis.get("as_of")
    return _iso(value)


def _max_engine_age_minutes(evidence: list[dict[str, Any]], now: datetime) -> int | None:
    ages = []
    for item in evidence:
        parsed = _parse_dt(item.get("as_of"))
        if parsed is not None:
            ages.append(max(0, int((now - parsed).total_seconds() // 60)))
    return max(ages) if ages else None


def _is_stale(value: Any, now: datetime) -> bool:
    parsed = _parse_dt(value)
    return bool(parsed and (now - parsed).total_seconds() > 120 * 60)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


def _confidence_bucket(confidence: int | float | None) -> int:
    if confidence is None:
        return -1
    return min(90, max(0, int(float(confidence) // 10 * 10)))


def _evidence_sort_key(item: dict[str, Any]) -> tuple[float, int]:
    return float(item.get("score") or 0), int(item.get("confidence") or 0)


def _stance_label(stance: str) -> str:
    return {
        "long_leaning": "롱 우위",
        "short_leaning": "숏 우위",
        "conflicted": "근거 충돌",
        "insufficient": "판단 유보",
    }.get(stance, stance)


def _compact_source(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    keep = ("id", "type", "label", "basis", "grade", "components", "state", "event", "zone", "method", "sources")
    return {key: value[key] for key in keep if key in value}


def _list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _num(value: Any, default: float) -> float:
    number = _optional_float(value)
    return default if number is None else number


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
