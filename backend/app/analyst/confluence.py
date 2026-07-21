from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.backtest.statistics import bootstrap_ci_from_counts
from app.db.models import JudgmentScore, utc_now

CALIBRATION_SAMPLE_FLOOR = 20
CONFLICT_RATIO = 0.25
MIN_DIRECTIONAL_EVIDENCE = 3

# WO-51: 증거의 "방향 기여"에 시간 감쇠를 곱한다. 존재/역사성은 삭제하지 않고
# (레벨은 계속 관측·표기), 방향 기여만 감쇠한다. 반감기는 절대 시간이 아니라
# 타임프레임 상대값(캔들 수)이다 — 4h·15m 어디서도 동일 규범이 성립하도록.
#   recency = floor + (1 - floor) * 0.5 ** (age_bars / half_life_bars)
# 유형별 반감기·floor (docs/DirectionalEngine.md 표와 동기화):
#   - event    : 스윕·와이코프 마커·BOS/CHoCH·하모닉 — 방금 발생이 중요, 급감쇠.
#   - structure: 지지/저항·POC·국면 — 오래 유효하나 마지막 터치 기준 완만 감쇠, 존재는 유지(높은 floor).
#   - state    : 펀딩·OI 등 현재 상태값 — stale이면 거의 0.
DECAY_PROFILES: dict[str, tuple[float, float]] = {
    # class: (half_life_bars, floor)
    "event": (6.0, 0.05),
    "structure": (30.0, 0.35),
    "state": (3.0, 0.0),
}
DEFAULT_DECAY_CLASS = "event"

# TF→분. analyst 계층이 exchange 세부에 역결합되지 않도록 로컬 상수로 둔다.
TIMEFRAME_MINUTES: dict[str, float] = {
    "1m": 1.0,
    "3m": 3.0,
    "5m": 5.0,
    "15m": 15.0,
    "30m": 30.0,
    "1h": 60.0,
    "2h": 120.0,
    "4h": 240.0,
    "6h": 360.0,
    "12h": 720.0,
    "1d": 1440.0,
    "1w": 10080.0,
}
DEFAULT_BAR_MINUTES = 240.0

# WO-52: HTF(상위 TF) 추세를 방향 산출의 기준선(baseline)으로 승격.
# 하위 증거는 이 기준선에 대한 편차로 해석 — 정렬 증거는 증폭, 역행 증거는 감쇠(0으로 죽이지 않음).
# 배수는 HTF 명확도(strength)에 비례: HTF가 불명확하면 배수≈1 → 하위 증거 비중 자연 회복.
HTF_MAX_BOOST = 0.25  # 정렬 증거 최대 ×1.25
HTF_MAX_DAMP = 0.45  # 역행 증거 최대 ×0.55 (floor, 절대 0 아님)
# htf_trend 라벨 → 방향 성향 [-1, 1] (양수 롱).
HTF_TREND_SCORES: dict[str, float] = {
    "bullish": 1.0,
    "neutral_to_bullish": 0.5,
    "neutral": 0.0,
    "undetermined": 0.0,
    "bearish_to_neutral": -0.5,
    "bearish": -1.0,
}
HTF_BIAS_THRESHOLD = 0.25  # |bias score| 이 값 이상이어야 방향 기준선 성립
HTF_GUARD_STRENGTH = 0.40  # 이 강도 이상의 HTF 기준선에 한해 반대-방향 판정을 보수화
# HTF 반대 방향(또는 엔진이 conflicting 선언)으로 기울려면 이 비율 이상 압도해야 함 —
# 그 미만이면 conflicted(전환 관찰은 WO-53). 통상 정렬 케이스는 CONFLICT_RATIO(0.25) 그대로.
HTF_STRONG_MARGIN = 0.50
HTF_ABSENT_COMPOSITE_CAP = 75.0  # HTF 부재 시 종합 신뢰도 상한 (하위만으로 강한 확정 금지)

