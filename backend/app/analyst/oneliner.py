"""TA별 1줄 판정 엔진 (WO-FCE-43 Part A).

각 TA 모듈의 내부 상태를 고정 어휘 1줄 판정으로 증류한다.
자유 문장 생성 금지 — 어휘 사전(PHRASES) 밖의 phrase는 빌드가 실패한다.
충돌 은폐 금지 — 모듈 간 stance 불일치는 그대로 노출하고, 종합은 카운트 1줄만.
결정 트리는 docs/OneLiner.md 참조.
"""

from __future__ import annotations

from typing import Any

Stance = str  # "상방" | "하방" | "횡보" | "판단불가"

STANCES = ("상방", "하방", "횡보", "판단불가")

# 공용 폴백: 모듈 입력 자체가 없을 때 (stance는 항상 판단불가).
FALLBACK_PHRASE = "데이터 부족"

# 어휘 사전 — phrase → stance 고정 매핑. 여기 없는 phrase는 _line()이 거부한다.
# stance를 phrase에서 파생시키므로 phrase-stance 불일치가 구조적으로 불가능하다.
PHRASES: dict[str, dict[str, Stance]] = {
    "wyckoff": {
        "매집 우세": "상방",
        "분산 우세": "하방",
        "레인지 미형성": "판단불가",
        "추세 진행 중": "횡보",  # stance는 추세 방향으로 오버라이드 (아래 참조)
    },
    "liquidity": {
        "저점 청소 후 반등 구조": "상방",
        "고점 청소 후 하락 경계": "하방",
        "풀 대기": "횡보",
        "신호 없음": "판단불가",
    },
    "volume": {
        "매수 체결 우위": "상방",
        "매도 체결 우위": "하방",
        "균형": "횡보",
        "데이터 부족": "판단불가",
    },
    "harmonic": {
        "상방 반전 구간 접근": "상방",
        "하방 반전 구간 접근": "하방",
        "패턴 없음": "판단불가",
    },
    "levels": {
        "지지 위 유지": "상방",
        "저항 아래 정체": "하방",
        "레벨 사이 중립": "횡보",
    },
    "derivatives": {
        "롱 쏠림 경계": "하방",
        "숏 쏠림 경계": "상방",
        "중립": "횡보",
    },
    "indicators": {
        "상승 우세": "상방",
        "하락 우세": "하방",
        "중립": "횡보",
    },
}

MODULE_LABELS = {
    "wyckoff": "와이코프",
    "liquidity": "유동성",
    "volume": "볼륨",
    "harmonic": "하모닉",
    "levels": "레벨",
    "derivatives": "수급",
    "indicators": "지표",
}

# "추세 진행 중"만 phrase 고정 + stance 가변 (추세 방향) — 어휘 사전 유일한 예외.
_TREND_STANCE_OVERRIDE = {"bullish": "상방", "neutral_to_bullish": "상방", "bearish": "하방", "bearish_to_neutral": "하방"}


class OneLinerVocabularyError(ValueError):
    """어휘 사전 밖 phrase로 1줄 판정을 빌드하려는 시도."""


def _line(
    module: str,
    phrase: str,
    confidence_class: str,
    evidence_ref: str,
    *,
    stance_override: Stance | None = None,
) -> dict[str, Any]:
    vocabulary = PHRASES.get(module)
    if vocabulary is None:
        raise OneLinerVocabularyError(f"unknown module: {module}")
    if phrase == FALLBACK_PHRASE:
        stance: Stance = "판단불가"
    elif phrase in vocabulary:
        stance = vocabulary[phrase]
    else:
        raise OneLinerVocabularyError(f"phrase not in vocabulary: {module}/{phrase}")
    if stance_override is not None:
        if stance_override not in STANCES:
            raise OneLinerVocabularyError(f"invalid stance override: {stance_override}")
        stance = stance_override
    if confidence_class not in {"강", "중", "약"}:
        raise OneLinerVocabularyError(f"invalid confidence class: {confidence_class}")
    if len(phrase) > 25:
        raise OneLinerVocabularyError(f"phrase exceeds 25 chars: {phrase}")
    return {
        "module": module,
        "module_label": MODULE_LABELS[module],
        "stance": stance,
        "phrase": phrase,
        "confidence_class": confidence_class,
        "evidence_ref": evidence_ref,
    }


