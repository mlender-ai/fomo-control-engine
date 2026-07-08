"""WO-FCE-44 — 포지션 라이프사이클 알림 수용 기준 테스트.

opened/closed/verdict/stance/insufficient/pulse · 가짜 종료 디바운스는 test_live_position_api에서.
판단 문형 가드("진입하세요" 류) grep 0 · 상태 영속화 · E2E open→verdict→close 순서.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import Settings
from app.notify.alerts import AlertEngine
from app.notify.lifecycle import (
    closed_candidate,
    opened_candidate,
    pulse_candidate,
    transition_candidates,
)
from app.notify.state import NotificationState

NOW = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)  # KST 21:00 — 무음 아님

FORBIDDEN_PHRASES = ["진입하세요", "진입해도 좋", "매수하세요", "매도하세요", "사세요", "파세요", "진입 추천"]


def _settings(**overrides) -> Settings:
    base = {
        "telegram_alerts_enabled": True,
        "telegram_quiet_hours_enabled": False,
        "notification_state_path": "",  # 테스트는 파일 영속화 비활성
    }
    base.update(overrides)
    return Settings(**base)


def _one_liners(overall: str = "상방", up: int = 5, down: int = 1) -> dict:
    unknown = 7 - up - down
    return {
        "lines": [
            {"module": "wyckoff", "module_label": "와이코프", "stance": "상방", "phrase": "매집 우세", "confidence_class": "강", "evidence_ref": "x"},
            {"module": "liquidity", "module_label": "유동성", "stance": "상방", "phrase": "저점 청소 후 반등 구조", "confidence_class": "중", "evidence_ref": "x"},
            {"module": "derivatives", "module_label": "수급", "stance": "하방", "phrase": "롱 쏠림 경계", "confidence_class": "중", "evidence_ref": "x"},
        ],
        "counts": {"상방": up, "하방": down, "횡보": 0, "판단불가": max(0, unknown)},
        "overall_stance": overall,
        "summary": f"종합: 상방 {up} · 하방 {down}",
    }


def _context(verdict: str = "holding", overall: str = "상방", **position_overrides) -> dict:
    position = {
        "id": "11111111-1111-1111-1111-111111111111",
        "symbol": "BTCUSDT",
        "direction": "long",
        "leverage": 10,
        "entry_price": 100.0,
        "quantity": 0.5,
        "opened_at": NOW.isoformat(),
        "scenario_id": None,
    }
    position.update(position_overrides)
    return {
        "position": position,
        "state": {"pnl_percent": 3.2, "health_score": 72, "as_of": NOW.isoformat()},
        "action_plan": {
            "verdict_state": verdict,
            "headline_action": "무효화 98.0 유지 관찰",
            "invalidation": {"price": 98.0},
            "take_profit": [{"price": 110.0}],
        },
        "chart_analysis": {"one_liners": _one_liners(overall)},
    }


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.enabled = True

    async def send_to_all(self, text: str, *, reply_markup=None) -> int:
        self.messages.append(text)
        return 1


def _engine(sender: FakeSender | None = None, settings: Settings | None = None) -> tuple[AlertEngine, FakeSender, NotificationState]:
    sender = sender or FakeSender()
    state = NotificationState()
    engine = AlertEngine(settings or _settings(), sender, state, now_provider=lambda: NOW)
    return engine, sender, state


# ── 이벤트 메시지 (Part B) ─────────────────────────────────────────

def test_opened_candidate_carries_initial_verdict_and_scenario() -> None:
    candidate = opened_candidate(_context())
    assert "포지션 진입 감지" in candidate.message
    assert "관측 양호: 상방 근거 5/6 · 반대 1" in candidate.message
    assert "판정: 와이코프: 매집 우세(강)" in candidate.message
    assert "시나리오: 매칭된 사전 시나리오 없음" in candidate.message


def test_opened_candidate_scenario_matched() -> None:
    candidate = opened_candidate(_context(scenario_id="abc"))
    assert "저장된 진입 시나리오와 매칭됨" in candidate.message


def test_closed_candidate_has_pnl_and_two_line_review() -> None:
    trade = {
        "id": "22222222-2222-2222-2222-222222222222",
        "pnl_amount": 42.5,
        "pnl_percent": 4.25,
        "judgment_scorecard": {"correct": 3, "wrong": 1, "whipsaw": 1},
        "review_v2": {"realized_r": 1.4},
    }
    candidate = closed_candidate(_context()["position"], trade)
    assert "실현 손익 +42.50 USDT (+4.25%)" in candidate.message
    assert "복기: 판단 5건 채점 — 적중 3 · 오판 1 · 휩쏘 1" in candidate.message
    assert "실현 R +1.40" in candidate.message


def test_verdict_transition_fires_with_reason_and_next_price() -> None:
    tracker = {"verdict_state": "holding", "overall_stance": "상방"}
    candidates, updated = transition_candidates(_context(verdict="danger"), tracker, _settings(), now=NOW)
    verdicts = [candidate for candidate in candidates if candidate.rule_id == "verdict_changed"]
    assert verdicts and "관찰 유지 → 위험 확인 필요" in verdicts[0].message
    assert "다음 가격: 무효화" in verdicts[0].message
    assert verdicts[0].severity == "warn"  # 악화 방향
    assert updated["verdict_state"] == "danger"


def test_stance_flip_fires_with_top_reason() -> None:
    tracker = {"verdict_state": "holding", "overall_stance": "상방"}
    candidates, updated = transition_candidates(_context(overall="하방"), tracker, _settings(), now=NOW)
    flips = [candidate for candidate in candidates if candidate.rule_id == "stance_flipped"]
    assert flips and "상방 → 하방" in flips[0].message
    assert "반전 근거:" in flips[0].message
    assert updated["overall_stance"] == "하방"


def test_evidence_insufficient_requires_sustained_window() -> None:
    settings = _settings(alert_evidence_insufficient_hours=2.0)
    context = _context(verdict="standby")
    # 첫 관측: 시계만 시작, 발화 없음.
    candidates, tracker = transition_candidates(context, {}, settings, now=NOW)
    assert not [c for c in candidates if c.rule_id == "evidence_insufficient"]
    # 2시간 지속 후 발화.
    candidates, tracker = transition_candidates(context, tracker, settings, now=NOW + timedelta(hours=2, minutes=1))
    fired = [c for c in candidates if c.rule_id == "evidence_insufficient"]
    assert fired and "판단 근거 부족" in fired[0].message
    # 재발화 방지 플래그.
    candidates, tracker = transition_candidates(context, tracker, settings, now=NOW + timedelta(hours=3))
    assert not [c for c in candidates if c.rule_id == "evidence_insufficient"]


def test_pulse_bundles_positions_and_reports_all_normal() -> None:
    candidate = pulse_candidate([_context(), _context(symbol="ETHUSDT")])
    assert candidate is not None
    assert candidate.message.count("•") == 2
    assert "전 포지션 관측 정상 범위" in candidate.message
    assert "정상 상태도 발송" in candidate.message


def test_pulse_merges_pending_redelivery() -> None:
    pending = [{"rule_id": "verdict_changed", "symbol": "BTCUSDT", "title": "판정 상태 전이", "fired_at": NOW.isoformat()}]
    candidate = pulse_candidate([_context()], pending_redelivery=pending)
    assert "미도달 알림 1건 병합" in candidate.message


# ── 판단 문형 가드 (수용 기준: "진입하세요/좋다" grep 0) ────────────

def test_no_directive_trading_phrases_in_messages() -> None:
    messages = [
        opened_candidate(_context()).message,
        closed_candidate(_context()["position"], {"pnl_amount": 1.0, "pnl_percent": 0.1}).message,
        pulse_candidate([_context()]).message,
    ]
    tracker = {"verdict_state": "holding", "overall_stance": "상방"}
    candidates, _ = transition_candidates(_context(verdict="danger", overall="하방"), tracker, _settings(), now=NOW)
    messages.extend(candidate.message for candidate in candidates)
    for message in messages:
        for phrase in FORBIDDEN_PHRASES:
            assert phrase not in message, f"금지 문형 발견: {phrase}"


def test_no_directive_phrases_in_notify_sources() -> None:
    notify_dir = Path(__file__).resolve().parents[1] / "app" / "notify"
    for path in notify_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN_PHRASES:
            assert phrase not in text, f"{path.name}에 금지 문형: {phrase}"


# ── E2E: open → verdict change → close 순서 (데모/페이크 싱크) ──────

def test_e2e_lifecycle_sequence(monkeypatch) -> None:
    engine, sender, state = _engine()
    monkeypatch.setattr("app.notify.alerts.service.record_alert", lambda record: record)
    monkeypatch.setattr("app.notify.alerts.service.alert_response_history_line", lambda rule_id: None)
    opened_context = _context()
    monkeypatch.setattr(
        "app.notify.alerts.service.live_position_alert_context",
        lambda position_id: opened_context,
    )

    # 1) 진입 감지.
    asyncio.run(engine.evaluate_lifecycle({"created_position_ids": [opened_context["position"]["id"]], "positions": [opened_context]}))
    # 2) 판정 전이 (holding → danger).
    asyncio.run(engine.evaluate_lifecycle({"positions": [_context(verdict="danger")]}))
    # 3) 종료 감지.
    trade = {"id": "33333333-3333-3333-3333-333333333333", "pnl_amount": -12.0, "pnl_percent": -1.2, "judgment_scorecard": {}}
    asyncio.run(
        engine.evaluate_lifecycle({"closed_positions": [{"position": opened_context["position"], "trade": trade}]})
    )

    joined = "\n---\n".join(sender.messages)
    assert sender.messages, joined
    opened_index = next(i for i, m in enumerate(sender.messages) if "포지션 진입 감지" in m)
    verdict_index = next(i for i, m in enumerate(sender.messages) if "판정 상태 전이" in m)
    closed_index = next(i for i, m in enumerate(sender.messages) if "포지션 종료 감지" in m)
    assert opened_index < verdict_index < closed_index
    # 종료 후 트래커 정리.
    assert opened_context["position"]["id"] not in state.lifecycle_positions


def test_failed_delivery_queued_for_next_pulse(monkeypatch) -> None:
    class DeadSender(FakeSender):
        async def send_to_all(self, text: str, *, reply_markup=None) -> int:
            self.messages.append(text)
            return 0  # 발송 실패

    sender = DeadSender()
    engine, _, state = _engine(sender=sender)
    monkeypatch.setattr("app.notify.alerts.service.record_alert", lambda record: record)
    monkeypatch.setattr("app.notify.alerts.service.alert_response_history_line", lambda rule_id: None)
    context = _context()
    monkeypatch.setattr("app.notify.alerts.service.live_position_alert_context", lambda position_id: context)

    asyncio.run(engine.evaluate_lifecycle({"created_position_ids": [context["position"]["id"]], "positions": []}))
    assert state.pending_redelivery and state.pending_redelivery[0]["rule_id"] == "position_opened"

    # 발송 복구 → 펄스에 병합 발송 + 큐 비움.
    engine.sender = FakeSender()
    asyncio.run(engine.maybe_send_pulse({"positions": [context]}))
    assert "미도달 알림 1건 병합" in engine.sender.messages[0]
    assert state.pending_redelivery == []


def test_pulse_respects_quiet_hours(monkeypatch) -> None:
    settings = _settings(
        telegram_quiet_hours_enabled=True,
        telegram_quiet_hours_start="00:00",
        telegram_quiet_hours_end="23:59",
    )
    engine, sender, state = _engine(settings=settings)
    monkeypatch.setattr("app.notify.alerts.service.record_alert", lambda record: record)
    sent = asyncio.run(engine.maybe_send_pulse({"positions": [_context()]}))
    assert sent == 0
    assert sender.messages == []  # 무음 준수
    assert state.suppressed_alerts  # 아침 요약 병합 대기
    assert state.last_pulse_at is not None


def test_pulse_interval_gate(monkeypatch) -> None:
    engine, sender, state = _engine(settings=_settings(alert_pulse_interval_hours=4.0))
    monkeypatch.setattr("app.notify.alerts.service.record_alert", lambda record: record)
    state.last_pulse_at = NOW - timedelta(hours=1)
    assert asyncio.run(engine.maybe_send_pulse({"positions": [_context()]})) == 0
    state.last_pulse_at = NOW - timedelta(hours=5)
    assert asyncio.run(engine.maybe_send_pulse({"positions": [_context()]})) == 1
    assert "정기 상태 펄스" in sender.messages[0]


# ── 상태 영속화 (Part C) ───────────────────────────────────────────

def test_notification_state_roundtrip(tmp_path) -> None:
    state = NotificationState()
    state.last_summary_date = "2026-07-08"
    state.lifecycle_positions["p1"] = {"verdict_state": "danger", "overall_stance": "하방"}
    state.last_pulse_at = NOW
    state.pending_redelivery.append({"rule_id": "position_opened", "symbol": "BTCUSDT", "title": "t", "fired_at": NOW.isoformat()})
    state.suppressed_alerts.append({"rule_id": "periodic_pulse", "symbol": "PULSE", "title": "펄스"})
    path = str(tmp_path / "state.json")
    state.save(path)

    restored = NotificationState()
    restored.load(path)
    assert restored.last_summary_date == "2026-07-08"
    assert restored.lifecycle_positions["p1"]["verdict_state"] == "danger"
    assert restored.last_pulse_at == NOW
    assert restored.pending_redelivery[0]["rule_id"] == "position_opened"
    assert restored.suppressed_alerts[0]["symbol"] == "PULSE"
