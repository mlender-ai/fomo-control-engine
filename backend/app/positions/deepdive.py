from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timezone
import math
from statistics import pstdev
from typing import Any

from app.db.models import Position


SOURCE_BADGES = {
    "bitget": "Bitget",
    "toss": "Toss",
    "position": "내 포지션",
}


def build_position_deepdive(
    position: Position,
    raw_analysis: dict[str, Any],
    joined_analysis: dict[str, Any],
    heatmap: dict[str, Any],
    action_plan: dict[str, Any],
    entry_snapshot: dict[str, Any],
    *,
    ledger: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    effective_action_plan = _with_snapshot_invalidation(action_plan, entry_snapshot)
    joined = joined_analysis.get("underlying_join") if isinstance(joined_analysis.get("underlying_join"), dict) else {}
    if joined.get("status") != "joined":
        return {
            "status": "unavailable",
            "position_id": str(position.id),
            "symbol": position.symbol,
            "as_of": now.isoformat(),
            "reason": joined.get("reason") or "검증된 Toss 기초자산 조인이 필요합니다.",
            "entry_snapshot": entry_snapshot,
            "cross_signals": [],
            "risk": _position_risk(position, raw_analysis, effective_action_plan, []),
            "ledger": ledger,
        }

    basis = _basis_signal(raw_analysis, joined, now)
    momentum = _funding_momentum_signal(raw_analysis, joined)
    shelf = _liquidation_shelf_signal(position, heatmap, effective_action_plan)
    flow = _flow_alignment_signal(position, joined)
    leverage = _leverage_stack_signal(position, joined)
    signals = [basis, momentum, shelf, flow, leverage]
    thesis = _thesis_comparison(position, entry_snapshot, raw_analysis, effective_action_plan, signals)
    risk = _position_risk(position, raw_analysis, effective_action_plan, signals)
    return {
        "status": "ready",
        "position_id": str(position.id),
        "symbol": position.symbol,
        "underlying": {
            "symbol": joined.get("toss_symbol"),
            "name": joined.get("underlying_name"),
            "exchange": joined.get("toss_exchange"),
            "kind": joined.get("underlying_kind"),
            "market_state": joined.get("market_state"),
            "stale": bool(joined.get("stale")),
        },
        "as_of": now.isoformat(),
        "truth_policy": "서로 다른 출처에서 동시에 관측된 값만 교차신호로 표시하며, 주문 판단에는 사용하지 않습니다.",
        "entry_snapshot": entry_snapshot,
        "thesis": thesis,
        "cross_signals": signals,
        "risk": risk,
        "ledger": ledger,
    }


def build_entry_snapshot_claim(
    position: Position,
    raw_analysis: dict[str, Any],
    joined_analysis: dict[str, Any],
    *,
    captured_at: datetime,
) -> dict[str, Any]:
    joined = joined_analysis.get("underlying_join") if isinstance(joined_analysis.get("underlying_join"), dict) else {}
    toss_candles = _toss_candles(joined)
    last_toss_close = toss_candles[-1][1] if toss_candles else None
    funding = _funding_rate(raw_analysis)
    thesis = (position.thesis_text or position.entry_memo or "").strip()
    if thesis:
        thesis_source = "user"
    else:
        thesis = _automatic_thesis(position, raw_analysis)
        thesis_source = "automatic_first_observation"
    return {
        "capture_policy": "first_observed_proxy",
        "opened_at": position.opened_at.isoformat(),
        "captured_at": captured_at.isoformat(),
        "capture_note": "실제 진입 시점 원본이 없어 최초 심화 관측 상태를 별도로 보존했습니다.",
        "entry": {
            "price": position.entry_price,
            "direction": position.direction.value,
            "quantity": position.quantity,
            "perpetual_leverage": position.leverage,
        },
        "structure": {
            "overall_stance": _overall_stance(raw_analysis),
            "mark_price": _number(raw_analysis.get("mark_price")),
            "invalidation": _plan_price(raw_analysis.get("price_levels"), "invalidation"),
        },
        "derivatives": {
            "funding_rate": funding,
            "open_interest": _open_interest(raw_analysis),
        },
        "underlying": {
            "symbol": joined.get("toss_symbol"),
            "close": last_toss_close,
            "basis_pct": _number(joined.get("basis_pct")),
            "market_state": joined.get("market_state"),
        },
        "thesis": {"text": thesis, "source": thesis_source},
    }


def _with_snapshot_invalidation(action_plan: dict[str, Any], entry_snapshot: dict[str, Any]) -> dict[str, Any]:
    if _action_price(action_plan.get("invalidation")) is not None or _action_price(action_plan.get("engine_invalidation")) is not None:
        return action_plan
    structure = entry_snapshot.get("structure") if isinstance(entry_snapshot.get("structure"), dict) else {}
    price = _number(structure.get("invalidation"))
    if price is None:
        return action_plan
    effective = dict(action_plan)
    effective["invalidation"] = {
        "price": price,
        "source": "first_observed_snapshot",
        "note": "최초 심화 관측 스냅샷의 구조 무효화선",
    }
    return effective


def deepdive_judgment_claim(payload: dict[str, Any], position: Position, mark_price: float) -> dict[str, Any]:
    reading = payload.get("risk", {}).get("market_reading", {})
    stance = str(reading.get("stance") or "insufficient")
    expected_move = "up" if stance == "up" else "down" if stance == "down" else None
    active_signals = [
        {
            "id": item.get("id"),
            "label": item.get("label"),
            "status": item.get("status"),
            "reading": item.get("reading"),
            "sources": item.get("sources"),
            "data": item.get("data"),
        }
        for item in payload.get("cross_signals", [])
        if isinstance(item, dict)
    ]
    return {
        "position_id": str(position.id),
        "position_symbol": position.symbol,
        "direction": position.direction.value,
        "price": mark_price,
        "expected_move": expected_move,
        "stance": stance,
        "cross_signals": active_signals,
        "policy": "observation_only",
    }


def _basis_signal(raw_analysis: dict[str, Any], joined: dict[str, Any], now: datetime) -> dict[str, Any]:
    series = _basis_series(raw_analysis, joined)
    current_basis = _number(joined.get("basis_pct"))
    if current_basis is not None:
        point = {"time": int(now.timestamp()), "value": round(current_basis, 4), "kind": "live_observation"}
        if not series or series[-1]["time"] != point["time"]:
            series.append(point)
    if not series:
        return _unavailable_signal(
            "basis_behavior",
            "베이시스 행동",
            ["bitget", "toss"],
            "퍼페추얼과 검증된 기초자산 가격이 모두 있어야 같은 시점의 괴리를 계산할 수 있습니다.",
            "동기화된 가격 표본이 없습니다.",
        )
    signed_change = series[-1]["value"] - series[0]["value"] if len(series) > 1 else None
    width_change = abs(series[-1]["value"]) - abs(series[0]["value"]) if len(series) > 1 else None
    elapsed_days = (series[-1]["time"] - series[0]["time"]) / 86_400 if len(series) > 1 else None
    velocity = width_change / elapsed_days if width_change is not None and elapsed_days and elapsed_days > 0 else None
    if width_change is None:
        reading = "현재 괴리 관측"
        state = "observed"
    elif width_change > 0.25:
        reading = "음(-) 괴리폭 확대 관측" if series[-1]["value"] < 0 else "양(+) 괴리폭 확대 관측"
        state = "expanding"
    elif width_change < -0.25:
        reading = "괴리폭 축소 관측"
        state = "contracting"
    else:
        reading = "괴리폭 변화 제한"
        state = "stable"
    change_detail = f" · 괴리폭 {width_change:+.2f}%p" if width_change is not None else " · 변화율 표본 축적 중"
    if velocity is not None:
        change_detail += f" · 일환산 {velocity:+.2f}%p"
    return _signal(
        "basis_behavior",
        "베이시스 행동",
        ["bitget", "toss"],
        "퍼페추얼과 검증된 기초자산 가격이 모두 있어야 같은 시점의 괴리를 계산할 수 있습니다.",
        reading,
        f"현재 {series[-1]['value']:+.2f}%" + change_detail,
        {
            "state": state,
            "current_pct": series[-1]["value"],
            "width_change_pct_points": round(width_change, 4) if width_change is not None else None,
            "signed_change_pct_points": round(signed_change, 4) if signed_change is not None else None,
            "velocity_pct_points_per_day": round(velocity, 4) if velocity is not None else None,
            "sparkline": series[-48:],
        },
    )


def _funding_momentum_signal(raw_analysis: dict[str, Any], joined: dict[str, Any]) -> dict[str, Any]:
    funding = _funding_rate(raw_analysis)
    closes = [close for _time, close in _toss_candles(joined)]
    if funding is None or len(closes) < 6:
        missing = []
        if funding is None:
            missing.append("Bitget 펀딩")
        if len(closes) < 6:
            missing.append("Toss 확정 일봉 6개")
        return _unavailable_signal(
            "funding_momentum_divergence",
            "펀딩 × 기초 모멘텀",
            ["bitget", "toss"],
            "퍼페추얼 펀딩과 검증된 기초자산의 확정 일봉 모멘텀을 동시에 비교해야 합니다.",
            f"{', '.join(missing)} 부족",
        )
    momentum = (closes[-1] / closes[-6] - 1) * 100
    divergent = (funding > 0 and momentum < 0) or (funding < 0 and momentum > 0)
    if divergent:
        reading = "쏠림과 기초 모멘텀 불일치 관측"
        state = "divergent"
    else:
        reading = "쏠림과 기초 모멘텀 같은 방향 관측"
        state = "aligned"
    return _signal(
        "funding_momentum_divergence",
        "펀딩 × 기초 모멘텀",
        ["bitget", "toss"],
        "퍼페추얼 펀딩과 검증된 기초자산의 확정 일봉 모멘텀을 동시에 비교해야 합니다.",
        reading,
        f"펀딩 {funding:+.4%} · 기초 5일 {momentum:+.2f}%",
        {"state": state, "funding_rate": funding, "underlying_momentum_5d_pct": round(momentum, 3)},
    )


def _liquidation_shelf_signal(position: Position, heatmap: dict[str, Any], action_plan: dict[str, Any]) -> dict[str, Any]:
    zones = [item for item in heatmap.get("top_zones", []) if isinstance(item, dict) and _number(item.get("price_mid")) is not None]
    if not zones:
        return _unavailable_signal(
            "liquidation_shelf",
            "청산 선반 상대 위치",
            ["bitget", "position"],
            "Bitget 실현 청산 밀집대에 내 진입가와 무효화선을 겹쳐야 상대 위치가 생깁니다.",
            "선택 구간의 실현 청산 밀집대 표본이 없습니다.",
        )
    shelf = min(zones, key=lambda item: abs(float(item["price_mid"]) - position.entry_price))
    shelf_price = float(shelf["price_mid"])
    relation = _relative_price(position.entry_price, float(shelf.get("price_low") or shelf_price), float(shelf.get("price_high") or shelf_price))
    invalidation = _action_price(action_plan.get("invalidation")) or _action_price(action_plan.get("engine_invalidation"))
    invalidation_relation = (
        _relative_price(invalidation, float(shelf.get("price_low") or shelf_price), float(shelf.get("price_high") or shelf_price)) if invalidation else None
    )
    reading = f"진입가는 청산 선반 {relation}"
    detail = f"선반 {shelf_price:,.2f} · 실현 추정 명목 ${float(shelf.get('total_usd_estimated') or 0):,.0f}"
    if invalidation_relation:
        detail += f" · 무효화선 {invalidation_relation}"
    return _signal(
        "liquidation_shelf",
        "청산 선반 상대 위치",
        ["bitget", "position"],
        "Bitget 실현 청산 밀집대에 내 진입가와 무효화선을 겹쳐야 상대 위치가 생깁니다.",
        reading,
        detail,
        {
            "state": relation,
            "entry_price": position.entry_price,
            "invalidation_price": invalidation,
            "shelf": shelf,
            "sample_n": int(heatmap.get("n_events") or 0),
            "sample_low": bool(heatmap.get("sample_low")),
            "truth_label": heatmap.get("truth_label"),
        },
    )


def _flow_alignment_signal(position: Position, joined: dict[str, Any]) -> dict[str, Any]:
    market_state = str(joined.get("market_state") or "unknown")
    if market_state != "open":
        return _unavailable_signal(
            "underlying_flow_alignment",
            "기초 수급 × 내 방향",
            ["toss", "position"],
            "Toss 투자자별 수급과 내 보유 방향을 함께 놓아야 정합 여부를 표시할 수 있습니다.",
            "기초자산 시장 비거래 시간 · 수급 신호 비활성",
            data={"market_state": market_state, "reason": "market_closed"},
        )
    if joined.get("flow_status") != "available":
        return _unavailable_signal(
            "underlying_flow_alignment",
            "기초 수급 × 내 방향",
            ["toss", "position"],
            "Toss 투자자별 수급과 내 보유 방향을 함께 놓아야 정합 여부를 표시할 수 있습니다.",
            str(joined.get("flow_note") or "Toss 투자자별 수급 미제공"),
            data={"market_state": market_state, "reason": "source_unavailable"},
        )
    flow = joined.get("investor_flow") if isinstance(joined.get("investor_flow"), dict) else None
    net = _number(flow.get("net_amount")) if flow else None
    if net is None:
        return _unavailable_signal(
            "underlying_flow_alignment",
            "기초 수급 × 내 방향",
            ["toss", "position"],
            "Toss 투자자별 수급과 내 보유 방향을 함께 놓아야 정합 여부를 표시할 수 있습니다.",
            "수급 관측값이 없습니다.",
        )
    aligned = (net > 0 and position.direction.value == "long") or (net < 0 and position.direction.value == "short")
    return _signal(
        "underlying_flow_alignment",
        "기초 수급 × 내 방향",
        ["toss", "position"],
        "Toss 투자자별 수급과 내 보유 방향을 함께 놓아야 정합 여부를 표시할 수 있습니다.",
        "내 방향과 수급 같은 방향 관측" if aligned else "내 방향과 수급 반대 방향 관측",
        f"관측 순금액 {net:+,.0f}",
        {"state": "aligned" if aligned else "opposed", "net_amount": net, "market_state": market_state},
    )


def _leverage_stack_signal(position: Position, joined: dict[str, Any]) -> dict[str, Any]:
    factor = _number(joined.get("underlying_leverage_factor"))
    kind = str(joined.get("underlying_kind") or "")
    if kind != "leveraged_etf" or factor is None:
        return _unavailable_signal(
            "leverage_stack",
            "레버리지 중첩",
            ["toss", "bitget", "position"],
            "Toss가 검증한 기초자산 레버리지 특성, Bitget 계약, 내 적용 배율이 모두 있어야 중첩을 수치화할 수 있습니다.",
            "레버리지 ETF 기초자산이 아닙니다.",
        )
    closes = [close for _time, close in _toss_candles(joined)]
    effective = factor * position.leverage
    decay = _volatility_drag_estimate(closes, factor)
    detail = f"기초 {factor:g}x × 퍼페추얼 {position.leverage:g}x = 명목 {effective:g}x"
    if decay is not None:
        detail += f" · 20거래일 변동성 감쇠 근사 {decay:.2f}%"
    return _signal(
        "leverage_stack",
        "레버리지 중첩",
        ["toss", "bitget", "position"],
        "Toss가 검증한 기초자산 레버리지 특성, Bitget 계약, 내 적용 배율이 모두 있어야 중첩을 수치화할 수 있습니다.",
        "중첩 익스포저 관측",
        detail,
        {
            "state": "stacked",
            "underlying_factor": factor,
            "perpetual_leverage": position.leverage,
            "effective_exposure_multiple": round(effective, 3),
            "decay_20d_estimate_pct": decay,
            "decay_method": "0.5×L×(L-1)×기초분산근사×20",
            "warning": "레버리지 ETF는 보유기간과 경로에 따라 기초지수 누적수익률과 괴리가 생길 수 있습니다.",
        },
    )


def _thesis_comparison(
    position: Position,
    entry_snapshot: dict[str, Any],
    raw_analysis: dict[str, Any],
    action_plan: dict[str, Any],
    signals: list[dict[str, Any]],
) -> dict[str, Any]:
    mark = _number(raw_analysis.get("mark_price")) or position.mark_price or position.current_price
    invalidation = _action_price(action_plan.get("invalidation")) or _action_price(action_plan.get("engine_invalidation"))
    invalidated = bool(
        mark is not None
        and invalidation is not None
        and ((position.direction.value == "long" and mark < invalidation) or (position.direction.value == "short" and mark > invalidation))
    )
    opposed = sum(1 for item in signals if item.get("data", {}).get("state") in {"divergent", "opposed"})
    status = "invalid" if invalidated else "weakened" if opposed >= 2 or action_plan.get("verdict_state") in {"weakening", "danger"} else "maintained"
    labels = {"invalid": "무효", "weakened": "약화", "maintained": "유지"}
    current = {
        "overall_stance": _overall_stance(raw_analysis),
        "mark_price": mark,
        "basis_pct": next((item.get("data", {}).get("current_pct") for item in signals if item.get("id") == "basis_behavior"), None),
        "funding_rate": _funding_rate(raw_analysis),
    }
    snapshot_thesis = entry_snapshot.get("thesis") if isinstance(entry_snapshot.get("thesis"), dict) else {}
    current_user_thesis = (position.thesis_text or position.entry_memo or "").strip()
    thesis = {"text": current_user_thesis, "source": "user"} if current_user_thesis else snapshot_thesis
    return {
        "status": status,
        "status_label": labels[status],
        "text": thesis.get("text"),
        "source": thesis.get("source"),
        "entry": entry_snapshot.get("structure", {}),
        "current": current,
        "comparison_note": "진입 시점 원본이 없는 값은 최초 심화 관측 스냅샷과 비교합니다.",
    }


def _position_risk(
    position: Position,
    raw_analysis: dict[str, Any],
    action_plan: dict[str, Any],
    signals: list[dict[str, Any]],
) -> dict[str, Any]:
    mark = _number(raw_analysis.get("mark_price")) or position.mark_price or position.current_price
    liquidation = position.liquidation_price
    invalidation = _action_price(action_plan.get("invalidation")) or _action_price(action_plan.get("engine_invalidation"))
    resistance = _next_level(raw_analysis, mark, position.direction.value)
    risk_per_unit = abs(position.entry_price - invalidation) if invalidation is not None else None
    reward_per_unit = abs(resistance - position.entry_price) if resistance is not None else None
    r_multiple = reward_per_unit / risk_per_unit if risk_per_unit and reward_per_unit is not None else None
    overall = _overall_stance(raw_analysis)
    stance = "up" if overall == "상방" else "down" if overall == "하방" else "insufficient"
    opposed = (position.direction.value == "long" and stance == "down") or (position.direction.value == "short" and stance == "up")
    reasons = _directional_reasons(raw_analysis, stance)
    cross_reasons = [
        item["reading"] for item in signals if item.get("status") == "active" and item.get("data", {}).get("state") in {"divergent", "opposed", "expanding"}
    ]
    reasons.extend(cross_reasons[:2])
    reversal = _reversal_condition(raw_analysis, mark, stance)
    return {
        "liquidation_distance_pct": _distance_pct(mark, liquidation),
        "invalidation_price": invalidation,
        "invalidation_distance_pct": _distance_pct(mark, invalidation),
        "next_structure_price": resistance,
        "reward_risk_r": round(r_multiple, 2) if r_multiple is not None else None,
        "market_reading": {
            "stance": stance,
            "label": "상방 관측" if stance == "up" else "하방 관측" if stance == "down" else "판정 유보",
            "position_alignment": "opposed" if opposed else "aligned" if stance != "insufficient" else "unknown",
            "reasons": reasons,
            "reversal_condition": reversal,
        },
        "partial_exit_simulation": _partial_exit_simulation(position, mark, invalidation),
    }


def _partial_exit_simulation(position: Position, mark: float | None, invalidation: float | None) -> list[dict[str, Any]]:
    if mark is None:
        return []
    liquidation_distance = _distance_pct(mark, position.liquidation_price)
    invalidation_risk_pct = _distance_pct(mark, invalidation)
    rows = []
    for reduction in (25, 50):
        remaining_ratio = 1 - reduction / 100
        rows.append(
            {
                "reduction_pct": reduction,
                "remaining_quantity": round(position.quantity * remaining_ratio, 8),
                "remaining_notional": round(position.quantity * remaining_ratio * mark, 2),
                "liquidation_distance_pct": liquidation_distance,
                "invalidation_risk_notional": (
                    round(position.quantity * remaining_ratio * mark * invalidation_risk_pct / 100, 2) if invalidation_risk_pct is not None else None
                ),
                "assumption": "진입가·증거금 배분·청산가가 그대로인 정적 계산이며 실행 기능이 아닙니다.",
            }
        )
    return rows


def _basis_series(raw_analysis: dict[str, Any], joined: dict[str, Any]) -> list[dict[str, Any]]:
    bitget = []
    for candle in raw_analysis.get("candles", []):
        if not isinstance(candle, dict):
            continue
        timestamp = _timestamp(candle.get("time"))
        close = _number(candle.get("close"))
        if timestamp is not None and close and close > 0:
            bitget.append((timestamp, close))
    toss = _toss_candles(joined)
    toss_times = [item[0] for item in toss]
    series: list[dict[str, Any]] = []
    for timestamp, close in bitget:
        index = bisect_right(toss_times, timestamp) - 1
        if index < 0:
            continue
        toss_close = toss[index][1]
        series.append({"time": timestamp, "value": round((close / toss_close - 1) * 100, 4), "kind": "confirmed_close"})
    return series


def _toss_candles(joined: dict[str, Any]) -> list[tuple[int, float]]:
    rows: list[tuple[int, float]] = []
    for candle in joined.get("raw_candles", []):
        if not isinstance(candle, dict):
            continue
        timestamp = _timestamp(candle.get("opened_at"))
        close = _number(candle.get("close"))
        if timestamp is not None and close and close > 0:
            rows.append((timestamp, close))
    return sorted(rows)


def _funding_rate(analysis: dict[str, Any]) -> float | None:
    derivatives = analysis.get("derivatives") if isinstance(analysis.get("derivatives"), dict) else {}
    latest = derivatives.get("latest") if isinstance(derivatives.get("latest"), dict) else {}
    return _number(latest.get("funding_rate"))


def _open_interest(analysis: dict[str, Any]) -> float | None:
    derivatives = analysis.get("derivatives") if isinstance(analysis.get("derivatives"), dict) else {}
    latest = derivatives.get("latest") if isinstance(derivatives.get("latest"), dict) else {}
    return _number(latest.get("open_interest"))


def _overall_stance(analysis: dict[str, Any]) -> str:
    one_liners = analysis.get("one_liners") if isinstance(analysis.get("one_liners"), dict) else {}
    return str(one_liners.get("overall_stance") or "판단불가")


def _automatic_thesis(position: Position, analysis: dict[str, Any]) -> str:
    stance = _overall_stance(analysis)
    return f"사용자 논거 미입력 · 최초 관측 시 {stance}, {position.direction.value} 포지션"


def _directional_reasons(analysis: dict[str, Any], stance: str) -> list[str]:
    target = "상방" if stance == "up" else "하방" if stance == "down" else None
    one_liners = analysis.get("one_liners") if isinstance(analysis.get("one_liners"), dict) else {}
    lines = one_liners.get("lines") if isinstance(one_liners.get("lines"), list) else []
    return [f"{item.get('module_label')}: {item.get('phrase')}" for item in lines if isinstance(item, dict) and item.get("stance") == target][:4]


def _reversal_condition(analysis: dict[str, Any], mark: float | None, stance: str) -> dict[str, Any] | None:
    if mark is None or stance == "insufficient":
        return None
    levels = analysis.get("price_levels") if isinstance(analysis.get("price_levels"), dict) else {}
    key = "resistance" if stance == "down" else "support"
    candidates = []
    for item in levels.get(key, []):
        if not isinstance(item, dict):
            continue
        price = _number(item.get("price"))
        if price is None:
            continue
        if (stance == "down" and price > mark) or (stance == "up" and price < mark):
            candidates.append(price)
    if not candidates:
        return None
    price = min(candidates, key=lambda value: abs(value - mark))
    return {
        "price": price,
        "condition": "확정 캔들이 상단 구조를 회복하면 현재 하방 읽기를 재평가"
        if stance == "down"
        else "확정 캔들이 하단 구조를 이탈하면 현재 상방 읽기를 재평가",
        "source": "Bitget 확정 캔들 구조 레벨",
    }


def _next_level(analysis: dict[str, Any], mark: float | None, direction: str) -> float | None:
    if mark is None:
        return None
    levels = analysis.get("price_levels") if isinstance(analysis.get("price_levels"), dict) else {}
    key = "resistance" if direction == "long" else "support"
    candidates = []
    for item in levels.get(key, []):
        if not isinstance(item, dict):
            continue
        price = _number(item.get("price"))
        if price is not None and ((direction == "long" and price > mark) or (direction == "short" and price < mark)):
            candidates.append(price)
    return min(candidates, key=lambda value: abs(value - mark)) if candidates else None


def _volatility_drag_estimate(closes: list[float], factor: float) -> float | None:
    if len(closes) < 21 or factor <= 1:
        return None
    returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes)) if closes[index - 1] > 0]
    if len(returns) < 20:
        return None
    etf_variance = pstdev(returns[-60:]) ** 2
    underlying_variance_estimate = etf_variance / (factor**2)
    drag = 0.5 * factor * (factor - 1) * underlying_variance_estimate * 20 * 100
    return round(max(0.0, drag), 3)


