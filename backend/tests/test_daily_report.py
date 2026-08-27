"""WO-FCE-DAILY-REPORT-01 — 5트랙 일일 계좌 리포트 계약.

리포트는 지표를 만들지 않는다. 이미 있는 산출기를 읽어 문장으로 바꾼다. 아래 테스트는
그 조립이 §1 의 금지를 넘지 않는지 고정한다 — 특히 **트랙 총합**과 **실현·미실현 합산**을.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.notify import daily_report as dr
from app.notify import delivery_gate

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 27, 23, 30, tzinfo=timezone.utc)

# C8 — 판정·진입·출구 로직은 이 WO 가 건드리지 않는다.
UNTOUCHABLE = ("backend/app/analyst", "backend/app/structure", "backend/app/paper/policy.py", "backend/app/paper/whale_follow.py")


def _capital(**overrides):
    base = {
        "currency": "USDT",
        "starting_capital": 500.0,
        "current_capital": 449.46,
        "realized_pnl": -50.54,
        "unrealized_pnl": 1.83,
        "return_on_capital_pct": -10.11,
        "current_capital_note": None,
    }
    return {**base, **overrides}


def _metrics(**overrides):
    base = {"trade_count": 67, "win_rate_pct": 56.7, "profit_factor": 0.73, "mdd_pct": 16.71}
    return {**base, **overrides}


def _report(**overrides):
    base = {
        "as_of_label": "2026-08-28 08:30 KST",
        "totals": {"entries": 6, "exits": 5},
        "tracks": {
            track: {
                "capital": _capital(currency="KRW" if track == "stock_kr" else "USDT"),
                "counts": {"entries": 1, "exits": 1, "wins": 1},
                "metrics": _metrics(),
                "state": {},
                "extra": [],
            }
            for track, _label in dr.TRACK_ORDER
        },
        "actions": [],
    }
    return {**base, **overrides}


# ── C4 · 실현과 미실현을 합산하지 않는다 ────────────────────────────────


def test_realized_and_unrealized_are_on_separate_lines() -> None:
    lines = dr.capital_lines("crypto", _capital())
    realized_line = next(line for line in lines if "실현" in line)
    assert "미실현" in realized_line, "같은 줄에 둘이 함께 있어야 값이 분리돼 보인다"
    # 합산값이 나오면 안 된다 — 실현 −50.54 + 미실현 1.83 = −48.71
    assert "-48.71" not in "\n".join(lines)


def test_unknown_current_capital_does_not_show_a_return() -> None:
    """`미상 (0.00%)` 은 손익분기처럼 읽힌다."""
    lines = dr.capital_lines(
        "poly", _capital(current_capital=None, return_on_capital_pct=None, current_capital_note="보유 포지션 평가액 미상 — NAV 미산출(C7)")
    )
    assert "미상" in lines[0]
    assert "0.00%" not in lines[0]


def test_negative_zero_is_normalised() -> None:
    """`+-0` 이 찍힌 전례가 있다."""
    assert dr._signed(-0.0) == "0.00"
    assert dr._pct(-0.0) == "0.00%"


# ── C5 · 트랙 총합 줄을 만들지 않는다 ──────────────────────────────────


def test_no_total_row_is_rendered() -> None:
    """통화가 다르고 판정이 독립이다."""
    text = dr.render(_report())
    for forbidden in ("총합", "합계", "전체 자본", "5트랙 합"):
        assert forbidden not in text
    assert "실현 합산 없음" in text


def test_every_track_appears() -> None:
    """이벤트가 없어서 침묵하던 구조를 만들지 않는다."""
    text = dr.render(_report())
    for _track, label in dr.TRACK_ORDER:
        assert label in text


# ── C6 · 표본 부족 명시 ────────────────────────────────────────────────


def test_small_sample_is_tagged() -> None:
    line = dr.metric_line(_metrics(trade_count=28))
    assert "N=28" in line
    assert f"[표본 부족 · N<{dr.MIN_SAMPLE}]" in line


def test_sufficient_sample_has_no_tag() -> None:
    assert "표본 부족" not in dr.metric_line(_metrics(trade_count=30))


def test_zero_sample_refuses_to_score() -> None:
    """N=0 이면 승률을 만들지 않는다."""
    line = dr.metric_line({"trade_count": 0})
    assert "표본 0" in line
    assert "%" not in line


def test_metric_line_always_carries_n() -> None:
    """승률과 수익률이 같은 N 을 쓴다 — 다른 모집단이면 서로를 설명하지 못한다(C3)."""
    assert "N=67" in dr.metric_line(_metrics())


# ── C7 · 막힌 트랙은 막혔다고 쓴다 ─────────────────────────────────────


def test_halted_track_shows_a_status_line_instead_of_a_score() -> None:
    block = dr.track_block(
        "stock_us",
        "📈 주식 US",
        capital=_capital(currency="USD"),
        counts={"entries": 0, "exits": 0, "wins": 0},
        metrics={"trade_count": 0},
        state={"kind": "halted", "detail": "체결 invariant (fill_price_outside_observed_range)"},
    )
    text = "\n".join(block)
    assert "⛔ 정지" in text
    assert "fill_price_outside_observed_range" in text
    # 막힌 트랙에 성적 줄이 붙으면 정상처럼 보인다.
    assert "승률" not in text
    assert "오늘  진입" not in text


def test_excluded_and_held_states_are_distinguished() -> None:
    assert "⛔ 검증 대상 제외" in (dr.blocked_line({"kind": "excluded", "detail": "451"}) or "")
    assert "⚠️ 큐 보류 중" in (dr.blocked_line({"kind": "held", "detail": "13,836건"}) or "")
    assert dr.blocked_line({}) is None


def test_blocked_track_still_shows_capital() -> None:
    """자본은 표시한다 — 막혔어도 계좌는 존재한다."""
    block = dr.track_block(
        "poly", "🎲 폴리마켓", capital=_capital(currency="USDC"), counts={}, metrics={"trade_count": 0}, state={"kind": "excluded", "detail": "451"}
    )
    assert any("자본" in line for line in block)


# ── §2-3 · 조치 없으면 꼬리 생략 ───────────────────────────────────────


def test_action_block_is_omitted_when_there_is_nothing_to_do() -> None:
    """매일 같은 경고가 붙으면 배경음이 된다."""
    assert dr.action_lines([]) == []
    assert "조치 필요" not in dr.render(_report(actions=[]))


def test_action_block_renders_a_copyable_command() -> None:
    lines = dr.action_lines([{"title": "호스트 절전", "command": "caffeinate -dimsu &"}])
    text = "\n".join(lines)
    assert "조치 필요" in text
    assert "<code>caffeinate -dimsu &</code>" in text


# ── C11 · 길이 초과 시 트랙을 빼지 않는다 ──────────────────────────────


def test_长_message_drops_items_not_tracks() -> None:
    """트랙이 사라지면 리포트의 목적이 사라진다."""
    report = _report(actions=[{"title": "x" * 400, "detail": "y" * 400} for _ in range(5)])
    text = dr.render(report, max_chars=900)
    assert len(text) <= 900
    for _track, label in dr.TRACK_ORDER:
        assert label in text, f"트랙이 누락됐다: {label}"


def test_compact_mode_drops_metric_lines_first() -> None:
    compact = dr.track_block("crypto", "🪙", capital=_capital(), counts={"entries": 1, "exits": 1, "wins": 1}, metrics=_metrics(), state={}, compact=True)
    assert not any("누적" in line for line in compact)
    assert any("자본" in line for line in compact)


# ── C1·C2 · 발송 ───────────────────────────────────────────────────────


def test_daily_summary_is_registered_in_the_gate() -> None:
    """C2 — 기본 차단이므로 등록 없이 경유시키면 요약이 사라진다."""
    from app.notify.alerts import DAILY_SUMMARY_RULE_ID

    assert delivery_gate.evaluate_rule(DAILY_SUMMARY_RULE_ID).allowed is True


def test_daily_summary_goes_through_the_gate() -> None:
    """이전에는 관문을 지나지 않고 바로 보냈다 — "관문은 하나다"의 예외였다."""
    source = (REPO_ROOT / "backend/app/notify/alerts.py").read_text(encoding="utf-8")
    body = source.split("async def maybe_send_daily_summary")[1].split("\n    async def ")[0]
    assert "delivery_gate.evaluate_rule(DAILY_SUMMARY_RULE_ID)" in body
    assert body.index("delivery_gate.evaluate_rule") < body.index("self.sender.send_to_all")


def test_no_new_send_cadence_was_added() -> None:
    """C1 — 발송 빈도를 늘리지 않는다. 기존 슬롯을 확장한다."""
    source = (REPO_ROOT / "backend/app/notify/alerts.py").read_text(encoding="utf-8")
    assert source.count("async def maybe_send_daily_summary") == 1
    assert "morning_summary_due" in source, "따라잡기 판정을 계속 쓴다"


def test_catchup_logic_is_untouched() -> None:
    """3-3 항목 3 — 정확 일치로 하루가 사라진 전례가 있다."""
    from app.notify.rules import morning_summary_due

    class _S:
        telegram_daily_summary_time = "08:30"
        telegram_quiet_hours_timezone = "Asia/Seoul"

    late = datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)  # KST 23:00 — 목표 한참 뒤
    due, key = morning_summary_due(_S(), None, late)
    assert due is True, "목표 시각을 지났는데 따라잡지 못했다"
    assert morning_summary_due(_S(), key, late)[0] is False, "하루 1회 상한이 깨졌다"


# ── 창 ─────────────────────────────────────────────────────────────────


def test_window_falls_back_to_24h_without_a_record() -> None:
    assert dr.window_start(None, now=NOW) == NOW - timedelta(hours=24)


def test_window_uses_the_last_report_time() -> None:
    last = NOW - timedelta(hours=6)
    assert dr.window_start(last, now=NOW) == last


def test_label_is_written_in_user_time() -> None:
    """UTC 로 적으면 매일 날짜가 어긋나 보인다."""
    assert dr.kst_label(datetime(2026, 8, 27, 23, 30, tzinfo=timezone.utc)) == "2026-08-28 08:30 KST"


# ── C9 · 인과 단정 금지 ────────────────────────────────────────────────


def test_no_causal_or_advisory_language() -> None:
    text = dr.render(_report(actions=[{"title": "호스트 절전", "command": "caffeinate -dimsu &"}]))
    for forbidden in ("때문에", "예상된다", "추천", "매수", "매도하세요", "전망"):
        assert forbidden not in text


# ── 제약 증명 ──────────────────────────────────────────────────────────


def test_report_does_not_compute_its_own_metrics() -> None:
    """3-1 항목 1 — 중복 구현이 곧 두 개의 진실이다.

    `metrics.get("profit_factor")` 는 **읽기**이고 `gross_profit / gross_loss` 는 **계산**이다.
    후자만 금지한다 — 읽기까지 막으면 렌더러가 값을 쓸 수 없다.
    """
    source = (REPO_ROOT / "backend/app/notify/daily_report.py").read_text(encoding="utf-8")
    for computing in ("gross_profit /", "gross_loss", "sum(returns)", "def _mdd", "/ capital_usdt"):
        assert computing not in source, f"지표를 직접 계산한다: {computing}"
    # 읽기는 있어야 한다 — 없으면 값을 어디서도 가져오지 않는다는 뜻이다.
    assert 'metrics.get("profit_factor")' in source
    assert 'metrics.get("win_rate_pct")' in source


def test_judgement_and_execution_layers_are_untouched() -> None:
    """C8 — analyst/·structure/·policy.py·whale_follow.py diff 0줄."""
    diff = subprocess.run(["git", "diff", "origin/main", "--stat", "--", *UNTOUCHABLE], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")
    assert diff.stdout.strip() == "", f"C8 위반:\n{diff.stdout}"
