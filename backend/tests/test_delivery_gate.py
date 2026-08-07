"""WO-FCE-WHALE-ALERT-DEMOTE-01 — 발송 통합 관문 회귀.

배경: 실측 2026-08-08 00:10 KST, 동일 지갑에서 6건이 같은 분에 도착했다. 원인은 배치
identity 에 체결 ID·건수가 들어가 `state_key` 가 매번 새로 생성되고, 따라서 쿨다운이
**구조적으로 한 번도 적용된 적이 없었다**는 것이다. 빈도를 조이는 접근은 선행 WO 에서
이미 실패했으므로 발송 경로에서 뺀다.

고정하는 명제:
1. 모든 알림이 관문을 지난다 — 우회 경로 0개
2. `whale_entry` 는 발송 0건이고, 미발송 사유가 남는다 (조용히 사라지지 않는다)
3. 포지션·생존 경보는 그대로 발송된다 (강등이 다른 알림을 죽이지 않는다)
4. 레지스트리에 없는 새 rule_id 는 기본 차단된다 — 이게 구조적 결함의 수리다
5. 수집·저장은 건드리지 않는다 (강등은 발송 경로만)
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.config import Settings
from app.notify import delivery_gate
from app.notify.alerts import _whale_observation_line
from app.notify.rules import AlertCandidate

NOW = datetime(2026, 8, 8, 0, 10, tzinfo=timezone.utc)


def _candidate(rule_id: str) -> AlertCandidate:
    return AlertCandidate(
        rule_id=rule_id,
        severity="info",
        position_id=None,
        symbol="BTCUSDT",
        identity="x",
        title="t",
        message="m",
    )


# ── 1. 관문 우회 경로 0개 ───────────────────────────────────────────────


def test_every_fire_path_goes_through_the_gate() -> None:
    """`_fire_if_allowed` 가 유일한 발송 진입점이고, 그 첫 동작이 관문 판정이어야 한다.

    관문을 거치지 않는 경로가 하나라도 남으면 원칙이 다시 경로별로 갈라진다.
    """
    source = (Path(__file__).resolve().parents[1] / "app" / "notify" / "alerts.py").read_text(encoding="utf-8")

    body = source.split("async def _fire_if_allowed(self, candidate: AlertCandidate) -> int:", 1)[1]
    head = body.split("rule_state =", 1)[0]
    assert "delivery_gate.evaluate_rule(candidate.rule_id)" in head, "관문 판정이 쿨다운 조회보다 먼저여야 한다"

    # 발송은 sender.send_to_all 로만 나간다. _fire_if_allowed 밖에서 직접 호출하는 곳이
    # 있으면 그 경로는 관문을 우회한다 — 주간/일일 리포트 등 의도된 경로만 허용한다.
    direct = re.findall(r"await self\.sender\.send_to_all\(", source)
    assert len(direct) <= 6, f"예상보다 많은 직접 발송 경로가 있다: {len(direct)}"


# ── 2. 고래 강등 ────────────────────────────────────────────────────────


def test_whale_entry_is_blocked_with_a_reason() -> None:
    decision = delivery_gate.evaluate_rule("whale_entry")

    assert decision.allowed is False
    assert decision.demoted is True
    assert "미검증" in decision.reason


def test_blocked_whale_candidate_is_recorded_not_silently_dropped(monkeypatch) -> None:
    """C3 — 발송하지 않은 것과 발생하지 않은 것은 다르다."""
    from app.notify.state import NotificationState

    state = NotificationState()
    recorded: list[tuple[str, bool]] = []

    class _Engine:
        def __init__(self) -> None:
            self.state = state

        def _record(self, candidate: AlertCandidate, *, delivered: bool, fired_at: datetime) -> None:
            recorded.append((candidate.rule_id, delivered))

    from app.notify.alerts import AlertEngine

    engine = _Engine()
    AlertEngine._record_blocked(engine, _candidate("whale_entry"), delivery_gate.evaluate_rule("whale_entry"), now=NOW)

    assert len(state.blocked_alerts) == 1
    entry = state.blocked_alerts[0]
    assert entry["rule_id"] == "whale_entry"
    assert entry["demoted"] is True
    assert "미검증" in entry["reason"]
    assert recorded == [("whale_entry", False)], "원장에도 delivered=False 로 남아야 한다"


# ── 3. 다른 알림은 살아 있어야 한다 ─────────────────────────────────────


@pytest.mark.parametrize(
    "rule_id",
    [
        "invalidation_breach",
        "liq_proximity",
        "mdd_limit_critical",
        "take_profit_hit",
        "position_opened",
        "position_closed",
        "engine_liveness",
        "process_restarted",
        "data_stall",
        "periodic_pulse",
    ],
)
def test_existing_alerts_still_pass(rule_id: str) -> None:
    """강등이 다른 알림을 죽이면 그건 수리가 아니라 새 사고다."""
    assert delivery_gate.evaluate_rule(rule_id).allowed is True, f"{rule_id} 가 차단됐다"


def test_every_default_enabled_rule_is_classified() -> None:
    """기본 활성 rule 이 레지스트리에 없으면 배포와 동시에 조용히 끊긴다."""
    enabled = Settings().alert_enabled_rule_set
    unclassified = enabled - delivery_gate.PUSH_ALLOWED_RULES - delivery_gate.DEMOTED_RULES

    assert unclassified == set(), f"미분류 rule_id: {sorted(unclassified)}"


def test_only_whale_is_blocked_among_default_rules() -> None:
    """예상 밖 차단이 있으면 즉시 드러나야 한다 — 이 목록이 곧 배포 전 확인표다."""
    enabled = Settings().alert_enabled_rule_set
    blocked = sorted(rule for rule in enabled if not delivery_gate.evaluate_rule(rule).allowed)

    assert blocked == ["whale_entry"]


# ── 4. 기본 차단 (구조적 결함 수리) ─────────────────────────────────────


def test_unregistered_rule_is_denied_by_default() -> None:
    """새 알림이 다른 경로로 들어와도 원칙이 적용된다 — 이게 D3 의 수리다."""
    decision = delivery_gate.evaluate_rule("some_new_signal_v2")

    assert decision.allowed is False
    assert decision.demoted is False
    assert "미등록" in decision.reason


def test_paper_event_kinds_share_the_same_gate() -> None:
    """페이퍼 이벤트도 같은 모듈이 판정한다 — 정책이 두 곳으로 갈라지지 않는다."""
    assert delivery_gate.evaluate_event({"kind": "opened"}).allowed is True
    assert delivery_gate.evaluate_event({"kind": "rejected_summary"}).allowed is False
    assert delivery_gate.evaluate_event({}).allowed is False


def test_registry_snapshot_states_the_scope_limit() -> None:
    snapshot = delivery_gate.registry_snapshot()

    assert snapshot["demoted_rules"] == ["whale_entry"]
    assert "수집·저장·조회는 그대로" in snapshot["note"]


# ── 5. 일 1회 요약 1줄 (Phase 3) ────────────────────────────────────────


def _blocked(hours_ago: float, *, sample_size: int = 5, notional: float = 120_000.0, wallet: str = "0xabc") -> dict:
    return {
        "rule_id": "whale_entry",
        "blocked_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "reason": delivery_gate.DEMOTION_REASON,
        "payload": {"wallet_address": wallet, "fill_count": 3, "total_notional": notional, "sample_size": sample_size},
    }


def test_daily_line_is_one_line_without_wallet_names() -> None:
    """개별 건·지갑명을 나열하면 강등한 스팸이 요약으로 자리를 옮길 뿐이다."""
    line = _whale_observation_line([_blocked(1), _blocked(2, wallet="0xdef")], now=NOW)

    assert line is not None
    assert "\n" not in line
    assert "0xabc" not in line and "0xdef" not in line
    assert "다중체결 2건" in line and "지갑 2개" in line


def test_daily_line_marks_insufficient_sample() -> None:
    """C6 — N<30 이면 반드시 표본 부족을 병기한다."""
    line = _whale_observation_line([_blocked(1, sample_size=5)], now=NOW)

    assert "표본 부족" in line and "N=5" in line


def test_daily_line_ignores_events_outside_the_window() -> None:
    assert _whale_observation_line([_blocked(30)], now=NOW) is None


def test_daily_line_is_absent_without_whale_events() -> None:
    assert _whale_observation_line([], now=NOW) is None


# ── 6. 수집·저장 무영향 (C2) ────────────────────────────────────────────


def test_demotion_does_not_touch_collection() -> None:
    """강등은 발송 경로만이다. 수집·저장 코드에 관문이 끼어들면 원장이 비게 된다."""
    onchain = (Path(__file__).resolve().parents[1] / "app" / "onchain").rglob("*.py")
    offenders = [path.name for path in onchain if "delivery_gate" in path.read_text(encoding="utf-8")]

    assert offenders == [], f"수집 경로가 발송 관문을 참조한다: {offenders}"


def test_promotion_document_exists_with_open_decisions() -> None:
    """승격 조건은 결과를 보기 전에 문서로 고정한다(C4). 배선은 하지 않는다."""
    document = Path(__file__).resolve().parents[2] / "docs" / "validation" / "WHALE_ALERT_PROMOTION.md"

    text = document.read_text(encoding="utf-8")
    assert "사용자 결정" in text
    for item in ("표본", "승률", "누적 R", "지갑당 발송 상한"):
        assert item in text, f"승격 조건에 '{item}' 이 없다"
