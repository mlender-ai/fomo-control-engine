from __future__ import annotations

from app.db.models import Position, PositionEvent, PositionHealthComponents, PositionInsight, PositionSnapshot, Report
from app.positions.pnl import resolve_position_pnl_percent

STATUS_LABELS = {
    "healthy": "진입 논리 유지",
    "watch": "관찰 필요",
    "risk_rising": "리스크 상승",
    "thesis_weakening": "진입 논리 약화",
    "critical": "긴급 점검",
    "unknown": "데이터 부족",
}

STATUS_SEVERITY_RANK = {
    "healthy": 0,
    "unknown": 0,
    "watch": 1,
    "risk_rising": 2,
    "thesis_weakening": 3,
    "critical": 4,
}


def clamp_score(value: float) -> int:
    return max(0, min(100, int(round(value))))


def calculate_pnl_amount(position: Position, mark_price: float | None) -> float | None:
    if position.unrealized_pl is not None:
        return round(position.unrealized_pl, 4)
    if mark_price is None:
        return None
    side_multiplier = 1 if position.direction == "long" else -1
    return round((mark_price - position.entry_price) * position.quantity * side_multiplier, 4)


def calculate_liquidation_distance(position: Position, mark_price: float | None = None) -> float | None:
    price = mark_price or position.mark_price or position.current_price
    liquidation_price = position.liquidation_price
    if price is None or liquidation_price is None or price <= 0 or liquidation_price <= 0:
        return None
    if position.direction == "long":
        distance = ((price - liquidation_price) / price) * 100
    else:
        distance = ((liquidation_price - price) / price) * 100
    return round(max(0, distance), 2)


def calculate_price_distance_from_entry(position: Position, mark_price: float | None) -> float | None:
    if mark_price is None or position.entry_price <= 0:
        return None
    side_multiplier = 1 if position.direction == "long" else -1
    return round(((mark_price - position.entry_price) / position.entry_price) * 100 * side_multiplier, 2)


def drawdown_from_peak(current_pnl: float, previous_snapshots: list[PositionSnapshot]) -> tuple[float, float]:
    pnl_values = [snapshot.pnl_percent for snapshot in previous_snapshots] + [current_pnl]
    peak = max(pnl_values) if pnl_values else current_pnl
    if peak <= 0 or current_pnl >= peak:
        return 0.0, 0.0
    giveback = ((peak - current_pnl) / abs(peak)) * 100
    return round(giveback, 2), round(giveback, 2)


