from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.db.models import Position, PositionSnapshot


def build_action_plan(position: Position, snapshot: PositionSnapshot, chart_analysis: dict[str, Any]) -> dict[str, Any]:
    mark_price = snapshot.mark_price or chart_analysis.get("mark_price") or position.mark_price or position.current_price
    levels = chart_analysis.get("price_levels", {})
    support = _levels(levels.get("support"))
    resistance = _levels(levels.get("resistance"))
    invalidation_levels = _levels(levels.get("invalidation"))
    volume_profile = chart_analysis.get("volume_profile", {})
    volume_xray = chart_analysis.get("volume_xray", {})
    harmonic_patterns = chart_analysis.get("harmonic_patterns", [])
    derivatives = chart_analysis.get("derivatives", {})
    liquidity = chart_analysis.get("liquidity", {})
    candles = chart_analysis.get("candles", [])
    direction = position.direction.value

    invalidation = _planned_invalidation(position, mark_price)
    engine_invalidation = _engine_invalidation(direction, invalidation_levels, support, resistance, mark_price)
    if invalidation is None:
        invalidation = engine_invalidation

    take_profit = _take_profit_targets(
        position,
        mark_price,
        support,
        resistance,
        volume_profile,
        candles,
        harmonic_patterns,
        derivatives,
    )
    invalidation = _with_liquidity_confluence(invalidation, liquidity)
    engine_invalidation = _with_liquidity_confluence(engine_invalidation, liquidity)
    take_profit = [_with_liquidity_confluence(item, liquidity) for item in take_profit]
    watch_triggers = _watch_triggers(position, mark_price, volume_profile, volume_xray, derivatives)
    reference_zones = (
        []
        if invalidation is not None
        else _reference_zones(
            direction=direction,
            mark_price=mark_price,
            support=support,
            resistance=resistance,
            liquidity=liquidity,
            candles=candles,
        )
    )
    verdict = _verdict_state(
        snapshot=snapshot,
        direction=direction,
        invalidation=invalidation,
        take_profit=take_profit,
        watch_triggers=watch_triggers,
        reference_zones=reference_zones,
        candles=candles,
        support=support,
        resistance=resistance,
    )

    return {
        "as_of": snapshot.as_of.isoformat(),
        "mark_price": mark_price,
        "invalidation": invalidation,
        "engine_invalidation": engine_invalidation if invalidation != engine_invalidation else None,
        "take_profit": take_profit,
        "watch_triggers": watch_triggers,
        "reference_zones": reference_zones,
        "liquidation": _liquidation(position),
        "verdict_state": verdict["state"],
        "standby_reason": verdict.get("standby_reason"),
        "headline_action": verdict["headline"],
    }


def _headline_action(
    direction: str,
    invalidation: dict[str, Any] | None,
    take_profit: list[dict[str, Any]],
    watch_triggers: list[dict[str, str]],
) -> str | None:
    """현재가에서 가장 가까운 트리거 1개를 골라 결정론적 next_action 문장을 만든다."""
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    if invalidation and isinstance(invalidation.get("distance_pct"), (int, float)):
        candidates.append((abs(invalidation["distance_pct"]), "invalidation", invalidation))
    for target in take_profit:
        if isinstance(target.get("distance_pct"), (int, float)):
            candidates.append((abs(target["distance_pct"]), "take_profit", target))
    if candidates:
        _, kind, item = min(candidates, key=lambda entry: entry[0])
        price = _format_price(item.get("price"))
        action = item.get("action") or "조건 확인"
        if kind == "invalidation":
            condition = "지지 유지 여부" if direction == "long" else "저항 유지 여부"
            return f"지금 볼 것: {price} {condition}. {action}."
        condition = "저항 반응" if direction == "long" else "지지 반응"
        return f"지금 볼 것: {price} {condition}. 도달 시 {action}."
    if watch_triggers:
        trigger = watch_triggers[0]
        return f"지금 볼 것: {trigger['condition']}. {trigger['meaning']}."
    return None


