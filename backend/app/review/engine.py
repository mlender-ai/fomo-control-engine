from app.db.models import Trade


def render_review(trade: Trade) -> str:
    result_label = "수익 거래" if trade.pnl_percent >= 0 else "손실 거래"
    score_delta = None
    if trade.entry_score is not None and trade.exit_score is not None:
        score_delta = trade.exit_score - trade.entry_score
    score_line = f"진입 대비 청산 점수 변화는 {score_delta:+d}점입니다." if score_delta is not None else "점수 변화 데이터가 충분하지 않습니다."

    return (
        f"📋 Trade Review: {trade.symbol}\n\n"
        f"결과:\n{result_label}, 수익률 {trade.pnl_percent:.2f}%, 손익 {trade.pnl_amount:.2f} USDT\n\n"
        f"진입 당시:\nEntry Score는 {trade.entry_score if trade.entry_score is not None else 'N/A'}점이었습니다.\n\n"
        f"청산 당시:\nExit Score는 {trade.exit_score if trade.exit_score is not None else 'N/A'}점이었습니다. {score_line}\n\n"
        f"분석:\n청산 사유는 '{trade.exit_reason}'입니다. 이번 거래는 감정적 판단보다 기록된 점수와 사유를 기준으로 복기해야 합니다.\n\n"
        f"다음 개선점:\n진입 전 점수, 보유 중 점수 하락폭, 실제 청산 이유가 서로 일치했는지 확인하세요. 점수 하락폭이 20점 이상이면 별도 점검 규칙으로 분류할 수 있습니다."
    )