# WO-53: 방향 히스테리시스 — 전환에 관성. 경계 진동(분단위 반전)을 흡수하되 진짜 전환은 통과.
# Schmitt 트리거(enter>exit) + EMA 평활 + 연속 확인(persist)의 3중 방어.
# 초기값은 "진동 흡수용"이지 "추세 차단용"이 아니다 — 둔감/민감 트레이드오프는 docs 참조.
#   enter(0.25) > exit(0.10): 방향을 켜기는 어렵게, 끄기도 어렵게 → [0.10,0.25] 데드존이 진동 흡수.
#   flip_margin(0.30): 방향→반대방향은 반대가 명확히 앞설 때만 (지속 확인과 결합).
# 주 방어는 persist(연속 확인)다 — 1바 스파이크는 persist 미달로 흡수. EMA·margin은 보조.
# 그래서 span은 짧게(반응성 유지), 강한 지속 전환은 2~3바에 통과한다(둔감 방지).
HYSTERESIS_EMA_SPAN = 2.0  # 짧은 span: 노이즈만 제거, 반응성 유지
HYSTERESIS_FLIP_MARGIN = 0.30  # 방향→반대방향 flip: EMA 상대우세가 이 이상 반대로
HYSTERESIS_ENTER_MARGIN = 0.25  # conflicted→방향 진입 문턱 (= CONFLICT_RATIO)
HYSTERESIS_EXIT_MARGIN = 0.10  # 방향→conflicted 이탈 문턱 (우세가 이 밑으로 줄면)
HYSTERESIS_FLIP_PERSIST = 2  # 상태 변경 채택에 필요한 연속 확인 횟수(주 진동 방어)
# WO-39 자율 튜닝 대상 + hard bound. 상한은 "진동 흡수 범위를 넘어 추세를 막지 못하도록" 고정.
HYSTERESIS_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "directional_ema_span": (1.0, 10.0),
    "directional_flip_margin": (0.25, 0.60),
    "directional_flip_persist": (1.0, 5.0),
}

ENGINE_BASE_WEIGHTS: dict[str, float] = {
    "liquidity": 18.0,
    "wyckoff": 18.0,
    "harmonic": 16.0,
    "level": 12.0,
    "derivatives": 11.0,
    "volume": 9.0,
    "mtf": 10.0,
    "structure": 8.0,
    "onchain": 6.0,
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
    prior_state: dict[str, Any] | None = None,
    hysteresis_params: dict[str, Any] | None = None,
    directional_v2: bool = True,
) -> dict[str, Any]:
    """Normalize existing engine outputs into long/short evidence stacks.

    This function does not recompute indicators. It only reads the already
    calculated chart-analysis payload and applies documented weighting.

    WO-53: 순수 함수를 유지한다. 히스테리시스 상태는 ``prior_state``로 주입받아
    새 상태를 ``stance_state``로 반환할 뿐, 저장/조회 I/O는 호출자(라이브·백테스트
    공유 래퍼)가 담당한다 — 전환 로직 자체는 여기 한 곳에만 있어 이중화가 없다.
    """

    now = generated_at or utc_now()
    calibration = _calibration_index(calibration_scores or [])
    htf_context = _htf_context(analysis)
    raw_evidence = _collect_evidence(analysis, htf_context)
    bar_minutes = _timeframe_minutes(timeframe)
    evidence = [
        _apply_weight(item, calibration, now, bar_minutes, htf_context, apply_recency=directional_v2, apply_htf=directional_v2) for item in raw_evidence
    ]
    if overlap_groups is None:
        historical = analysis.get("historical_backtest") if isinstance(analysis.get("historical_backtest"), dict) else {}
        overlap_groups = historical.get("overlap_groups") if isinstance(historical.get("overlap_groups"), list) else []
    evidence, overlap_suppressed = _suppress_overlaps(evidence, overlap_groups)
    directional = [item for item in evidence if item["direction"] in {"long", "short"} and item["score"] > 0]
    long_evidence = sorted([item for item in directional if item["direction"] == "long"], key=_evidence_sort_key, reverse=True)
    short_evidence = sorted([item for item in directional if item["direction"] == "short"], key=_evidence_sort_key, reverse=True)
    long_score = round(sum(item["score"] for item in long_evidence), 2)
    short_score = round(sum(item["score"] for item in short_evidence), 2)
    # v1(재설계 전) 재현 시 HTF 가드도 끈다.
    raw_stance = _stance(long_evidence, short_evidence, long_score, short_score, htf_context if directional_v2 else None)
    # WO-53: 히스테리시스 — 직전 상태(prior_state) 대비 전환에 관성을 적용.
    # stance는 "유지 중인(held)" 방향, raw_stance는 순간 스냅샷. 전환 로직은 이 한 곳에만.
    params = _hysteresis_params(hysteresis_params)
    if directional_v2:
        # WO-56: 상태는 확정 캔들에서만 전진 — 스냅샷의 마지막 캔들이 미마감이면 그 직전 캔들이 앵커.
        bar_at = _confirmed_bar_iso(analysis, bar_minutes, now)
        stance_state = _resolve_stance_state(raw_stance, long_score, short_score, prior_state, now, params, bar_at=bar_at, bar_minutes=bar_minutes)
    else:
        stance_state = _legacy_stance_state(raw_stance, long_score, short_score, now)
    stance = str(stance_state["stance"])
    long_ema = float(stance_state["long_score_ema"])
    short_ema = float(stance_state["short_score_ema"])
    composite = _composite_score(stance, long_ema, short_ema, long_score, short_score)
    # WO-52 금지 규정: 방향성 HTF 기준선이 없으면(데이터 부재 또는 횡보/전환) 하위 증거만으로
    # 강한 방향 확정 금지 → 종합 신뢰도 상한.
    if htf_context["bias"] == "neutral" and stance in {"long_leaning", "short_leaning"}:
        composite = min(composite, HTF_ABSENT_COMPOSITE_CAP)
    counter = _counter_evidence(stance, long_evidence, short_evidence)

    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "generated_at": now.isoformat(),
        "data_as_of": _analysis_as_of(analysis),
        "max_engine_age_minutes": _max_engine_age_minutes(evidence, now),
        "stance": stance,
        "raw_stance": raw_stance,
        "stance_state": stance_state,
        "stance_label": _stance_label(stance),
        "htf_context": htf_context,
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
            "formula": "effective_score = base_weight × confidence/100 × calibration_factor × recency_factor",
            "calibration_factor": "N>=20이면 적중률 CI 하한/70을 0.60~1.25 사이로 클램프 (점추정 아님, WO-36)",
            "recency_policy": "방향 기여는 마지막 발생/터치 시각 기준 시간 감쇠 (WO-51). 반감기는 TF 상대값 — event 6·structure 30·state 3 캔들, floor 0.05/0.35/0.0. 존재/관측 표기는 불변, 방향 기여만 감쇠.",
            "conflict_ratio": CONFLICT_RATIO,
            "overlap_policy": "같은 overlap_group 증거는 최강 1개만 가중 반영, 나머지는 동근원 확인으로 표기만",
            "hysteresis_policy": f"방향 전환에 관성 (WO-53). EMA(span {params['ema_span']}) 평활 + Schmitt(enter {params['enter_margin']}/exit {params['exit_margin']}) + flip 문턱 {params['flip_margin']}·연속 {int(params['flip_persist'])}회. stance=유지 방향, raw_stance=순간, stance_state.transitioning=전환 관찰 중.",
        },
    }