def build_position_state(position: Position, report: Report, previous_snapshots: list[PositionSnapshot] | None = None) -> dict:
    previous_snapshots = previous_snapshots or []
    mark_price = position.mark_price or position.current_price or report.price
    pnl_result = resolve_position_pnl_percent(position, mark_price)
    pnl_percent = round(pnl_result.pnl_percent, 2)
    pnl_source = pnl_result.source
    position.pnl_source = pnl_source
    pnl_amount = calculate_pnl_amount(position, mark_price)
    liquidation_distance = calculate_liquidation_distance(position, mark_price)
    as_of = _position_as_of(position, report)
    entry_score = position.entry_score if position.entry_score is not None else report.entry_score
    current_score = report.entry_score
    score_change = current_score - entry_score
    drawdown_pct, giveback_pct = drawdown_from_peak(pnl_percent, previous_snapshots)
    indicators = report.raw_json.get("indicators", {})
    structure = report.raw_json.get("structure", {})
    liquidity = report.raw_json.get("liquidity", {})
    current_direction_score = direction_aware_score(position.direction, structure, indicators)
    entry_direction_score = position.entry_direction_score
    if entry_direction_score is None:
        entry_direction_score = current_direction_score
        position.entry_direction_score = entry_direction_score
    thesis_delta = current_direction_score - entry_direction_score
    components = _health_components(
        position=position,
        report=report,
        liquidation_distance=liquidation_distance,
        pnl_percent=pnl_percent,
        giveback_pct=giveback_pct,
        structure=structure,
        indicators=indicators,
        liquidity=liquidity,
        entry_direction_score=entry_direction_score,
        current_direction_score=current_direction_score,
    )
    health_score = calculate_health_score(components)
    risk_score = calculate_position_risk_score(report.scores.risk, liquidation_distance, drawdown_pct)
    status = classify_status(
        health_score=health_score,
        liquidation_distance=liquidation_distance,
        thesis_delta=thesis_delta,
        risk_score=risk_score,
        drawdown_from_peak=drawdown_pct,
        data_missing=_data_missing(report, position),
        structure_broken=_structure_broken(structure, position),
        pnl_percent=pnl_percent,
        position=position,
    )
    severity_rank = STATUS_SEVERITY_RANK[status]

    analysis = {
        "position_analysis": {
            "symbol": position.symbol,
            "direction": position.direction.value,
            "health_score": health_score,
            "status": status,
            "status_label": STATUS_LABELS[status],
            "severity_rank": severity_rank,
            "survival": components.survival,
            "pnl_state": components.pnl_state,
            "thesis_integrity": components.thesis_integrity,
            "structure": components.structure,
            "flow": components.flow,
            "chart_structure": components.chart_structure,
            "risk_safety": components.risk_safety,
            "momentum_volume": components.momentum_volume,
            "liquidity_funding": components.liquidity_funding,
            "pnl_protection": components.pnl_protection,
            "liquidation_buffer": components.liquidation_buffer,
            "direction_alignment": components.direction_alignment,
            "health_formula_version": components.formula_version,
            "entry_score": entry_score,
            "current_score": current_score,
            "score_change": score_change,
            "entry_direction_score": entry_direction_score,
            "current_direction_score": current_direction_score,
            "thesis_delta": thesis_delta,
            "as_of": as_of.isoformat(),
            "pnl_source": pnl_source,
        },
        "wyckoff": _wyckoff_payload(structure),
        "technical": _technical_payload(indicators, structure, liquidity, position, report),
        "risk": {
            "liquidation_distance_pct": liquidation_distance,
            "risk_score": risk_score,
            "atr_risk": _atr_risk(indicators, mark_price),
            "drawdown_from_peak_pct": drawdown_pct,
            "profit_giveback_pct": giveback_pct,
            "price_distance_from_entry_pct": calculate_price_distance_from_entry(position, mark_price),
            "critical_levels": _critical_levels(report, position),
        },
        "reason_codes": _reason_codes(status, position, report, liquidation_distance, thesis_delta, drawdown_pct),
    }
    return {
        "position": position.model_dump(mode="json"),
        "as_of": as_of,
        "mark_price": mark_price,
        "pnl_percent": pnl_percent,
        "pnl_amount": pnl_amount,
        "pnl_source": pnl_source,
        "liquidation_distance_pct": liquidation_distance,
        "health_score": health_score,
        "status": status,
        "status_label": STATUS_LABELS[status],
        "severity_rank": severity_rank,
        "risk_score": risk_score,
        "score_change": score_change,
        "entry_direction_score": entry_direction_score,
        "current_direction_score": current_direction_score,
        "thesis_delta": thesis_delta,
        "entry_score": entry_score,
        "current_score": current_score,
        "analysis": analysis,
        "score_json": {
            "entry_score": entry_score,
            "current_score": current_score,
            "score_change": score_change,
            "health_components": components.model_dump(),
            "entry_breakdown": report.scores.model_dump(),
            "fomo_index": report.scores.fomo,
        },
    }


def calculate_health_score(components: PositionHealthComponents) -> int:
    weighted = clamp_score(
        components.survival * 0.30
        + components.pnl_state * 0.20
        + components.thesis_integrity * 0.20
        + components.structure * 0.20
        + components.flow * 0.10
    )
    if components.pnl_state == 0:
        weighted = min(weighted, 25)
    if components.pnl_state <= 10 and components.survival <= 30:
        weighted = min(weighted, 25)
    if components.pnl_state <= 10 and components.survival <= 10:
        weighted = min(weighted, 20)
    return weighted


