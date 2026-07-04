from uuid import UUID

from app.agents.contracts import AgentInput, AgentName, AgentResult, Stance
from app.db.models import AgentOutput, MarketSnapshotRecord, ResearchRun, Report
from app.indicators.engine import calculate_indicators


def create_research_run(repo, report: Report, memories: list[dict] | None = None) -> tuple[ResearchRun, list[AgentOutput]]:
    raw_json = report.raw_json
    indicators = raw_json.get("indicators", {})
    reason_codes = _reason_codes(report)
    snapshot = repo.add_market_snapshot(
        MarketSnapshotRecord(
            symbol=report.symbol,
            timeframe=report.timeframe,
            provider=report.provider,
            candle_count=report.data_quality.candles,
            latest_price=report.price,
            latest_candle_time=report.data_quality.last_candle_at,
            data_quality=report.data_quality.model_dump(mode="json"),
            indicators=indicators,
            scores=report.scores.model_dump(),
            reason_codes=reason_codes,
        )
    )
    agent_input = AgentInput(
        report_id=report.id,
        snapshot_id=snapshot.id,
        symbol=report.symbol,
        timeframe=report.timeframe,
        raw_json={**raw_json, "reason_codes": reason_codes},
        memories=memories or [],
    )
    results = [
        _market_structure(agent_input),
        _liquidity(agent_input),
        _momentum(agent_input),
        _bull(agent_input),
        _bear(agent_input),
        _risk(agent_input),
        _fomo(agent_input),
    ]
    final_summary, final_label = _compose_final(report, results)
    run = repo.add_research_run(
        ResearchRun(
            symbol=report.symbol,
            timeframe=report.timeframe,
            report_id=report.id,
            snapshot_id=snapshot.id,
            entry_score=report.entry_score,
            fomo_index=report.scores.fomo,
            state_label=report.state_label,
            final_summary=final_summary,
            final_action_label=final_label,
            raw_input=agent_input.model_dump(mode="json"),
            raw_output={"checklists": [_checklist_payload(result) for result in results]},
        )
    )
    outputs = [
        repo.add_agent_output(
            AgentOutput(
                research_run_id=run.id,
                agent_name=result.agent.value,
                confidence=result.confidence,
                stance=result.stance.value,
                raw_json=result.raw_json,
                text_output=result.text_output,
            )
        )
        for result in results
    ]
    return run, outputs


def _checklist_payload(result: AgentResult) -> dict:
    return {
        "check": result.raw_json.get("check", result.agent.value),
        "stance": result.stance.value,
        "rule_score": result.confidence,
        "raw_json": result.raw_json,
        "text_output": result.text_output,
    }


def _score(raw: dict, key: str) -> int:
    return int(raw.get("scores", {}).get(key, 0))


def _reason_codes(report: Report) -> list[str]:
    codes = []
    scores = report.scores
    if scores.structure >= 75:
        codes.append("structure_supportive")
    if scores.volume >= 75:
        codes.append("volume_supportive")
    if scores.liquidity >= 70:
        codes.append("liquidity_supportive")
    if scores.momentum >= 70:
        codes.append("momentum_supportive")
    if scores.risk >= 60:
        codes.append("risk_elevated")
    if scores.fomo >= 70:
        codes.append("fomo_elevated")
    if report.entry_score < 65:
        codes.append("entry_score_not_confirmed")
    return codes or ["mixed_signals"]


def _market_structure(agent_input: AgentInput) -> AgentResult:
    raw = agent_input.raw_json
    score = _score(raw, "structure")
    trend = raw.get("structure", {}).get("trend", {})
    stance = Stance.supportive if score >= 70 else Stance.neutral
    text = f"시장 구조 점수는 {score}입니다. 방향 힌트는 {trend.get('direction', 'unknown')}이며 바닥 확정이 아니라 구조 관찰 구간입니다."
    return AgentResult(
        agent=AgentName.market_structure_analyst,
        stance=stance,
        confidence=min(90, max(35, score)),
        raw_json={
            "check": "market_structure",
            "direction": trend.get("direction", "unknown"),
            "rule_score": min(90, max(35, score)),
            "key_points": [text],
            "risk_notes": ["SOS 또는 Spring 신호가 없으면 확정 표현을 피해야 합니다."],
            "invalidations": ["저점 구조가 다시 깨지면 진입 논리는 약화됩니다."],
            "score_contribution": "supportive" if score >= 70 else "mixed",
        },
        text_output=text,
    )


