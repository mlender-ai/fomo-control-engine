"""WO-FCE-55 Part A — 압축 라이브 차트의 2게이지 판정.

방향 게이지와 익절 압력 게이지는 서로 다른 축이다(합치지 않는다):
- 방향 게이지: WO-50~54 재설계된 stance(시간가중·HTF앵커·히스테리시스)를 바늘로 번역만 한다.
  새 판정 로직 없음 — confluence가 유일한 방향 소스.
- 익절 압력 게이지: 수익 반납 리스크. 결정론 3입력(상단 유동성 소진·거래량 둔화·저항/PRZ 근접).
  포지션 보유 시에만 활성. "롱 우세인데 익절 압력 높음"이 정상 표현된다.

read-only 불변 — 게이지는 판단 표시일 뿐 주문과 무관. 공식은 docs/CompressedChart.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.analyst.briefing import _engine_label
from app.analyst.confluence import TIMEFRAME_MINUTES, _parse_dt

# ── 익절 압력 상수 (docs/CompressedChart.md §익절 공식과 동기화) ──────────────
TP_WEIGHTS = {"liquidity_exhaustion": 0.40, "barrier_proximity": 0.35, "volume_slowdown": 0.25}
TP_LEVEL_LOW_MAX = 0.35  # pressure < 0.35 → 낮음
TP_LEVEL_MID_MAX = 0.65  # < 0.65 → 중간, 이상 → 높음
TP_PROXIMITY_FULL_PCT = 3.0  # 장벽까지 3% 이상이면 근접 0, 0%면 1 (선형)
TP_RV_CALM = 1.2  # 상대거래량 이 값 이상이면 둔화 0
TP_RV_SPAN = 0.8  # (1.2-rv)/0.8 선형 → rv 0.4에서 1.0
TP_RECENT_SWEEP_BARS = 6.0  # 최근 N캔들 내 profit 방향 확정 스윕 = 소진 보너스

# 티어2 오버레이 후보 엔진 (티어1 관측 3축 제외 — 성적이 자격을 정한다)
TIER2_ENGINES = {"wyckoff", "harmonic", "derivatives"}
TIER2_MAX_OVERLAYS = 2
EVENT_PILL_MAX = 6


def build_gauges(
    *,
    analysis: dict[str, Any],
    confluence: dict[str, Any],
    historical_backtest: dict[str, Any] | None = None,
    position: dict[str, Any] | None = None,
    now: datetime | None = None,
    timeframe: str = "4h",
) -> dict[str, Any]:
    """압축 차트 페이로드: 방향 게이지 + 익절 게이지 + 티어2 자동 선별 + 잠정/확정."""
    bar_state = _bar_state(analysis, timeframe, now)
    direction = build_direction_gauge(confluence)
    event_pills = select_validated_event_pills(analysis, historical_backtest, bar_state)
    return {
        "direction": direction,
        "market_view": build_market_view(analysis, confluence, direction),
        "position_context": build_position_context(position, direction),
        "take_profit": build_take_profit_gauge(analysis, position),
        "tier2_overlays": select_tier2_overlays(confluence, historical_backtest),
        "event_pills": event_pills,
        "event_pill_audit": {
            "confirmed_events": _confirmed_event_count(analysis),
            "validated_stats": len(_validated_stats(historical_backtest or {})),
            "rendered": len(event_pills),
        },
        "bar_state": bar_state,
        "policy": "게이지는 판단 표시입니다. 주문과 무관하며 판단은 사용자 몫입니다.",
    }


# ── 방향 게이지 ──────────────────────────────────────────────────────────────


def build_direction_gauge(confluence: dict[str, Any]) -> dict[str, Any]:
    """stance(held) → 세로 바늘. -1(숏 극단)~+1(롱 극단), 0=균형.

    바늘 위치는 히스테리시스 EMA 점수 기반 — 순간 스파이크에 바늘이 튀지 않는다.
    transitioning이면 UI가 바늘 떨림 + "전환 관찰 중"을 표시한다(억지 방향 금지, WO-53).
    """
    stance = str(confluence.get("stance") or "insufficient")
    state = _dict(confluence.get("stance_state"))
    long_ema = _num(state.get("long_score_ema"), _num(confluence.get("long_score"), 0.0))
    short_ema = _num(state.get("short_score_ema"), _num(confluence.get("short_score"), 0.0))
    total = long_ema + short_ema
    needle = round((long_ema - short_ema) / total, 3) if total > 0 else 0.0
    transitioning = bool(state.get("transitioning"))
    active = stance != "insufficient"
    return {
        "active": active,
        "needle": needle if active else 0.0,
        "stance": stance,
        "stance_label": _market_stance_label(stance),
        "confidence": round(abs(needle), 3),
        "transitioning": transitioning,
        "target": state.get("target"),
        "flip_progress": _num(state.get("flip_threshold_progress"), 0.0),
        "candles_in_state": state.get("candles_in_state"),
        "since": state.get("since"),
        "last_flip_at": state.get("last_flip_at"),
        "previous_stance": state.get("previous_stance"),
        "long_evidence_count": len(_list(confluence.get("long_evidence"))),
        "short_evidence_count": len(_list(confluence.get("short_evidence"))),
        "reason": _direction_reason(stance, transitioning, state, confluence),
    }


def build_market_view(
    analysis: dict[str, Any],
    confluence: dict[str, Any],
    direction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """포지션 보유 여부와 무관한 압축 차트 1층 페이로드."""
    gauge = direction or build_direction_gauge(confluence)
    stance = str(gauge.get("stance") or "insufficient")
    why_key = "long_evidence" if stance == "long_leaning" else "short_evidence" if stance == "short_leaning" else None
    counter_key = "short_evidence" if stance == "long_leaning" else "long_evidence" if stance == "short_leaning" else None
    why = _first_claim(confluence, why_key) if why_key else _balanced_claim(confluence)
    counter = _first_claim(confluence, counter_key) if counter_key else _counter_claim(confluence)
    return {
        "stance": stance,
        "stance_label": _market_stance_label(stance),
        "why": _neutral_market_phrase(why or str(gauge.get("reason") or "시장 근거 갱신 중")),
        "counter": _neutral_market_phrase(counter or "반대 근거는 아직 강하게 확인되지 않았습니다."),
        "next_price": _market_next_price(analysis, stance),
    }


def build_position_context(position: dict[str, Any] | None, direction: dict[str, Any]) -> dict[str, Any]:
    """시장 판정과 분리된 보유 포지션 정합/역행 2층 페이로드."""
    position_direction = str((position or {}).get("direction") or "")
    if position_direction not in {"long", "short"}:
        return {"active": False, "alignment": None, "headline": None, "detail": None}
    stance = str(direction.get("stance") or "insufficient")
    aligned = (position_direction == "long" and stance == "long_leaning") or (position_direction == "short" and stance == "short_leaning")
    opposed = (position_direction == "long" and stance == "short_leaning") or (position_direction == "short" and stance == "long_leaning")
    alignment = "aligned" if aligned else "opposed" if opposed else "neutral"
    position_label = "롱" if position_direction == "long" else "숏"
    return {
        "active": True,
        "direction": position_direction,
        "alignment": alignment,
        "headline": f"{position_label} 보유 중 · 시장은 {_market_stance_label(stance)}",
        "detail": "시장 방향과 정합" if aligned else "역행 포지션" if opposed else "시장 판단 유보",
    }


def select_validated_event_pills(
    analysis: dict[str, Any],
    historical_backtest: dict[str, Any] | None,
    bar_state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return immutable chart pills for confirmed events with validated statistics.

    Candidate engines are intentionally not inspected here.  Qualification is
    derived from the persisted signature lifecycle, not from the event label.
    """
    if not isinstance(historical_backtest, dict):
        return []
    validated = _validated_stats(historical_backtest)
    events: list[dict[str, Any]] = []

    liquidity = _dict(analysis.get("liquidity"))
    for sweep in _list(liquidity.get("sweeps")) + _list(liquidity.get("htf_range_sweeps")):
        if not sweep.get("confirmed"):
            continue
        direction = "long" if sweep.get("side") == "sell_side" else "short" if sweep.get("side") == "buy_side" else None
        if direction is None:
            continue
        prefix = "htf_sweep" if sweep.get("type") == "htf_range_sweep" else "sweep"
        event_type = f"{prefix}_{'low' if direction == 'long' else 'high'}"
        confidence = int(_num(sweep.get("confidence"), 0))
        stat = _matching_validated_stat(validated, "liquidity", event_type, direction, confidence)
        if stat is None:
            continue
        events.append(
            _pill(
                event_id=str(sweep.get("id") or f"{event_type}:{sweep.get('timestamp')}"),
                time_value=sweep.get("return_at") or sweep.get("timestamp"),
                price=sweep.get("price") or sweep.get("pool_price"),
                direction=direction,
                label="저점 청소" if direction == "long" else "고점 청소",
                confidence=confidence,
                stat=stat,
            )
        )

    for marker in _list(analysis.get("wyckoff_markers")):
        marker_type = str(marker.get("type") or "").lower()
        label = str(marker.get("label") or marker_type)
        if marker_type in {"spring", "sos", "lps"} or any(token in label.lower() for token in ("spring", "sos", "lps")):
            direction, event_type = "long", "spring_confirmed" if "spring" in f"{marker_type} {label}".lower() else "wyckoff_long_event"
        elif marker_type in {"utad", "sow", "lpsy"} or any(token in label.lower() for token in ("utad", "sow", "lpsy")):
            direction, event_type = "short", "utad_confirmed" if "utad" in f"{marker_type} {label}".lower() else "wyckoff_short_event"
        else:
            continue
        confidence = int(_num(marker.get("confidence"), 0))
        stat = _matching_validated_stat(validated, "wyckoff", event_type, direction, confidence)
        if stat is None:
            continue
        events.append(
            _pill(
                event_id=str(marker.get("id") or f"{event_type}:{marker.get('time')}"),
                time_value=marker.get("time"),
                price=marker.get("price"),
                direction=direction,
                label=label,
                confidence=confidence,
                stat=stat,
            )
        )

    for pattern in _list(analysis.get("harmonic_patterns")):
        if str(pattern.get("status")) != "completed":
            continue
        direction = "long" if pattern.get("direction") == "bullish" else "short" if pattern.get("direction") == "bearish" else None
        if direction is None:
            continue
        confidence = int(_num(pattern.get("confidence"), 0))
        stat = _matching_validated_stat(validated, "harmonic", "prz_touch", direction, confidence)
        points = _list(pattern.get("points"))
        anchor = points[-1] if points else {}
        prz = _dict(pattern.get("prz"))
        if stat is None:
            continue
        events.append(
            _pill(
                event_id=str(pattern.get("id") or f"harmonic:{anchor.get('time')}"),
                time_value=anchor.get("time"),
                price=anchor.get("price") or prz.get("mid"),
                direction=direction,
                label=f"{pattern.get('label') or pattern.get('name') or '반전 구간'}",
                confidence=confidence,
                stat=stat,
            )
        )

    confirmed_cutoff = _last_confirmed_timestamp(analysis, bar_state)
    qualified = [event for event in events if event["time"] <= confirmed_cutoff]
    qualified.sort(key=lambda item: (item["time"], item["confidence"]), reverse=True)
    return qualified[:EVENT_PILL_MAX]