def build_one_liners(analysis: dict[str, Any], *, confluence: dict[str, Any] | None = None) -> dict[str, Any]:
    """분석 페이로드 → 7모듈 1줄 판정 + 종합 카운트.

    confluence(선택)는 종합 stance 산출에만 쓴다(CI 가중) — 개별 줄은 왜곡하지 않는다.
    """
    lines = [
        _wyckoff_line(analysis),
        _liquidity_line(analysis),
        _volume_line(analysis),
        _harmonic_line(analysis),
        _levels_line(analysis),
        _derivatives_line(analysis),
        _indicators_line(analysis),
    ]
    counts = {stance: 0 for stance in STANCES}
    for line in lines:
        counts[line["stance"]] += 1
    overall = _overall_stance(counts, confluence)
    summary_parts = [f"{stance} {counts[stance]}" for stance in STANCES if counts[stance] > 0]
    return {
        "lines": lines,
        "counts": counts,
        "overall_stance": overall,
        "summary": "종합: " + " · ".join(summary_parts),
        "policy": "모듈 간 불일치는 그대로 노출합니다. 판단은 사용자 몫입니다.",
    }


def _overall_stance(counts: dict[str, int], confluence: dict[str, Any] | None) -> Stance:
    """종합 stance — 컨플루언스(CI 가중)가 있으면 그것을, 없으면 단순 다수결."""
    if isinstance(confluence, dict):
        stance = str(confluence.get("stance") or "")
        if stance == "long_leaning":
            return "상방"
        if stance == "short_leaning":
            return "하방"
        if stance in {"conflicted", "insufficient"}:
            return "판단불가"
    if counts["상방"] > counts["하방"]:
        return "상방"
    if counts["하방"] > counts["상방"]:
        return "하방"
    if counts["횡보"] > 0 and counts["상방"] == counts["하방"] == 0:
        return "횡보"
    return "판단불가"


# ── 와이코프 ──────────────────────────────────────────────────────
# 결정 트리 (docs/OneLiner.md §와이코프):
#   1. phase == trending → "추세 진행 중" (stance = 추세 방향)
#   2. side == accumulation → "매집 우세" / distribution → "분산 우세"
#      단, 반대측 최고 신뢰 이벤트 ≥ 65면 confidence_class는 "약"으로 강등 (혼합 신호 노출)
#   3. 그 외 (undetermined, 레인지 없음/이벤트 근거 부족) → "레인지 미형성"

def _wyckoff_line(analysis: dict[str, Any]) -> dict[str, Any]:
    wyckoff = analysis.get("wyckoff") if isinstance(analysis.get("wyckoff"), dict) else {}
    if not wyckoff:
        return _line("wyckoff", FALLBACK_PHRASE, "약", "wyckoff")
    phase = str(wyckoff.get("phase") or "undetermined")
    side = str(wyckoff.get("side") or "neutral")
    events = [event for event in wyckoff.get("events", []) if isinstance(event, dict)]

    if phase == "trending":
        trend = wyckoff.get("trend") if isinstance(wyckoff.get("trend"), dict) else {}
        direction = str(trend.get("direction") or "neutral")
        stance = _TREND_STANCE_OVERRIDE.get(direction, "횡보")
        strong = direction in {"bullish", "bearish"}
        return _line("wyckoff", "추세 진행 중", "중" if strong else "약", "wyckoff.trend.direction", stance_override=stance)

    if side in {"accumulation", "distribution"}:
        own_score = int(wyckoff.get("accumulation_score" if side == "accumulation" else "distribution_score") or 0)
        opposing_side = "distribution" if side == "accumulation" else "accumulation"
        opposing_best = max(
            (int(event.get("confidence") or 0) for event in events if event.get("side") == opposing_side),
            default=0,
        )
        if opposing_best >= 65:
            confidence_class = "약"  # 혼합 신호 — 판정은 유지하되 강도를 낮춰 노출
        elif own_score >= 70:
            confidence_class = "강"
        elif own_score >= 55:
            confidence_class = "중"
        else:
            confidence_class = "약"
        phrase = "매집 우세" if side == "accumulation" else "분산 우세"
        return _line("wyckoff", phrase, confidence_class, f"wyckoff.side={side}")

    return _line("wyckoff", "레인지 미형성", "약", "wyckoff.phase=undetermined")


# ── 유동성 ────────────────────────────────────────────────────────
# 결정 트리: 확정 스윕(최근 것 우선) → 방향. 확정 스윕 없고 풀 존재 → "풀 대기". 그 외 "신호 없음".

def _liquidity_line(analysis: dict[str, Any]) -> dict[str, Any]:
    liquidity = analysis.get("liquidity") if isinstance(analysis.get("liquidity"), dict) else {}
    if not liquidity:
        return _line("liquidity", FALLBACK_PHRASE, "약", "liquidity")
    sweeps = [
        sweep
        for sweep in (list(liquidity.get("sweeps") or []) + list(liquidity.get("htf_range_sweeps") or []))
        if isinstance(sweep, dict) and sweep.get("confirmed")
    ]
    if sweeps:
        latest = max(sweeps, key=lambda sweep: str(sweep.get("return_at") or ""))
        grade = str(latest.get("grade") or "")
        confidence_class = "강" if grade == "Strong" else "중" if grade == "Mid" else "약"
        if latest.get("side") == "sell_side":
            return _line("liquidity", "저점 청소 후 반등 구조", confidence_class, f"liquidity.sweep:{latest.get('id') or latest.get('return_at')}")
        if latest.get("side") == "buy_side":
            return _line("liquidity", "고점 청소 후 하락 경계", confidence_class, f"liquidity.sweep:{latest.get('id') or latest.get('return_at')}")
    pools = [pool for pool in (liquidity.get("pools") or []) if isinstance(pool, dict)]
    if pools:
        return _line("liquidity", "풀 대기", "약", "liquidity.pools")
    return _line("liquidity", "신호 없음", "약", "liquidity")


