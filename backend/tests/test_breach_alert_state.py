"""WO-FCE-BREACH-ALERT-FIX-01 — 무효화 이탈 반복 발송 수리 회귀.

두 가지를 방어한다:
1. 이탈은 **상태**다 — 같은 깊이를 반복 발송하지 않는다. 단 최초 1회는 반드시 보낸다(C1).
2. 영속 상한이 **활성 쿨다운을 축출하지 않는다** — 축출되면 재로드 시 armed 로 부활해
   같은 알림이 다시 나간다(2026-08-04 실측 원인).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.notify.alerts import _breach_depth_pct, _breach_repeat_suppressed
from app.notify.rules import AlertCandidate
from app.notify.state import AlertRuleState, NotificationState, _retained_rule_states

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def _breach(distance_pct: float | None = -0.52) -> AlertCandidate:
    return AlertCandidate(
        rule_id="invalidation_breach",
        severity="critical",
        position_id="pos-1",
        symbol="DRAMUSDT",
        identity="invalidation",
        title="무효화 이탈",
        message="무효화 이탈",
        payload={"trigger_price": 50.1710, "current_price": 49.9100, "distance_pct": distance_pct},
    )


def _settings(threshold: float = 1.0) -> Settings:
    return Settings(alert_breach_reescalate_pct=threshold)


def test_depth_is_absolute() -> None:
    assert _breach_depth_pct(_breach(-0.52)) == 0.52
    assert _breach_depth_pct(_breach(None)) is None


def test_first_breach_always_sends() -> None:
    """C1 — 포지션 위험 신호를 없애는 것이 아니라 반복만 줄인다."""
    state = AlertRuleState()
    assert _breach_repeat_suppressed(_breach(-0.52), state, _settings()) is False


def test_same_depth_is_suppressed() -> None:
    """01:46/02:22/03:22/04:22 반복(내용 동일)이 여기서 막힌다."""
    state = AlertRuleState(last_breach_pct=0.52)
    assert _breach_repeat_suppressed(_breach(-0.52), state, _settings()) is True


def test_minor_worsening_is_suppressed() -> None:
    state = AlertRuleState(last_breach_pct=0.52)
    assert _breach_repeat_suppressed(_breach(-1.0), state, _settings(threshold=1.0)) is True


def test_significant_worsening_resends() -> None:
    state = AlertRuleState(last_breach_pct=0.52)
    assert _breach_repeat_suppressed(_breach(-1.6), state, _settings(threshold=1.0)) is False


def test_missing_depth_suppressed_when_already_alerted() -> None:
    """깊이를 모르면 이미 알린 상태에서 또 보내지 않는다 — 새 정보가 없다."""
    state = AlertRuleState(last_breach_pct=0.52)
    assert _breach_repeat_suppressed(_breach(None), state, _settings()) is True


def test_other_rules_untouched() -> None:
    candidate = _breach().__class__(
        rule_id="status_worsened",
        severity="warn",
        position_id="pos-1",
        symbol="DRAMUSDT",
        identity="severity",
        title="상태 악화",
        message="x",
        payload={"distance_pct": -0.5},
    )
    assert _breach_repeat_suppressed(candidate, AlertRuleState(last_breach_pct=0.1), _settings()) is False


# ── 영속 상한이 활성 쿨다운을 버리지 않는다 ──────────────────────────


def test_active_cooldown_survives_truncation() -> None:
    """2026-08-04 실측 원인: 500 상한을 삽입 순서로 잘라 활성 쿨다운이 축출됐다."""
    states: dict[str, AlertRuleState] = {}
    # 활성 이탈 쿨다운을 **가장 먼저** 넣는다 — 삽입 순서 절단이면 가장 먼저 버려진다.
    states["invalidation_breach:pos-1:invalidation"] = AlertRuleState(
        status="cooldown",
        last_fired_at=NOW - timedelta(minutes=5),
        cooldown_until=NOW + timedelta(minutes=25),
        last_breach_pct=0.52,
    )
    # 그 뒤로 만료된 고래 키를 상한 넘게 채운다(실측: whale_entry 가 288/500 을 점유했다).
    for index in range(2500):
        states[f"whale_entry:0x{index:040x}:fill"] = AlertRuleState(
            status="cooldown",
            last_fired_at=NOW - timedelta(days=30),
            cooldown_until=NOW - timedelta(days=30),
        )

    retained = _retained_rule_states(states, now=NOW)

    assert "invalidation_breach:pos-1:invalidation" in retained
    kept = retained["invalidation_breach:pos-1:invalidation"]
    assert kept["status"] == "cooldown"
    assert kept["last_breach_pct"] == 0.52


def test_expired_states_are_dropped_first() -> None:
    states = {
        "old:a:x": AlertRuleState(status="cooldown", last_fired_at=NOW - timedelta(days=90), cooldown_until=NOW - timedelta(days=90)),
        "fresh:b:x": AlertRuleState(status="cooldown", last_fired_at=NOW, cooldown_until=NOW + timedelta(minutes=10)),
    }
    retained = _retained_rule_states(states, now=NOW)
    keys = list(retained)
    # 활성 쿨다운이 먼저 온다 — 상한에 걸릴 때 살아남는 쪽이다.
    assert keys[0] == "fresh:b:x"


def test_breach_depth_round_trips_through_persistence(tmp_path) -> None:
    state = NotificationState()
    state.alert_rule_states["invalidation_breach:pos-1:invalidation"] = AlertRuleState(
        status="cooldown",
        last_fired_at=NOW,
        cooldown_until=NOW + timedelta(minutes=30),
        last_breach_pct=0.52,
    )
    path = str(tmp_path / "state.json")
    state.save(path)

    restored = NotificationState()
    restored.load(path)

    rule = restored.alert_rule_states["invalidation_breach:pos-1:invalidation"]
    assert rule.status == "cooldown"
    assert rule.last_breach_pct == 0.52