def calculate_position_risk_score(base_risk_score: int, liquidation_distance: float | None, drawdown_from_peak: float) -> int:
    risk = float(base_risk_score)
    if liquidation_distance is None:
        risk += 12
    elif liquidation_distance < 5:
        risk = max(risk, 90)
    elif liquidation_distance < 10:
        risk = max(risk, 72)
    elif liquidation_distance < 15:
        risk = max(risk, 58)
    if drawdown_from_peak > 50:
        risk += 15
    return clamp_score(risk)


def classify_status(
    *,
    health_score: int,
    liquidation_distance: float | None,
    thesis_delta: int,
    risk_score: int,
    drawdown_from_peak: float,
    data_missing: bool,
    structure_broken: bool,
    pnl_percent: float,
    position: Position,
) -> str:
    if (liquidation_distance is not None and liquidation_distance < 5) or pnl_percent <= -75:
        return "critical"
    if data_missing:
        return "unknown"
    status = "healthy" if health_score >= 80 and (liquidation_distance is None or liquidation_distance > 15) and thesis_delta > -10 else "watch"
    if position.status == "open" and position.liquidation_price is None and position.leverage >= 5:
        status = _max_status(status, "watch")
    if thesis_delta < -20 or structure_broken:
        status = _max_status(status, "thesis_weakening")
    if risk_score > 65 or (liquidation_distance is not None and liquidation_distance < 10) or drawdown_from_peak > 50:
        status = _max_status(status, "risk_rising")
    if pnl_percent <= -50:
        status = _max_status(status, "risk_rising")
    elif pnl_percent <= -30:
        status = _max_status(status, "watch")
    if health_score < 50:
        status = _max_status(status, "thesis_weakening")
    return status


def make_snapshot(position: Position, state: dict) -> PositionSnapshot:
    return PositionSnapshot(
        position_id=position.id,
        symbol=position.symbol,
        as_of=state["as_of"],
        mark_price=state["mark_price"],
        pnl_percent=state["pnl_percent"],
        pnl_amount=state["pnl_amount"],
        pnl_source=state["pnl_source"],
        liquidation_price=position.liquidation_price,
        liquidation_distance_pct=state["liquidation_distance_pct"],
        health_score=state["health_score"],
        severity_rank=state["severity_rank"],
        status_label=state["status_label"],
        risk_score=state["risk_score"],
        score_json=state["score_json"],
        analysis_json=state["analysis"],
    )


def build_events(position: Position, snapshot: PositionSnapshot, previous_snapshot: PositionSnapshot | None = None) -> list[PositionEvent]:
    events: list[PositionEvent] = []
    if previous_snapshot is not None:
        score_delta = snapshot.health_score - previous_snapshot.health_score
        if abs(score_delta) >= 10:
            severity = "high" if score_delta <= -15 else "medium"
            events.append(
                PositionEvent(
                    position_id=position.id,
                    event_type="health_score_change",
                    severity=severity,
                    title=f"Health Score {previous_snapshot.health_score} → {snapshot.health_score}",
                    description=f"포지션 상태 점수가 {score_delta:+d}점 변했습니다.",
                    data={"previous": previous_snapshot.health_score, "current": snapshot.health_score},
                )
            )
        if previous_snapshot.status_label != snapshot.status_label:
            events.append(
                PositionEvent(
                    position_id=position.id,
                    event_type="status_change",
                    severity=_severity_from_status(snapshot.analysis_json["position_analysis"]["status"]),
                    title=f"상태 변경: {previous_snapshot.status_label} → {snapshot.status_label}",
                    description="포지션 상태 라벨이 변경되었습니다.",
                    data={"previous": previous_snapshot.status_label, "current": snapshot.status_label},
                )
            )
    if position.status == "open" and position.liquidation_price is None and position.leverage >= 5:
        events.append(
            PositionEvent(
                position_id=position.id,
                event_type="missing_liquidation_price",
                severity="medium",
                title="청산가 미수신",
                description="거래소에서 청산가가 내려오지 않았습니다. 5x 이상 포지션은 별도 리스크 확인이 필요합니다.",
                data={"leverage": position.leverage},
            )
        )
    if snapshot.liquidation_distance_pct is not None and snapshot.liquidation_distance_pct < 10:
        events.append(
            PositionEvent(
                position_id=position.id,
                event_type="liquidation_distance",
                severity="critical" if snapshot.liquidation_distance_pct < 5 else "high",
                title=f"청산가 거리 {snapshot.liquidation_distance_pct:.2f}%",
                description="청산가와 현재가의 거리가 좁아졌습니다. 포지션 점검이 필요합니다.",
                data={"liquidation_distance_pct": snapshot.liquidation_distance_pct},
            )
        )
    if snapshot.risk_score > 75:
        events.append(
            PositionEvent(
                position_id=position.id,
                event_type="risk_score",
                severity="high",
                title=f"Risk Score {snapshot.risk_score}/100",
                description="리스크 점수가 높은 구간입니다.",
                data={"risk_score": snapshot.risk_score},
            )
        )
    return events