def _signal(
    signal_id: str,
    label: str,
    sources: list[str],
    moat_reason: str,
    reading: str,
    detail: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": signal_id,
        "label": label,
        "status": "active",
        "sources": [{"id": source, "label": SOURCE_BADGES[source]} for source in sources],
        "moat_reason": moat_reason,
        "reading": reading,
        "detail": detail,
        "data": data,
    }


def _unavailable_signal(
    signal_id: str,
    label: str,
    sources: list[str],
    moat_reason: str,
    detail: str,
    *,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _signal(signal_id, label, sources, moat_reason, "데이터 없음", detail, data or {})
    payload["status"] = "unavailable"
    return payload


def _relative_price(price: float, low: float, high: float) -> str:
    if price < low:
        return "아래"
    if price > high:
        return "위"
    return "내부"


def _action_price(value: Any) -> float | None:
    return _number(value.get("price")) if isinstance(value, dict) else None


def _plan_price(levels: Any, key: str) -> float | None:
    if not isinstance(levels, dict):
        return None
    values = levels.get(key)
    if not isinstance(values, list) or not values:
        return None
    return _number(values[0].get("price")) if isinstance(values[0], dict) else None


def _distance_pct(origin: float | None, target: float | None) -> float | None:
    if origin is None or target is None or origin == 0:
        return None
    return round(abs(target - origin) / abs(origin) * 100, 2)


def _timestamp(value: Any) -> int | None:
    if isinstance(value, (int, float)) and math.isfinite(value):
        return int(value / 1000) if value > 10_000_000_000 else int(value)
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