def _collect_evidence(analysis: dict[str, Any], htf_context: dict[str, Any], directional_v2: bool = True) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    as_of = _analysis_as_of(analysis)
    evidence.extend(_level_evidence(analysis, as_of))
    evidence.extend(_wyckoff_evidence(analysis, as_of))
    evidence.extend(_liquidity_evidence(analysis, as_of))
    evidence.extend(_harmonic_evidence(analysis, as_of))
    evidence.extend(_volume_evidence(analysis, as_of))
    evidence.extend(_derivative_evidence(analysis, as_of))
    evidence.extend(_mtf_evidence(analysis, as_of, htf_context, directional_v2))
    evidence.extend(_validated_onchain_evidence(analysis, as_of))
    return evidence


def _validated_onchain_evidence(analysis: dict[str, Any], as_of: str | None) -> list[dict[str, Any]]:
    """Only already-promoted whale signatures may enter directional scoring."""
    result: list[dict[str, Any]] = []
    for item in _list(analysis.get("validated_onchain_evidence")):
        direction = str(item.get("direction") or "")
        if direction not in {"long", "short"}:
            continue
        result.append(
            _evidence(
                "onchain",
                str(item.get("claim") or "검증 고래 포지션"),
                direction,
                _num(item.get("confidence"), 55),
                item.get("as_of") or as_of,
                price=item.get("price"),
                source={**item, "validation_state": "validated"},
            )
        )
    return result


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
                # WO-51: 레벨의 방향 기여는 "마지막 터치 시각" 기준으로 감쇠 —
                # 오래 안 건드린 레벨은 방향 기여가 줄되(존재는 유지) 3주 전 터치가 방금 반등과 동급이 되지 않게.
                support.get("last_touch_at") or as_of,
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
                resistance.get("last_touch_at") or as_of,
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
                # WO-51: 패턴 완성 시각 기준 감쇠 (없으면 분석 as_of로 폴백).
                _harmonic_time(pattern) or as_of,
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
    money_flow = signals.get("money_flow") if isinstance(signals.get("money_flow"), dict) else None
    if money_flow and money_flow.get("available") and not money_flow.get("provisional"):
        state = str(money_flow.get("state") or "mixed")
        direction = "long" if state in {"spot_led", "spot_absorb"} else "short" if state == "futures_led" else "neutral"
        if state != "mixed":
            result.append(
                _evidence(
                    "derivatives",
                    str(money_flow.get("label") or "자금 흐름"),
                    direction,
                    _num(money_flow.get("confidence"), 0),
                    money_flow.get("as_of") or signals.get("as_of") or as_of,
                    source=money_flow,
                )
            )
    divergence = signals.get("oi_price_divergence") if isinstance(signals.get("oi_price_divergence"), dict) else None
    if divergence:
        state = str(divergence.get("state") or "")
        direction = "long" if state in {"price_up_oi_up", "price_up_oi_down"} else "short" if state in {"price_down_oi_up", "price_down_oi_down"} else None
        if direction:
            confidence = 62 if state in {"price_up_oi_up", "price_down_oi_up"} else 55
            result.append(
                _evidence(
                    "derivatives", str(divergence.get("label") or "OI/가격 변화"), direction, confidence, signals.get("as_of") or as_of, source=divergence
                )
            )

    funding = signals.get("funding_state") if isinstance(signals.get("funding_state"), dict) else None
    crowding = signals.get("crowding_score") if isinstance(signals.get("crowding_score"), dict) else None
    funding_value = _optional_float(funding.get("funding")) if funding else None
    crowd_score = _optional_float(crowding.get("score")) if crowding else None
    # Zero has no directional sign.  Keep this guard even if an upstream
    # percentile payload is malformed so 0% funding can never vote long/short.
    if funding and funding.get("state") == "extreme" and funding_value is not None and funding_value != 0:
        direction = "short" if funding_value > 0 else "long"
        confidence = min(80, 58 + (crowd_score or 0) * 0.2)
        result.append(
            _evidence(
                "derivatives",
                f"{funding.get('label')} + 쏠림 {crowd_score or '-'}",
                direction,
                confidence,
                signals.get("as_of") or as_of,
                source={"funding": funding, "crowding": crowding},
            )
        )
    return result