# ── 볼륨 ──────────────────────────────────────────────────────────
# 결정 트리: 실체결 없음 → "데이터 부족". |delta| ≥ 0.2 → 우위 (0.45 강 / 0.3 중 / 약). 그 외 "균형".

def _volume_line(analysis: dict[str, Any]) -> dict[str, Any]:
    xray = analysis.get("volume_xray") if isinstance(analysis.get("volume_xray"), dict) else {}
    if not xray or not xray.get("data_available"):
        return _line("volume", "데이터 부족", "약", "volume_xray.data_available=false")
    delta = xray.get("delta_ratio")
    try:
        delta_value = float(delta)
    except (TypeError, ValueError):
        return _line("volume", "데이터 부족", "약", "volume_xray.delta_ratio=null")
    magnitude = abs(delta_value)
    if magnitude >= 0.2:
        confidence_class = "강" if magnitude >= 0.45 else "중" if magnitude >= 0.3 else "약"
        phrase = "매수 체결 우위" if delta_value > 0 else "매도 체결 우위"
        return _line("volume", phrase, confidence_class, f"volume_xray.delta_ratio={round(delta_value, 3)}")
    return _line("volume", "균형", "중" if xray.get("volume_state") == "balanced_flow" else "약", "volume_xray.delta_ratio")


# ── 하모닉 ────────────────────────────────────────────────────────
# 결정 트리: 최고 신뢰 패턴 방향 → 반전 구간. PRZ 거리 >5%면 "약" 강등. 패턴 없으면 "패턴 없음".

def _harmonic_line(analysis: dict[str, Any]) -> dict[str, Any]:
    patterns = [pattern for pattern in (analysis.get("harmonic_patterns") or []) if isinstance(pattern, dict)]
    if not patterns:
        return _line("harmonic", "패턴 없음", "약", "harmonic_patterns=[]")
    best = max(patterns, key=lambda pattern: float(pattern.get("confidence") or 0))
    direction = str(best.get("direction") or "")
    if direction not in {"bullish", "bearish"}:
        return _line("harmonic", "패턴 없음", "약", "harmonic.direction=unknown")
    confidence = float(best.get("confidence") or 0)
    confidence_class = "강" if confidence >= 80 else "중" if confidence >= 70 else "약"
    prz = best.get("prz") if isinstance(best.get("prz"), dict) else {}
    mark = analysis.get("mark_price")
    try:
        mid = float(prz.get("mid"))
        distance_pct = abs(float(mark) - mid) / float(mark) * 100
        if distance_pct > 5.0:
            confidence_class = "약"  # PRZ가 멀면 "접근" 판정 강도 강등
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    phrase = "상방 반전 구간 접근" if direction == "bullish" else "하방 반전 구간 접근"
    ref = str(best.get("label") or best.get("name") or "harmonic")
    return _line("harmonic", phrase, confidence_class, f"harmonic:{ref}")


# ── 레벨 ──────────────────────────────────────────────────────────
# 결정 트리: 지지-저항 밴드 내 위치. 하단 1/3 (지지 근접·상회) → "지지 위 유지",
# 상단 1/3 → "저항 아래 정체", 중앙 → "레벨 사이 중립". 레벨 없으면 "데이터 부족".