def _validated_stats(historical: dict[str, Any]) -> list[dict[str, Any]]:
    combined = [*_list(historical.get("stats")), *_list(historical.get("event_stats"))]
    unique: dict[str, dict[str, Any]] = {}
    for stat in combined:
        if str(stat.get("lifecycle_state")) != "validated":
            continue
        signature = _dict(stat.get("signature"))
        key = "|".join(
            str(value)
            for value in (
                signature.get("engine") or stat.get("engine"),
                signature.get("event_type") or stat.get("event_type"),
                signature.get("strength_class") or stat.get("strength_class"),
                signature.get("direction") or stat.get("direction"),
            )
        )
        unique[key] = stat
    return list(unique.values())


def _matching_validated_stat(stats: list[dict[str, Any]], engine: str, event_type: str, direction: str, confidence: int) -> dict[str, Any] | None:
    for stat in stats:
        signature = _dict(stat.get("signature"))
        if str(signature.get("engine") or stat.get("engine")) != engine:
            continue
        if str(signature.get("event_type") or stat.get("event_type")) != event_type:
            continue
        if str(signature.get("direction") or stat.get("direction")) != direction:
            continue
        strength = str(signature.get("strength_class") or stat.get("strength_class") or "")
        normalized_strength = strength.lower()
        threshold = (
            80
            if ">=80" in strength
            else 70
            if ">=70" in strength or normalized_strength == "strong"
            else 55
            if ">=55" in strength or normalized_strength in {"mid", "weak"}
            else 70
        )
        if confidence >= threshold:
            return stat
    return None


