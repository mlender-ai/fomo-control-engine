from app.db.models import Report
from app.indicators.engine import calculate_indicators
from app.liquidity.engine import analyze_liquidity
from app.scoring.engine import build_breakdown, state_label
from app.structure.levels.engine import detect_structure_levels
from app.structure.wyckoff.engine import analyze_structure


def generate_report(snapshot) -> Report:
    indicators = calculate_indicators(snapshot)
    structure = analyze_structure(snapshot, indicators)
    liquidity = analyze_liquidity(snapshot)
    entry_score, scores = build_breakdown(
        snapshot.price,
        snapshot.change_24h,
        snapshot.funding_rate,
        structure,
        liquidity,
        indicators,
    )
    label = state_label(entry_score, scores.fomo)
    structure_levels = detect_structure_levels(snapshot.candles, snapshot.price)
    raw_json = {
        "symbol": snapshot.symbol,
        "timeframe": snapshot.timeframe,
        "price": snapshot.price,
        "change_24h": snapshot.change_24h,
        "scores": scores.model_dump(),
        "indicators": indicators,
        "structure": structure,
        "structure_levels": {
            "support": [level.model_dump() for level in structure_levels["support"]],
            "resistance": [level.model_dump() for level in structure_levels["resistance"]],
        },
        "liquidity": liquidity,
        "provider": snapshot.provider,
        "data_quality": snapshot.data_quality.model_dump(mode="json"),
    }
    report_text = _render_korean_report(snapshot.symbol, label, entry_score, scores, raw_json)
    return Report(
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        price=snapshot.price,
        change_24h=snapshot.change_24h,
        entry_score=entry_score,
        scores=scores,
        state_label=label,
        raw_json=raw_json,
        report=report_text,
        provider=snapshot.provider,
        data_quality=snapshot.data_quality,
    )


def _render_korean_report(symbol: str, label: str, entry_score: int, scores, raw_json: dict) -> str:
    indicators = raw_json["indicators"]
    structure = raw_json["structure"]
    liquidity = raw_json["liquidity"]
    fomo_warning = ""
    if scores.fomo >= 75:
        fomo_warning = "\n\nFOMO 위험이 높은 편입니다. 가격 움직임만 보고 추격 진입하기보다 다음 캔들의 거래량과 리스크를 확인하는 쪽이 합리적입니다."

    return (
        f"📌 {symbol}\n\n"
        f"현재 시장은 '{label}' 단계로 판단됩니다.\n\n"
        f"시장 구조 점수는 {scores.structure}/100입니다. 와이코프 관점에서는 "
        f"{structure['wyckoff']['phase_hint']} 가능성을 보고 있으며, 저점 높임은 "
        f"{'확인됩니다' if structure['trend']['higher_low'] else '아직 약합니다'}.\n\n"
        f"거래량 점수는 {scores.volume}/100입니다. 최근 거래량은 평균 대비 "
        f"{indicators['relative_volume']}배 수준입니다.\n\n"
        f"유동성 점수는 {scores.liquidity}/100입니다. 현재 우세 방향은 "
        f"{liquidity['dominant_direction']}이고, Funding 상태는 {liquidity['funding_rate_state']}입니다.\n\n"
        f"모멘텀 점수는 {scores.momentum}/100입니다. RSI는 {indicators['rsi']}이며 "
        f"MACD 히스토그램은 {indicators['macd_histogram']}입니다.\n\n"
        f"리스크 점수는 {scores.risk}/100입니다. Risk는 높을수록 위험하므로 총점에는 반전해서 반영했습니다.\n\n"
        f"현재 점수: {entry_score}/100\n"
        f"FOMO Index: {scores.fomo}/100"
        f"{fomo_warning}\n\n"
        f"제 의견:\n"
        f"지금의 판단은 매수 지시가 아니라 진입 근거의 강도 점검입니다. 점수가 높더라도 손절 기준과 분할 진입 계획이 없다면 대기하는 편이 낫습니다."
    )