def _liquidity(agent_input: AgentInput) -> AgentResult:
    raw = agent_input.raw_json
    liquidity = raw.get("liquidity", {})
    score = _score(raw, "liquidity")
    dominant = liquidity.get("dominant_direction", "unknown")
    stance = Stance.supportive if score >= 70 else Stance.neutral
    text = f"유동성 점수는 {score}입니다. 우세 방향은 {dominant}이나, 청산 구간은 목표가가 아니라 자석 후보로만 봅니다."
    return AgentResult(
        agent=AgentName.liquidity_analyst,
        stance=stance,
        confidence=min(88, max(35, score)),
        raw_json={
            "check": "liquidity",
            "dominant_magnet": "upside" if dominant == "upside_liquidity" else "downside" if dominant == "downside_liquidity" else "balanced",
            "upper_liquidity_strength": liquidity.get("upper_liquidity", 0),
            "lower_liquidity_strength": liquidity.get("lower_liquidity", 0),
            "cascade_risk": "moderate_downside" if liquidity.get("funding_rate_state") == "heated" else "low_to_moderate",
            "key_levels": [],
            "interpretation": text,
        },
        text_output=text,
    )


def _momentum(agent_input: AgentInput) -> AgentResult:
    raw = agent_input.raw_json
    indicators = raw.get("indicators", {})
    score = _score(raw, "momentum")
    rsi = indicators.get("rsi", 50)
    state = "recovering_from_oversold" if rsi < 45 else "heated" if rsi > 70 else "neutral"
    stance = Stance.caution if rsi > 70 else Stance.supportive if score >= 70 else Stance.neutral
    text = f"모멘텀 점수는 {score}이고 RSI는 {rsi}입니다. 반등 신호와 추격 위험을 분리해서 봐야 합니다."
    return AgentResult(
        agent=AgentName.momentum_analyst,
        stance=stance,
        confidence=min(85, max(35, score)),
        raw_json={
            "check": "momentum",
            "state": state,
            "rsi_comment": f"RSI {rsi}",
            "macd_comment": f"MACD histogram {indicators.get('macd_histogram', 'unknown')}",
            "bollinger_comment": f"lower {indicators.get('bollinger_lower', 'unknown')}, upper {indicators.get('bollinger_upper', 'unknown')}",
            "momentum_risk": "high" if rsi > 70 else "medium",
        },
        text_output=text,
    )


def _bull(agent_input: AgentInput) -> AgentResult:
    raw = agent_input.raw_json
    strength = round((_score(raw, "structure") + _score(raw, "volume") + _score(raw, "liquidity")) / 3)
    text = f"Bull case strength는 {strength}입니다. 근거는 reason codes {', '.join(raw.get('reason_codes', []))}에 한정합니다."
    return AgentResult(
        agent=AgentName.bull_researcher,
        stance=Stance.supportive if strength >= 70 else Stance.neutral,
        confidence=strength,
        raw_json={
            "check": "bull_case",
            "bull_case_strength": strength,
            "arguments": [text],
            "required_confirmations": ["다음 캔들 거래량 유지", "시장 구조 훼손 없음"],
            "best_entry_style": "wait_for_confirmation" if strength < 75 else "small_probe_only",
        },
        text_output=text,
    )


def _bear(agent_input: AgentInput) -> AgentResult:
    raw = agent_input.raw_json
    risk = _score(raw, "risk")
    fomo = _score(raw, "fomo")
    strength = min(95, max(35, round((risk + fomo + (100 - _score(raw, "structure"))) / 3)))
    text = f"Bear case strength는 {strength}입니다. 위험 점수 {risk}, FOMO {fomo}를 먼저 봅니다."
    return AgentResult(
        agent=AgentName.bear_researcher,
        stance=Stance.caution if strength >= 55 else Stance.neutral,
        confidence=strength,
        raw_json={
            "check": "bear_case",
            "bear_case_strength": strength,
            "arguments": [text],
            "invalidation_risks": ["거래량 약화", "저점 재이탈", "FOMO 급등"],
            "avoid_conditions": ["손절 기준 없음", "가격 급등만 보고 추격"],
        },
        text_output=text,
    )