def _verdict_state(
    *,
    snapshot: PositionSnapshot,
    direction: str,
    invalidation: dict[str, Any] | None,
    take_profit: list[dict[str, Any]],
    watch_triggers: list[dict[str, str]],
    reference_zones: list[dict[str, Any]],
    candles: list[dict[str, Any]],
    support: list[dict[str, Any]],
    resistance: list[dict[str, Any]],
) -> dict[str, str | None]:
    headline = _headline_action(direction, invalidation, take_profit, watch_triggers)
    primary_distances = [
        abs(float(item["distance_pct"]))
        for item in [invalidation, *take_profit]
        if isinstance(item, dict) and isinstance(item.get("distance_pct"), (int, float))
    ]
    if snapshot.severity_rank >= 4 or snapshot.health_score <= 25:
        if headline:
            danger_headline = headline.replace("지금 볼 것:", "긴급 확인:")
        else:
            reason = _standby_reason(candles, support, resistance)
            danger_headline = _danger_standby_headline(reason, reference_zones)
        return {"state": "danger", "headline": danger_headline, "standby_reason": None}
    if headline and primary_distances:
        state = "weakening" if min(primary_distances) <= 3 or snapshot.severity_rank >= 2 else "holding"
        return {"state": state, "headline": headline, "standby_reason": None}
    if headline and (invalidation or take_profit):
        state = "weakening" if snapshot.severity_rank >= 2 else "holding"
        return {"state": state, "headline": headline, "standby_reason": None}
    if headline and watch_triggers:
        state = "weakening" if snapshot.severity_rank >= 2 else "holding"
        return {"state": state, "headline": headline, "standby_reason": None}
    reason = _standby_reason(candles, support, resistance)
    return {
        "state": "standby",
        "headline": _standby_headline(reason, reference_zones),
        "standby_reason": reason,
    }


def _standby_reason(candles: list[dict[str, Any]], support: list[dict[str, Any]], resistance: list[dict[str, Any]]) -> str:
    candle_count = len(candles) if isinstance(candles, list) else 0
    if candle_count and candle_count < 60:
        return f"캔들 {candle_count}개 — 표본 축적 중"
    scored = [float(item["score"]) for item in [*support, *resistance] if isinstance(item.get("score"), (int, float))]
    if scored:
        return f"유효 레벨 부재 (최고 score {max(scored):.0f})"
    if candle_count:
        return "레인지 형성 대기"
    return "데이터 부족 — 캔들 표본 없음"


def _standby_headline(reason: str, reference_zones: list[dict[str, Any]]) -> str:
    reference = reference_zones[0] if reference_zones else None
    if reference and reference.get("price") is not None:
        return f"채점 가능한 구조 없음 — {reason}. 참조 {reference.get('label', '가격')} {_format_price(reference.get('price'))}."
    return f"채점 가능한 구조 없음 — {reason}. 참조 존 형성 대기."


def _danger_standby_headline(reason: str, reference_zones: list[dict[str, Any]]) -> str:
    reference = reference_zones[0] if reference_zones else None
    if reference and reference.get("price") is not None:
        return f"긴급 확인: {reason}. 참고 {reference.get('label', '가격')} {_format_price(reference.get('price'))}."
    return f"긴급 확인: {reason}. 수동 리스크 점검 필요."