def _mtf_evidence(analysis: dict[str, Any], as_of: str | None, htf_context: dict[str, Any], directional_v2: bool = True) -> list[dict[str, Any]]:
    # WO-52: HTF는 더 이상 60점 동급 투표자가 아니라 기준선(앵커)이다.
    # 앵커 증거의 신뢰도는 추세 명확도(strength)에 비례 — 명확할수록 강하게 앵커,
    # 횡보/전환이면 약해져 하위 증거 비중이 자연히 회복된다.
    # v1(재설계 전)은 옛 약한 단일 투표(conf 60)로 되돌린다 — WO-54 전후 대조/백테스트.
    bias = htf_context.get("bias")
    if bias not in {"long", "short"}:
        return []
    mtf = analysis.get("wyckoff_mtf") if isinstance(analysis.get("wyckoff_mtf"), dict) else {}
    strength = float(htf_context.get("strength") or 0.0)
    confidence = round(50 + 40 * strength) if directional_v2 else 60
    label = htf_context.get("htf_trend") or htf_context.get("htf_phase") or ("상승" if bias == "long" else "하락")
    return [_evidence("mtf", f"상위 TF {label} 추세 (기준선)", bias, confidence, as_of, source=mtf)]


def _apply_weight(
    evidence: dict[str, Any],
    calibration: dict[tuple[str, int], dict[str, Any]],
    now: datetime,
    bar_minutes: float = DEFAULT_BAR_MINUTES,
    htf_context: dict[str, Any] | None = None,
    *,
    apply_recency: bool = True,
    apply_htf: bool = True,
) -> dict[str, Any]:
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
    as_of = evidence.get("as_of")
    # WO-51: 시간 감쇠를 score에 곱한다. is_stale(하드 boolean)은 UI 표기로만 유지하고,
    # 방향 기여는 연속 감쇠(recency)로 대체 — 하드 컷 대신 소프트 감쇠.
    decay_class = _decay_class(engine, str(evidence.get("claim") or ""))
    # apply_recency/apply_htf=False는 재설계 이전(v1) 엔진 재현용 — WO-54 전후 대조/백테스트 비교.
    recency = _recency_factor(as_of, now, decay_class, bar_minutes) if apply_recency else 1.0
    # WO-52: HTF 기준선 배수 — 정렬 증거 증폭, 역행 증거 감쇠(0으로 죽지 않음).
    # mtf 앵커 자신은 기준선이므로 자기 배수를 적용하지 않는다(순환 방지).
    htf_factor = 1.0 if (engine == "mtf" or not apply_htf) else _htf_factor(str(evidence.get("direction") or ""), htf_context)
    score = round(base_weight * (confidence / 100.0) * factor * recency * htf_factor, 2)
    return {
        **evidence,
        "base_weight": base_weight,
        "calibration_factor": round(factor, 3),
        "recency_factor": recency,
        "decay_class": decay_class,
        "htf_factor": round(htf_factor, 3),
        "score": score,
        "is_stale": _is_stale(as_of, now),
        "calibration": calibration_meta,
    }


def _timeframe_minutes(timeframe: Any) -> float:
    return TIMEFRAME_MINUTES.get(str(timeframe).lower(), DEFAULT_BAR_MINUTES)


