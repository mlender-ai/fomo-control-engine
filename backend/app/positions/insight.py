from __future__ import annotations

from typing import Any

from app.db.models import Position, PositionInsight, PositionSnapshot


class PositionInsightConfigError(RuntimeError):
    """Raised when an external LLM insight provider is requested but not configured."""


def require_openai_api_key(api_key: str | None) -> None:
    if not api_key or not api_key.strip():
        raise PositionInsightConfigError("OpenAI API key is not configured. Set FCE_OPENAI_API_KEY or OPENAI_API_KEY.")


def build_position_insight_input(
    position: Position,
    snapshot: PositionSnapshot,
    chart_analysis: dict[str, Any],
    snapshots: list[PositionSnapshot],
    previous_insight: PositionInsight | None = None,
) -> dict[str, Any]:
    analysis = snapshot.analysis_json
    position_analysis = analysis.get("position_analysis", {})
    technical = analysis.get("technical", {})
    wyckoff = analysis.get("wyckoff", {})
    risk = analysis.get("risk", {})
    previous_snapshot = _previous_snapshot(snapshot, snapshots)
    entry_snapshot = _entry_snapshot(snapshot, snapshots)
    price_levels = chart_analysis.get("price_levels", {})
    support = _first_level(price_levels.get("support"))
    resistance = _first_level(price_levels.get("resistance"))
    invalidation = _first_level(price_levels.get("invalidation"))
    volume_profile = chart_analysis.get("volume_profile", {})
    volume_xray = chart_analysis.get("volume_xray", {})
    mark_price = snapshot.mark_price or chart_analysis.get("mark_price") or position.current_price or position.mark_price
    poc_price = volume_profile.get("poc_price")

    return {
        "position": {
            "symbol": position.symbol,
            "direction": position.direction.value,
            "leverage": position.leverage,
            "entry_price": position.entry_price,
            "mark_price": mark_price,
            "liquidation_price": position.liquidation_price,
            "pnl_percent": snapshot.pnl_percent,
            "pnl_amount": snapshot.pnl_amount,
            "liquidation_distance_pct": snapshot.liquidation_distance_pct,
        },
        "health": {
            "health_score": snapshot.health_score,
            "status_label": snapshot.status_label,
            "previous_health_score": previous_snapshot.health_score if previous_snapshot else None,
            "entry_health_score": entry_snapshot.health_score if entry_snapshot else None,
            "score_change": position_analysis.get("score_change", 0),
            "risk_score": snapshot.risk_score,
        },
        "chart": {
            "trend": technical.get("trend"),
            "support_status": technical.get("support_status"),
            "resistance_status": technical.get("resistance_status"),
            "critical_support": support.get("price") if support else _level_from_risk(risk, "support"),
            "critical_resistance": resistance.get("price") if resistance else _level_from_risk(risk, "resistance"),
            "invalidation_price": invalidation.get("price") if invalidation else position.planned_stop_price,
        },
        "wyckoff": {
            "phase_hint": wyckoff.get("phase_hint"),
            "accumulation_score": wyckoff.get("accumulation_score"),
            "distribution_score": wyckoff.get("distribution_score"),
            "spring_candidate": wyckoff.get("spring_candidate", False),
            "sos_candidate": wyckoff.get("sos_candidate", False),
            "lps_candidate": wyckoff.get("lps_candidate", False),
            "comment": wyckoff.get("structure_comment", ""),
        },
        "technical": {
            "rsi_state": technical.get("rsi_state"),
            "macd_state": technical.get("macd_state"),
            "bollinger_state": technical.get("bollinger_state"),
            "volume_state": volume_xray.get("volume_state") or technical.get("volume_state"),
            "relative_volume": volume_xray.get("relative_volume"),
        },
        "volume_profile": {
            "poc_price": poc_price,
            "value_area_high": volume_profile.get("value_area_high"),
            "value_area_low": volume_profile.get("value_area_low"),
            "current_position_vs_poc": _position_vs_poc(mark_price, poc_price),
            "method": volume_profile.get("method", "estimated_ohlcv_proxy"),
        },
        "entry_context": {
            "entry_memo": position.entry_memo or position.memo,
            "entry_score": position.entry_score if position.entry_score is not None else position_analysis.get("entry_score"),
            "entry_reason_codes": _reason_codes(entry_snapshot) if entry_snapshot else [],
        },
        "reason_codes": analysis.get("reason_codes", []),
        "previous_insight": {
            "created_at": previous_insight.created_at.isoformat() if previous_insight else None,
            "status_label": previous_insight.status_label if previous_insight else None,
            "health_score": previous_insight.health_score if previous_insight else None,
        },
    }