def render_position_insight(position: Position, snapshot: PositionSnapshot, previous_insight: PositionInsight | None = None) -> str:
    analysis = snapshot.analysis_json
    position_analysis = analysis["position_analysis"]
    technical = analysis["technical"]
    wyckoff = analysis["wyckoff"]
    risk = analysis["risk"]
    previous_line = ""
    if previous_insight is not None:
        previous_line = "\n\n이전 인사이트와 비교:\n직전 인사이트 이후 최신 snapshot 기준으로 다시 점검했습니다."

    return (
        f"📍 {position.symbol} {position.direction.value.upper()} 포지션 상태\n\n"
        f"현재 상태:\n{position_analysis['status_label']} 상태입니다. Health Score는 {snapshot.health_score}/100입니다.\n\n"
        f"수익/리스크:\n현재 PnL은 {snapshot.pnl_percent:.2f}%이며, "
        f"청산가까지 거리는 {_format_pct(snapshot.liquidation_distance_pct)}입니다. "
        f"리스크 점수는 {snapshot.risk_score}/100입니다.\n\n"
        f"차트 구조:\n추세는 {technical['trend']}로 해석됩니다. "
        f"지지 상태는 {technical['support_status']}, 저항 상태는 {technical['resistance_status']}입니다.\n\n"
        f"와이코프/기술적 분석:\n와이코프 관점에서는 {wyckoff['phase_hint']} 가능성을 봅니다. "
        f"RSI 상태는 {technical['rsi_state']}, MACD 상태는 {technical['macd_state']}, "
        f"거래량 상태는 {technical['volume_state']}입니다.\n\n"
        f"진입 논리:\n진입 당시 방향 점수는 {position_analysis['entry_direction_score']}점, "
        f"현재 방향 점수는 {position_analysis['current_direction_score']}점이며 "
        f"변화폭은 {position_analysis['thesis_delta']:+d}점입니다. "
        f"{'진입 메모: ' + position.entry_memo if position.entry_memo else '진입 메모가 없어 논리 비교는 점수와 차트 구조 중심으로만 판단합니다.'}\n\n"
        f"주의할 가격:\n{_render_critical_levels(risk['critical_levels'])}\n\n"
        f"제 의견:\n현재 포지션은 {position_analysis['status_label']} 관점에서 "
        f"손절 기준, 수익 반납 기준, 다음 캔들에서의 지지/저항 반응을 조건별로 점검해야 합니다."
        f"{previous_line}"
    )


def make_insight(position: Position, snapshot: PositionSnapshot, previous_insight: PositionInsight | None = None) -> PositionInsight:
    return PositionInsight(
        position_id=position.id,
        snapshot_id=snapshot.id,
        as_of=snapshot.as_of,
        health_score=snapshot.health_score,
        status_label=snapshot.status_label,
        input_json=snapshot.analysis_json,
        insight_text=render_position_insight(position, snapshot, previous_insight),
    )


