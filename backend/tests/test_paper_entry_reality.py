"""WO-FCE-PAPER-ENTRY-REALITY-01 — 거부 스팸 차단 + 진입 불능 수리 회귀.

핵심 명제:
1. 같은 최다거부 게이트가 반복되면 알림은 1건 이하 (D2 — 일 1,440건 스팸 차단)
2. 진입/청산은 즉시 발송 유지 (C3 회귀 금지)
3. 미장은 KR 장 마감과 무관하게 독립 판정 (D3)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.notify.paper_events import SUPPRESSIBLE_KINDS, suppression_key, track_event
from app.notify.state import NotificationState
from app.worker import liveness

NOW = datetime(2026, 7, 28, 2, 0, tzinfo=timezone.utc)


def _rejected(gate: str, rejected: int) -> dict:
    """폴리 트랙의 거부 집계 이벤트 — 건수는 매 틱 흔들리고 게이트는 그대로인 실제 패턴."""
    return track_event(
        "poly",
        "rejected_summary",
        "*",
        detail={"evaluated": 0, "rejected": rejected, "top_reject_gate": gate},
    )


# ── D2: 거부 스팸 차단 ──────────────────────────────────────────────


def test_rejected_summary_is_suppressible() -> None:
    assert "rejected_summary" in SUPPRESSIBLE_KINDS, "거부 집계가 억제 대상에서 빠지면 60초마다 스팸"


def test_reject_count_churn_does_not_create_new_state() -> None:
    """거부 건수(40→41→42)는 상태가 아니다 — 건수를 키에 넣으면 스팸이 그대로 유지된다."""
    assert suppression_key(_rejected("macro_base_rate_provider_unavailable", 40)) == suppression_key(_rejected("macro_base_rate_provider_unavailable", 44))


def test_same_top_gate_100_ticks_sends_at_most_once() -> None:
    """수용 기준: 동일 top_reject_gate 100틱 반복 → 발송 1건 이하.

    2026-07-28 실측 사고: 12:00~12:09 매 분 발송, 사유 100% 동일.
    """
    state = NotificationState()
    sent = 0
    for tick in range(100):
        event = _rejected("macro_base_rate_provider_unavailable", 40 + (tick % 5))
        if state.register_paper_skip(suppression_key(event), now=NOW + timedelta(minutes=tick)):
            sent += 1
    assert sent <= 1, f"100틱에 {sent}건 발송 — 스팸 차단 실패"


def test_top_gate_transition_sends_once() -> None:
    """게이트가 실제로 바뀌면 그때는 알린다 — 침묵이 아니라 전이 발송."""
    state = NotificationState()
    first = state.register_paper_skip(suppression_key(_rejected("macro_base_rate_provider_unavailable", 40)), now=NOW)
    repeat = state.register_paper_skip(suppression_key(_rejected("macro_base_rate_provider_unavailable", 41)), now=NOW)
    changed = state.register_paper_skip(suppression_key(_rejected("edge_below_threshold", 12)), now=NOW)
    assert (first, repeat, changed) == (True, False, True)


def test_effective_run_does_not_reset_reject_suppression() -> None:
    """평가가 정상일 때마다 거부 억제를 리셋하면 스팸이 재발한다(가장 빠지기 쉬운 함정)."""
    state = NotificationState()
    key = suppression_key(_rejected("macro_base_rate_provider_unavailable", 40))
    assert state.register_paper_skip(key, now=NOW) is True
    state.clear_paper_skips_for_track("poly")  # effective_run 경로
    assert state.register_paper_skip(key, now=NOW) is False, "거부 억제 상태가 리셋돼 재발송됨"


def test_skipped_suppression_still_resets_on_recovery() -> None:
    """반면 skipped(미발생)는 회복 시 리셋되어야 한다 — 기존 계약 회귀 금지."""
    state = NotificationState()
    skip = track_event("poly", "skipped", "*", detail={"reason": "collector_unavailable"})
    assert state.register_paper_skip(suppression_key(skip), now=NOW) is True
    state.clear_paper_skips_for_track("poly")
    assert state.register_paper_skip(suppression_key(skip), now=NOW) is True


def test_entry_and_exit_are_never_suppressed() -> None:
    """C3: 사용자가 받아야 할 것은 '무엇이 일어났는가' — 진입·청산은 항상 즉시 발송."""
    for kind in ("opened", "closed"):
        assert kind not in SUPPRESSIBLE_KINDS


# ── D3: 미장 독립 liveness ──────────────────────────────────────────


def test_us_track_is_monitored_independently_of_kr_session() -> None:
    """KR 장 마감(한국 밤) 시각에 US 정규장이 열려 있으면 US 트랙은 감시 대상이어야 한다.

    기존: toss_stock_scout 하나가 KR·US 를 함께 수집하는데 liveness 는 KR 로만 판정 →
    미국 장중 미장이 죽어도 market_closed 로 분류돼 경보가 구조적으로 불가능했다.
    """
    us_session = datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc)  # 11:00 ET(월) · 00:00 KST
    assert liveness.market_session_active("KR", us_session) is False
    assert liveness.market_session_active("US", us_session) is True

    us_tracks = [key for key, spec in liveness.MARKET_DATA_TRACKS.items() if spec.get("market") == "US"]
    assert us_tracks, "US 시장을 담당하는 감시 트랙이 없다"


def test_us_track_stale_fires_during_us_session(tmp_path) -> None:
    settings = Settings(database_url="memory://", worker_liveness_path=str(tmp_path / "liveness.json"))
    us_session = datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc)
    us_track = next(key for key, spec in liveness.MARKET_DATA_TRACKS.items() if spec.get("market") == "US")
    # 미국 장중인데 3시간째 관측 없음 = 미장 정지. 과거엔 KR 기준이라 market_closed 로 묻혔다.
    market_data = {us_track: (us_session - timedelta(hours=3)).isoformat()}
    rows = {row["job"]: row for row in liveness.track_liveness({}, settings, us_session, market_data)}
    assert rows[us_track]["stale"] is True
    assert rows["stock_kr"]["state"] == "market_closed"  # 같은 시각 KR 은 조용해야 한다
    alerts = liveness.evaluate_liveness({}, settings, us_session, market_data)
    assert any(c.identity == us_track for c in alerts if c.rule_id == "engine_liveness")


@pytest.mark.parametrize(
    "moment,expect_open",
    [
        (datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc), True),  # 11:00 ET 화
        (datetime(2026, 7, 28, 21, 0, tzinfo=timezone.utc), False),  # 17:00 ET 장 마감 후
        (datetime(2026, 7, 26, 15, 0, tzinfo=timezone.utc), False),  # 일요일
    ],
)
def test_us_regular_session_window(moment, expect_open) -> None:
    """정규장(09:30~16:00 ET)만. 프리·애프터는 범위 외."""
    assert liveness.market_session_active("US", moment) is expect_open
