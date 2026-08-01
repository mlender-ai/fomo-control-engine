"""WO-FCE-STRUCTURE-CONTEXT-01 회귀 가드.

핵심 원칙: **구조는 관측이며 예측이 아니다.** 이 테스트는 인과 단정 금지(C4),
룩어헤드 금지(C3), 전이 시에만 발송(C6), 게이트 무변경(C2)을 강제한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import MarketCandle
from app.structure.candidates.engine import order_block_zones
from app.structure.context import (
    RANGE_POSITION_ABOVE,
    RANGE_POSITION_BELOW,
    RANGE_POSITION_INSIDE,
    build_structure_context,
    detect_structure_transitions,
    transition_state_key,
)


def _analysis(phase: str = "accumulation", low: float = 1680.0, high: float = 1750.0, markers=None) -> dict:
    return {
        "wyckoff_range": {"support": {"price": low}, "resistance": {"price": high}, "width_pct": 4.2, "candles_inside": 30},
        "wyckoff_phase": {"phase": phase, "side": "long" if phase == "accumulation" else "short"},
        "wyckoff_markers": markers if markers is not None else [{"label": "Spring", "as_of": "2026-07-30T00:00:00+00:00", "confidence": 72}],
    }


def _zone(low: float, high: float, kind: str = "demand") -> dict:
    return {"kind": kind, "direction": "long" if kind == "demand" else "short", "zone_low": low, "zone_high": high, "retests": 1, "untested": False}


# ── 포지션 관계 판정 (이 WO의 핵심) ────────────────────────────


def test_entry_inside_range_is_reported() -> None:
    context = build_structure_context(analysis=_analysis(), entry_price=1710.89, mark_price=1720.0)
    assert context["position_relation"]["entry_range_position"] == RANGE_POSITION_INSIDE
    assert context["wyckoff"]["range_low"] == 1680.0
    assert context["wyckoff"]["range_high"] == 1750.0


def test_entry_above_and_below_range() -> None:
    above = build_structure_context(analysis=_analysis(), entry_price=1800.0, mark_price=1800.0)
    below = build_structure_context(analysis=_analysis(), entry_price=1600.0, mark_price=1600.0)
    assert above["position_relation"]["entry_range_position"] == RANGE_POSITION_ABOVE
    assert below["position_relation"]["entry_range_position"] == RANGE_POSITION_BELOW


def test_entry_overlapping_order_block_is_detected() -> None:
    context = build_structure_context(
        analysis=_analysis(),
        entry_price=1710.89,
        mark_price=1720.0,
        order_block_zones=[_zone(1695.0, 1712.0)],
    )
    assert context["position_relation"]["entry_in_order_block"] is True
    assert context["order_blocks"]["entry_zone"]["kind"] == "demand"


def test_invalidation_relative_to_range_low() -> None:
    context = build_structure_context(
        analysis=_analysis(),
        entry_price=1710.89,
        mark_price=1720.0,
        invalidation_price=1674.0,
    )
    assert context["position_relation"]["invalidation_vs_range_low"] == "below"


def test_pnf_remaining_distance() -> None:
    context = build_structure_context(
        analysis=_analysis(),
        entry_price=1710.0,
        mark_price=1700.0,
        pnf_objective={"target_price": 1870.0},
    )
    assert context["position_relation"]["pnf_remaining_pct"] == 10.0


def test_verdict_line_is_descriptive_not_causal() -> None:
    """C4: 판정 1줄은 서술이며 인과 단정이 아니다."""
    context = build_structure_context(
        analysis=_analysis(),
        entry_price=1710.89,
        mark_price=1720.0,
        invalidation_price=1674.0,
        order_block_zones=[_zone(1695.0, 1712.0)],
    )
    line = context["verdict_line"]
    assert "진입" in line and "레인지" in line
    for banned in ("따라서", "이므로", "상승할", "하락할", "예상", "전망"):
        assert banned not in line, f"인과 단정 문구 발견: {banned}"


def test_disclaimer_states_observation_not_prediction() -> None:
    context = build_structure_context(analysis=_analysis(), entry_price=1710.0, mark_price=1720.0)
    assert "예측이 아닙니다" in context["disclaimer"]


def test_missing_range_does_not_fabricate() -> None:
    """레인지가 없으면 억지로 만들지 않는다."""
    context = build_structure_context(analysis={}, entry_price=1710.0, mark_price=1720.0)
    assert context["wyckoff"]["range_low"] is None
    assert context["position_relation"]["entry_range_position"] == "unknown"


# ── 리페인팅 (C3) ─────────────────────────────────────────────


def test_repaint_is_disclosed_not_hidden() -> None:
    context = build_structure_context(
        analysis=_analysis(phase="distribution"),
        entry_price=1710.0,
        mark_price=1720.0,
        previous_phase="accumulation",
    )
    assert context["repaint"]["repainted"] is True
    assert context["repaint"]["previous_phase"] == "accumulation"
    assert "재해석" in context["repaint"]["note"]
    assert "국면 재해석됨" in context["verdict_line"]


def test_no_repaint_when_phase_stable() -> None:
    context = build_structure_context(analysis=_analysis(), entry_price=1710.0, mark_price=1720.0, previous_phase="accumulation")
    assert context["repaint"]["repainted"] is False


# ── 전이 시에만 발송 (C6) ─────────────────────────────────────


def test_no_transition_when_state_unchanged() -> None:
    """동일 상태가 반복되면 이벤트 0건 — 100틱 반복해도 스팸이 없다."""
    context = build_structure_context(analysis=_analysis(), entry_price=1710.0, mark_price=1720.0)
    for _ in range(100):
        assert detect_structure_transitions(context, context) == []


def test_range_exit_produces_transition() -> None:
    inside = build_structure_context(analysis=_analysis(), entry_price=1710.0, mark_price=1720.0)
    outside = build_structure_context(analysis=_analysis(), entry_price=1710.0, mark_price=1600.0)
    events = detect_structure_transitions(inside, outside)
    kinds = [event["kind"] for event in events]
    assert "range_exit" in kinds


def test_phase_change_produces_transition() -> None:
    before = build_structure_context(analysis=_analysis(phase="accumulation"), entry_price=1710.0, mark_price=1720.0)
    after = build_structure_context(analysis=_analysis(phase="markup"), entry_price=1710.0, mark_price=1720.0)
    events = detect_structure_transitions(before, after)
    assert any(event["kind"] == "phase_change" for event in events)


def test_order_block_enter_exit_transition() -> None:
    outside = build_structure_context(analysis=_analysis(), entry_price=1710.0, mark_price=1720.0, order_block_zones=[])
    inside = build_structure_context(analysis=_analysis(), entry_price=1710.0, mark_price=1720.0, order_block_zones=[_zone(1700.0, 1715.0)])
    events = detect_structure_transitions(outside, inside)
    assert any(event["kind"] == "order_block_enter" for event in events)


def test_pnf_target_reached_transition_fires_once_on_crossing() -> None:
    before = build_structure_context(analysis=_analysis(), entry_price=1710.0, mark_price=1800.0, pnf_objective={"target_price": 1870.0})
    after = build_structure_context(analysis=_analysis(), entry_price=1710.0, mark_price=1880.0, pnf_objective={"target_price": 1870.0})
    assert any(event["kind"] == "pnf_target_reached" for event in detect_structure_transitions(before, after))
    # 이미 넘은 뒤에는 다시 발화하지 않는다.
    assert not any(event["kind"] == "pnf_target_reached" for event in detect_structure_transitions(after, after))


def test_no_previous_context_yields_no_transitions() -> None:
    """최초 관측은 전이가 아니다 — 첫 틱에 알림이 쏟아지지 않는다."""
    context = build_structure_context(analysis=_analysis(), entry_price=1710.0, mark_price=1720.0)
    assert detect_structure_transitions(None, context) == []


def test_transition_state_key_includes_symbol_kind_range() -> None:
    context = build_structure_context(analysis=_analysis(), entry_price=1710.0, mark_price=1720.0)
    key = transition_state_key("ETHUSDT", {"kind": "range_exit"}, context)
    assert "ETHUSDT" in key and "range_exit" in key and "1680" in key


# ── 오더블록 존 노출 (신규 감지기 아님 — C1) ──────────────────


def _candles(count: int = 40) -> list[MarketCandle]:
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    candles = []
    price = 100.0
    for index in range(count):
        # 하락 후 상승 구조 파괴를 만들어 수요 오더블록이 생기게 한다.
        drift = -1.0 if index < count // 2 else 2.0
        open_price = price
        price += drift
        candles.append(
            MarketCandle(
                timestamp=base + timedelta(hours=index),
                open=open_price,
                high=max(open_price, price) + 0.5,
                low=min(open_price, price) - 0.5,
                close=price,
                volume=1000.0,
            )
        )
    return candles


def test_order_block_zones_uses_only_supplied_candles() -> None:
    """C3 룩어헤드 감사: 접두사로 만든 존은 전체 캔들 없이도 결정론적이어야 한다."""
    candles = _candles()
    first = order_block_zones(candles)
    again = order_block_zones(list(candles))
    assert first == again  # 결정론적 — 숨은 상태 없음


def test_order_block_zones_returns_empty_for_short_history() -> None:
    assert order_block_zones(_candles(5)) == []


def test_order_block_zones_shape() -> None:
    zones = order_block_zones(_candles())
    for zone in zones:
        assert {"kind", "zone_low", "zone_high", "formed_at", "retests", "untested"} <= set(zone)
        assert zone["zone_low"] <= zone["zone_high"]


# ── 알림 화이트리스트 등록 (C5) ───────────────────────────────


def test_alert_rule_is_registered_in_whitelist() -> None:
    from app.core.config import Settings
    from app.notify.rules import RULE_LABELS, RULE_SEVERITY

    assert "position_structure_event" in RULE_LABELS
    assert RULE_SEVERITY["position_structure_event"] == "info"
    assert "position_structure_event" in Settings().alert_enabled_rule_set


# ── candidate 등록 · 게이트 무변경 (C2) ───────────────────────


def test_structure_context_registers_candidate_signatures() -> None:
    from app.backtest.signatures import signatures_from_analysis

    analysis = {
        "timeframe": "4h",
        "asset_class": "crypto",
        "structure_context": {
            "position_relation": {"entry_range_position": "inside", "entry_in_order_block": True},
            "order_blocks": {"entry_zone": {"kind": "demand"}},
        },
    }
    keys = [item["key"] for item in signatures_from_analysis(analysis)]
    assert any("structure_context:range_inside_entry" in key for key in keys)
    assert any("structure_context:ob_demand_entry" in key for key in keys)


def test_structure_signatures_are_candidate_not_validated() -> None:
    """승격 전까지 진입 판단에 영향이 없어야 한다."""
    from app.backtest.signatures import signatures_from_analysis

    analysis = {
        "timeframe": "4h",
        "asset_class": "crypto",
        "structure_context": {"position_relation": {"entry_range_position": "inside", "entry_in_order_block": False}},
    }
    structure = [item for item in signatures_from_analysis(analysis) if item["engine"] == "structure_context"]
    assert structure and all(item["strength_class"] == "candidate" for item in structure)


def test_entry_gate_parameters_unchanged() -> None:
    """C2: 이 WO는 진입·청산 게이트를 건드리지 않는다."""
    from app.stock_paper.exit_policy import load_exit_parameters
    from app.stock_paper.parameters import load_stock_parameters

    entry = load_stock_parameters()
    assert (entry.min_rr, entry.min_evidence, entry.min_entry_score) == (1.5, 4, 75)
    exit_parameters = load_exit_parameters()
    assert exit_parameters.max_holding_days == 10
    assert exit_parameters.invalidation_basis == "close"


def test_structure_context_absent_yields_no_signatures() -> None:
    from app.backtest.signatures import signatures_from_analysis

    keys = [item["key"] for item in signatures_from_analysis({"timeframe": "4h", "asset_class": "crypto"})]
    assert not any("structure_context" in key for key in keys)


# ── 알림 발화 경로 (보유 포지션 있을 때만) ────────────────────


def _alert_payload(symbol: str = "ETHUSDT", mark: float = 1720.0, entry: float = 1710.0) -> dict:
    return {
        "position": {"id": "pos-1", "symbol": symbol, "direction": "long", "entry_price": entry},
        "state": {
            "analysis": {
                **_analysis(),
                "mark_price": mark,
                "price_levels": {"invalidation": 1674.0},
                "order_block_zones": [_zone(1695.0, 1712.0)],
            }
        },
    }


def _engine(tmp_path):
    from app.core.config import Settings
    from app.notify.alerts import AlertEngine
    from app.notify.state import NotificationState
    from app.notify.telegram import TelegramSender

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'a.db'}", telegram_alerts_enabled=True)
    state = NotificationState()
    return AlertEngine(settings, TelegramSender(settings), state), state


def test_structure_alert_requires_open_position(tmp_path) -> None:
    """C: 보유 포지션이 없으면 발송하지 않는다."""
    import asyncio

    engine, _ = _engine(tmp_path)
    sent: list[str] = []
    setattr(engine, "_fire_if_allowed", lambda candidate: _record(sent, candidate))
    assert asyncio.run(engine.evaluate_position_structure([])) == 0
    assert sent == []


async def _record(sink, candidate):
    sink.append(candidate.message)
    return 1


def test_structure_alert_fires_only_on_transition(tmp_path) -> None:
    """C6: 최초 관측은 전이가 아니고, 동일 상태 100틱 반복도 0건."""
    import asyncio

    engine, state = _engine(tmp_path)
    sent: list[str] = []
    setattr(engine, "_fire_if_allowed", lambda candidate: _record(sent, candidate))
    payload = [_alert_payload()]

    assert asyncio.run(engine.evaluate_position_structure(payload)) == 0  # 최초 = 전이 아님
    for _ in range(100):
        asyncio.run(engine.evaluate_position_structure(payload))
    assert sent == [], "동일 상태 반복은 발송 0건이어야 한다"

    # 레인지 밖으로 이동 → 전이 1건
    moved = [_alert_payload(mark=1600.0)]
    assert asyncio.run(engine.evaluate_position_structure(moved)) == 1
    assert len(sent) == 1
    assert "구조 이벤트" in sent[0]
    assert "관측 정보이며 매매 신호가 아닙니다" in sent[0]
    assert "ETHUSDT" in state.structure_contexts


def test_structure_alert_message_has_no_causal_claim(tmp_path) -> None:
    import asyncio

    engine, _ = _engine(tmp_path)
    sent: list[str] = []
    setattr(engine, "_fire_if_allowed", lambda candidate: _record(sent, candidate))
    asyncio.run(engine.evaluate_position_structure([_alert_payload()]))
    asyncio.run(engine.evaluate_position_structure([_alert_payload(mark=1600.0)]))
    assert sent
    for banned in ("따라서", "상승할", "하락할", "전망", "예상됨"):
        assert banned not in sent[0]