def _pill(*, event_id: str, time_value: Any, price: Any, direction: str, label: str, confidence: int, stat: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event_id,
        "time": _event_timestamp(time_value),
        "price": _num(price, 0),
        "direction": direction,
        "label": label,
        "confidence": confidence,
        "win_1r_pct": stat.get("win_1r_pct"),
        "sample_size": int(stat.get("sample_size") or 0),
        "qualification": "validated",
        "confirmed": True,
    }


def _last_confirmed_timestamp(analysis: dict[str, Any], bar_state: dict[str, Any]) -> int:
    candles = _list(analysis.get("candles"))
    if not candles:
        return 0
    index = -2 if bar_state.get("provisional") is True and len(candles) > 1 else -1
    return int(_num(candles[index].get("time"), 0))


def _direction_reason(stance: str, transitioning: bool, state: dict[str, Any], confluence: dict[str, Any]) -> str:
    """바늘을 움직인 이유 1줄 — 상위 기여 증거의 plain 문구 (WO-43 계승, 점수 노출 없음)."""
    if stance == "insufficient":
        return "유효 증거 부족 — 판단 보류"
    if transitioning:
        target_label = {"long_leaning": "상방", "short_leaning": "하방", "conflicted": "균형"}.get(str(state.get("target")), "반대 방향")
        return f"{target_label} 전환 관찰 중 — 아직 기존 방향 유지"
    if stance == "conflicted":
        return "상방/하방 근거가 팽팽함 — 방향을 고르지 않음"
    key = "long_evidence" if stance == "long_leaning" else "short_evidence"
    evidence = confluence.get(key) if isinstance(confluence.get(key), list) else []
    top = evidence[0] if evidence and isinstance(evidence[0], dict) else None
    if not top:
        return "우세 근거 갱신 중"
    return _neutral_market_phrase(f"{_engine_label(str(top.get('engine') or '-'))}: {top.get('claim', '-')}")


