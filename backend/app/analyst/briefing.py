from __future__ import annotations

from typing import Any

from app.analyst.confluence import build_confluence
from app.backtest.statistics import bootstrap_ci_from_counts, format_stat_line
from app.db.models import JudgmentScore


def build_analyst_briefing(
    *,
    symbol: str,
    timeframe: str,
    analysis: dict[str, Any],
    action_plan: dict[str, Any] | None = None,
    calibration_scores: list[JudgmentScore] | None = None,
    context: str = "pre_entry",
) -> dict[str, Any]:
    confluence = build_confluence(
        symbol=symbol,
        timeframe=timeframe,
        analysis=analysis,
        calibration_scores=calibration_scores or [],
    )
    scenario = _scenario_lines(confluence, action_plan or {}, analysis)
    hit_rates = _hit_rate_lines(confluence, analysis.get("historical_backtest"))
    text = render_briefing(confluence, scenario, hit_rates, context=context)
    return {
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "context": context,
        "briefing_source": "deterministic",
        "confluence": confluence,
        "scenario": scenario,
        "hit_rates": hit_rates,
        "text": text,
        "llm_text": None,
        "llm_source": "disabled",
        "warnings": _warnings(confluence),
    }


def render_briefing(
    confluence: dict[str, Any],
    scenario: list[dict[str, Any]],
    hit_rates: list[str],
    *,
    context: str,
) -> str:
    stance = confluence.get("stance")
    evidence = _primary_evidence(confluence)
    counter = confluence.get("counter_evidence") if isinstance(confluence.get("counter_evidence"), list) else []
    title = "유지 브리핑" if context == "position" else "진입 전 브리핑"
    lines = [
        f"📋 {confluence.get('symbol')} {confluence.get('timeframe')} {title} · 기준 {_time(confluence.get('data_as_of'))}",
        f"스탠스: {confluence.get('stance_label')} (롱 {confluence.get('long_score')} vs 숏 {confluence.get('short_score')} · 종합 {confluence.get('composite_score')}/100)",
        "",
    ]
    if stance == "insufficient":
        lines.append("근거: 유효 증거가 부족하거나 반대 근거가 없어 판단을 보류합니다.")
    elif stance == "conflicted":
        lines.append("근거: 롱/숏 증거 점수차가 작아 방향을 억지로 고르지 않습니다.")
    else:
        lines.append("근거 (강한 순)")
        for index, item in enumerate(evidence[:3], start=1):
            lines.append(f"{index}. {_claim(item)}")
    lines.append("")
    lines.append("반대 근거 ⚠")
    if counter:
        for index, item in enumerate(counter[:2], start=1):
            lines.append(f"{index}. {_claim(item)}")
    else:
        lines.append("1. 반대 근거가 부족해 스탠스를 확정하지 않습니다.")
    if scenario:
        lines.append("")
        lines.append("시나리오")
        for item in scenario[:3]:
            lines.append(f"· {item['condition']} → {item['meaning']}")
    lines.append("")
    if hit_rates:
        lines.append("근거별 과거 적중률: " + " · ".join(hit_rates[:3]))
    else:
        lines.append("근거별 과거 적중률: 표본 부족")
    lines.append("판단은 사용자 몫입니다. 가격·조건 도달 시 재평가합니다.")
    return "\n".join(lines)


def briefing_summary(briefing: dict[str, Any], max_evidence: int = 2) -> str:
    confluence = briefing.get("confluence") if isinstance(briefing.get("confluence"), dict) else {}
    evidence = _primary_evidence(confluence)
    lines = [
        f"브리핑: {confluence.get('stance_label', '판단 유보')} · 롱 {confluence.get('long_score', 0)} / 숏 {confluence.get('short_score', 0)}",
    ]
    for item in evidence[:max_evidence]:
        lines.append(f"• {_claim(item)}")
    counter = confluence.get("counter_evidence") if isinstance(confluence.get("counter_evidence"), list) else []
    if counter:
        lines.append(f"반대: {_claim(counter[0])}")
    return "\n".join(lines)


def _primary_evidence(confluence: dict[str, Any]) -> list[dict[str, Any]]:
    stance = confluence.get("stance")
    if stance == "short_leaning":
        return _dict_list(confluence.get("short_evidence"))
    if stance == "long_leaning":
        return _dict_list(confluence.get("long_evidence"))
    return sorted(
        [*_dict_list(confluence.get("long_evidence")), *_dict_list(confluence.get("short_evidence"))],
        key=lambda item: (float(item.get("score") or 0), int(item.get("confidence") or 0)),
        reverse=True,
    )


