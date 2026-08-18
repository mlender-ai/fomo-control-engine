"""WO-FCE-RISK-SIZING-01 Phase 3 — 재진입 잠금.

같은 확정봉 안에서 청산하고 곧바로 같은 가격·같은 방향으로 다시 들어간 건이 실측 9건
있었다. 새 판단이 아니라 왕복이다 — gross 우위가 **음수**(−0.914R)이면서 비용만 낸다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db.models import Direction
from app.paper.policy import PaperPolicy, reentry_locked

BAR = 14400.0  # 4h
T0 = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)

OFF = PaperPolicy()
SAME_BAR = PaperPolicy(reentry_lock_mode="same_bar")
THREE_BARS = PaperPolicy(reentry_lock_mode="bars", reentry_lock_bars=3)
BOTH_DIRECTIONS = PaperPolicy(reentry_lock_mode="same_bar", reentry_lock_same_direction_only=False)


def call(
    policy: PaperPolicy, *, entry: datetime, last_exit: datetime | None, direction: Direction = Direction.long, last_dir: Direction | None = Direction.long
) -> str | None:
    return reentry_locked(
        entry_bar_at=entry,
        direction=direction,
        last_exit_bar_at=last_exit,
        last_exit_direction=last_dir,
        bar_seconds=BAR,
        policy=policy,
    )


def test_default_is_previous_behaviour() -> None:
    """기본값은 잠금 없음이어야 한다 — 옵트인이 아니면 회귀다."""
    assert OFF.reentry_lock_mode == "off"
    assert call(OFF, entry=T0, last_exit=T0) is None


def test_same_bar_roundtrip_is_blocked() -> None:
    """실측 사례: HYPEUSDT 청산 08-14 12:00 → 진입 08-14 12:00 (간격 0s)."""
    assert call(SAME_BAR, entry=T0, last_exit=T0) == "reentry_lock:same_bar"


def test_next_bar_is_allowed() -> None:
    """1봉 뒤 재진입 3건은 gross 우위가 +1.608R 로 양수였다. 막으면 안 된다."""
    assert call(SAME_BAR, entry=T0 + timedelta(seconds=BAR), last_exit=T0) is None


def test_first_ever_entry_is_never_locked() -> None:
    assert call(SAME_BAR, entry=T0, last_exit=None, last_dir=None) is None


def test_opposite_direction_is_allowed_by_default() -> None:
    """청산 직후 반대 방향은 왕복이 아니라 전환이다. 기본은 막지 않는다."""
    assert call(SAME_BAR, entry=T0, last_exit=T0, direction=Direction.short, last_dir=Direction.long) is None


def test_opposite_direction_is_blocked_when_configured() -> None:
    assert call(BOTH_DIRECTIONS, entry=T0, last_exit=T0, direction=Direction.short, last_dir=Direction.long) == "reentry_lock:same_bar"


@pytest.mark.parametrize("bars_later", [0, 1, 2, 3])
def test_bars_mode_blocks_within_window(bars_later: int) -> None:
    entry = T0 + timedelta(seconds=BAR * bars_later)
    assert call(THREE_BARS, entry=entry, last_exit=T0) == "reentry_lock:3bars"


def test_bars_mode_allows_beyond_window() -> None:
    assert call(THREE_BARS, entry=T0 + timedelta(seconds=BAR * 4), last_exit=T0) is None


def test_adopted_config_is_same_bar_same_direction() -> None:
    """채택값 — 더 긴 잠금은 우위가 양수인 거래까지 버려 오히려 나빠진다."""
    payload = json.loads(Path("app/paper/params/crypto-v2.json").read_text(encoding="utf-8"))
    assert payload["reentry_lock_mode"] == "same_bar"
    assert payload["reentry_lock_same_direction_only"] is True


def test_quality_gate_thresholds_are_untouched() -> None:
    """C1 — 잠금은 품질 게이트가 아니라 표본 독립성 게이트다. 임계 값은 그대로."""
    assert SAME_BAR.min_rr == OFF.min_rr
    assert SAME_BAR.min_evidence == OFF.min_evidence
    assert SAME_BAR.min_checklist_passed == OFF.min_checklist_passed


def test_sizing_and_exit_params_are_untouched() -> None:
    """C2·C3 — 출구 사다리와 Phase 1 사이징을 건드리지 않는다."""
    assert SAME_BAR.take_profit_atr_k1 == OFF.take_profit_atr_k1
    assert SAME_BAR.take_profit_atr_k2 == OFF.take_profit_atr_k2
    assert SAME_BAR.risk_budget_usdt == OFF.risk_budget_usdt
    assert SAME_BAR.max_notional_usdt == OFF.max_notional_usdt


# ---------------------------------------------------------------------------
# Phase 3-4 — 순 기준 RR (병행 기록 · 게이트 전환은 사용자 결정)
# ---------------------------------------------------------------------------


def test_rr_basis_defaults_to_gross() -> None:
    """순 기준으로 전환하면 진입이 83% 줄어든다. 기본값은 기존 동작이어야 한다."""
    assert OFF.rr_basis == "gross"


def test_adopted_config_keeps_gross_gate() -> None:
    payload = json.loads(Path("app/paper/params/crypto-v2.json").read_text(encoding="utf-8"))
    assert payload["rr_basis"] == "gross"


def test_min_rr_threshold_is_untouched() -> None:
    """C1 — 3-4 는 임계가 아니라 측정 대상을 바꾸는 항목이다."""
    assert PaperPolicy(rr_basis="net").min_rr == OFF.min_rr == 1.5
