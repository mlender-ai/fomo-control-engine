"""WO-FCE-PAPER-OBSERVABILITY-01 — 침묵 금지 회귀 가드.

3트랙 이벤트 계약, 조용한 스킵의 관측화, skipped 스팸 억제, 스플릿 플래그 경고,
last_effective_run_at 괴리, 크립토 포맷 회귀 방지, 진단 표면을 검증한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from app.core.config import Settings
from app.db.migrations import run_migrations
from app.notify.bot.formatters import format_paper_event
from app.notify.paper_events import SUPPRESSIBLE_KINDS, suppression_key, track_event
from app.notify.state import NotificationState
from app.stock_paper.service import run_stock_paper_engine
from app.worker.heartbeat import HeartbeatRecord, SQLiteHeartbeatStore


def _db(tmp_path) -> str:
    path = tmp_path / "obs.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    connection.close()
    return f"sqlite:///{path}"


# ── 이벤트 계약 ────────────────────────────────────────────────


def test_track_event_conforms_to_common_contract() -> None:
    event = track_event("stock", "opened", "AAPL", detail={"market": "US"})
    assert set(event) >= {"track", "kind", "symbol", "ts", "detail"}
    assert event["track"] == "stock"
    assert event["kind"] == "opened"
    assert event["detail"] == {"market": "US"}
    # ts는 ISO8601 파싱 가능
    datetime.fromisoformat(event["ts"])


def test_stock_engine_disabled_emits_skipped_not_silence(tmp_path) -> None:
    settings = Settings(database_url=_db(tmp_path), stock_paper_engine_enabled=False)
    result = run_stock_paper_engine(settings, {})
    assert result["effective_run"] is False
    events = result["events"]
    assert len(events) == 1
    assert events[0]["kind"] == "skipped"
    assert events[0]["detail"]["reason"] == "disabled"
    assert events[0]["track"] == "stock"


def test_stock_engine_not_configured_emits_skipped(tmp_path) -> None:
    # 엔진은 켜졌지만 구동잡/자격증명 미구성 → 조용한 성공이 아니라 skipped.
    settings = Settings(database_url=_db(tmp_path), stock_paper_engine_enabled=True, toss_stock_scout_enabled=False)
    result = run_stock_paper_engine(settings, {})
    assert result["effective_run"] is False
    assert result["events"][0]["detail"]["reason"] == "toss_observation_not_configured"


def test_stock_rejections_are_aggregated_not_individual(tmp_path) -> None:
    settings = Settings(
        database_url=_db(tmp_path),
        stock_paper_engine_enabled=True,
        toss_stock_scout_enabled=True,
        toss_client_id="cid",
        toss_client_secret="secret",
    )
    now = datetime.now(timezone.utc).isoformat()
    candidate = {
        "market": "US",
        "symbol": "AAPL",
        "price": 225.0,
        "observed_at": now,
        "warning_badges": [],
        "signals": [{"type": "momentum", "tone": "candidate", "change_pct": 2.5}],
    }
    result = run_stock_paper_engine(
        settings,
        {"US": {"market_state": "open", "groups": {"momentum": [candidate]}}, "KR": {"market_state": "closed", "groups": {}}},
    )
    assert result["effective_run"] is True
    summaries = [event for event in result["events"] if event["kind"] == "rejected_summary"]
    # 거부가 있어도 개별 발송이 아니라 집계 1건.
    assert len(summaries) == 1
    assert summaries[0]["detail"]["rejected"] >= 1
    assert summaries[0]["detail"]["top_reject_gate"]


# ── skipped 스팸 억제 ──────────────────────────────────────────


def test_paper_skip_suppression_transition_and_daily_reminder() -> None:
    state = NotificationState()
    event = track_event("stock", "skipped", "*", detail={"reason": "disabled"})
    key = suppression_key(event)
    day1 = datetime(2026, 7, 24, 9, 0, tzinfo=timezone.utc)
    # 최초(상태 전이) → 발송
    assert state.register_paper_skip(key, now=day1) is True
    # 같은 날 같은 사유 연속 → 억제
    assert state.register_paper_skip(key, now=day1 + timedelta(minutes=1)) is False
    assert state.register_paper_skip(key, now=day1 + timedelta(minutes=2)) is False
    assert state.paper_skip_state[key]["suppressed"] == 2
    # 다음 날 → 일 1회 리마인더 발송
    day2 = day1 + timedelta(days=1)
    assert state.register_paper_skip(key, now=day2) is True
    assert state.paper_skip_state[key]["suppressed"] == 0


def test_effective_run_clears_skip_so_recovery_is_new_transition() -> None:
    state = NotificationState()
    event = track_event("stock", "skipped", "*", detail={"reason": "disabled"})
    key = suppression_key(event)
    assert state.register_paper_skip(key) is True
    assert state.register_paper_skip(key) is False
    state.clear_paper_skips_for_track("stock")
    # 회복 후 다시 스킵되면 새 상태 전이 → 발송.
    assert state.register_paper_skip(key) is True


def test_skip_state_survives_persistence(tmp_path) -> None:
    state = NotificationState()
    key = suppression_key(track_event("poly", "skipped", "*", detail={"reason": "disabled"}))
    state.register_paper_skip(key)
    path = str(tmp_path / "state.json")
    state.save(path)
    reloaded = NotificationState()
    reloaded.load(path)
    assert key in reloaded.paper_skip_state


def test_non_events_never_reach_telegram() -> None:
    """WO-FCE-ALERT-WHITELIST-02: 억제(빈도 제한) → **화이트리스트**(원천 제외)로 전환.

    빈도를 조이는 접근은 유니버스가 오염돼 최다 거부 게이트가 계속 뒤바뀌면 전이가 반복
    발생해 실패했다(2026-08-01 실측). 이제 "무엇이 안 일어났는가"는 발송 경로에 아예
    들어가지 않는다. 상세 계약은 tests/test_alert_whitelist.py.
    """
    from app.notify.paper_events import TELEGRAM_SENDABLE_KINDS, is_telegram_sendable

    assert TELEGRAM_SENDABLE_KINDS == frozenset({"opened", "closed"})
    for kind in ("skipped", "rejected_summary", "error"):
        assert is_telegram_sendable({"kind": kind}) is False
    for kind in ("opened", "closed"):
        assert is_telegram_sendable({"kind": kind}) is True
    # 억제 목록은 화이트리스트가 대체했으므로 비어 있다.
    assert SUPPRESSIBLE_KINDS == frozenset()


# ── 포맷터 (크립토 회귀 방지 + 트랙 렌더) ────────────────────────


def test_crypto_paper_event_format_unchanged() -> None:
    # 크립토 이벤트는 track 키가 없다 — 기존 경로를 그대로 타야 한다 (C3).
    crypto_event = {
        "kind": "opened",
        "reason": None,
        "trade": {"symbol": "BTCUSDT", "direction": "long", "entry_price": 50000.0, "entry_evidence": {"items": []}},
    }
    text = format_paper_event(crypto_event)
    assert "엔진 진입" in text
    assert "BTCUSDT" in text
    assert "롱" in text


def test_stock_track_event_renders_with_track_label() -> None:
    text = format_paper_event(track_event("stock", "opened", "AAPL", detail={"market": "US", "entry_mode": "strict_signal", "price": 225.0}))
    assert "주식 페이퍼" in text
    assert "AAPL" in text


def test_rejected_summary_renders_aggregate_line() -> None:
    text = format_paper_event(
        track_event(
            "stock",
            "rejected_summary",
            "*",
            detail={"evaluated": 42, "rejected": 42, "top_reject_gate": "evidence", "strict_entered": 0},
        )
    )
    assert "평가 42" in text
    assert "진입 0" in text
    assert "증거 부족" in text


def test_poly_skipped_renders_reason() -> None:
    text = format_paper_event(track_event("poly", "skipped", "*", detail={"reason": "disabled"}))
    assert "폴리 페이퍼" in text
    assert "엔진 비활성" in text


# ── last_effective_run_at ──────────────────────────────────────


def test_heartbeat_persists_last_effective_run_at(tmp_path) -> None:
    store = SQLiteHeartbeatStore(_db(tmp_path))
    moment = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
    record = HeartbeatRecord(job_name="toss_stock_scout", last_success_at=moment, last_effective_run_at=moment)
    store.upsert(record)
    listed = store.list()
    assert listed["toss_stock_scout"]["last_effective_run_at"] is not None


# ── 진단 표면 (작업 7) ─────────────────────────────────────────


def test_paper_diagnosis_endpoint_reports_three_tracks(client) -> None:
    response = client.get("/api/system/paper/diagnosis")
    assert response.status_code == 200
    payload = response.json()
    assert set(payload["tracks"]) == {"crypto", "stock", "poly"}
    for track in payload["tracks"].values():
        assert "enabled_flags" in track
        assert "last_effective_run_at" in track
        assert "telegram_wired" in track
        assert "mute_state" in track
        assert "ready_to_start_reason" in track
    assert "침묵" in payload["principle"]