def _health_components(
    *,
    position: Position,
    report: Report,
    liquidation_distance: float | None,
    pnl_percent: float,
    giveback_pct: float,
    structure: dict,
    indicators: dict,
    liquidity: dict,
    entry_direction_score: int,
    current_direction_score: int,
) -> PositionHealthComponents:
    survival = _survival_score(liquidation_distance, position.leverage)
    pnl_state = _pnl_state_score(pnl_percent, giveback_pct)
    direction_alignment = current_direction_score
    thesis = _thesis_integrity_score(entry_direction_score, current_direction_score)
    structure_score = _directional_structure_score(position, structure, report.scores.structure, current_direction_score)
    flow = _flow_score(position, indicators, liquidity)
    return PositionHealthComponents(
        survival=survival,
        pnl_state=pnl_state,
        thesis_integrity=thesis,
        structure=structure_score,
        flow=flow,
        chart_structure=structure_score,
        risk_safety=survival,
        momentum_volume=flow,
        liquidity_funding=flow,
        pnl_protection=pnl_state,
        liquidation_buffer=survival,
        direction_alignment=direction_alignment,
    )


def _survival_score(liquidation_distance: float | None, leverage: float) -> int:
    if liquidation_distance is None:
        if leverage >= 10:
            return 30
        if leverage >= 5:
            return 45
        return 60
    if liquidation_distance < 3:
        return 0
    if liquidation_distance < 5:
        return clamp_score(_interpolate(liquidation_distance, 3, 5, 0, 10))
    if liquidation_distance < 10:
        return clamp_score(_interpolate(liquidation_distance, 5, 10, 10, 40))
    if liquidation_distance < 15:
        return clamp_score(_interpolate(liquidation_distance, 10, 15, 40, 70))
    if liquidation_distance < 30:
        return clamp_score(_interpolate(liquidation_distance, 15, 30, 70, 100))
    return 100


def _pnl_state_score(pnl_percent: float, giveback_pct: float) -> int:
    if pnl_percent >= 20:
        score = clamp_score(90 + min(10, (pnl_percent - 20) * 0.5))
    elif pnl_percent >= 0:
        score = clamp_score(_interpolate(pnl_percent, 0, 20, 70, 90))
    elif pnl_percent >= -10:
        score = clamp_score(_interpolate(pnl_percent, -10, 0, 50, 70))
    elif pnl_percent >= -30:
        score = clamp_score(_interpolate(pnl_percent, -30, -10, 25, 50))
    elif pnl_percent >= -50:
        score = clamp_score(_interpolate(pnl_percent, -50, -30, 10, 25))
    else:
        score = clamp_score(_interpolate(max(pnl_percent, -75), -75, -50, 0, 10))
    if giveback_pct > 50:
        score -= 15
    return clamp_score(score)


def _thesis_integrity_score(entry_direction_score: int, current_direction_score: int) -> int:
    delta = current_direction_score - entry_direction_score
    if delta >= 10:
        return clamp_score(82 + min(18, delta * 0.9))
    if delta >= 0:
        return clamp_score(72 + delta)
    if delta >= -15:
        return clamp_score(72 + delta * 1.5)
    if delta >= -30:
        return clamp_score(50 + (delta + 15) * 1.4)
    return clamp_score(25 + (delta + 30) * 0.8)


def _directional_structure_score(position: Position, structure: dict, base_structure_score: int, direction_score: int) -> int:
    # Until the structural S/R engine lands in WO-FCE-04, short-side structure uses
    # the inverse of the existing long-biased structure score plus directional trend evidence.
    if position.direction == "long":
        score = direction_score * 0.70 + base_structure_score * 0.30
    else:
        score = direction_score * 0.70 + (100 - base_structure_score) * 0.30
    trend = structure.get("trend", {})
    if position.direction == "long" and trend.get("support_broken", False):
        score -= 18
    if position.direction == "short" and trend.get("resistance_broken", False):
        score -= 18
    return clamp_score(score)


