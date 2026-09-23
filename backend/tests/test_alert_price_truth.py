"""WO-FCE-ALERT-TRUTH-01 — 알림이 말하는 숫자가 실제 숫자인가.

실사건 (2026-09-21 15:44 · MARSCOINUSDT 롱 10.0x @ 0.133510):

    🟢 MARSCOINUSDT 롱 10.0x — 익절1 도달
    익절1 0.136000에 도달했습니다. 현재 0.138190 · 목표 대비 +1.61%
    건강도 60 · PnL -2.25% · 15:41 기준

한 메시지 안에서 두 숫자가 모순이다. "현재 0.138190"은 진입가 대비 +3.5% 인데 PnL 은
−2.25%(10배 레버리지 → 가격 −0.225% → 실제 마크 ≈ 0.13321)다. **익절1 0.136 에 닿은 적이
없다.** 앞 숫자는 확정 4시간봉 종가(최대 4시간 묵은 값)였고 뒤 숫자는 거래소 스냅샷이었다.

고정하는 명제:

1. 익절 도달·트리거 근접은 **현재가**로만 판정한다 — 묵은 종가로 "도달"을 외치지 않는다
2. 무효화 이탈은 **종가 이탈**이 규칙이므로 확정봉을 쓰되 "현재"라고 부르지 않는다
3. 재무장은 발화와 **같은 가격**으로 잰다 — 두 시계가 엇갈리면 알림이 멈추거나 반복된다
4. 무효화는 **가장 강한** 레벨이 아니라 **가장 가까운 유효** 레벨이다
5. 계획이 뒤집혔으면(R:R < 1) 알림이 그 사실을 말한다
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.core.config import Settings
from app.notify import rules
from app.notify.lifecycle import _risk_reward_sentence
from app.positions.chart_analysis import (
    MIN_INVALIDATION_DISTANCE_PCT,
    PositionContext,
    _invalidation_levels,
    _select_invalidation_level,
)
from app.structure.levels.engine import StructureLevel


NOW = datetime(2026, 9, 21, 15, 44, tzinfo=timezone.utc)

# 실사건의 숫자 그대로.
ENTRY = 0.133510
TAKE_PROFIT = 0.136000
STALE_CLOSE = 0.138190  # 확정 4시간봉 종가 — 목표 위
LIVE_MARK = 0.133210  # PnL −2.25% (10x) 에서 역산한 실제 마크 — 목표 아래


def _payload(*, mark: float | None, close: float | None, invalidation: float = 0.097105) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "position": {
            "id": "11111111-1111-1111-1111-111111111111",
            "symbol": "MARSCOINUSDT",
            "direction": "long",
            "leverage": 10,
            "entry_price": ENTRY,
        },
        "state": {
            "health_score": 60,
            "pnl_percent": -2.25,
            "as_of": (NOW - timedelta(minutes=3)).isoformat(),
        },
        "action_plan": {
            "invalidation": {"price": invalidation, "label": "무효화"},
            "take_profit": [{"price": TAKE_PROFIT, "label": "익절1", "action": "부분 익절 검토"}],
        },
    }
    if mark is not None:
        payload["state"]["mark_price"] = mark
    if close is not None:
        payload["chart_analysis"] = {"candles": [{"time": (NOW - timedelta(hours=1)).isoformat(), "close": close}]}
    return payload


# ── 1. 익절 도달은 현재가로만 판정한다 ──────────────────────────────────


def test_take_profit_does_not_fire_when_only_the_stale_close_is_above_target() -> None:
    """실사건 재현 — 확정봉 종가는 목표 위, 실제 마크는 목표 아래.

    이 테스트가 실패하면 사용자는 다시 **닿은 적 없는 익절 도달** 알림을 받는다.
    """
    candidates = rules._take_profit_candidates(_payload(mark=LIVE_MARK, close=STALE_CLOSE))
    assert candidates == [], "묵은 종가로 익절 도달을 판정했다"


def test_take_profit_fires_on_the_live_mark() -> None:
    candidates = rules._take_profit_candidates(_payload(mark=0.1365, close=0.1300))
    assert len(candidates) == 1
    assert "현재가 0.136500" in candidates[0].message
    assert candidates[0].payload["current_price"] == pytest.approx(0.1365)
    assert candidates[0].payload["number_sources"]


def test_take_profit_is_silent_without_a_live_mark() -> None:
    """현재가가 없으면 **판정하지 않는다.**

    없는 것을 마지막 종가로 대신하는 것이 이 결함의 시작이었다. 묵은 값으로 행동을
    지시하느니 말하지 않는 쪽이 낫다.
    """
    assert rules._take_profit_candidates(_payload(mark=None, close=STALE_CLOSE)) == []


def test_alert_never_mixes_two_clocks_in_one_message() -> None:
    """가격에는 출처 딱지가 붙고, 손익 줄에는 그 줄의 시각이 붙는다."""
    message = rules._take_profit_candidates(_payload(mark=0.1365, close=0.1300))[0].message
    assert "현재가" in message
    assert "PnL -2.25%" in message
    # 출처 없는 맨 "현재 <숫자>" 는 남아 있으면 안 된다 — 그것이 모순을 숨긴 표현이다.
    assert "현재 0.13" not in message


# ── 2. 무효화 이탈은 종가 규칙을 유지한다 ───────────────────────────────


def test_invalidation_breach_still_uses_the_confirmed_close_but_labels_it() -> None:
    """종가 이탈은 꼬리에 털리지 않으려는 **설계**다 — 출처를 바꾸지 않는다.

    다만 그 값을 "현재"라고 부르지 않는다. 최대 한 봉만큼 묵은 값이다.
    """
    payload = _payload(mark=0.1200, close=0.0900, invalidation=0.0971)
    candidates = rules._invalidation_candidates(payload)
    assert len(candidates) == 1
    assert "확정봉 종가 0.090000" in candidates[0].message
    assert "종가 이탈했습니다" in candidates[0].message


def test_invalidation_breach_does_not_fire_on_an_intrabar_mark_alone() -> None:
    """마크가 무효화선 아래로 내려가도 봉이 닫히기 전에는 이탈이 아니다."""
    payload = _payload(mark=0.0900, close=0.1200, invalidation=0.0971)
    assert rules._invalidation_candidates(payload) == []


# ── 3. 재무장은 발화와 같은 가격으로 잰다 ───────────────────────────────


def test_rearm_uses_the_same_price_basis_as_the_rule_that_fires() -> None:
    """익절을 현재가로 켜고 종가로 끄면 두 시계가 엇갈린다.

    실사건 상태(마크는 목표 아래, 종가는 목표 위)에서 익절 재무장이 **켜져 있어야** 한다 —
    도달하지 않았으므로. 종가로 재면 꺼진 채 굳는다.
    """
    settings = Settings()
    signals = rules.rearm_signals(_payload(mark=LIVE_MARK, close=STALE_CLOSE), settings)
    take_profit = [value for key, value in signals.items() if key.startswith("take_profit_hit:")]
    assert take_profit == [True], "익절이 도달하지 않았는데 재무장이 꺼져 있다"

    breach = [value for key, value in signals.items() if key.startswith("invalidation_breach:")]
    assert breach == [True], "종가가 무효화선 위인데 이탈 상태로 굳었다"


# ── 4. 무효화는 가장 가까운 유효 레벨 ───────────────────────────────────


def _level(price: float, score: int, *, kind: str = "support") -> StructureLevel:
    return StructureLevel(price=price, score=score, touches=2, last_touch_at=NOW, kind=kind, sources=["swing"])


def _context(**overrides: Any) -> PositionContext:
    base = {"direction": "long", "entry_price": ENTRY, "mark_price": ENTRY}
    base.update(overrides)
    return PositionContext(**base)


def test_invalidation_picks_the_nearest_qualified_level_not_the_strongest() -> None:
    """실사건의 반대편 — 27% 짜리 최고점수 지지 대신 가까운 유효 레벨을 고른다."""
    candidates = [_level(0.097105, 95), _level(0.130500, 60), _level(0.125000, 45)]
    level, selection = _select_invalidation_level(candidates, _context())
    assert level is not None
    assert level.price == pytest.approx(0.130500)
    assert selection == "nearest_qualified"


def test_invalidation_does_not_lower_the_score_threshold() -> None:
    """가까운 것을 고르되 **품질 문턱은 그대로다.** 39점짜리는 여전히 후보가 아니다."""
    candidates = [_level(0.132000, 39), _level(0.097105, 95)]
    level, selection = _select_invalidation_level(candidates, _context())
    assert level is not None and level.price == pytest.approx(0.097105)
    assert selection == "nearest_qualified"


def test_invalidation_skips_levels_inside_the_noise_band() -> None:
    near = ENTRY * (1 - MIN_INVALIDATION_DISTANCE_PCT / 200.0)  # 문턱의 절반 거리
    candidates = [_level(near, 90), _level(0.130500, 50)]
    level, _selection = _select_invalidation_level(candidates, _context())
    assert level is not None and level.price == pytest.approx(0.130500)


def test_invalidation_reports_insufficient_structure_rather_than_using_a_far_level() -> None:
    """전부 노이즈 범위 안이면 **쓸 수 있는 구조가 없다**고 말한다.

    27% 짜리를 조용히 손절로 쓰는 것보다 정직하다.
    """
    near = ENTRY * (1 - MIN_INVALIDATION_DISTANCE_PCT / 400.0)
    level, selection = _select_invalidation_level([_level(near, 90)], _context())
    assert level is None
    assert selection == "all_levels_within_noise_band"

    payload = _invalidation_levels(_context(), [_level(near, 90)], [])
    assert payload[0]["price"] is None
    assert payload[0]["source"] == "insufficient_structure"


def test_invalidation_records_why_that_level_was_chosen() -> None:
    """사후에 "왜 손절이 저기냐"를 답할 수 있어야 한다."""
    payload = _invalidation_levels(_context(), [_level(0.097105, 95), _level(0.130500, 60)], [])
    assert payload[0]["selection"] == "nearest_qualified"
    assert payload[0]["distance_pct"] == pytest.approx(2.25, abs=0.05)


def test_short_position_uses_resistance_above_the_mark() -> None:
    levels = [_level(0.180000, 95, kind="resistance"), _level(0.137000, 60, kind="resistance")]
    payload = _invalidation_levels(_context(direction="short"), [], levels)
    assert payload[0]["price"] == pytest.approx(0.137000)


# ── 5. 뒤집힌 계획은 조용히 나가지 않는다 ───────────────────────────────


def test_entry_alert_states_the_risk_reward_and_warns_when_inverted() -> None:
    sentence = _risk_reward_sentence(
        {"entry_price": ENTRY},
        {"invalidation": {"price": 0.097105}, "take_profit": [{"price": TAKE_PROFIT}]},
    )
    assert "R:R 0.07" in sentence
    assert sentence.startswith("⚠️"), "1.87% 먹자고 27% 거는 계획이 경고 없이 나갔다"


def test_entry_alert_states_the_risk_reward_without_warning_when_sound() -> None:
    sentence = _risk_reward_sentence(
        {"entry_price": 100.0},
        {"invalidation": {"price": 98.0}, "take_profit": [{"price": 106.0}]},
    )
    assert "R:R 3.00" in sentence
    assert not sentence.startswith("⚠️")


def test_entry_alert_says_nothing_rather_than_guessing_a_ratio() -> None:
    assert _risk_reward_sentence({"entry_price": ENTRY}, {"take_profit": [{"price": TAKE_PROFIT}]}) == ""
    assert _risk_reward_sentence({}, {"invalidation": {"price": 0.09}, "take_profit": [{"price": 0.14}]}) == ""