def _levels_line(analysis: dict[str, Any]) -> dict[str, Any]:
    levels = analysis.get("price_levels") if isinstance(analysis.get("price_levels"), dict) else {}
    mark = analysis.get("mark_price")
    try:
        mark_value = float(mark)
    except (TypeError, ValueError):
        return _line("levels", FALLBACK_PHRASE, "약", "price_levels.mark=null")
    supports = [level for level in (levels.get("support") or []) if isinstance(level, dict) and isinstance(level.get("price"), (int, float))]
    resistances = [level for level in (levels.get("resistance") or []) if isinstance(level, dict) and isinstance(level.get("price"), (int, float))]
    below = [level for level in supports if float(level["price"]) <= mark_value]
    above = [level for level in resistances if float(level["price"]) >= mark_value]
    if not below or not above:
        return _line("levels", FALLBACK_PHRASE, "약", "price_levels=insufficient")
    support = max(below, key=lambda level: float(level["price"]))
    resistance = min(above, key=lambda level: float(level["price"]))
    band = float(resistance["price"]) - float(support["price"])
    if band <= 0:
        return _line("levels", "레벨 사이 중립", "약", "price_levels.band=0")
    position = (mark_value - float(support["price"])) / band
    if position <= 1 / 3:
        score = int(support.get("score") or 0)
        confidence_class = "강" if score >= 80 else "중" if score >= 70 else "약"
        return _line("levels", "지지 위 유지", confidence_class, f"levels.support={support['price']}")
    if position >= 2 / 3:
        score = int(resistance.get("score") or 0)
        confidence_class = "강" if score >= 80 else "중" if score >= 70 else "약"
        return _line("levels", "저항 아래 정체", confidence_class, f"levels.resistance={resistance['price']}")
    return _line("levels", "레벨 사이 중립", "중", f"levels.position={round(position, 2)}")


# ── 수급 ──────────────────────────────────────────────────────────
# 결정 트리: 펀딩 극단 양수(롱 과밀) → "롱 쏠림 경계"(하방), 극단 음수 → "숏 쏠림 경계"(상방).
# 극단 아님 → "중립". 파생 데이터 없음 → "데이터 부족".

def _derivatives_line(analysis: dict[str, Any]) -> dict[str, Any]:
    derivatives = analysis.get("derivatives") if isinstance(analysis.get("derivatives"), dict) else {}
    signals = derivatives.get("signals") if isinstance(derivatives.get("signals"), dict) else {}
    if not signals:
        return _line("derivatives", FALLBACK_PHRASE, "약", "derivatives=absent")
    funding = signals.get("funding_state") if isinstance(signals.get("funding_state"), dict) else {}
    crowding = signals.get("crowding_score") if isinstance(signals.get("crowding_score"), dict) else {}
    try:
        crowd_score = float(crowding.get("score"))
    except (TypeError, ValueError):
        crowd_score = None
    if funding.get("state") == "extreme":
        try:
            funding_value = float(funding.get("funding"))
        except (TypeError, ValueError):
            funding_value = None
        if funding_value is not None:
            confidence_class = "강" if (crowd_score or 0) >= 70 else "중"
            phrase = "롱 쏠림 경계" if funding_value > 0 else "숏 쏠림 경계"
            return _line("derivatives", phrase, confidence_class, f"derivatives.funding={funding_value}")
    return _line("derivatives", "중립", "약", "derivatives.funding_state")


# ── 지표 ──────────────────────────────────────────────────────────
# 결정 트리: 3표 합산 — RSI(>55 / <45), MACD 히스토그램 부호, 볼린저 중심선 대비 종가.
# |합| 3 → 강, 2 → 중, 1 → 약. 0 → "중립".

def _indicators_line(analysis: dict[str, Any]) -> dict[str, Any]:
    indicators = analysis.get("indicators") if isinstance(analysis.get("indicators"), dict) else {}
    if not indicators:
        return _line("indicators", FALLBACK_PHRASE, "약", "indicators=absent")
    votes = 0
    voted = 0
    rsi_series = indicators.get("rsi") or []
    if isinstance(rsi_series, list) and rsi_series:
        rsi = float(rsi_series[-1].get("value") or 50)
        voted += 1
        if rsi > 55:
            votes += 1
        elif rsi < 45:
            votes -= 1
    macd_series = indicators.get("macd") or []
    if isinstance(macd_series, list) and macd_series:
        last = macd_series[-1]
        try:
            histogram = float(last.get("histogram"))
            voted += 1
            if histogram > 0:
                votes += 1
            elif histogram < 0:
                votes -= 1
        except (TypeError, ValueError):
            pass
    bollinger = indicators.get("bollinger") if isinstance(indicators.get("bollinger"), dict) else {}
    middle_series = bollinger.get("middle") or []
    if isinstance(middle_series, list) and middle_series:
        try:
            close = float(analysis.get("mark_price"))
            middle = float(middle_series[-1].get("value"))
            voted += 1
            if close > middle:
                votes += 1
            elif close < middle:
                votes -= 1
        except (TypeError, ValueError):
            pass
    if voted == 0:
        return _line("indicators", FALLBACK_PHRASE, "약", "indicators=empty")
    if votes > 0:
        confidence_class = "강" if votes >= 3 else "중" if votes == 2 else "약"
        return _line("indicators", "상승 우세", confidence_class, f"indicators.votes=+{votes}")
    if votes < 0:
        confidence_class = "강" if votes <= -3 else "중" if votes == -2 else "약"
        return _line("indicators", "하락 우세", confidence_class, f"indicators.votes={votes}")
    return _line("indicators", "중립", "중", "indicators.votes=0")