def _htf_context(analysis: dict[str, Any]) -> dict[str, Any]:
    """상위 TF 추세 기준선. wyckoff_mtf(htf_phase·htf_trend·alignment)에서 방향 성향과
    명확도(strength)를 도출한다. 수치 strength가 원천에 없으므로 라벨에서 계산한다."""
    mtf_raw = analysis.get("wyckoff_mtf")
    mtf: dict[str, Any] = mtf_raw if isinstance(mtf_raw, dict) else {}
    htf_phase = str(mtf.get("htf_phase") or "")
    htf_trend = str(mtf.get("htf_trend") or "")
    alignment = str(mtf.get("alignment") or "neutral")
    available = bool(htf_phase or htf_trend)  # HTF 원천 데이터 존재 여부(정보성)
    bias, strength = _htf_bias_strength(htf_phase, htf_trend)
    return {
        "bias": bias,
        "strength": strength,
        "alignment": alignment,
        "htf_phase": htf_phase or None,
        "htf_trend": htf_trend or None,
        "available": available,
    }


def _htf_bias_strength(htf_phase: str, htf_trend: str) -> tuple[str, float]:
    """추세 라벨(60%) + 국면 라벨(40%)을 [-1,1] 성향 점수로 합성. 부호=방향, 크기=명확도."""
    phase = htf_phase.lower()
    trend_score = HTF_TREND_SCORES.get(htf_trend.lower(), 0.0)
    if "accumulation" in phase:  # reaccumulation 포함
        phase_score = 1.0
    elif "distribution" in phase:  # redistribution 포함
        phase_score = -1.0
    else:
        phase_score = 0.0
    raw = 0.6 * trend_score + 0.4 * phase_score
    strength = round(min(1.0, abs(raw)), 3)
    if raw >= HTF_BIAS_THRESHOLD:
        return "long", strength
    if raw <= -HTF_BIAS_THRESHOLD:
        return "short", strength
    return "neutral", strength


def _htf_factor(direction: str, htf_context: dict[str, Any] | None) -> float:
    """HTF 기준선 대비 정렬/역행 배수. 역행도 0으로 죽이지 않고 감쇠만(전환 감지 보존).

    단, 엔진이 alignment="conflicting"(HTF-하위 정면 충돌)을 선언하면 배수를 적용하지 않는다.
    앵커 배수는 "하위 신호가 HTF 추세 내 편차(되돌림)"라는 전제 위에서만 성립한다 —
    정면 충돌은 되돌림이 아니라 전환/충돌이므로 증폭·감쇠 대신 conflicted 판정(_stance 가드)에 맡긴다.
    """
    if not htf_context or htf_context.get("alignment") == "conflicting":
        return 1.0
    bias = htf_context.get("bias")
    strength = float(htf_context.get("strength") or 0.0)
    if bias not in {"long", "short"} or direction not in {"long", "short"}:
        return 1.0
    if direction == bias:
        return 1.0 + HTF_MAX_BOOST * strength
    return 1.0 - HTF_MAX_DAMP * strength  # >= 1 - HTF_MAX_DAMP (절대 0 아님)


def _hysteresis_params(overrides: dict[str, Any] | None) -> dict[str, float]:
    """튜닝 오버라이드(WO-39 자율 채택 대상)를 hard bound로 클램프해 병합한다."""
    values = {
        "ema_span": HYSTERESIS_EMA_SPAN,
        "flip_margin": HYSTERESIS_FLIP_MARGIN,
        "enter_margin": HYSTERESIS_ENTER_MARGIN,
        "exit_margin": HYSTERESIS_EXIT_MARGIN,
        "flip_persist": float(HYSTERESIS_FLIP_PERSIST),
    }
    if not overrides:
        return values
    bound_key = {
        "ema_span": "directional_ema_span",
        "flip_margin": "directional_flip_margin",
        "flip_persist": "directional_flip_persist",
    }
    for key in values:
        raw = _optional_float(overrides.get(key))
        if raw is None:
            continue
        bounds = HYSTERESIS_PARAM_BOUNDS.get(bound_key.get(key, ""))
        values[key] = _clamp(raw, bounds[0], bounds[1]) if bounds else raw
    return values


