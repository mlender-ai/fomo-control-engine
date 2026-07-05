"""진입 시뮬레이터 (WO-FCE-13): 결정론 계산만. 주문 API 연동 없음(read-only).

추정 청산가는 거래소 실제 계산과 다를 수 있으며 항상 "추정(산식 기준)"으로 표기한다.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.db.models import Direction, Position, PositionSnapshot, utc_now
from app.positions.action_plan import build_action_plan

DEFAULT_MMR = 0.005  # Bitget 티어값 미수신 시 보수적 기본 유지증거금률
FEE_BUFFER = 0.0006  # 청산 근처 수수료/펀딩 버퍼
RR_MIN = 1.5
LEVEL_SCORE_MIN = 55
FUNDING_ADVERSE_THRESHOLD = 0.0005  # 방향에 불리한 펀딩 임계 (config 대체 가능)


def estimate_liquidation(entry_price: float, leverage: float, direction: str, mmr: float | None = None) -> float | None:
    """격리 마진 기준 추정 청산가.

    long:  entry × (1 - (1/lev) + mmr + fee)
    short: entry × (1 + (1/lev) - mmr - fee)
    """
    if entry_price <= 0 or leverage <= 0:
        return None
    rate = mmr if mmr is not None else DEFAULT_MMR
    margin_fraction = 1.0 / leverage
    if direction == "long":
        price = entry_price * (1 - margin_fraction + rate + FEE_BUFFER)
    else:
        price = entry_price * (1 + margin_fraction - rate - FEE_BUFFER)
    return round(max(price, 0.0), 8)


def _distance_pct(price: float | None, entry: float, direction: str) -> float | None:
    if price is None or entry <= 0:
        return None
    side = 1 if direction == "long" else -1
    return round(((price - entry) / entry) * 100 * side, 2)


def simulate_entry(
    *,
    symbol: str,
    direction: str,
    entry_price: float,
    leverage: float,
    margin_usdt: float | None,
    margin_mode: str,
    chart_analysis: dict[str, Any],
    mmr: float | None,
    direction_score: int | None,
) -> dict[str, Any]:
    """가상 진입 시뮬레이션 결과 (액션 플랜 프리뷰 + R:R + 생존 마진 + 체크리스트)."""
    direction_enum = Direction.long if direction == "long" else Direction.short

    liquidation = estimate_liquidation(entry_price, leverage, direction, mmr)

    # 무효화·익절 후보는 실제 action_plan을 hypothetical position_context로 재사용.
    hypo = Position(
        id=uuid4(),
        symbol=symbol,
        direction=direction_enum,
        entry_price=entry_price,
        quantity=(margin_usdt * leverage / entry_price) if (margin_usdt and entry_price > 0) else 1.0,
        leverage=leverage,
        mark_price=entry_price,
        liquidation_price=liquidation,
    )
    hypo_snapshot = PositionSnapshot(
        position_id=hypo.id,
        symbol=symbol,
        as_of=utc_now(),
        mark_price=entry_price,
        pnl_percent=0.0,
        health_score=50,
        status_label="시뮬레이션",
        risk_score=50,
        score_json={},
        analysis_json={},
    )
    action_plan = build_action_plan(hypo, hypo_snapshot, chart_analysis)

    invalidation = action_plan.get("invalidation") or action_plan.get("engine_invalidation")
    invalidation_price = invalidation.get("price") if isinstance(invalidation, dict) else None
    invalidation_distance = _distance_pct(invalidation_price, entry_price, direction)

    take_profit = [item for item in action_plan.get("take_profit", []) if isinstance(item, dict)]
    first_tp = take_profit[0] if take_profit else None
    tp_distance = _distance_pct(first_tp.get("price"), entry_price, direction) if first_tp else None

    rr_ratio = None
    loss_usdt = None
    profit_usdt = None
    if invalidation_distance is not None and tp_distance is not None and invalidation_distance < 0 and tp_distance > 0:
        rr_ratio = round(tp_distance / abs(invalidation_distance), 2)
        if margin_usdt:
            # 손익 = 증거금 × 레버리지 × 가격변화율
            loss_usdt = round(margin_usdt * leverage * abs(invalidation_distance) / 100, 2)
            profit_usdt = round(margin_usdt * leverage * tp_distance / 100, 2)

    liq_distance = _distance_pct(liquidation, entry_price, direction)
    # 생존 마진: 무효화가 청산보다 entry에 가까워야(안쪽) 손절이 먼저 걸린다.
    survives = None
    if invalidation_distance is not None and liq_distance is not None:
        survives = abs(invalidation_distance) < abs(liq_distance)

    mtf = _mtf_from_analysis(chart_analysis)
    htf_conflict = mtf.get("alignment") == "conflicting"

    checklist = _build_checklist(
        rr_ratio=rr_ratio,
        survives=survives,
        invalidation=invalidation if isinstance(invalidation, dict) else None,
        htf_conflict=htf_conflict,
        funding_rate=chart_analysis.get("funding_rate"),
        direction=direction,
        volume_state=chart_analysis.get("volume_xray", {}).get("volume_state"),
    )
    passed = sum(1 for item in checklist if item["status"] == "pass")
    total = sum(1 for item in checklist if item["status"] != "na")

    return {
        "symbol": symbol.upper(),
        "direction": direction,
        "entry_price": entry_price,
        "leverage": leverage,
        "margin_usdt": margin_usdt,
        "margin_mode": margin_mode,
        "estimated_liquidation": liquidation,
        "estimated_liquidation_distance_pct": liq_distance,
        "mmr_used": mmr if mmr is not None else DEFAULT_MMR,
        "mmr_source": "exchange" if mmr is not None else "default",
        "liquidation_formula": "격리 기준 추정: entry × (1 ∓ 1/lev ± MMR ± 수수료버퍼). 실제 청산가는 거래소 계산과 다를 수 있음",
        "action_plan": action_plan,
        "invalidation_distance_pct": invalidation_distance,
        "first_take_profit_distance_pct": tp_distance,
        "rr_ratio": rr_ratio,
        "loss_usdt": loss_usdt,
        "profit_usdt": profit_usdt,
        "survives_to_invalidation": survives,
        "direction_score": direction_score,
        "mtf": mtf,
        "htf_conflict": htf_conflict,
        "checklist": checklist,
        "checklist_passed": passed,
        "checklist_total": total,
        "verdict_line": _verdict_line(rr_ratio, invalidation_distance, liq_distance, htf_conflict),
    }


def _mtf_from_analysis(chart_analysis: dict[str, Any]) -> dict[str, Any]:
    mtf = chart_analysis.get("wyckoff_mtf")
    if isinstance(mtf, dict):
        return mtf
    wyckoff = chart_analysis.get("wyckoff")
    if isinstance(wyckoff, dict) and isinstance(wyckoff.get("mtf"), dict):
        return wyckoff["mtf"]
    return {"htf_phase": None, "htf_trend": None, "alignment": "neutral"}


def _build_checklist(
    *,
    rr_ratio: float | None,
    survives: bool | None,
    invalidation: dict[str, Any] | None,
    htf_conflict: bool,
    funding_rate: Any,
    direction: str,
    volume_state: Any,
) -> list[dict[str, str]]:
    checklist: list[dict[str, str]] = []

    if rr_ratio is None:
        checklist.append(_item("rr", f"손익비 R:R ≥ {RR_MIN}", "na", "무효화 또는 익절 후보가 없어 R:R 계산 불가"))
    elif rr_ratio >= RR_MIN:
        checklist.append(_item("rr", f"손익비 R:R ≥ {RR_MIN}", "pass", f"R:R {rr_ratio}"))
    else:
        checklist.append(_item("rr", f"손익비 R:R ≥ {RR_MIN}", "fail", f"R:R {rr_ratio} — 보상 대비 위험이 큼"))

    if survives is None:
        checklist.append(_item("survival", "무효화가 추정 청산보다 안쪽", "na", "무효화 또는 청산 추정 불가"))
    elif survives:
        checklist.append(_item("survival", "무효화가 추정 청산보다 안쪽", "pass", "손절이 청산보다 먼저 걸림"))
    else:
        checklist.append(_item("survival", "무효화가 추정 청산보다 안쪽", "fail", "손절 계획이 청산보다 늦음: 레버리지 과다"))

    level_score = invalidation.get("score") if invalidation else None
    if invalidation is None or invalidation.get("source") == "user":
        note = "사용자 지정 손절" if invalidation and invalidation.get("source") == "user" else "구조 레벨 무효화 없음"
        checklist.append(_item("level_score", f"무효화 근거 레벨 점수 ≥ {LEVEL_SCORE_MIN}", "na", note))
    elif isinstance(level_score, (int, float)) and level_score >= LEVEL_SCORE_MIN:
        checklist.append(_item("level_score", f"무효화 근거 레벨 점수 ≥ {LEVEL_SCORE_MIN}", "pass", f"레벨 점수 {int(level_score)}"))
    else:
        score_text = int(level_score) if isinstance(level_score, (int, float)) else "-"
        checklist.append(_item("level_score", f"무효화 근거 레벨 점수 ≥ {LEVEL_SCORE_MIN}", "fail", f"레벨 점수 {score_text} — 근거가 약함"))

    if htf_conflict:
        checklist.append(_item("htf", "상위 TF 국면과 방향 비충돌", "fail", "상위 시간프레임 국면이 진입 방향과 충돌"))
    else:
        checklist.append(_item("htf", "상위 TF 국면과 방향 비충돌", "pass", "상위 TF 충돌 없음"))

    funding = _to_float(funding_rate)
    if funding is None:
        checklist.append(_item("funding", "펀딩이 방향에 극단적으로 불리하지 않음", "na", "펀딩 데이터 없음"))
    else:
        adverse = (direction == "long" and funding >= FUNDING_ADVERSE_THRESHOLD) or (direction == "short" and funding <= -FUNDING_ADVERSE_THRESHOLD)
        if adverse:
            checklist.append(_item("funding", "펀딩이 방향에 극단적으로 불리하지 않음", "fail", f"펀딩 {funding:.4%}가 {'롱' if direction == 'long' else '숏'}에 불리"))
        else:
            checklist.append(_item("funding", "펀딩이 방향에 극단적으로 불리하지 않음", "pass", f"펀딩 {funding:.4%}"))

    if volume_state == "drying_up":
        checklist.append(_item("volume", "거래량이 고갈 상태 아님", "fail", "거래량 고갈 — 돌파/이탈 신뢰도 낮음"))
    elif volume_state in (None, "data_unavailable"):
        checklist.append(_item("volume", "거래량이 고갈 상태 아님", "na", "거래량 데이터 부족"))
    else:
        checklist.append(_item("volume", "거래량이 고갈 상태 아님", "pass", ""))

    return checklist


def _item(key: str, label: str, status: str, reason: str) -> dict[str, str]:
    return {"key": key, "label": label, "status": status, "reason": reason}


def _verdict_line(rr: float | None, invalidation_distance: float | None, liq_distance: float | None, htf_conflict: bool) -> str:
    parts = []
    parts.append(f"R:R {rr}" if rr is not None else "R:R -")
    parts.append(f"무효화 {invalidation_distance:.1f}%" if invalidation_distance is not None else "무효화 -")
    parts.append(f"추정 청산 {liq_distance:.1f}%" if liq_distance is not None else "추정 청산 -")
    parts.append("상위TF 충돌" if htf_conflict else "상위TF 충돌 없음")
    return "이 셋업: " + " · ".join(parts)


def _to_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None
