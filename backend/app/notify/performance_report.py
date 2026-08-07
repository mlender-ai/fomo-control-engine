"""페이퍼 성과 리포트 텔레그램 포맷 (WO-FCE-PERFORMANCE-REPORT-01).

C1: 거부 상세는 텔레그램으로 보내지 않는다. 이 리포트는 성과를 보고하며, 거부는
최다 게이트 **1줄 참고용**까지만 싣는다(별도 알림 금지 — 조회는 대시보드·진단 API).

C3: 표본 규칙은 `review/paper_performance.py` 의 계산부가 이미 강제한다. 여기서는
``state`` 와 ``None`` 여부만 보고 문장을 고른다 — 포맷터가 숫자를 만들 수 없다.
"""

from __future__ import annotations

from html import escape
from typing import Any

from app.review.paper_performance import SAMPLE_INSUFFICIENT, SAMPLE_NONE


def _clock_suffix(track: dict[str, Any]) -> str:
    """검증 D+N (유실 M일 제외). 시계가 없는 트랙(크립토)은 빈 문자열."""
    clock = track.get("clock")
    if not isinstance(clock, dict):
        return ""
    elapsed = int(clock.get("elapsed_days") or 0)
    lost = int(clock.get("lost_days") or 0)
    suffix = f"검증 D+{elapsed}"
    if lost > 0:
        suffix += f", 유실 {lost}일 제외"
    return f" ({suffix})"


def _sample_line(closed: dict[str, Any], sample_min_n: int) -> str | None:
    """표본 상태 한 줄. 표본 0이면 숫자를 만들지 않고 '표본 없음'만 말한다(C3)."""
    state = str(closed.get("state") or "")
    if state == SAMPLE_NONE:
        return "· 표본 없음 — 청산 완료 0건"
    if state == SAMPLE_INSUFFICIENT:
        return f"· 표본 부족 (N<{sample_min_n}) — 판정 유보"
    return None


def _closed_line(closed: dict[str, Any]) -> str | None:
    """누적 청산 라인. 승률·평균 R 은 **항상 N과 함께** 표기한다(C3)."""
    n = int(closed.get("n") or 0)
    if n <= 0:
        return None
    win_rate = closed.get("win_rate_pct")
    parts = [f"· 누적 청산 {n}건"]
    if win_rate is not None:
        parts.append(f"승률 {win_rate:.0f}% ({int(closed.get('wins') or 0)}/{n})")
    avg_r = closed.get("avg_r")
    if avg_r is not None:
        parts.append(f"평균 {avg_r:+.2f}R")
    else:
        # 폴리처럼 R 이 정의되지 않는 트랙은 금액 단위를 명시해 쓴다 — R 자리에 금액을
        # 넣으면 -42.51R 같은 단위 오표기가 그대로 나간다.
        avg_pnl = closed.get("avg_pnl")
        if avg_pnl is not None:
            parts.append(f"평균 손익 {avg_pnl:+.2f}")
    return " · ".join(parts)


def _holding_line(track: dict[str, Any]) -> str:
    holding = track.get("holding") or {}
    count = int(holding.get("count") or 0)
    parts = [f"· 보유 {count}건"]
    unrealized = holding.get("unrealized_pct")
    if unrealized is not None:
        parts.append(f"미실현 {unrealized:+.2f}%")
    today = track.get("today") or {}
    gate = today.get("top_reject_gate")
    if count == 0 and gate:
        # 진입 0인 트랙은 "왜 진입이 없는가"를 이 1줄이 담당한다(작업 3). 상세는 대시보드.
        parts.append(f"진입 0 · 최다거부 {escape(str(gate))}")
    elif count == 0:
        parts.append("진입 0")
    return " · ".join(parts)


def _brier_lines(track: dict[str, Any]) -> list[str]:
    """폴리 대표 지표. 정산 포지션 기준이 대표이고, 추정 기준은 자명 시장 비중과 함께."""
    brier = track.get("brier")
    if not isinstance(brier, dict):
        return []
    lines: list[str] = []
    # 만기 분포 — "언제 성과가 나오는가"의 답 (WO-FCE-ENTRY-THROUGHPUT-01 작업 4).
    expiry = track.get("expiry") or {}
    nearest = expiry.get("nearest_end_at")
    if nearest:
        lines.append(f"· 정산 대기 {int(expiry.get('open_count') or 0)}건 · 최근접 만기 {escape(str(nearest)[:10])}")
    if expiry.get("sample_possible_in_window") is False:
        lines.append(f"⚠️ 검증 종료({escape(str(expiry.get('validation_ends_at') or '')[:10])}) 내 만기 0건 — <b>검증 기간 내 표본 불가</b>")
    settled = brier.get("settled_positions") or {}
    settled_avg = settled.get("avg")
    settled_n = int(settled.get("n") or 0)
    if settled_avg is None:
        # 정산은 됐는데 브라이어가 없는 경우와 정산 자체가 없는 경우를 구분한다.
        settled_trades = int((track.get("closed") or {}).get("n") or 0)
        if settled_trades:
            lines.append(f"· 브라이어 미산출 — 정산 {settled_trades}건에 브라이어 기록 없음")
        else:
            lines.append("· 브라이어 표본 없음 — 정산 완료 포지션 0건")
    else:
        lines.append(f"· 브라이어 평균 {settled_avg:.4f} (정산 {settled_n}건)")
    estimates = brier.get("estimates") or {}
    estimate_avg = estimates.get("avg")
    if estimate_avg is not None:
        share = estimates.get("trivial_share_pct")
        note = f"· 참고 — 추정 해소 브라이어 {estimate_avg:.4f} (N={int(estimates.get('n') or 0)}"
        if share is not None:
            note += f", 이미 확정된 자명 시장 {share:.0f}% 포함"
        lines.append(note + ")")
    return lines