def direction_aware_score(direction, structure: dict, indicators: dict) -> int:
    trend = structure.get("trend", {})
    wyckoff = structure.get("wyckoff", {})
    trend_direction = trend.get("direction", "unknown")
    higher_low = bool(trend.get("higher_low", False))
    lower_high = bool(trend.get("lower_high", False))
    if not lower_high:
        lower_high = trend_direction in {"bearish", "bearish_to_neutral"} and not higher_low
    break_of_structure = bool(trend.get("break_of_structure", False))
    spring = bool(wyckoff.get("spring_candidate", False))
    sos = bool(wyckoff.get("sos_confirmed", False))
    accumulation = float(wyckoff.get("accumulation_score", 0))
    distribution = float(wyckoff.get("distribution_score", 0))
    close = _optional_float(indicators.get("last_close"))
    upper = _optional_float(indicators.get("bollinger_upper"))
    lower = _optional_float(indicators.get("bollinger_lower"))
    direction_value = getattr(direction, "value", str(direction))

    if direction_value == "long":
        base = {
            "bullish": 78,
            "neutral_to_bullish": 68,
            "neutral": 55,
            "bearish_to_neutral": 45,
            "bearish": 25,
        }.get(trend_direction, 50)
        if higher_low:
            base += 10
        if break_of_structure:
            base += 8
        if spring:
            base += 6
        if sos:
            base += 8
        if lower is not None and close is not None and close < lower:
            base -= 18
        if distribution > accumulation + 20:
            base -= 10
        return clamp_score(base)

    base = {
        "bearish": 78,
        "bearish_to_neutral": 68,
        "neutral": 55,
        "neutral_to_bullish": 35,
        "bullish": 22,
    }.get(trend_direction, 50)
    if lower_high:
        base += 10
    if higher_low:
        base -= 10
    if break_of_structure and trend_direction in {"neutral_to_bullish", "bullish"}:
        base -= 12
    if upper is not None and close is not None and close > upper:
        base -= 18
    if distribution > accumulation + 10:
        base += 8
    if spring:
        base -= 6
    if sos:
        base -= 8
    return clamp_score(base)


def _flow_score(position: Position, indicators: dict, liquidity: dict) -> int:
    relative_volume = _optional_float(indicators.get("relative_volume")) or 1.0
    macd = _optional_float(indicators.get("macd_histogram")) or 0.0
    last_close = _optional_float(indicators.get("last_close"))
    previous_close = _optional_float(indicators.get("previous_close"))
    price_delta = 0.0 if last_close is None or previous_close is None else last_close - previous_close
    side = 1 if position.direction == "long" else -1
    score = 55.0
    if price_delta * side > 0:
        score += 12
    elif price_delta * side < 0:
        score -= 12
    if macd * side > 0:
        score += 12
    elif macd * side < 0:
        score -= 12
    if relative_volume >= 1.8:
        score += 10 if price_delta * side >= 0 else -8
    elif relative_volume >= 1.2:
        score += 6 if price_delta * side >= 0 else -5
    elif relative_volume < 0.8:
        score -= 6
    funding = str(liquidity.get("funding_rate_state", "neutral"))
    if position.direction == "long" and funding in {"positive", "bullish", "long_favored"}:
        score += 4
    elif position.direction == "short" and funding in {"negative", "bearish", "short_favored"}:
        score += 4
    elif funding in {"overheated", "crowded"}:
        score -= 6
    open_interest = str(liquidity.get("open_interest_change", "stable"))
    if open_interest in {"rising", "increasing", "expanding"} and price_delta * side > 0:
        score += 5
    elif open_interest in {"rising", "increasing", "expanding"} and price_delta * side < 0:
        score -= 5
    return clamp_score(score)


def _interpolate(value: float, left: float, right: float, left_score: float, right_score: float) -> float:
    if right == left:
        return 45
    ratio = (value - left) / (right - left)
    return left_score + ratio * (right_score - left_score)