# ── 익절 압력 게이지 ─────────────────────────────────────────────────────────


def build_take_profit_gauge(analysis: dict[str, Any], position: dict[str, Any] | None) -> dict[str, Any]:
    """수익 반납 리스크. 포지션 보유 시에만 활성 — 진입 전엔 비활성(흐림 렌더는 Part B).

    결정론 3입력 (가중 0.40/0.35/0.25, docs/CompressedChart.md §익절 공식):
    1. 상단 유동성 소진 — profit 방향의 미스윕 풀 잔여 수 + 최근 확정 스윕.
    2. 저항/PRZ 근접 — profit 방향 최근접 장벽까지 거리% (3% 선형).
    3. 거래량 둔화 — 상대거래량 하락 + drying_up 상태.
    """
    direction = str((position or {}).get("direction") or "")
    if direction not in {"long", "short"}:
        return {"active": False, "level": None, "pressure": None, "reason": "포지션 없음 — 익절 판단 대상 아님", "components": []}
    mark = _num(analysis.get("mark_price"), 0.0)
    if mark <= 0:
        return {"active": False, "level": None, "pressure": None, "reason": "현재가 미확인", "components": []}

    components: list[dict[str, Any]] = []
    exhaustion = _liquidity_exhaustion(analysis, direction, mark)
    if exhaustion is not None:
        components.append(exhaustion)
    proximity = _barrier_proximity(analysis, direction, mark)
    if proximity is not None:
        components.append(proximity)
    slowdown = _volume_slowdown(analysis)
    if slowdown is not None:
        components.append(slowdown)

    if not components:
        return {"active": True, "level": "낮음", "pressure": 0.0, "reason": "익절 압력 신호 없음", "components": []}

    total_weight = sum(TP_WEIGHTS[c["key"]] for c in components)
    pressure = round(sum(c["score"] * TP_WEIGHTS[c["key"]] for c in components) / total_weight, 3)
    level = "낮음" if pressure < TP_LEVEL_LOW_MAX else "중간" if pressure < TP_LEVEL_MID_MAX else "높음"
    strongest = max(components, key=lambda c: c["score"] * TP_WEIGHTS[c["key"]])
    return {
        "active": True,
        "level": level,
        "pressure": pressure,
        "reason": strongest["phrase"] if strongest["score"] > 0 else "익절 압력 신호 없음",
        "components": components,
    }