def _scenario_lines(confluence: dict[str, Any], action_plan: dict[str, Any], analysis: dict[str, Any]) -> list[dict[str, Any]]:
    stance = confluence.get("stance")
    if not action_plan:
        action_plan = _scenario_plan_from_analysis(analysis, stance)
    invalidation = action_plan.get("invalidation") or action_plan.get("engine_invalidation") if isinstance(action_plan, dict) else None
    targets = action_plan.get("take_profit") if isinstance(action_plan.get("take_profit"), list) else []
    result: list[dict[str, Any]] = []
    if isinstance(invalidation, dict) and invalidation.get("price") is not None:
        direction_word = "롱" if stance == "long_leaning" else "숏" if stance == "short_leaning" else "현재"
        result.append(
            {
                "condition": f"{_price(invalidation.get('price'))} 무효화 기준 유지",
                "meaning": f"{direction_word} 논리 유지 후보 · 이탈/돌파 시 재평가",
                "source": "action_plan.invalidation",
            }
        )
        result.append(
            {
                "condition": f"{_price(invalidation.get('price'))} 무효화 기준 훼손",
                "meaning": "스탠스 폐기 또는 재산정",
                "source": "action_plan.invalidation",
            }
        )
    if targets and isinstance(targets[0], dict) and targets[0].get("price") is not None:
        result.append(
            {
                "condition": f"{_price(targets[0].get('price'))} 1차 목표 도달",
                "meaning": "부분 익절 또는 반응 점검",
                "source": "action_plan.take_profit[0]",
            }
        )
    return result


def _scenario_plan_from_analysis(analysis: dict[str, Any], stance: Any) -> dict[str, Any]:
    scenarios = analysis.get("scenarios") if isinstance(analysis.get("scenarios"), dict) else {}
    if stance == "short_leaning":
        plan = scenarios.get("short")
    elif stance == "long_leaning":
        plan = scenarios.get("long")
    else:
        plan = scenarios.get("long") or scenarios.get("short")
    return plan if isinstance(plan, dict) else {}


def _hit_rate_lines(confluence: dict[str, Any], historical_backtest: Any = None) -> list[str]:
    lines = []
    seen: set[str] = set()
    for item in _primary_evidence(confluence) + _dict_list(confluence.get("counter_evidence")):
        calibration = item.get("calibration") if isinstance(item.get("calibration"), dict) else None
        if not calibration or int(calibration.get("tested") or 0) < 10:
            continue
        engine = str(item.get("engine") or "근거")
        if engine in seen:
            continue
        seen.add(engine)
        # WO-36 §7: 모든 승률/적중률 표기에 CI 병기.
        ci = bootstrap_ci_from_counts(int(calibration.get("correct") or 0), int(calibration.get("tested") or 0))
        ci_text = f", CI {ci[0]}~{ci[1]}%" if ci else ""
        lines.append(f"라이브 {_engine_label(engine)} {calibration.get('accuracy_pct')}% (N={calibration.get('tested')}{ci_text})")
    if isinstance(historical_backtest, dict):
        sample_floor = int(historical_backtest.get("sample_floor") or 10)
        current = historical_backtest.get("current_regime") if isinstance(historical_backtest.get("current_regime"), dict) else {}
        current_regime = str(current.get("regime")) if current.get("regime") else None
        if current.get("regime_label"):
            regime_line = f"현재 레짐 {current.get('regime_label')}"
            if current.get("market_regime_label"):
                regime_line += f" · 시장(BTC) {current.get('market_regime_label')}"
            lines.append(regime_line)
        for stat in _dict_list(historical_backtest.get("stats"))[:3]:
            n = int(stat.get("sample_size") or 0)
            if n <= 0:
                continue
            # WO-37: 격리된 시그니처는 브리핑 증거에서 제외 (복귀 판정용 데이터는 계속 수집).
            state = stat.get("lifecycle_state")
            if state == "quarantined":
                continue
            # WO-36 §5/§7: 현재 레짐 기준 통계 우선 + CI·N·레짐 표기 표준.
            line = "백테스트 " + format_stat_line(stat, sample_floor=sample_floor, current_regime=current_regime)
            # WO-37: 성능 저하 관찰 중 표기.
            if state == "degraded":
                line += " · 성능 저하 관찰 중"
            lines.append(line)
    return lines


def _warnings(confluence: dict[str, Any]) -> list[str]:
    warnings = []
    if confluence.get("max_engine_age_minutes") is not None and int(confluence["max_engine_age_minutes"]) > 120:
        warnings.append(f"일부 근거가 {confluence['max_engine_age_minutes']}분 전 데이터 기준입니다.")
    if confluence.get("stance") == "insufficient":
        warnings.append("반대 근거 또는 유효 증거 수가 부족해 스탠스를 보류했습니다.")
    if confluence.get("stance") == "conflicted":
        warnings.append("롱/숏 근거가 충돌해 방향을 확정하지 않았습니다.")
    return warnings


def _claim(item: dict[str, Any]) -> str:
    stale = "⏱ " if item.get("is_stale") else ""
    confidence = item.get("confidence")
    score = item.get("score")
    suffix = f" · 신뢰도 {confidence} · 점수 {score}" if confidence is not None else ""
    return f"{stale}{_engine_label(str(item.get('engine') or '-'))}: {item.get('claim', '-')}{suffix}"


def _engine_label(engine: str) -> str:
    return {
        "liquidity": "유동성",
        "wyckoff": "와이코프",
        "harmonic": "하모닉",
        "level": "레벨",
        "derivatives": "수급",
        "volume": "볼륨",
        "mtf": "상위 TF",
        "structure": "구조",
    }.get(engine, engine)


def _time(value: Any) -> str:
    if not value:
        return "-"
    text = str(value)
    return text.replace("T", " ")[:16]


def _price(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if abs(number) >= 100:
        return f"{number:,.2f}"
    if abs(number) >= 1:
        return f"{number:.4f}"
    return f"{number:.6f}".rstrip("0").rstrip(".")


def _dict_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