def _optional_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _data_missing(report: Report, position: Position) -> bool:
    return not report.data_quality.ohlcv_ok or not report.data_quality.min_candles_met or report.price <= 0


def _max_status(left: str, right: str) -> str:
    return left if STATUS_SEVERITY_RANK[left] >= STATUS_SEVERITY_RANK[right] else right


def _structure_broken(structure: dict, position: Position) -> bool:
    trend = structure.get("trend", {})
    if position.direction == "long":
        return bool(not trend.get("higher_low", False) and not trend.get("break_of_structure", False))
    return bool(trend.get("higher_low", False) and trend.get("break_of_structure", False))


def _wyckoff_payload(structure: dict) -> dict:
    wyckoff = structure.get("wyckoff", {})
    return {
        "accumulation_score": wyckoff.get("accumulation_score", 0),
        "distribution_score": wyckoff.get("distribution_score", 0),
        "phase_hint": wyckoff.get("phase_hint", "unknown"),
        "spring_candidate": bool(wyckoff.get("spring_candidate", False)),
        "sos_candidate": bool(wyckoff.get("sos_confirmed", False)),
        "lps_candidate": bool(wyckoff.get("sos_confirmed", False) and not wyckoff.get("spring_candidate", False)),
        "structure_comment": "상승 구조는 유지되지만 강한 SOS 확정은 아닙니다." if not wyckoff.get("sos_confirmed", False) else "거래량을 동반한 구조 돌파 후보가 있습니다.",
    }


def _technical_payload(indicators: dict, structure: dict, liquidity: dict, position: Position, report: Report) -> dict:
    rsi = float(indicators.get("rsi", 50))
    macd = float(indicators.get("macd_histogram", 0))
    close = float(indicators.get("last_close", report.price))
    upper = float(indicators.get("bollinger_upper", close))
    lower = float(indicators.get("bollinger_lower", close))
    relative_volume = float(indicators.get("relative_volume", 1))
    structure_levels = report.raw_json.get("structure_levels", {})
    trend = structure.get("trend", {})
    trend_direction = trend.get("direction", "unknown")
    if position.direction == "short" and trend_direction == "neutral_to_bullish":
        trend_alignment = "against_short"
    elif position.direction == "long" and trend_direction == "bearish_to_neutral":
        trend_alignment = "against_long"
    else:
        trend_alignment = "aligned_or_neutral"
    return {
        "trend": trend_direction,
        "trend_alignment": trend_alignment,
        "rsi_state": "cooling_from_overbought" if rsi > 70 else "neutral" if 35 <= rsi <= 65 else "oversold_or_weak",
        "macd_state": "bullish_but_weakening" if macd > 0 else "bearish_or_weak",
        "bollinger_state": "above_upper_band" if close > upper else "near_lower_band" if close < lower else "inside_band",
        "volume_state": "expanding" if relative_volume >= 1.4 else "declining_after_push" if relative_volume < 0.9 else "normal",
        "support_status": _support_status_from_levels(close, structure_levels.get("support", [])),
        "resistance_status": _resistance_status_from_levels(close, structure_levels.get("resistance", [])),
        "open_interest": liquidity.get("open_interest_change", "unknown"),
        "funding": liquidity.get("funding_rate_state", "unknown"),
        "break_of_structure": bool(trend.get("break_of_structure", False)),
        "higher_low": bool(trend.get("higher_low", False)),
    }


def _atr_risk(indicators: dict, mark_price: float | None) -> str:
    atr = float(indicators.get("atr", 0))
    if not mark_price:
        return "unknown"
    atr_pct = (atr / mark_price) * 100
    if atr_pct > 5:
        return "high"
    if atr_pct > 3:
        return "medium"
    return "low"