def _liquidity_exhaustion(analysis: dict[str, Any], direction: str, mark: float) -> dict[str, Any] | None:
    """profit 방향 목표 유동성이 얼마나 남았나. 롱의 목표=상단 buy_side 풀, 숏=하단 sell_side."""
    liquidity = _dict(analysis.get("liquidity"))
    pools = [p for p in _list(liquidity.get("pools"))]
    side = "buy_side" if direction == "long" else "sell_side"
    relevant = [p for p in pools if str(p.get("side")) == side]
    if not relevant:
        return None  # 풀 데이터 없음 — 이 성분 제외 (가중 재정규화)
    ahead = [p for p in relevant if _is_ahead(_num(p.get("price"), 0.0), mark, direction)]
    unswept_ahead = [p for p in ahead if not p.get("swept")]
    if len(unswept_ahead) == 0:
        score, phrase = 1.0, "목표 유동성 소진 — 남은 미스윕 풀 없음"
    elif len(unswept_ahead) == 1:
        score, phrase = 0.6, "목표 유동성 1개 잔여 — 청소 임박"
    else:
        score, phrase = 0.2, f"목표 유동성 {len(unswept_ahead)}개 잔여"
    # 최근 profit 방향 확정 스윕 = 방금 목표 풀을 청소했다는 직접 신호.
    sweeps = _list(liquidity.get("sweeps")) + _list(liquidity.get("htf_range_sweeps"))
    recent = [s for s in sweeps if s.get("confirmed") and str(s.get("side")) == side]
    if recent:
        score = min(1.0, score + 0.2)
        if score >= 1.0:
            phrase = "목표 풀 청소 확인 — 상단 유동성 소진" if direction == "long" else "목표 풀 청소 확인 — 하단 유동성 소진"
    return {"key": "liquidity_exhaustion", "label": "목표 유동성 소진", "score": round(score, 3), "phrase": phrase}


def _barrier_proximity(analysis: dict[str, Any], direction: str, mark: float) -> dict[str, Any] | None:
    """profit 방향 최근접 장벽(저항/지지 레벨 + 하모닉 PRZ)까지 거리 기반 근접도."""
    barriers: list[tuple[float, str]] = []
    levels = _dict(analysis.get("price_levels"))
    level_key = "resistance" if direction == "long" else "support"
    for level in _list(levels.get(level_key)):
        price = _num(level.get("price"), 0.0)
        if price > 0 and _is_ahead(price, mark, direction):
            barriers.append((price, "저항" if direction == "long" else "지지"))
    for pattern in _list(analysis.get("harmonic_patterns")):
        prz = _dict(pattern.get("prz"))
        mid = _num(prz.get("mid"), 0.0)
        if mid > 0 and _is_ahead(mid, mark, direction):
            barriers.append((mid, "PRZ"))
    if not barriers:
        return {"key": "barrier_proximity", "label": "장벽 근접", "score": 0.0, "phrase": "전방 장벽 없음"}
    nearest_price, kind = min(barriers, key=lambda b: abs(b[0] - mark))
    distance_pct = abs(nearest_price - mark) / mark * 100.0
    score = _clamp(1.0 - distance_pct / TP_PROXIMITY_FULL_PCT, 0.0, 1.0)
    return {
        "key": "barrier_proximity",
        "label": "장벽 근접",
        "score": round(score, 3),
        "phrase": f"{kind} {_price_text(nearest_price)}까지 {distance_pct:.1f}%",
    }


