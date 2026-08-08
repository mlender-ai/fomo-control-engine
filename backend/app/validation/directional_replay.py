"""WO-FCE-DIRECTIONAL-INTEGRITY-01 Phase 0 — 방향 판정 재판정 하네스 (계측 전용).

## 왜 이 모듈이 생겼나

`SAMPLE-RATE-01` 결론상 필요 표본은 트랙당 283건이고 현 검증 창은 4일 남았다.
**거래 결과(PnL)로는 이 WO 의 Phase 들을 절대 측정할 수 없다.** 그래서 각 Phase 의 효과를
**판정 분포 변화**로 측정한다. 이 모듈이 그 분포를 만든다.

## 이 모듈은 판정하지 않는다

기존 판정 경로를 **그대로 호출해 관측만 기록한다.** `app/analyst/` · `app/structure/` 의
판정 로직 diff 는 0줄이다. 여기서 무언가를 새로 계산하면 그 순간 기준선이 기준선이 아니게 된다.

## 룩어헤드 (C4)

각 시점 판정은 `candles[:end]` 프리픽스만 입력받는다. 그래서 입력 구간을 절반으로 잘라도
그 절반의 판정은 전체 입력 시와 **완전히 동일**해야 하며, 이를 테스트로 강제한다
(`test_directional_replay.py::test_prefix_invariance_proves_no_lookahead`).

기존 `app/analyst/stance_history.py::replay_confirmed_stance_points` 가 이미 같은 워크포워드
구조를 갖고 있다(불변 규칙 2 중복 검사에서 확인). 그러나 그것은 **채택 stance 만** 남기고
국면·엔진별 stance·커버리지를 버린다. 이 모듈은 같은 워크포워드를 돌면서 **판정의 내부를**
기록한다 — 그것이 Phase 1~5 를 측정하는 데 필요한 것이다.

## 시계 주의

`build_chart_analysis` 는 `confirmed_chart_candles` 로 **미마감 캔들을 벽시계 기준으로**
버린다. 따라서 이 하네스는 **구간 전체가 이미 마감된 과거 캔들**에 대해서만 재현 가능하다.
막 생성 중인 캔들이 입력에 섞이면 실행 시각에 따라 레코드 수가 달라진다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from app.analyst.confluence import CONFLICT_RATIO, ENGINE_BASE_WEIGHTS, MIN_DIRECTIONAL_EVIDENCE, TIMEFRAME_MINUTES, build_confluence
from app.db.models import MarketCandle, MarketSnapshot
from app.positions.chart_analysis import MIN_CHART_CANDLES, build_chart_analysis

# 재판정 시 엔진에 넘기는 분석 창. `stance_history.REPLAY_ANALYSIS_WINDOW` 와 같은 값이며,
# 라이브 경로와 다른 창을 쓰면 기준선이 라이브를 대변하지 못한다.
REPLAY_ANALYSIS_WINDOW = 200

ENGINE_WEIGHT_TOTAL = sum(ENGINE_BASE_WEIGHTS.values())

# 판정 가능 엔진 수 구간. `lifecycle.py::_observation_line` 의 "판정 가능 모듈 n/7 — 구조
# 형성 대기" 선례(임계 3)를 분포로 확인하기 위한 절단점이다. **임계를 정하지 않는다** —
# 결정 3은 이 분포를 보고 사용자가 정한다.
COVERAGE_BUCKETS: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6, 7, 8, 9)

# 침묵 가중 비율 구간 (%). WO 본문이 지정한 절단점.
SILENT_WEIGHT_BUCKETS: tuple[tuple[str, float, float], ...] = (
    ("0%", 0.0, 0.0),
    ("~10%", 0.0, 10.0),
    ("~25%", 10.0, 25.0),
    ("~40%", 25.0, 40.0),
    ("40%+", 40.0, 100.0),
)


def replay_judgments(
    *,
    symbol: str,
    timeframe: str,
    candles: list[MarketCandle],
    hysteresis_params: dict[str, Any] | None = None,
    min_candles: int = MIN_CHART_CANDLES,
) -> list[dict[str, Any]]:
    """확정 캔들 프리픽스마다 판정을 재현하고 그 내부를 기록한다.

    히스테리시스는 라이브와 같이 **매 봉 전진**한다. 채택 stance 는 경로 의존이므로
    봉을 건너뛰면 기준선이 라이브를 대변하지 못한다.
    """

    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    records: list[dict[str, Any]] = []
    prior: dict[str, Any] | None = None
    for end in range(max(1, int(min_candles)), len(ordered) + 1):
        prefix = ordered[:end]
        try:
            analysis, confluence = _judge(symbol, timeframe, prefix, prior, hysteresis_params)
        except ValueError:
            # 캔들 부족 등으로 분석 자체가 성립하지 않는 시점. 건너뛴다 —
            # 없는 판정을 만들어 내지 않는다.
            continue
        state = confluence.get("stance_state") if isinstance(confluence.get("stance_state"), dict) else {}
        prior = dict(state) if state else prior
        records.append(judgment_record(timestamp_iso=prefix[-1].timestamp.isoformat(), analysis=analysis, confluence=confluence))
    return records


def _judge(
    symbol: str,
    timeframe: str,
    prefix: list[MarketCandle],
    prior: dict[str, Any] | None,
    hysteresis_params: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current = prefix[-1]
    snapshot = MarketSnapshot(
        symbol=symbol.upper(),
        timeframe=timeframe,
        price=current.close,
        change_24h=0.0,
        funding_rate=0.0,
        open_interest_change=0.0,
        candles=prefix[-REPLAY_ANALYSIS_WINDOW:],
        provider="directional_replay",
    )
    analysis = build_chart_analysis(snapshot, None, None)
    # 판정 시각은 그 봉이 마감된 직후. 시간 감쇠(recency)가 봉 나이에 걸리므로
    # 벽시계를 쓰면 과거 재판정이 전부 stale 로 눌린다.
    generated_at = current.timestamp + timedelta(minutes=_timeframe_minutes(timeframe), seconds=1)
    confluence = build_confluence(
        symbol=symbol.upper(),
        timeframe=timeframe,
        analysis=analysis,
        generated_at=generated_at,
        prior_state=prior,
        hysteresis_params=hysteresis_params,
    )
    return analysis, confluence


def judgment_record(*, timestamp_iso: str, analysis: dict[str, Any], confluence: dict[str, Any]) -> dict[str, Any]:
    """한 시점의 판정 내부를 평평한 레코드로 만든다.

    Phase 1~5 의 대조는 전부 이 레코드 위에서 이뤄진다. 여기 없는 축은 측정되지 않는다.
    """

    wyckoff = analysis.get("wyckoff") if isinstance(analysis.get("wyckoff"), dict) else {}
    trend = wyckoff.get("trend") if isinstance(wyckoff.get("trend"), dict) else {}
    engines = engine_stances(confluence)
    judged = sorted(name for name, item in engines.items() if item["stance"] != "silent")
    silent = sorted(name for name in ENGINE_BASE_WEIGHTS if name not in judged)
    silent_weight = round(sum(ENGINE_BASE_WEIGHTS[name] for name in silent), 2)
    long_score = _float(confluence.get("long_score"))
    short_score = _float(confluence.get("short_score"))
    leader = max(long_score, short_score)
    margin = round(abs(long_score - short_score), 4)
    wyckoff_side = str(wyckoff.get("side") or "undetermined")
    return {
        "as_of": timestamp_iso,
        "symbol": str(confluence.get("symbol") or ""),
        "timeframe": str(confluence.get("timeframe") or ""),
        # ── 와이코프 국면
        "wyckoff_phase": str(wyckoff.get("phase") or "undetermined"),
        "wyckoff_side": wyckoff_side,
        "wyckoff_event_types": sorted({str(event.get("type")) for event in _dicts(wyckoff.get("events")) if event.get("type")}),
        "wyckoff_event_count": len(_dicts(wyckoff.get("events"))),
        "has_trading_range": isinstance(wyckoff.get("range"), dict) and bool(wyckoff.get("range")),
        # ── 선행 추세 (D1 대조축)
        "trend_direction": str(trend.get("direction") or "unknown"),
        "trend_break_of_structure": bool(trend.get("break_of_structure")),
        "trend_breakdown_structure": bool(trend.get("breakdown_structure")),
        "prior_trend_conflict": _trend_conflict(str(trend.get("direction") or "unknown"), wyckoff_side),
        # ── 엔진별 stance / 커버리지 (E1 대조축)
        "engine_stances": {name: item["stance"] for name, item in engines.items()},
        "judged_engines": judged,
        "judged_engine_count": len(judged),
        "silent_engines": silent,
        "silent_weight": silent_weight,
        "silent_weight_pct": round(silent_weight / ENGINE_WEIGHT_TOTAL * 100, 2),
        # ── 증거 수 vs 엔진 수 (E2 대조축)
        "directional_evidence_count": int(confluence.get("evidence_count") or 0),
        "directional_engine_count": len([name for name, item in engines.items() if item["net"] in {"long", "short"}]),
        "min_directional_evidence": MIN_DIRECTIONAL_EVIDENCE,
        # ── 점수·판정 (E3 대조축: raw 는 순간, stance 는 채택)
        "long_score": long_score,
        "short_score": short_score,
        "margin": margin,
        "margin_ratio": round(margin / leader, 4) if leader > 0 else 0.0,
        "conflict_ratio": CONFLICT_RATIO,
        "raw_stance": str(confluence.get("raw_stance") or "insufficient"),
        "stance": str(confluence.get("stance") or "insufficient"),
        "stance_disagrees_with_raw": str(confluence.get("stance") or "") != str(confluence.get("raw_stance") or ""),
        "transitioning": bool((confluence.get("stance_state") or {}).get("transitioning")),
        # ── 엔진 간 불일치 (증상 B 대조축)
        "wyckoff_stance": engines["wyckoff"]["net"],
        "liquidity_stance": engines["liquidity"]["net"],
        "wyckoff_liquidity_conflict": _opposed(engines["wyckoff"]["net"], engines["liquidity"]["net"]),
    }


def engine_stances(confluence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """엔진별 방향 기여를 집계한다.

    `level` 처럼 지지·저항을 동시에 내는 엔진은 내부적으로 양방향 증거를 낸다. 그래서
    ``net`` (점수 우세 방향)과 ``internal_conflict`` (양쪽 다 있음)를 분리해 기록한다 —
    합쳐 버리면 "레벨 엔진은 항상 충돌"이라는 무의미한 결론만 남는다.
    """

    tally: dict[str, dict[str, float]] = {name: {"long": 0.0, "short": 0.0, "items": 0.0} for name in ENGINE_BASE_WEIGHTS}
    for key, direction in (("long_evidence", "long"), ("short_evidence", "short")):
        for item in _dicts(confluence.get(key)):
            engine = str(item.get("engine") or "")
            if engine not in tally:
                continue
            tally[engine][direction] += _float(item.get("score"))
            tally[engine]["items"] += 1
    result: dict[str, dict[str, Any]] = {}
    for name, scores in tally.items():
        long_score, short_score = scores["long"], scores["short"]
        if long_score <= 0 and short_score <= 0:
            net, stance = "silent", "silent"
        elif long_score > short_score:
            net = stance = "long"
        elif short_score > long_score:
            net = stance = "short"
        else:
            net, stance = "balanced", "balanced"
        result[name] = {
            "stance": stance,
            "net": net,
            "long_score": round(long_score, 4),
            "short_score": round(short_score, 4),
            "evidence_items": int(scores["items"]),
            "internal_conflict": long_score > 0 and short_score > 0,
            "base_weight": ENGINE_BASE_WEIGHTS[name],
        }
    return result


# ── 분포 산출 ────────────────────────────────────────────────────────────
# 어느 함수도 임계를 정하지 않는다. 임계는 이 분포를 보고 사람이 정한다(결정 3).


def coverage_distribution(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {bucket: 0 for bucket in COVERAGE_BUCKETS}
    for record in records:
        judged = int(record.get("judged_engine_count") or 0)
        if judged in counts:
            counts[judged] += 1
    total = len(records)
    return {
        "total": total,
        "engine_total": len(ENGINE_BASE_WEIGHTS),
        "buckets": [{"judged_engines": bucket, "count": counts[bucket], "pct": _pct(counts[bucket], total)} for bucket in COVERAGE_BUCKETS],
        "note": "임계 미정 — 결정 3에서 사용자가 정한다. 참고 선례: lifecycle.py 는 보유 포지션에서 판정 가능 모듈 3 미만이면 관측 보류.",
    }


def silent_weight_distribution(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {label: 0 for label, _low, _high in SILENT_WEIGHT_BUCKETS}
    for record in records:
        pct = _float(record.get("silent_weight_pct"))
        counts[_silent_bucket(pct)] += 1
    total = len(records)
    return {
        "total": total,
        "engine_weight_total": ENGINE_WEIGHT_TOTAL,
        "buckets": [{"range": label, "count": counts[label], "pct": _pct(counts[label], total)} for label, _low, _high in SILENT_WEIGHT_BUCKETS],
        "note": "침묵한 엔진은 현재 양쪽에 0을 기여하고 끝난다 — 7개가 살아있을 때와 4개만 살아있을 때가 구분되지 않는다(E1).",
    }


def engine_silence_frequency(records: list[dict[str, Any]]) -> dict[str, Any]:
    """엔진별 침묵 빈도. 어느 엔진이 커버리지를 깎고 있는지 지목한다."""
    total = len(records)
    counts = {name: 0 for name in ENGINE_BASE_WEIGHTS}
    for record in records:
        for name in record.get("silent_engines") or []:
            if name in counts:
                counts[name] += 1
    return {
        "total": total,
        "engines": sorted(
            (
                {"engine": name, "silent_count": counts[name], "silent_pct": _pct(counts[name], total), "base_weight": ENGINE_BASE_WEIGHTS[name]}
                for name in ENGINE_BASE_WEIGHTS
            ),
            key=lambda item: (-item["silent_count"], item["engine"]),
        ),
    }


def wyckoff_liquidity_disagreement(records: list[dict[str, Any]]) -> dict[str, Any]:
    """증상 B(같은 가격 행동을 두 엔진이 정반대로 읽음)가 예외인지 상시인지 판정한다."""
    total = len(records)
    both_directional = [
        record for record in records if record.get("wyckoff_stance") in {"long", "short"} and record.get("liquidity_stance") in {"long", "short"}
    ]
    conflicts = [record for record in both_directional if record.get("wyckoff_liquidity_conflict")]
    return {
        "total": total,
        "both_directional": len(both_directional),
        "conflict_count": len(conflicts),
        "conflict_pct_of_total": _pct(len(conflicts), total),
        "conflict_pct_of_both_directional": _pct(len(conflicts), len(both_directional)),
        "sample_warning": _sample_warning(len(both_directional)),
    }


def prior_trend_crosstab(records: list[dict[str, Any]]) -> dict[str, Any]:
    """D1 실제 발생률 — `마크다운 뒤 분산` / `마크업 뒤 축적` 건수.

    ⚠️ 해석 주의: 현행 `_trend_payload` 의 산출 범위가 **박스 직전이 아니라 최근 28봉**이다
    (`wyckoff/engine.py:619-647`, `RANGE_LOOKBACK=60`). 즉 이 표의 "추세"는 박스로 들어오기
    전의 선행 추세가 아니라 **박스 내부를 포함한 최근 흐름**이다. Phase 4 는 이 사실을
    먼저 처리해야 한다.
    """

    matrix: dict[str, dict[str, int]] = {}
    for record in records:
        direction = str(record.get("trend_direction") or "unknown")
        side = str(record.get("wyckoff_side") or "undetermined")
        matrix.setdefault(direction, {}).setdefault(side, 0)
        matrix[direction][side] += 1
    total = len(records)
    conflicts = [record for record in records if record.get("prior_trend_conflict")]
    markdown_then_distribution = [record for record in conflicts if record.get("wyckoff_side") == "distribution"]
    markup_then_accumulation = [record for record in conflicts if record.get("wyckoff_side") == "accumulation"]
    return {
        "total": total,
        "matrix": {direction: dict(sorted(sides.items())) for direction, sides in sorted(matrix.items())},
        "conflict_count": len(conflicts),
        "conflict_pct": _pct(len(conflicts), total),
        "markdown_then_distribution": len(markdown_then_distribution),
        "markup_then_accumulation": len(markup_then_accumulation),
        "trend_window_caveat": "trend 는 최근 28봉 기준(candles[-28:])이고 거래 박스는 최근 60봉에서 탐지된다 — 추세 창이 박스 창 안에 들어 있다.",
        "sample_warning": _sample_warning(total),
    }


def evidence_vs_engine_gap(records: list[dict[str, Any]]) -> dict[str, Any]:
    """E2 — "근거 3건"이 실제로는 "엔진 1개"인 구간의 빈도."""
    total = len(records)
    met_by_items = [
        record
        for record in records
        if int(record.get("directional_evidence_count") or 0) >= int(record.get("min_directional_evidence") or MIN_DIRECTIONAL_EVIDENCE)
    ]
    single_engine = [record for record in met_by_items if int(record.get("directional_engine_count") or 0) <= 1]
    two_engines_or_fewer = [record for record in met_by_items if int(record.get("directional_engine_count") or 0) <= 2]
    return {
        "total": total,
        "meets_item_threshold": len(met_by_items),
        "single_engine_count": len(single_engine),
        "single_engine_pct_of_met": _pct(len(single_engine), len(met_by_items)),
        "two_engines_or_fewer_count": len(two_engines_or_fewer),
        "two_engines_or_fewer_pct_of_met": _pct(len(two_engines_or_fewer), len(met_by_items)),
        "note": "현행 검사는 증거 항목 수만 센다 — 한 엔진이 국면 1건 + 마커 N건으로 단독 충족할 수 있다.",
    }


def stance_split_frequency(records: list[dict[str, Any]]) -> dict[str, Any]:
    """E3 — 채택 stance 와 순간 raw_stance 가 갈리는 빈도.

    **설계상 갈리는 것은 정상이다**(히스테리시스의 목적이 진동 흡수다). 결함은 그 사실이
    화면에 없다는 것이므로, 여기서는 빈도만 센다.
    """

    total = len(records)
    split = [record for record in records if record.get("stance_disagrees_with_raw")]
    directional_held = [record for record in split if record.get("stance") in {"long_leaning", "short_leaning"}]
    opposed = [record for record in split if _opposed(_leaning(record.get("stance")), _leaning(record.get("raw_stance")))]
    return {
        "total": total,
        "split_count": len(split),
        "split_pct": _pct(len(split), total),
        "held_directional_while_raw_differs": len(directional_held),
        "opposed_count": len(opposed),
        "opposed_pct": _pct(len(opposed), total),
        "note": "히스테리시스 설계상 정상 동작이다. 계측 목적은 표시 정합(Phase 1)의 대상 빈도 확인이다.",
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    stances: dict[str, int] = {}
    phases: dict[str, int] = {}
    for record in records:
        stances[str(record.get("stance"))] = stances.get(str(record.get("stance")), 0) + 1
        phases[str(record.get("wyckoff_phase"))] = phases.get(str(record.get("wyckoff_phase")), 0) + 1
    return {
        "record_count": len(records),
        "period": {"from": records[0]["as_of"], "to": records[-1]["as_of"]} if records else None,
        "stance_distribution": dict(sorted(stances.items())),
        "phase_distribution": dict(sorted(phases.items())),
        "coverage": coverage_distribution(records),
        "silent_weight": silent_weight_distribution(records),
        "engine_silence": engine_silence_frequency(records),
        "wyckoff_liquidity": wyckoff_liquidity_disagreement(records),
        "prior_trend": prior_trend_crosstab(records),
        "evidence_vs_engine": evidence_vs_engine_gap(records),
        "stance_split": stance_split_frequency(records),
        "sample_warning": _sample_warning(len(records)),
    }


def baseline_snapshot(records: list[dict[str, Any]], *, symbol: str, timeframe: str, label: str = "baseline") -> dict[str, Any]:
    """이후 모든 Phase 가 대조할 고정 스냅샷.

    `records_sha256` 은 기준선이 조용히 바뀌지 않았음을 증명한다 — 대조표의 신뢰는
    "같은 기준선인가"에 전적으로 달려 있다.
    """

    return {
        "label": label,
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "records_sha256": records_digest(records),
        "summary": summarize(records),
        "records": records,
    }


def records_digest(records: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(records, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def compare_to_baseline(baseline: dict[str, Any], candidate: list[dict[str, Any]]) -> dict[str, Any]:
    """기준선 대비 판정이 어떻게 움직였는지. **CI green 은 완료 조건이 아니다** — 이 표가 조건이다."""

    base_records = {str(record["as_of"]): record for record in (baseline.get("records") or [])}
    cand_records = {str(record["as_of"]): record for record in candidate}
    shared = sorted(set(base_records) & set(cand_records))
    changed_phase = [key for key in shared if base_records[key]["wyckoff_phase"] != cand_records[key]["wyckoff_phase"]]
    changed_stance = [key for key in shared if base_records[key]["stance"] != cand_records[key]["stance"]]
    directional = {"long_leaning", "short_leaning"}
    base_directional = sum(1 for key in shared if base_records[key]["stance"] in directional)
    cand_directional = sum(1 for key in shared if cand_records[key]["stance"] in directional)
    return {
        "baseline_sha256": str(baseline.get("records_sha256") or ""),
        "candidate_sha256": records_digest(candidate),
        "identical": str(baseline.get("records_sha256") or "") == records_digest(candidate),
        "compared_points": len(shared),
        "only_in_baseline": len(set(base_records) - set(cand_records)),
        "only_in_candidate": len(set(cand_records) - set(base_records)),
        "phase_changed": len(changed_phase),
        "stance_changed": len(changed_stance),
        "directional_baseline": base_directional,
        "directional_candidate": cand_directional,
        "directional_delta": cand_directional - base_directional,
        "monotone_decrease": cand_directional <= base_directional,
        "changed_points": changed_stance[:50],
    }


# ── 보조 ─────────────────────────────────────────────────────────────────

_BULLISH_TRENDS = {"bullish", "neutral_to_bullish"}
_BEARISH_TRENDS = {"bearish", "bearish_to_neutral"}


def _trend_conflict(direction: str, side: str) -> bool:
    """마크업 뒤 축적 / 마크다운 뒤 분산 — 정석상 인과가 뒤집힌 조합."""
    if side == "accumulation" and direction in _BULLISH_TRENDS:
        return True
    return side == "distribution" and direction in _BEARISH_TRENDS


def _opposed(left: str | None, right: str | None) -> bool:
    return {left, right} == {"long", "short"}


def _leaning(stance: Any) -> str | None:
    value = str(stance or "")
    return "long" if value == "long_leaning" else "short" if value == "short_leaning" else None


def _silent_bucket(pct: float) -> str:
    if pct <= 0:
        return "0%"
    for label, _low, high in SILENT_WEIGHT_BUCKETS[1:]:
        if pct <= high:
            return label
    return SILENT_WEIGHT_BUCKETS[-1][0]


def _sample_warning(count: int) -> str | None:
    return f"표본 부족 — N={count} < 30, 분포를 결론으로 읽지 않는다" if count < 30 else None


def _pct(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 2) if whole else None


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _timeframe_minutes(timeframe: str) -> int:
    value = TIMEFRAME_MINUTES.get(timeframe)
    return int(value if value is not None else 240)


__all__ = [
    "COVERAGE_BUCKETS",
    "ENGINE_WEIGHT_TOTAL",
    "SILENT_WEIGHT_BUCKETS",
    "baseline_snapshot",
    "compare_to_baseline",
    "coverage_distribution",
    "engine_silence_frequency",
    "engine_stances",
    "evidence_vs_engine_gap",
    "judgment_record",
    "prior_trend_crosstab",
    "records_digest",
    "replay_judgments",
    "silent_weight_distribution",
    "stance_split_frequency",
    "summarize",
    "wyckoff_liquidity_disagreement",
]