def _resolve_stance_state(
    raw_stance: str,
    long_score: float,
    short_score: float,
    prior_state: dict[str, Any] | None,
    now: datetime,
    params: dict[str, float],
    bar_at: str | None = None,
    bar_minutes: float = DEFAULT_BAR_MINUTES,
) -> dict[str, Any]:
    """방향 히스테리시스 상태 전이. 순수 함수 — prior_state를 받아 새 상태를 돌려줄 뿐 I/O 없음.

    라이브(스카우트 스냅샷)와 백테스트(인메모리 스레딩)가 이 한 함수를 공유한다(이중화 0).

    WO-56 시간축 규약: **상태는 확정 캔들에서만 전진한다.** ``bar_at``(마지막 확정 캔들
    시각)이 직전 상태의 ``last_bar_at``과 같으면 EMA·persist·candles를 전진시키지 않고
    상태를 그대로 반환한다 — 호출 빈도(워커 90s·스카우트 15m·온디맨드 연타)와 무관하게
    persist=2는 정확히 "확정 캔들 2개 연속"을 의미하고, 같은 확정 캔들 = 같은 상태(재현성).
    미마감 캔들의 최신 관측은 ``preview``로만 노출한다(상태와 프리뷰의 분리) —
    프리뷰는 UI 잠정 표시 전용이며 알림·원장에 사용 금지.
    """
    prior = prior_state if isinstance(prior_state, dict) else {}
    prior_stance = prior.get("stance")
    has_prior = prior_stance in {"long_leaning", "short_leaning", "conflicted", "insufficient"}
    preview = {
        "raw_stance": raw_stance,
        "long_score": round(long_score, 2),
        "short_score": round(short_score, 2),
        "as_of": now.isoformat(),
    }

    # ── 캔들 앵커 동결 ──
    # 같은 확정 캔들(또는 확정 캔들 시각 미상)에서는 상태를 전진시키지 않는다.
    bar_dt = _parse_dt(bar_at)
    prior_bar_dt = _parse_dt(prior.get("last_bar_at"))
    if has_prior and prior_bar_dt is not None and (bar_dt is None or bar_dt <= prior_bar_dt):
        return {**prior, "flipped": False, "preview": preview}
    if has_prior and prior_bar_dt is None and bar_dt is None:
        # 양쪽 다 시각 미상 — 전진 근거가 없으므로 동결 (구버전 스냅샷 + 데이터 결손 케이스).
        return {**prior, "flipped": False, "preview": preview}
    # prior에 last_bar_at이 없는 구버전 상태는 이번 확정 캔들에서 1회 전진하며 앵커를 획득한다(마이그레이션).

    # 확정 캔들 전진: 여러 캔들 갭이면 관측은 1회(현재 분석 1개)지만
    # 유지 상태의 경과 캔들 수(candles_in_state)에는 갭을 반영한다 (docs §시간축).
    if bar_dt is not None and prior_bar_dt is not None:
        gap_minutes = (bar_dt - prior_bar_dt).total_seconds() / 60.0
        candles_advanced = max(1, int(round(gap_minutes / max(bar_minutes, 1.0))))
    else:
        candles_advanced = 1

    alpha = 2.0 / (max(float(params["ema_span"]), 1.0) + 1.0)
    long_ema = _ema(prior.get("long_score_ema"), long_score, alpha)
    short_ema = _ema(prior.get("short_score_ema"), short_score, alpha)
    now_iso = now.isoformat()
    bar_iso = _iso(bar_at)

    def build(
        stance: str,
        *,
        transitioning: bool,
        target: str | None,
        pending: str | None,
        pending_count: int,
        since: str | None,
        last_flip_at: str | None,
        candles: int,
        progress: float,
        flipped: bool,
    ) -> dict[str, Any]:
        return {
            "stance": stance,
            "previous_stance": prior_stance,
            "transitioning": transitioning,
            "target": target,
            "pending_stance": pending,
            "pending_count": pending_count,
            "since": since or now_iso,
            "last_flip_at": last_flip_at,
            "last_bar_at": bar_iso,
            "candles_in_state": candles,
            "flip_threshold_progress": round(progress, 2),
            "flipped": flipped,
            "long_score_ema": long_ema,
            "short_score_ema": short_ema,
            "preview": preview,
        }

    prior_candles = int(_optional_float(prior.get("candles_in_state")) or 0)

    # 증거 부족은 방향 게이트가 이긴다 — 히스테리시스로 방향을 붙들지 않는다.
    if raw_stance == "insufficient":
        changed = prior_stance != "insufficient"
        return build(
            "insufficient",
            transitioning=False,
            target=None,
            pending=None,
            pending_count=0,
            since=None if changed else prior.get("since"),
            last_flip_at=prior.get("last_flip_at"),
            candles=1 if changed else prior_candles + candles_advanced,
            progress=0.0,
            flipped=False,
        )

    stronger = max(long_ema, short_ema)
    rel = (long_ema - short_ema) / stronger if stronger > 0 else 0.0

    # 부트스트랩: 유효한 직전 held stance 없음 → 즉시 채택(관성 없음).
    # WO-56: 부트스트랩 flip 오발화 방지 — flipped는 확정 캔들 전진의 실제 flip에만 True.
    if not has_prior:
        return build(
            raw_stance,
            transitioning=False,
            target=None,
            pending=None,
            pending_count=0,
            since=None,
            last_flip_at=prior.get("last_flip_at"),
            candles=1,
            progress=0.0,
            flipped=False,
        )

    cand = _hysteresis_candidate(str(prior_stance), rel, float(params["flip_margin"]), float(params["enter_margin"]), float(params["exit_margin"]))

    # 후보가 현 상태와 같으면 건강하게 유지.
    if cand == prior_stance:
        return build(
            str(prior_stance),
            transitioning=False,
            target=None,
            pending=None,
            pending_count=0,
            since=prior.get("since"),
            last_flip_at=prior.get("last_flip_at"),
            candles=prior_candles + candles_advanced,
            progress=0.0,
            flipped=False,
        )

    # 후보가 다르면 연속 확인(persist) 카운트 — 확정 캔들 전진에서만 증가한다 (WO-56).
    pending = prior.get("pending_stance")
    pending_count = int(_optional_float(prior.get("pending_count")) or 0)
    pending_count = pending_count + 1 if pending == cand else 1
    required = max(1, int(float(params["flip_persist"])))
    if pending_count >= required:
        directional_flip = prior_stance in {"long_leaning", "short_leaning"} and cand in {"long_leaning", "short_leaning"}
        return build(
            cand,
            transitioning=False,
            target=None,
            pending=None,
            pending_count=0,
            since=None,
            last_flip_at=now_iso if directional_flip else prior.get("last_flip_at"),
            candles=1,
            progress=1.0,
            flipped=True,
        )
    # 문턱 미달 → 현 상태 HOLD, 전환 관찰 중.
    return build(
        str(prior_stance),
        transitioning=True,
        target=cand,
        pending=cand,
        pending_count=pending_count,
        since=prior.get("since"),
        last_flip_at=prior.get("last_flip_at"),
        candles=prior_candles + candles_advanced,
        progress=pending_count / required,
        flipped=False,
    )