def _volume_slowdown(analysis: dict[str, Any]) -> dict[str, Any] | None:
    xray = _dict(analysis.get("volume_xray"))
    rv = _optional_float(xray.get("relative_volume"))
    if rv is None:
        return None
    score = _clamp((TP_RV_CALM - rv) / TP_RV_SPAN, 0.0, 1.0)
    if str(xray.get("volume_state")) == "drying_up":
        score = max(score, 0.8)
    phrase = f"상대거래량 {rv:.2f}배" + (" — 고갈" if str(xray.get("volume_state")) == "drying_up" else " — 둔화" if score >= 0.5 else "")
    return {"key": "volume_slowdown", "label": "거래량 둔화", "score": round(score, 3), "phrase": phrase}


# ── 티어2 자동 선별 (오버레이는 엔진이 고른다 — 사용자 선택 없음) ─────────────


def select_tier2_overlays(confluence: dict[str, Any], historical_backtest: dict[str, Any] | None) -> list[dict[str, Any]]:
    """현재 방향을 결정한 티어2 증거 중 캘리브레이션 validated인 상위 1~2개만.

    자격 = 같은 engine·direction의 백테스트 시그니처 lifecycle_state == "validated" (WO-37).
    candidate(표본 부족)·degraded·quarantined는 미표시 — 성적이 자격을 정한다.
    stance가 conflicted/insufficient면 방향이 없으므로 오버레이 0개.
    """
    stance = str(confluence.get("stance") or "")
    if stance not in {"long_leaning", "short_leaning"}:
        return []
    direction = "long" if stance == "long_leaning" else "short"
    validated = _validated_engine_directions(historical_backtest)
    key = "long_evidence" if direction == "long" else "short_evidence"
    candidates = [
        item
        for item in _list(confluence.get(key))
        if str(item.get("engine")) in TIER2_ENGINES and (str(item.get("engine")), direction) in validated and _num(item.get("score"), 0.0) > 0
    ]
    candidates.sort(key=lambda item: _num(item.get("score"), 0.0), reverse=True)
    return [
        {
            "engine": item.get("engine"),
            "engine_label": _engine_label(str(item.get("engine") or "-")),
            "claim": item.get("claim"),
            "direction": item.get("direction"),
            "price": item.get("price"),
            "qualification": "validated",
        }
        for item in candidates[:TIER2_MAX_OVERLAYS]
    ]


def _validated_engine_directions(historical_backtest: dict[str, Any] | None) -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    if not isinstance(historical_backtest, dict):
        return result
    for stat in [*_list(historical_backtest.get("stats")), *_list(historical_backtest.get("event_stats"))]:
        if str(stat.get("lifecycle_state")) == "validated":
            engine = str(stat.get("engine") or "")
            direction = str(stat.get("direction") or "")
            if engine and direction:
                result.add((engine, direction))
    return result


def _market_stance_label(stance: str) -> str:
    return {
        "long_leaning": "상방 우세",
        "short_leaning": "하방 우세",
        "conflicted": "충돌 — 판단 유보",
        "insufficient": "근거 부족 — 판단 유보",
    }.get(stance, "시장 판단 대기")


def _first_claim(confluence: dict[str, Any], key: str | None) -> str | None:
    if not key:
        return None
    evidence = _list(confluence.get(key))
    return str(evidence[0].get("claim")) if evidence and evidence[0].get("claim") else None


def _balanced_claim(confluence: dict[str, Any]) -> str | None:
    candidates = [*_list(confluence.get("long_evidence"))[:1], *_list(confluence.get("short_evidence"))[:1]]
    if not candidates:
        return None
    strongest = max(candidates, key=lambda item: _num(item.get("score"), 0.0))
    return str(strongest.get("claim")) if strongest.get("claim") else None


def _counter_claim(confluence: dict[str, Any]) -> str | None:
    counter = _list(confluence.get("counter_evidence"))
    return str(counter[0].get("claim")) if counter and counter[0].get("claim") else None


