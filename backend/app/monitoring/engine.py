from app.db.models import MonitoringLog, Position, Report
from app.positions.pnl import (
    calculate_computed_pnl_percent,
    resolve_position_pnl_percent,
)


def calculate_pnl(position: Position, current_price: float) -> float:
    return calculate_computed_pnl_percent(position, current_price)


def build_monitoring_log(position: Position, report: Report) -> MonitoringLog:
    entry_score = position.entry_score or report.entry_score
    score_change = report.entry_score - entry_score
    pnl_percent = resolve_position_pnl_percent(position, report.price).pnl_percent
    if score_change <= -20:
        logic_status = "진입 근거 약화"
    elif score_change < -8:
        logic_status = "포지션 점검 필요"
    else:
        logic_status = "진입 논리 유지"

    report_text = (
        f"📈 {position.symbol} 포지션 모니터링\n\n"
        f"현재 수익률: {pnl_percent:.2f}%\n\n"
        f"진입 당시 점수: {entry_score}\n"
        f"현재 점수: {report.entry_score}\n\n"
        f"상태 변화:\n"
        f"- 점수 변화는 {score_change:+d}점입니다.\n"
        f"- 현재 시장 상태는 '{report.state_label}'입니다.\n"
        f"- FOMO Index는 {report.scores.fomo}/100입니다.\n\n"
        f"진입 논리 상태:\n{logic_status}\n\n"
        f"제 의견:\n점수 하락과 리스크 상승이 동시에 나타나는지 확인하고, 손절가 또는 분할 익절 기준을 다시 점검할 구간입니다."
    )
    return MonitoringLog(
        position_id=position.id,
        report_id=report.id,
        current_price=report.price,
        pnl_percent=round(pnl_percent, 2),
        score_change=score_change,
        logic_status=logic_status,
        report_text=report_text,
    )