def _reference_zones(
    *,
    direction: str,
    mark_price: float | None,
    support: list[dict[str, Any]],
    resistance: list[dict[str, Any]],
    liquidity: Any,
    candles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    liquidity_zone = _reference_liquidity_pool(direction, mark_price, liquidity)
    if liquidity_zone:
        zones.append(liquidity_zone)
    weak_level = _weak_structural_reference(direction, mark_price, support, resistance)
    if weak_level:
        zones.append(weak_level)
    atr_zone = _atr_reference_zone(direction, mark_price, candles)
    if atr_zone:
        zones.append(atr_zone)
    return _dedupe_price_items(zones)[:3]


def _reference_liquidity_pool(direction: str, mark_price: float | None, liquidity: Any) -> dict[str, Any] | None:
    if not isinstance(liquidity, dict):
        return None
    pools = liquidity.get("pools")
    if not isinstance(pools, list):
        return None
    candidates: list[tuple[float, dict[str, Any]]] = []
    for pool in pools:
        if not isinstance(pool, dict) or pool.get("swept"):
            continue
        price = _optional_float(pool.get("price"))
        if price is None:
            continue
        if mark_price is not None:
            if direction == "long" and price >= mark_price:
                continue
            if direction == "short" and price <= mark_price:
                continue
            distance = abs(price - mark_price)
        else:
            distance = 0.0
        candidates.append((distance, pool))
    if not candidates:
        return None
    pool = min(candidates, key=lambda item: item[0])[1]
    price = _optional_float(pool.get("price"))
    if price is None:
        return None
    return _reference_item(
        price=price,
        mark_price=mark_price,
        direction=direction,
        label=_liquidity_pool_basis(pool),
        basis="구조 레벨 없음 · 유동성 풀 참조",
        source="liquidity_pool",
    )


def _weak_structural_reference(
    direction: str,
    mark_price: float | None,
    support: list[dict[str, Any]],
    resistance: list[dict[str, Any]],
) -> dict[str, Any] | None:
    levels = support if direction == "long" else resistance
    candidates: list[dict[str, Any]] = []
    for level in levels:
        price = _optional_float(level.get("price"))
        if price is None:
            continue
        if mark_price is not None:
            if direction == "long" and price >= mark_price:
                continue
            if direction == "short" and price <= mark_price:
                continue
        candidates.append(level)
    if not candidates:
        return None
    best = max(candidates, key=lambda item: float(item.get("score") or 0))
    price = _optional_float(best.get("price"))
    if price is None:
        return None
    score = best.get("score")
    label = "약한 지지 레벨" if direction == "long" else "약한 저항 레벨"
    return _reference_item(
        price=price,
        mark_price=mark_price,
        direction=direction,
        label=f"{label} score {float(score):.0f}" if isinstance(score, (int, float)) else label,
        basis="구조 레벨 기준 미달 · 참고 전용",
        source="weak_structure_level",
    )


def _atr_reference_zone(direction: str, mark_price: float | None, candles: list[dict[str, Any]]) -> dict[str, Any] | None:
    if mark_price is None or mark_price <= 0:
        return None
    atr = _average_true_range(candles)
    if atr is None or atr <= 0:
        return None
    price = mark_price - atr * 1.5 if direction == "long" else mark_price + atr * 1.5
    return _reference_item(
        price=price,
        mark_price=mark_price,
        direction=direction,
        label=f"ATR 참조 존 ±{_format_price(atr * 1.5)}",
        basis="구조 부재 · 변동성 참조 ±ATR×1.5",
        source="atr_reference",
    )


def _reference_item(price: float, mark_price: float | None, direction: str, label: str, basis: str, source: str) -> dict[str, Any]:
    return {
        "price": price,
        "label": label,
        "basis": basis,
        "source": source,
        "distance_pct": _distance_pct(price, mark_price, direction),
        "reference_only": True,
        "action": "알림 미사용 · 수동 참고",
    }


def _average_true_range(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    if not isinstance(candles, list) or len(candles) < 2:
        return None
    recent = candles[-period:]
    ranges: list[float] = []
    previous_close: float | None = None
    for candle in recent:
        high = _optional_float(candle.get("high"))
        low = _optional_float(candle.get("low"))
        close = _optional_float(candle.get("close"))
        if high is None or low is None:
            continue
        values = [high - low]
        if previous_close is not None:
            values.extend([abs(high - previous_close), abs(low - previous_close)])
        ranges.append(max(values))
        if close is not None:
            previous_close = close
    if not ranges:
        return None
    return sum(ranges) / len(ranges)


def _planned_invalidation(position: Position, mark_price: float | None) -> dict[str, Any] | None:
    if position.planned_stop_price is None:
        return None
    return _plan_item(
        position=position,
        price=position.planned_stop_price,
        mark_price=mark_price,
        basis="사용자 기록 손절/무효화 가격",
        action="이탈 시 손절 검토" if position.direction.value == "long" else "돌파 시 손절 검토",
    )


def _engine_invalidation(
    direction: str,
    invalidation_levels: list[dict[str, Any]],
    support: list[dict[str, Any]],
    resistance: list[dict[str, Any]],
    mark_price: float | None,
) -> dict[str, Any] | None:
    source = invalidation_levels[0] if invalidation_levels else None
    if source is None:
        source = support[0] if direction == "long" and support else resistance[0] if direction == "short" and resistance else None
    if not source or source.get("price") is None:
        return None
    min_score = get_settings().min_invalidation_level_score
    if isinstance(source.get("score"), (int, float)) and float(source["score"]) < min_score:
        return None
    action = "이탈 시 손절 검토" if direction == "long" else "돌파 시 손절 검토"
    return {
        "price": source["price"],
        "score": source.get("score"),
        "source": "engine",
        "basis": _basis(source, "현행 차트 레벨 기반 무효화 가격"),
        "distance_pct": _distance_pct(source["price"], mark_price, direction),
        "action": action,
    }


def _take_profit_targets(
    position: Position,
    mark_price: float | None,
    support: list[dict[str, Any]],
    resistance: list[dict[str, Any]],
    volume_profile: dict[str, Any],
    candles: list[dict[str, Any]],
    harmonic_patterns: list[dict[str, Any]],
    derivatives: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    direction = position.direction.value
    targets: list[dict[str, Any]] = []
    if position.planned_take_profit_price is not None and _is_favorable_target(
        position.planned_take_profit_price,
        mark_price,
        direction,
        position.entry_price,
    ):
        targets.append(
            _plan_item(
                position=position,
                price=position.planned_take_profit_price,
                mark_price=mark_price,
                basis="사용자 기록 익절 가격",
                action="부분 익절 검토",
            )
        )

    for item in _harmonic_targets(position, mark_price, harmonic_patterns):
        targets.append(item)

    for item in _liquidation_cluster_targets(position, mark_price, derivatives):
        targets.append(item)

    level_targets = resistance if direction == "long" else support
    target_label = "저항" if direction == "long" else "지지"
    for level in level_targets:
        price = level.get("price")
        if price is None or not _is_favorable_target(price, mark_price, direction, position.entry_price):
            continue
        targets.append(
            _plan_item(
                position=position,
                price=price,
                mark_price=mark_price,
                basis=_basis(level, f"현행 차트 레벨 {target_label}"),
                action="부분 익절 검토",
            )
        )

    profile_key = "value_area_high" if direction == "long" else "value_area_low"
    profile_price = volume_profile.get(profile_key)
    if profile_price is not None and _is_favorable_target(profile_price, mark_price, direction, position.entry_price):
        targets.append(
            _plan_item(
                position=position,
                price=profile_price,
                mark_price=mark_price,
                basis="추정 볼륨 프로파일 VAH" if direction == "long" else "추정 볼륨 프로파일 VAL",
                action="부분 익절 검토",
            )
        )

    previous_extreme = _previous_extreme(candles, direction)
    if previous_extreme is not None and _is_favorable_target(previous_extreme, mark_price, direction, position.entry_price):
        targets.append(
            _plan_item(
                position=position,
                price=previous_extreme,
                mark_price=mark_price,
                basis="최근 100개 캔들 직전 고점" if direction == "long" else "최근 100개 캔들 직전 저점",
                action="추가 익절 검토",
            )
        )
    return _dedupe_price_items(targets)[:3]


def _harmonic_targets(
    position: Position,
    mark_price: float | None,
    harmonic_patterns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(harmonic_patterns, list):
        return []
    direction = position.direction.value
    target_direction = "bearish" if direction == "long" else "bullish"
    targets: list[dict[str, Any]] = []
    for pattern in sorted(
        _valid_harmonic_patterns(harmonic_patterns),
        key=lambda item: -int(item.get("confidence", 0)),
    ):
        if pattern.get("direction") != target_direction:
            continue
        prz = pattern.get("prz")
        if not isinstance(prz, dict):
            continue
        price = prz.get("low") if direction == "long" else prz.get("high")
        if not isinstance(price, (int, float)) or not _is_favorable_target(price, mark_price, direction, position.entry_price):
            continue
        basis = pattern.get("basis") or f"{pattern.get('label', 'Harmonic')} PRZ"
        confidence = pattern.get("confidence")
        suffix = f" · 신뢰도 {confidence}" if isinstance(confidence, int) else ""
        targets.append(
            _plan_item(
                position=position,
                price=float(price),
                mark_price=mark_price,
                basis=f"{basis}{suffix}",
                action="PRZ 도달 시 부분 익절/반전 경계 점검",
            )
        )
    return targets[:2]


def _valid_harmonic_patterns(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [pattern for pattern in patterns if isinstance(pattern, dict) and isinstance(pattern.get("confidence"), int)]


_VOLUME_STATE_LABELS = {
    "climax_candidate": "클라이맥스 후보 (거래 폭발 뒤 반전 경계)",
    "absorption_candidate": "흡수 후보 (큰 물량이 소화되는 흔적)",
    "volume_expanding": "거래량 확대",
    "delta_imbalanced": "체결이 한쪽으로 쏠림",
    "drying_up": "거래량 고갈",
    "balanced_flow": "체결 균형",
    "rebound_with_volume": "거래량 동반 반등",
    "declining_after_push": "가격 이동 후 거래량 둔화",
    "weak_rebound": "약한 반응",
    "data_unavailable": "체결 데이터 부족",
}


def _watch_triggers(
    position: Position,
    mark_price: float | None,
    volume_profile: dict[str, Any],
    volume_xray: dict[str, Any],
    derivatives: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    triggers: list[dict[str, str]] = []
    poc = volume_profile.get("poc_price")
    if poc is not None and mark_price is not None:
        if position.direction.value == "long":
            condition = f"최다 거래 가격(POC) {_format_price(poc)} 상향 회복" if mark_price < poc else f"최다 거래 가격(POC) {_format_price(poc)} 지지 유지"
            meaning = "상방 지지 유지"
        else:
            condition = f"최다 거래 가격(POC) {_format_price(poc)} 하향 이탈" if mark_price > poc else f"최다 거래 가격(POC) {_format_price(poc)} 저항 유지"
            meaning = "하방 저항 유지"
        triggers.append({"condition": condition, "meaning": meaning})
    volume_state = volume_xray.get("volume_state")
    if volume_xray.get("spike_detected"):
        triggers.append(
            {
                "condition": "거래량 급증 캔들 출현",
                "meaning": "포지션 방향과 캔들 방향이 같은지 확인",
            }
        )
    if volume_state:
        state_label = _VOLUME_STATE_LABELS.get(str(volume_state), str(volume_state))
        triggers.append(
            {
                "condition": f"거래량 상태: {state_label}",
                "meaning": "거래량 변화가 포지션 방향과 정렬되는지 확인",
            }
        )
    derivative_triggers = _derivative_watch_triggers(position, mark_price, derivatives)
    return (derivative_triggers + triggers)[:3]


def _derivative_watch_triggers(position: Position, mark_price: float | None, derivatives: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(derivatives, dict):
        return []
    signals = derivatives.get("signals") if isinstance(derivatives.get("signals"), dict) else {}
    latest = derivatives.get("latest") if isinstance(derivatives.get("latest"), dict) else {}
    triggers: list[dict[str, str]] = []
    funding = signals.get("funding_state") if isinstance(signals.get("funding_state"), dict) else None
    funding_value = _optional_float((funding or {}).get("funding"))
    direction = position.direction.value
    if funding and funding.get("state") == "extreme" and funding_value is not None:
        adverse = (direction == "long" and funding_value > 0) or (direction == "short" and funding_value < 0)
        if adverse:
            side = "롱" if direction == "long" else "숏"
            triggers.append(
                {
                    "condition": f"펀딩 극단({_format_funding(funding_value)}/8h) 지속",
                    "meaning": f"{side} 쏠림 청산 리스크 감시",
                }
            )
    divergence = signals.get("oi_price_divergence") if isinstance(signals.get("oi_price_divergence"), dict) else None
    if divergence:
        state = str(divergence.get("state") or "")
        adverse_states = {
            "long": {"price_down_oi_up", "price_down_oi_down"},
            "short": {"price_up_oi_up", "price_up_oi_down"},
        }
        if state in adverse_states.get(direction, set()):
            oi = _optional_float(divergence.get("oi_change_pct"))
            condition = f"OI 24h {_format_signed_pct(oi)} + {divergence.get('label', '가격/OI 역행')}"
            triggers.append({"condition": condition, "meaning": "포지션 방향과 수급이 맞는지 확인"})
    for cluster in _liquidation_clusters(derivatives):
        price = _optional_float(cluster.get("price") or cluster.get("mid") or cluster.get("level"))
        if price is None or mark_price is None or mark_price <= 0:
            continue
        distance = ((price - mark_price) / mark_price) * 100
        if abs(distance) > 3:
            continue
        side = "상방" if distance > 0 else "하방"
        triggers.append(
            {
                "condition": f"{side} {abs(distance):.1f}% 청산 밀집대 추정 {_format_price(price)}",
                "meaning": "도달 시 변동성 확대 가능성 감시",
            }
        )
    if latest.get("source_status") == "locked":
        return triggers
    return triggers[:3]


def _liquidation_cluster_targets(position: Position, mark_price: float | None, derivatives: dict[str, Any] | None) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    if mark_price is None:
        return targets
    for cluster in _liquidation_clusters(derivatives):
        price = _optional_float(cluster.get("price") or cluster.get("mid") or cluster.get("level"))
        if price is None or not _is_favorable_target(price, mark_price, position.direction.value, position.entry_price):
            continue
        targets.append(
            _plan_item(
                position=position,
                price=price,
                mark_price=mark_price,
                basis="청산 밀집대 추정 · Coinglass(liq_cluster)",
                action="도달 시 변동성 확대 가능, 부분 익절 검토",
            )
        )
    return targets[:2]


def _with_liquidity_confluence(item: dict[str, Any] | None, liquidity: Any) -> dict[str, Any] | None:
    if item is None or not isinstance(liquidity, dict):
        return item
    price = _optional_float(item.get("price"))
    if price is None:
        return item
    pool = _nearest_liquidity_pool(price, liquidity)
    if pool is None:
        return item
    label = _liquidity_pool_basis(pool)
    basis = str(item.get("basis") or "")
    if label in basis:
        return item
    return {**item, "basis": f"{basis} + {label}" if basis else label}


def _nearest_liquidity_pool(price: float, liquidity: dict[str, Any]) -> dict[str, Any] | None:
    pools = liquidity.get("pools")
    if not isinstance(pools, list):
        return None
    candidates: list[tuple[float, dict[str, Any]]] = []
    tolerance = max(abs(price) * 0.003, 1e-8)
    for pool in pools:
        if not isinstance(pool, dict) or pool.get("swept"):
            continue
        pool_price = _optional_float(pool.get("price"))
        if pool_price is None:
            continue
        distance = abs(pool_price - price)
        if distance <= tolerance:
            candidates.append((distance, pool))
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _liquidity_pool_basis(pool: dict[str, Any]) -> str:
    touches = pool.get("touch_count") or pool.get("touches") or 1
    kind = str(pool.get("kind") or "")
    if kind == "eqh":
        return f"상단 풀(EQH {touches}터치)"
    if kind == "eql":
        return f"하단 풀(EQL {touches}터치)"
    if kind == "old_high":
        return f"상단 풀(전고 {touches}터치)"
    if kind == "old_low":
        return f"하단 풀(전저 {touches}터치)"
    label = pool.get("label")
    return str(label or "유동성 풀")


def _liquidation_clusters(derivatives: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(derivatives, dict):
        return []
    coinglass = derivatives.get("coinglass") if isinstance(derivatives.get("coinglass"), dict) else {}
    if coinglass.get("source_status") != "ok":
        return []
    signals = derivatives.get("signals") if isinstance(derivatives.get("signals"), dict) else {}
    clusters = signals.get("liquidation_clusters")
    if not isinstance(clusters, list):
        return []
    return [cluster for cluster in clusters if isinstance(cluster, dict)]


def _liquidation(position: Position) -> dict[str, Any]:
    if position.liquidation_price is None:
        return {"price": None, "warning": "거래소 청산가 미수신"}
    return {"price": position.liquidation_price, "warning": None}


def _plan_item(position: Position, price: float, mark_price: float | None, basis: str, action: str) -> dict[str, Any]:
    return {
        "price": price,
        "basis": basis,
        "distance_pct": _distance_pct(price, mark_price, position.direction.value),
        "action": action,
    }


def _distance_pct(price: float | None, mark_price: float | None, direction: str) -> float | None:
    if price is None or mark_price is None or mark_price <= 0:
        return None
    side = 1 if direction == "long" else -1
    return round(((price - mark_price) / mark_price) * 100 * side, 2)


def _is_favorable_target(
    price: float,
    mark_price: float | None,
    direction: str,
    entry_price: float | None = None,
) -> bool:
    anchors = [value for value in (mark_price, entry_price) if value is not None and value > 0]
    if not anchors:
        return True
    if direction == "long":
        return all(price > anchor for anchor in anchors)
    return all(price < anchor for anchor in anchors)


def _previous_extreme(candles: list[dict[str, Any]], direction: str) -> float | None:
    if not candles:
        return None
    recent = candles[-100:]
    if direction == "long":
        values = [item.get("high") for item in recent if item.get("high") is not None]
        return max(values) if values else None
    values = [item.get("low") for item in recent if item.get("low") is not None]
    return min(values) if values else None


def _levels(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict) and item.get("price") is not None]
    return []


_STRENGTH_LABELS = {"strong": "반응 강함", "medium": "반응 보통", "weak": "반응 약함"}


def _basis(level: dict[str, Any], fallback: str) -> str:
    label = level.get("label") or fallback
    strength = level.get("strength")
    if strength:
        return f"{label} · {_STRENGTH_LABELS.get(str(strength), str(strength))}"
    return str(label)


def _dedupe_price_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[float] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        price = item.get("price")
        key = round(float(price), 8) if price is not None else None
        if key is None or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _format_price(value: Any) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.8f}".rstrip("0").rstrip(".")
    return str(value)


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_signed_pct(value: float | None) -> str:
    if value is None:
        return "표본 부족"
    return f"{value:+.2f}%"


def _format_funding(value: float) -> str:
    return f"{value * 100:+.4f}%"