def _neutral_market_phrase(value: str) -> str:
    replacements = (
        ("롱 유지", "상방 유지"),
        ("숏 유지", "하방 유지"),
        ("롱 시나리오", "상방 구조"),
        ("숏 시나리오", "하방 구조"),
        ("롱 근거", "상방 근거"),
        ("숏 근거", "하방 근거"),
        ("롱 우위", "상방 우세"),
        ("숏 우위", "하방 우세"),
    )
    result = value
    for before, after in replacements:
        result = result.replace(before, after)
    return result


def _market_next_price(analysis: dict[str, Any], stance: str) -> dict[str, Any] | None:
    mark = _num(analysis.get("mark_price"), 0.0)
    if mark <= 0:
        return None
    levels = _dict(analysis.get("price_levels"))
    candidates: list[tuple[float, str]] = []
    for level in _list(levels.get("support")):
        price = _num(level.get("price"), 0.0)
        if price > 0:
            candidates.append((price, "하단 지지"))
    for level in _list(levels.get("resistance")):
        price = _num(level.get("price"), 0.0)
        if price > 0:
            candidates.append((price, "상단 저항"))
    for pool in _list(_dict(analysis.get("liquidity")).get("pools")):
        if pool.get("swept"):
            continue
        price = _num(pool.get("price"), 0.0)
        if price <= 0:
            continue
        label = "상단 유동성" if str(pool.get("side")) == "buy_side" else "하단 유동성"
        candidates.append((price, label))
    if stance == "long_leaning":
        directional = [item for item in candidates if item[0] > mark]
    elif stance == "short_leaning":
        directional = [item for item in candidates if item[0] < mark]
    else:
        directional = candidates
    pool = directional or candidates
    if not pool:
        return None
    price, label = min(pool, key=lambda item: abs(item[0] - mark))
    return {"label": label, "price": price, "detail": "도달 시 시장 구조 재평가"}


def _event_timestamp(value: Any) -> int:
    parsed = _parse_dt(value)
    if parsed is not None:
        return int(parsed.timestamp())
    return int(_num(value, 0))


def _confirmed_event_count(analysis: dict[str, Any]) -> int:
    liquidity = _dict(analysis.get("liquidity"))
    sweeps = [item for item in [*_list(liquidity.get("sweeps")), *_list(liquidity.get("htf_range_sweeps"))] if item.get("confirmed")]
    markers = [item for item in _list(analysis.get("wyckoff_markers")) if item.get("time")]
    patterns = [item for item in _list(analysis.get("harmonic_patterns")) if str(item.get("status")) == "completed"]
    return len(sweeps) + len(markers) + len(patterns)


# ── 잠정/확정 (시간봉 확정 기준) ─────────────────────────────────────────────


def _bar_state(analysis: dict[str, Any], timeframe: str, now: datetime | None) -> dict[str, Any]:
    """캔들 미마감이면 잠정 — 마감까지 남은 시간을 함께 노출 (렌더 흐림은 Part B)."""
    quality = _dict(analysis.get("data_quality"))
    last_candle = _parse_dt(quality.get("last_candle_at") or analysis.get("as_of"))
    if last_candle is None or now is None:
        return {"provisional": None, "minutes_to_close": None, "bar_close_at": None}
    bar_minutes = TIMEFRAME_MINUTES.get(str(timeframe).lower(), 240.0)
    elapsed = (now - last_candle).total_seconds() / 60.0
    # 마지막 캔들 시각으로부터 1캔들이 지나기 전이면 해당 캔들은 아직 진행 중 = 잠정.
    provisional = elapsed < bar_minutes
    minutes_to_close = max(0, int(bar_minutes - elapsed)) if provisional else 0
    close_at = last_candle.timestamp() + bar_minutes * 60
    return {
        "provisional": provisional,
        "minutes_to_close": minutes_to_close,
        "bar_close_at": datetime.fromtimestamp(close_at, tz=last_candle.tzinfo).isoformat(),
    }


# ── helpers ──────────────────────────────────────────────────────────────────


def _is_ahead(price: float, mark: float, direction: str) -> bool:
    return price > mark if direction == "long" else price < mark


def _price_text(value: float) -> str:
    if abs(value) >= 100:
        return f"{value:,.0f}"
    if abs(value) >= 1:
        return f"{value:.2f}"
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


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