def _risk(agent_input: AgentInput) -> AgentResult:
    risk = _score(agent_input.raw_json, "risk")
    level = "high" if risk >= 65 else "medium" if risk >= 40 else "low"
    text = f"Risk level은 {level}입니다. 진입한다면 한 번에 크게 들어가기보다 작은 탐색 또는 분할 접근이 맞습니다."
    return AgentResult(
        agent=AgentName.risk_guardian,
        stance=Stance.risk_first,
        confidence=max(45, risk),
        raw_json={
            "check": "risk_guardian",
            "risk_level": level,
            "max_risk_per_trade_pct": 0.5 if level == "high" else 1.0,
            "suggested_position_mode": "small_probe" if level != "low" else "small_or_split",
            "stop_logic": "진입 전 무효화 가격을 정하고, ATR 변동성 확대 시 사이즈를 줄입니다.",
            "do_not_enter_if": ["손절 기준이 없음", "레버리지를 높여 손실을 만회하려는 상태"],
        },
        text_output=text,
    )


def _fomo(agent_input: AgentInput) -> AgentResult:
    fomo = _score(agent_input.raw_json, "fomo")
    warning = "high" if fomo >= 70 else "medium" if fomo >= 45 else "low"
    memory_notes = [memory.get("summary", "") for memory in agent_input.memories[:3]]
    text = f"FOMO 위험은 {warning}입니다. 점수가 낮은데 가격만 급등하는 상황은 대기해야 합니다."
    return AgentResult(
        agent=AgentName.fomo_gatekeeper,
        stance=Stance.fomo_warning if fomo >= 70 else Stance.caution if fomo >= 45 else Stance.neutral,
        confidence=max(35, fomo),
        raw_json={
            "check": "fomo_gate",
            "fomo_risk": fomo,
            "warning_level": warning,
            "why_this_may_be_fomo": ["가격 움직임만으로 판단하면 이전 실수 패턴과 겹칠 수 있습니다."] + memory_notes,
            "cooldown_recommendation": "wait_next_candle" if fomo >= 45 else "normal_review",
        },
        text_output=text,
    )


def _compose_final(report: Report, results: list[AgentResult]) -> tuple[str, str]:
    bull = next(result for result in results if result.agent == AgentName.bull_researcher)
    bear = next(result for result in results if result.agent == AgentName.bear_researcher)
    risk = next(result for result in results if result.agent == AgentName.risk_guardian)
    fomo = next(result for result in results if result.agent == AgentName.fomo_gatekeeper)
    if report.scores.fomo >= 70:
        label = "cooldown_required"
    elif report.entry_score >= 75 and report.scores.risk < 60:
        label = "watch_or_small_probe"
    elif report.entry_score >= 65:
        label = "watch_for_confirmation"
    else:
        label = "wait"
    summary = (
        f"📌 {report.symbol} 리서치 런\n\n"
        f"현재 시장은 '{report.state_label}' 단계로 판단됩니다.\n\n"
        f"종합 점수: {report.entry_score}/100\n"
        f"FOMO Index: {report.scores.fomo}/100\n\n"
        f"1. 시장 구조\n{results[0].text_output}\n\n"
        f"2. 거래량과 모멘텀\n{results[2].text_output}\n\n"
        f"3. 유동성\n{results[1].text_output}\n\n"
        f"4. Bull Case\n{bull.text_output}\n\n"
        f"5. Bear Case\n{bear.text_output}\n\n"
        f"6. Risk Guardian\n{risk.text_output}\n\n"
        f"7. FOMO Gatekeeper\n{fomo.text_output}\n\n"
        f"제 의견:\n매수/매도 지시가 아니라 진입 판단 검토입니다. 손절 기준과 다음 캔들 확인 없이 확신하지 않는 편이 낫습니다."
    )
    return summary, label