def make_ai_position_insight(
    position: Position,
    snapshot: PositionSnapshot,
    input_json: dict[str, Any],
    previous_insight: PositionInsight | None = None,
) -> PositionInsight:
    return PositionInsight(
        position_id=position.id,
        snapshot_id=snapshot.id,
        insight_type="position_status",
        health_score=snapshot.health_score,
        status_label=snapshot.status_label,
        input_json=input_json,
        insight_text=render_ai_position_insight(input_json, previous_insight),
    )


def render_ai_position_insight(input_json: dict[str, Any], previous_insight: PositionInsight | None = None) -> str:
    position = input_json["position"]
    health = input_json["health"]
    chart = input_json["chart"]
    wyckoff = input_json["wyckoff"]
    technical = input_json["technical"]
    volume_profile = input_json["volume_profile"]
    entry_context = input_json["entry_context"]
    direction = str(position["direction"]).upper()
    symbol = position["symbol"]
    previous_line = _health_change_line(health)
    risk_line = _risk_line(position, health)
    chart_line = _chart_line(position["direction"], chart)
    wyckoff_line = _wyckoff_line(wyckoff, technical, volume_profile)
    entry_line = _entry_line(entry_context, health, previous_insight)
    price_line = _price_line(position["direction"], chart)
    opinion = _opinion_line(health, technical, chart)

    return (
        f"📍 {symbol} {direction} 포지션 상태\n\n"
        f"현재 상태:\n현재 포지션은 {health['status_label']} 상태입니다. Health Score는 {health['health_score']}/100입니다. {previous_line}\n\n"
        f"수익/리스크:\n{risk_line}\n\n"
        f"차트 구조:\n{chart_line}\n\n"
        f"와이코프/기술적 분석:\n{wyckoff_line}\n\n"
        f"진입 논리:\n{entry_line}\n\n"
        f"주의할 가격:\n{price_line}\n\n"
        f"제 의견:\n{opinion} 이 문장은 매수/매도 지시가 아닙니다. 최종 판단은 사용자가 정한 손절 기준, 수익 반납 기준, 다음 캔들의 반응을 함께 보고 내려야 합니다."
    )


def _previous_snapshot(snapshot: PositionSnapshot, snapshots: list[PositionSnapshot]) -> PositionSnapshot | None:
    ordered = sorted(snapshots, key=lambda item: item.created_at, reverse=True)
    for candidate in ordered:
        if candidate.id != snapshot.id and candidate.created_at <= snapshot.created_at:
            return candidate
    return None


def _entry_snapshot(snapshot: PositionSnapshot, snapshots: list[PositionSnapshot]) -> PositionSnapshot | None:
    ordered = sorted([item for item in snapshots if item.id != snapshot.id], key=lambda item: item.created_at)
    return ordered[0] if ordered else None


