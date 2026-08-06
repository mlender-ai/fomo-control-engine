"""WO-FCE-VALIDATION-VERDICT-01 Phase 1 — 판정 발행·전이 감시 회귀.

배경: 측정 장치는 완성됐는데 **그 장치가 산출한 숫자가 사용자에게 발행되지 않았다.**
대시보드를 열어야만 보이는 판정은 안 보이는 판정이다.

고정하는 명제:
1. 판정별 대응 규칙은 **결과를 보기 전에** 고정돼 있고, 어디에도 "기준 완화"가 없다
2. 첫 관측은 전이가 아니다 — 배선 직후 4트랙이 한꺼번에 알림으로 나가면 안 된다
3. 같은 판정이 유지되면 0건 (스팸 금지)
4. 좋아지는 전이도 알린다 (조용히 넘기면 언제부터 좋아졌는지 알 수 없다)
5. 주간 리포트는 판정과 D+28 예상 표본을 상시 포함한다
"""

from __future__ import annotations

from app.validation import sample_viability as sv
from app.validation import verdict_watch as vw


def _row(track: str, verdict: str, *, projected: int = 0) -> dict:
    return {
        "label": f"{track} 트랙",
        "verdict": verdict,
        "verdict_reason": "테스트 사유",
        "effective_days": 14,
        "effective_days_target": 28,
        "scored_samples": 4,
        "scored_samples_target": 30,
        "projected_samples_at_target": projected,
        "statement": f"{track} 유효 관측일 14/28, 채점 가능 표본 4/30 — 표본 부족",
    }


# ── 1. 대응 규칙 사전 확정 ──────────────────────────────────────────────


def test_every_verdict_has_a_predefined_action() -> None:
    """판정이 나온 뒤에 규칙을 만들면 결과에 맞춰 규칙을 고르게 된다."""
    for verdict in (sv.VERDICT_VIABLE, sv.VERDICT_SLOW, sv.VERDICT_STRUCTURALLY_BLOCKED, sv.VERDICT_INSUFFICIENT_DATA):
        assert vw.VERDICT_ACTIONS.get(verdict), f"{verdict} 의 대응 규칙이 없다"


def test_no_action_permits_loosening_the_criteria() -> None:
    """C1 — 어떤 판정에서도 기준 완화는 대응이 아니다."""
    joined = " ".join(vw.VERDICT_ACTIONS.values())

    assert "완화 금지" in joined
    assert "기준 완화" not in joined.replace("기준 완화 금지", "")


def test_slow_keeps_observing_instead_of_relaxing() -> None:
    assert "완화 금지" in vw.VERDICT_ACTIONS[sv.VERDICT_SLOW]


def test_insufficient_data_forbids_other_moves() -> None:
    """무엇이 문제인지 모르는 상태에서 기준을 바꾸면 효과를 영원히 알 수 없다."""
    assert "다른 조치 금지" in vw.VERDICT_ACTIONS[sv.VERDICT_INSUFFICIENT_DATA]


# ── 2. 전이 감지 ────────────────────────────────────────────────────────


def test_first_observation_is_not_a_transition() -> None:
    """배선 직후 4트랙이 한꺼번에 알림으로 나가면 그건 변화가 아니라 초기화다."""
    transitions = vw.detect_transitions({}, {"crypto": _row("crypto", sv.VERDICT_SLOW)})

    assert transitions == []


def test_unchanged_verdict_produces_nothing() -> None:
    previous = {"crypto": sv.VERDICT_SLOW}

    assert vw.detect_transitions(previous, {"crypto": _row("crypto", sv.VERDICT_SLOW)}) == []


def test_worsening_transition_is_flagged() -> None:
    previous = {"crypto": sv.VERDICT_VIABLE}

    transitions = vw.detect_transitions(previous, {"crypto": _row("crypto", sv.VERDICT_SLOW)})

    assert len(transitions) == 1
    assert transitions[0]["worsened"] is True
    assert (transitions[0]["from"], transitions[0]["to"]) == (sv.VERDICT_VIABLE, sv.VERDICT_SLOW)


def test_improving_transition_is_also_reported() -> None:
    """좋아진 것을 조용히 넘기면 '언제부터 좋아졌는가'를 사후에 알 수 없다."""
    previous = {"poly": sv.VERDICT_STRUCTURALLY_BLOCKED}

    transitions = vw.detect_transitions(previous, {"poly": _row("poly", sv.VERDICT_VIABLE)})

    assert len(transitions) == 1
    assert transitions[0]["worsened"] is False


def test_only_changed_tracks_appear() -> None:
    previous = {"crypto": sv.VERDICT_VIABLE, "poly": sv.VERDICT_STRUCTURALLY_BLOCKED}
    current = {
        "crypto": _row("crypto", sv.VERDICT_VIABLE),
        "poly": _row("poly", sv.VERDICT_SLOW),
    }

    transitions = vw.detect_transitions(previous, current)

    assert [item["track"] for item in transitions] == ["poly"]


# ── 3. 메시지 형식 ──────────────────────────────────────────────────────


def test_transition_message_carries_the_action_rule() -> None:
    message = vw.format_transition_message(vw.detect_transitions({"crypto": sv.VERDICT_VIABLE}, {"crypto": _row("crypto", sv.VERDICT_SLOW)}))

    assert "판정 변경" in message
    assert vw.VERDICT_ACTIONS[sv.VERDICT_SLOW] in message
    assert "결과를 보기 전에 고정" in message


def test_weekly_lines_report_verdict_and_projection() -> None:
    """주간 리포트는 판정과 D+28 예상 표본을 상시 포함한다."""
    lines = vw.format_weekly_verdict_lines({"crypto": _row("crypto", sv.VERDICT_SLOW, projected=12)})

    joined = "\n".join(lines)
    assert "완주 가능성 판정" in joined
    assert "유효일 14/28" in joined
    assert "표본 4/30" in joined
    assert "D+28 예상 12건" in joined
    assert vw.VERDICT_ACTIONS[sv.VERDICT_SLOW] in joined


def test_weekly_lines_state_the_sample_rule() -> None:
    """N<30 에서 승률·수익률을 계산하지 않는다는 사실을 리포트가 스스로 말한다."""
    lines = vw.format_weekly_verdict_lines({"crypto": _row("crypto", sv.VERDICT_VIABLE, projected=40)})

    assert any("표본 부족" in line and "승률" in line for line in lines)


def test_weekly_lines_are_empty_without_verdicts() -> None:
    assert vw.format_weekly_verdict_lines({}) == []


def test_viable_track_does_not_get_an_action_line() -> None:
    """개입 없음이 대응이므로 지시를 덧붙이지 않는다 — 잡음이 된다."""
    lines = vw.format_weekly_verdict_lines({"crypto": _row("crypto", sv.VERDICT_VIABLE, projected=40)})

    assert not any("└ 대응" in line for line in lines)


def test_labels_cover_every_verdict() -> None:
    """영문 상수를 그대로 알림에 내보내면 무슨 뜻인지 매번 찾아봐야 한다."""
    for verdict in (sv.VERDICT_VIABLE, sv.VERDICT_SLOW, sv.VERDICT_STRUCTURALLY_BLOCKED, sv.VERDICT_INSUFFICIENT_DATA):
        assert vw.VERDICT_LABELS.get(verdict)