def _confirmed_bar_iso(analysis: dict[str, Any], bar_minutes: float, now: datetime) -> str | None:
    """마지막 **확정** 캔들 시각 (WO-56 앵커). 스냅샷의 마지막 캔들이 진행 중이면 직전 캔들.

    last_candle_at은 캔들 오픈 시각 — now가 오픈 + 1캔들을 지나기 전이면 그 캔들은 미마감이다.
    """
    quality = analysis.get("data_quality") if isinstance(analysis.get("data_quality"), dict) else {}
    last_candle = _parse_dt(quality.get("last_candle_at") or analysis.get("as_of"))
    if last_candle is None:
        return None
    elapsed_minutes = (now - last_candle).total_seconds() / 60.0
    if elapsed_minutes >= bar_minutes:
        return last_candle.isoformat()
    confirmed = last_candle - timedelta(minutes=bar_minutes)
    return confirmed.isoformat()


def _legacy_stance_state(raw_stance: str, long_score: float, short_score: float, now: datetime) -> dict[str, Any]:
    """v1(재설계 전) 재현: 히스테리시스 없이 순간 stance를 그대로 사용. WO-54 전후 대조용."""
    return {
        "stance": raw_stance,
        "previous_stance": None,
        "transitioning": False,
        "target": None,
        "pending_stance": None,
        "pending_count": 0,
        "since": now.isoformat(),
        "last_flip_at": None,
        "last_bar_at": None,
        "candles_in_state": 1,
        "flip_threshold_progress": 0.0,
        "flipped": False,
        "long_score_ema": round(long_score, 3),
        "short_score_ema": round(short_score, 3),
        "preview": None,
    }


def _hysteresis_candidate(held: str, rel: float, flip_margin: float, enter_margin: float, exit_margin: float) -> str:
    """Schmitt 트리거: enter(방향 진입) > exit(방향 이탈)로 데드존을 만들어 진동을 흡수."""
    if held == "long_leaning":
        if rel <= -flip_margin:
            return "short_leaning"
        if rel < exit_margin:
            return "conflicted"
        return "long_leaning"
    if held == "short_leaning":
        if rel >= flip_margin:
            return "long_leaning"
        if rel > -exit_margin:
            return "conflicted"
        return "short_leaning"
    # conflicted에서 방향으로 나가려면 enter 문턱을 넘어야 한다.
    if rel >= enter_margin:
        return "long_leaning"
    if rel <= -enter_margin:
        return "short_leaning"
    return "conflicted"


def _ema(prev: Any, current: float, alpha: float) -> float:
    prev_f = _optional_float(prev)
    if prev_f is None:
        return round(current, 3)
    return round(alpha * current + (1.0 - alpha) * prev_f, 3)