def _first_level(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def _level_from_risk(risk: dict[str, Any], level_type: str) -> float | None:
    for level in risk.get("critical_levels", []):
        if isinstance(level, dict) and level.get("type") == level_type:
            return level.get("price")
    return None


def _position_vs_poc(mark_price: float | None, poc_price: float | None) -> str:
    if mark_price is None or poc_price is None:
        return "unknown"
    if abs(mark_price - poc_price) / poc_price < 0.003:
        return "near"
    return "above" if mark_price > poc_price else "below"


def _reason_codes(snapshot: PositionSnapshot) -> list[str]:
    codes = snapshot.analysis_json.get("reason_codes", [])
    return codes if isinstance(codes, list) else []


def _format_price(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return str(value)


def _format_pct(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):+.2f}%"


def _health_change_line(health: dict[str, Any]) -> str:
    previous = health.get("previous_health_score") or health.get("entry_health_score")
    if previous is None:
        return "이전 기준점이 부족해 현재 snapshot 중심으로 판단합니다."
    delta = health["health_score"] - previous
    if delta < 0:
        return f"이전 기준보다 Health가 {abs(delta)}점 낮아져 진입 논리의 힘이 약해졌는지 확인해야 합니다."
    if delta > 0:
        return f"이전 기준보다 Health가 {delta}점 높아져 상태는 개선된 편입니다."
    return "이전 기준과 Health Score 변화는 크지 않습니다."


def _risk_line(position: dict[str, Any], health: dict[str, Any]) -> str:
    liquidation = position.get("liquidation_price")
    liquidation_distance = position.get("liquidation_distance_pct")
    if liquidation is None or liquidation_distance is None:
        liquidation_text = "청산가 또는 청산가 거리 데이터가 없어 거래소 리스크 값을 별도로 확인해야 합니다."
    else:
        liquidation_text = f"청산가는 {_format_price(liquidation)}이고, 현재가 대비 거리는 {_format_pct(liquidation_distance)}입니다."
    return (
        f"현재 PnL은 {_format_pct(position.get('pnl_percent'))}"
        f"{' / ' + _format_price(position.get('pnl_amount')) + ' USDT' if position.get('pnl_amount') is not None else ''}입니다. "
        f"{liquidation_text} 리스크 점수는 {health.get('risk_score')}/100입니다."
    )


def _chart_line(direction: str, chart: dict[str, Any]) -> str:
    support = chart.get("critical_support")
    resistance = chart.get("critical_resistance")
    trend = chart.get("trend") or "unknown"
    if direction == "long":
        focus = f"롱 기준으로는 {_format_price(support)} 지지 유지가 가장 중요합니다."
    else:
        focus = f"숏 기준으로는 {_format_price(resistance)} 저항 유지가 가장 중요합니다."
    return (
        f"현재 추세는 {trend}로 분류됩니다. 지지 상태는 {chart.get('support_status')}, "
        f"저항 상태는 {chart.get('resistance_status')}입니다. {focus}"
    )


def _wyckoff_line(wyckoff: dict[str, Any], technical: dict[str, Any], volume_profile: dict[str, Any]) -> str:
    marker_text = []
    if wyckoff.get("spring_candidate"):
        marker_text.append("Spring 후보가 있습니다. Spring은 지지 이탈 후 빠르게 회복되는지 보는 신호입니다.")
    if wyckoff.get("sos_candidate"):
        marker_text.append("SOS 후보가 있습니다. SOS는 수요가 강하게 들어오는지 보는 후보 신호입니다.")
    if wyckoff.get("lps_candidate"):
        marker_text.append("LPS 후보가 있습니다. LPS는 지지 재확인 구간인지 보는 후보 신호입니다.")
    markers = " ".join(marker_text) if marker_text else "확정적인 와이코프 신호로 단정하지 않습니다."
    return (
        f"와이코프 phase hint는 {wyckoff.get('phase_hint')}입니다. "
        f"Accumulation {wyckoff.get('accumulation_score')}, Distribution {wyckoff.get('distribution_score')}입니다. "
        f"{markers} RSI는 {technical.get('rsi_state')}, MACD는 {technical.get('macd_state')}, "
        f"볼린저는 {technical.get('bollinger_state')}, 거래량은 {technical.get('volume_state')}입니다. "
        f"Estimated Volume Profile 기준 POC는 {_format_price(volume_profile.get('poc_price'))}이고 현재가는 POC {volume_profile.get('current_position_vs_poc')}에 있습니다."
    )


def _entry_line(entry_context: dict[str, Any], health: dict[str, Any], previous_insight: PositionInsight | None) -> str:
    memo = entry_context.get("entry_memo")
    entry_score = entry_context.get("entry_score")
    score_change = health.get("score_change")
    base = f"진입 당시 점수는 {entry_score}점, 현재 변화폭은 {score_change:+d}점입니다." if isinstance(score_change, int) else f"진입 당시 점수는 {entry_score}점입니다."
    memo_line = f"진입 메모는 “{memo}”입니다." if memo else "진입 메모가 없어 점수와 차트 구조 중심으로만 비교합니다."
    previous = " 직전 인사이트 이후 최신 데이터 기준으로 다시 점검했습니다." if previous_insight else ""
    return f"{base} {memo_line}{previous}"


def _price_line(direction: str, chart: dict[str, Any]) -> str:
    support = chart.get("critical_support")
    resistance = chart.get("critical_resistance")
    invalidation = chart.get("invalidation_price")
    if direction == "long":
        return f"{_format_price(support)} 지지를 이탈하면 진입 논리가 약해질 수 있습니다. 무효화 기준은 {_format_price(invalidation)}입니다. 저항은 {_format_price(resistance)} 부근입니다."
    return f"{_format_price(resistance)} 저항을 돌파하면 숏 진입 논리가 약해질 수 있습니다. 무효화 기준은 {_format_price(invalidation)}입니다. 지지는 {_format_price(support)} 부근입니다."


def _opinion_line(health: dict[str, Any], technical: dict[str, Any], chart: dict[str, Any]) -> str:
    status = str(health.get("status_label", "포지션 점검 필요"))
    volume = str(technical.get("volume_state", "unknown"))
    if "긴급" in status or "약화" in status:
        return f"지금은 {status} 구간으로 보고, 가격 라인과 거래량 변화를 우선 점검하는 편이 좋습니다. 거래량 상태는 {volume}입니다."
    if "리스크" in status:
        return f"리스크가 커지는 구간입니다. 지지/저항 반응과 무효화 가격 {_format_price(chart.get('invalidation_price'))}을 먼저 확인해야 합니다."
    return f"현재는 급하게 결론내리기보다 {status} 관점에서 다음 지지/저항 반응을 확인하는 구간입니다."