def format_paper_performance(payload: dict[str, Any], *, date_label: str) -> list[str]:
    """일일 성과 리포트 라인. 4트랙 전부 등장한다 — 진입이 0이어도(작업 4)."""
    sample_min_n = int(payload.get("sample_min_n") or 30)
    lines = [f"<b>📊 페이퍼 성과 · {escape(date_label)}</b>"]
    for track in payload.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        lines.append("")
        lines.append(f"<b>{escape(str(track.get('label') or '-'))}</b>{_clock_suffix(track)}")
        lines.append(_holding_line(track))
        closed = track.get("closed") or {}
        closed_line = _closed_line(closed)
        if closed_line:
            lines.append(closed_line)
        lines.extend(_brier_lines(track))
        sample_line = _sample_line(closed, sample_min_n)
        if sample_line:
            lines.append(sample_line)
    return lines


def format_weekly_performance(
    payload: dict[str, Any],
    *,
    verdicts: dict[str, Any] | None = None,
    pending: list[dict[str, Any]] | None = None,
) -> str:
    """주간 성과 리포트 — 4주 검증 진행도와 N>=30 도달 예상 여부를 매주 반복 보고(§2-2).

    도달 불가로 판정되면 그 사실을 매주 다시 말한다. 문제를 잊지 않게 하는 것이 목적이다.

    WO-FCE-VALIDATION-VERDICT-01 Phase 1-3: 트랙별 **완주 가능성 판정**과 D+28 예상 표본을
    같은 리포트에 상시 포함한다. 대시보드를 열어야만 보이는 판정은 안 보이는 판정이다.
    """
    sample_min_n = int(payload.get("sample_min_n") or 30)
    lines = ["<b>📆 페이퍼 주간 성과</b>"]
    for track in payload.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        closed = track.get("closed") or {}
        projection = track.get("projection") or {}
        clock = track.get("clock") if isinstance(track.get("clock"), dict) else {}
        elapsed = int((clock or {}).get("elapsed_days") or 0)
        horizon = int(projection.get("horizon_days") or 28)
        lines.append("")
        lines.append(f"<b>{escape(str(track.get('label') or '-'))}</b>")
        if clock:
            lines.append(f"· 검증 진행 {elapsed}/{horizon}일 (유실 {int(clock.get('lost_days') or 0)}일 제외)")
        else:
            lines.append(f"· 검증 시계 없음 — 경과일 미산출 (표본 {int(closed.get('n') or 0)}건)")
        reaches = projection.get("reaches_min")
        projected = projection.get("projected_n")
        if reaches is None:
            lines.append(f"· N≥{sample_min_n} 도달 예상 판정 불가 — 청산 속도 표본 없음")
        elif reaches:
            lines.append(f"· {horizon}일 시점 예상 표본 {projected}건 — N≥{sample_min_n} 도달 예상")
        else:
            lines.append(f"⚠️ {horizon}일 시점 예상 표본 {projected}건 — <b>N≥{sample_min_n} 도달 불가</b>")
        closed_line = _closed_line(closed)
        if closed_line:
            lines.append(closed_line)
        sample_line = _sample_line(closed, sample_min_n)
        if sample_line:
            lines.append(sample_line)
        benchmark = track.get("benchmark") or {}
        start = benchmark.get("start")
        current = benchmark.get("current")
        if start and current:
            change = (float(current) - float(start)) / float(start) * 100.0
            lines.append(f"· 벤치마크 {change:+.2f}% (기준 {escape(str(benchmark.get('symbol') or '-'))})")
    if verdicts:
        from app.validation.verdict_watch import format_weekly_verdict_lines

        lines.extend(format_weekly_verdict_lines(verdicts, target_samples=sample_min_n))
    # WO-FCE-SAMPLE-RATE-01 Phase 5: 코드로 풀 수 없는 결정은 매주 다시 보고한다.
    if pending is not None:
        from app.validation.pending_decisions import format_pending_lines

        lines.extend(format_pending_lines(pending))
    lines.append("")
    lines.append("<i>트랙별 원통화 성적이며 서로 합산하지 않습니다. 실계좌 성과와도 분리됩니다.</i>")
    return "\n".join(lines)


def format_track_record_suffix(closed: dict[str, Any], *, sample_min_n: int = 30) -> str:
    """청산·정산 즉시 알림 하단에 붙는 트랙 누적 승률·N (§2-3).

    "결과 뭐였다 → 승률 어떻다" 를 한 메시지에서 보게 한다.
    """
    n = int(closed.get("n") or 0)
    if n <= 0:
        return "트랙 누적: 표본 없음"
    win_rate = closed.get("win_rate_pct")
    if win_rate is None:
        return f"트랙 누적: 청산 {n}건 · 승률 미산출"
    suffix = f"트랙 누적: 승률 {win_rate:.0f}% (N={n})"
    if str(closed.get("state") or "") == SAMPLE_INSUFFICIENT:
        suffix += f" · 표본 부족 (N<{sample_min_n})"
    return suffix