def _composite_score(stance: str, long_ema: float, short_ema: float, long_score: float, short_score: float) -> float:
    """종합 신뢰도. 방향 판정 시 held 방향의 EMA 점유율 — 전환 중이면 자연히 낮아져
    약한 확신을 정직하게 표현한다. conflicted/insufficient는 순간 우세 비율."""
    total_ema = long_ema + short_ema
    if stance == "long_leaning" and total_ema > 0:
        return round(long_ema / total_ema * 100, 1)
    if stance == "short_leaning" and total_ema > 0:
        return round(short_ema / total_ema * 100, 1)
    total = long_score + short_score
    return round(max(long_score, short_score) / total * 100, 1) if total > 0 else 0.0


def _decay_class(engine: str, claim: str) -> str:
    """증거 유형 → 감쇠 클래스. WO-51 설계표와 일치."""
    if engine == "derivatives":
        return "state"
    if engine in {"level", "volume", "mtf"}:
        return "structure"
    if engine == "wyckoff" and "국면" in claim:
        # 매집/분산 국면은 레짐 성격 — 이벤트 마커보다 완만하게.
        return "structure"
    # liquidity 스윕 · wyckoff 마커 · harmonic PRZ · structure(BOS/CHoCH·존) = 이벤트성.
    return "event"


def _recency_factor(as_of: Any, now: datetime, decay_class: str, bar_minutes: float) -> float:
    """방향 기여의 시간 감쇠 계수 (0~1). 시각 불명이면 감쇠 없음(1.0)."""
    parsed = _parse_dt(as_of)
    if parsed is None:
        return 1.0
    half_life_bars, floor = DECAY_PROFILES.get(decay_class, DECAY_PROFILES[DEFAULT_DECAY_CLASS])
    age_minutes = max(0.0, (now - parsed).total_seconds() / 60.0)
    age_bars = age_minutes / max(bar_minutes, 1.0)
    decay = 0.5 ** (age_bars / half_life_bars) if half_life_bars > 0 else 0.0
    return round(floor + (1.0 - floor) * decay, 4)


def _harmonic_time(pattern: dict[str, Any]) -> Any:
    for key in ("detected_at", "completed_at", "as_of", "time"):
        value = pattern.get(key)
        if value is not None:
            return value
    points = pattern.get("points")
    if isinstance(points, list) and points and isinstance(points[-1], dict):
        return points[-1].get("time")
    return None


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


def _stance(
    long_evidence: list[dict[str, Any]],
    short_evidence: list[dict[str, Any]],
    long_score: float,
    short_score: float,
    htf_context: dict[str, Any] | None = None,
) -> str:
    if len(long_evidence) + len(short_evidence) < MIN_DIRECTIONAL_EVIDENCE:
        return "insufficient"
    if not long_evidence or not short_evidence:
        return "insufficient"
    stronger = max(long_score, short_score)
    if stronger <= 0:
        return "insufficient"
    margin = abs(long_score - short_score) / stronger
    if margin < CONFLICT_RATIO:
        return "conflicted"
    leaning = "long_leaning" if long_score > short_score else "short_leaning"
    # WO-52: HTF 기준선을 거스르는 판정, 또는 엔진이 명시적으로 conflicting을 선언한 경우
    # 강한 우세(HTF_STRONG_MARGIN)가 아니면 conflicted로 보수화한다.
    # (역행 증거는 이미 감쇠만 됐고 여기서 삭제하지 않음 — 진짜 압도적 전환은 그대로 통과.)
    if htf_context and margin < HTF_STRONG_MARGIN:
        bias = htf_context.get("bias")
        strength = float(htf_context.get("strength") or 0.0)
        opposes_htf = (
            bias in {"long", "short"}
            and strength >= HTF_GUARD_STRENGTH
            and ((bias == "long" and leaning == "short_leaning") or (bias == "short" and leaning == "long_leaning"))
        )
        if opposes_htf or htf_context.get("alignment") == "conflicting":
            return "conflicted"
    return leaning


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
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # WO-51: 와이코프 마커·하모닉 time은 epoch 초(정수) — 현행 _parse_dt는 이를 놓쳐
        # 마커 staleness가 늘 False였다. epoch(초/밀리초)를 UTC로 정규화한다.
        seconds = float(value)
        if seconds > 1e12:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
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
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # WO-51: epoch(초/밀리초) 정수를 ISO로 정규화 — 종전엔 None으로 버려져
        # 마커·하모닉 as_of가 소실되고 is_stale이 늘 False였다.
        parsed = _parse_dt(value)
        return parsed.isoformat() if parsed else None
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