def _critical_levels(report: Report, position: Position) -> list[dict]:
    structure_levels = report.raw_json.get("structure_levels", {})
    levels = []
    for level in structure_levels.get("support", [])[:2]:
        if isinstance(level, dict) and level.get("price") is not None:
            levels.append({**level, "type": "support", "meaning": "이탈 시 진입 논리 약화"})
    for level in structure_levels.get("resistance", [])[:2]:
        if isinstance(level, dict) and level.get("price") is not None:
            levels.append({**level, "type": "resistance", "meaning": "돌파 실패 시 수익 반납 가능"})
    if position.planned_stop_price:
        levels.append({"type": "invalidation", "price": position.planned_stop_price, "meaning": "사용자가 기록한 손절/무효화 기준"})
    if position.planned_take_profit_price:
        levels.append({"type": "take_profit", "price": position.planned_take_profit_price, "meaning": "사용자가 기록한 수익 실현 기준"})
    return levels


def _support_status_from_levels(close: float, levels: list[dict]) -> str:
    supports = [level for level in levels if isinstance(level, dict) and isinstance(level.get("price"), (int, float))]
    if not supports:
        return "unknown"
    nearest = min(supports, key=lambda level: abs(close - level["price"]))
    price = float(nearest["price"])
    if close < price:
        return "broken"
    if abs(close - price) / close <= 0.01:
        return "near"
    return "holding"


def _resistance_status_from_levels(close: float, levels: list[dict]) -> str:
    resistances = [level for level in levels if isinstance(level, dict) and isinstance(level.get("price"), (int, float))]
    if not resistances:
        return "unknown"
    nearest = min(resistances, key=lambda level: abs(close - level["price"]))
    price = float(nearest["price"])
    if close > price:
        return "broken"
    if abs(close - price) / close <= 0.01:
        return "testing"
    return "not_near"


def _reason_codes(status: str, position: Position, report: Report, liquidation_distance: float | None, thesis_delta: int, drawdown_from_peak: float) -> list[str]:
    codes = [f"STATUS_{status.upper()}"]
    codes.append("POSITION_PNL_POSITIVE" if position.pnl_percent >= 0 else "POSITION_PNL_NEGATIVE")
    if liquidation_distance is None:
        codes.append("LIQUIDATION_DISTANCE_UNKNOWN")
        if position.status == "open" and position.liquidation_price is None and position.leverage >= 5:
            codes.append("LIQUIDATION_PRICE_MISSING_HIGH_LEVERAGE")
    elif liquidation_distance >= 15:
        codes.append("LIQUIDATION_DISTANCE_SAFE")
    elif liquidation_distance < 10:
        codes.append("LIQUIDATION_DISTANCE_NARROW")
    if thesis_delta < -20:
        codes.extend(["DIRECTION_THESIS_DROP_OVER_20", "SCORE_DROP_OVER_20"])
    elif thesis_delta < -10:
        codes.extend(["DIRECTION_THESIS_DROP_OVER_10", "SCORE_DROP_OVER_10"])
    if report.scores.risk > 65:
        codes.append("RISK_SCORE_HIGH")
    if report.scores.fomo > 70:
        codes.append("FOMO_INDEX_HIGH")
    if drawdown_from_peak > 50:
        codes.append("PROFIT_GIVEBACK_OVER_50")
    return codes


def _severity_from_status(status: str) -> str:
    if status == "critical":
        return "critical"
    if status in {"risk_rising", "thesis_weakening"}:
        return "high"
    if status == "watch":
        return "medium"
    return "low"


def _format_pct(value: float | None) -> str:
    return "데이터 부족" if value is None else f"{value:.2f}%"


def _render_critical_levels(levels: list[dict]) -> str:
    if not levels:
        return "중요 가격대 데이터가 충분하지 않습니다."
    return "\n".join([f"- {level['type']}: {level['price']} ({level['meaning']})" for level in levels[:4]])


def _position_as_of(position: Position, report: Report):
    if position.mark_price is not None and position.synced_at is not None:
        return position.synced_at
    if report.data_quality.last_candle_at is not None:
        return report.data_quality.last_candle_at
    return report.created_at
