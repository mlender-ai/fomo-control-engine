"""WO-FCE-VALIDATION-VERDICT-01 Phase 4 — 자동매매 전환 게이트 회귀.

배경: 최종 목표는 4주 검증 후 자동매매인데 **전환 조건이 한 번도 문서로 확정된 적이 없었다.**
결과를 보고 조건을 정하면 "이 정도면 됐다"가 조건이 된다.

고정하는 명제:
1. 게이트는 판정만 한다 — 어떤 봉인도 풀지 않는다 (C5)
2. 사용자 서명(`GATE_APPROVED`) 없이는 어떤 트랙도 준비 완료가 될 수 없다
3. 성과 우위는 **CI 비겹침**으로만 인정한다 — 승률 단독 수치 금지
4. 벤치마크가 미배선인 트랙은 미충족으로 남는다 (모르는 것을 충족으로 처리하지 않는다)
5. 트랙별 독립 판정 — 한 트랙 통과가 다른 트랙을 해제하지 않는다 (C4)
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.config import Settings
from app.db.migrations import run_migrations
from app.validation import live_trading_gate as gate

NOW = datetime(2026, 8, 6, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "gate.db")
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    yield connection
    connection.close()


def _closed_trade(connection: sqlite3.Connection, trade_id: str, *, pnl: float, **payload_extra) -> None:
    payload = {"net_pnl_usdt": pnl, "exit_reason": "invalidation_breach", "holding_bars": 5, **payload_extra}
    connection.execute(
        "INSERT INTO paper_trades (id, symbol, timeframe, status, entry_bar_at, exit_at, updated_at, payload) VALUES (?,?,?,?,?,?,?,?)",
        (trade_id, "BTCUSDT", "4h", "closed", "2026-08-01T00:00:00+00:00", "2026-08-02T00:00:00+00:00", "2026-08-02T00:00:00+00:00", json.dumps(payload)),
    )
    connection.commit()


def _user_trade(connection: sqlite3.Connection, trade_id: str, *, pnl: float) -> None:
    connection.execute(
        "INSERT INTO user_trades (id, symbol, direction, entry_at, exit_at, updated_at, payload) VALUES (?,?,?,?,?,?,?)",
        (trade_id, "BTCUSDT", "long", "2026-08-01T00:00:00+00:00", "2026-08-02T00:00:00+00:00", "2026-08-02T00:00:00+00:00", json.dumps({"net_pnl_usdt": pnl})),
    )
    connection.commit()


def _coverage(connection: sqlite3.Connection, track: str, days: list[str], *, coverage_pct: float = 100.0, valid: int = 1) -> None:
    connection.executemany(
        """INSERT INTO observation_coverage (day, track, coverage_pct, valid, trading_day, computed_at)
        VALUES (?, ?, ?, ?, 1, '2026-08-06T00:00:00+00:00')""",
        [(day, track, coverage_pct, valid) for day in days],
    )
    connection.commit()


# ── 1. 봉인 불변 (C5) ───────────────────────────────────────────────────


def test_readiness_never_unseals_anything(conn) -> None:
    """판정 함수가 설정을 건드리면 그건 판정이 아니라 해제다."""
    before = Settings().stock_live_trading_enabled

    result = gate.live_trading_readiness(conn, "crypto", now=NOW)

    assert Settings().stock_live_trading_enabled == before is False
    assert "해제하지 않는다" in result["seal"]


def test_live_broker_remains_a_contract_without_an_implementation() -> None:
    """`LiveBroker` 는 Protocol(타입 계약)이며 구현체가 없어야 한다.

    구현이 생겼다는 신호는 "클래스가 존재한다"가 아니라 **Protocol 을 실제로 만족하는
    구체 클래스가 생겼다**는 것이다. `PaperBroker` 는 페이퍼 전용이므로 대상이 아니다.
    """
    from app.stock_paper import broker as broker_module

    concrete = [
        name
        for name, obj in vars(broker_module).items()
        if isinstance(obj, type) and name not in {"Broker", "PaperBroker", "LiveBroker"} and issubclass(obj, broker_module.Broker)
    ]

    assert getattr(broker_module.LiveBroker, "_is_protocol", False) is True, "LiveBroker 가 Protocol 이 아니게 됐다 — 구현이 생겼다는 뜻이다"
    assert concrete == [], f"새 브로커 구현체가 생겼다: {concrete}"


def test_create_broker_refuses_to_build_a_live_broker() -> None:
    """봉인의 실체는 이 예외다 — 라이브 요청 시 브로커 생성 자체가 실패해야 한다."""
    from app.stock_paper.broker import create_broker
    from app.stock_paper.store import StockPaperStore

    with pytest.raises(RuntimeError, match="sealed"):
        create_broker(live_trading_enabled=True, store=StockPaperStore(""))


def test_enabling_live_trading_still_fails_startup(monkeypatch) -> None:
    monkeypatch.setenv("FCE_STOCK_LIVE_TRADING_ENABLED", "true")
    with pytest.raises(ValueError, match="sealed"):
        Settings()


# ── 2. 인적 승인이 최종 조건 ────────────────────────────────────────────


def test_gate_is_not_approved_by_default() -> None:
    """서명 전 승인 플래그가 True 면 게이트가 무의미하다."""
    assert gate.GATE_APPROVED is False


def test_ready_is_false_without_human_approval(conn, monkeypatch) -> None:
    """측정 축을 전부 통과해도 사용자 서명 없이는 준비 완료가 아니다(비대칭 자율)."""
    monkeypatch.setattr(gate, "GATE_APPROVED", False)

    result = gate.live_trading_readiness(conn, "crypto", now=NOW)

    assert result["ready"] is False
    assert "인적 승인" in result["unmet"]


# ── 3. 성과 우위는 CI 비겹침으로만 ──────────────────────────────────────


def test_edge_requires_non_overlapping_ci() -> None:
    """승률이 높아도 CI 가 겹치면 '판별 불가'다 — 운을 실력으로 오인하지 않는다."""
    engine = [True] * 7 + [False] * 3  # 70%
    benchmark = [True] * 6 + [False] * 4  # 60%

    verdict = gate.edge_verdict(engine, benchmark)

    assert verdict["met"] is False
    assert "겹침" in verdict["verdict"]


def test_clear_separation_is_recognised_as_superiority() -> None:
    engine = [True] * 95 + [False] * 5
    benchmark = [True] * 10 + [False] * 90

    verdict = gate.edge_verdict(engine, benchmark)

    assert verdict["met"] is True
    assert "우위" in verdict["verdict"]
    assert verdict["engine_ci"][0] > verdict["benchmark_ci"][1]


def test_empty_benchmark_is_insufficient_not_superior() -> None:
    verdict = gate.edge_verdict([True] * 40, [])

    assert verdict["met"] is False
    assert verdict["verdict"] == "표본 부족"


def test_crypto_edge_axis_reads_engine_versus_user(conn) -> None:
    for index in range(12):
        _closed_trade(conn, f"paper-{index}", pnl=1.0 if index < 9 else -1.0)
    for index in range(12):
        _user_trade(conn, f"user-{index}", pnl=1.0 if index < 4 else -1.0)

    axis = next(item for item in gate.live_trading_readiness(conn, "crypto", now=NOW)["axes"] if item["axis"] == gate.AXIS_EDGE)

    assert axis["available"] is True
    assert "엔진 N=12" in axis["current"]


def test_unwired_benchmark_stays_unmet(conn) -> None:
    """알 수 없는 것을 충족으로 처리하면 게이트가 무의미해진다."""
    axis = next(item for item in gate.live_trading_readiness(conn, "stock_kr", now=NOW)["axes"] if item["axis"] == gate.AXIS_EDGE)

    assert (axis["met"], axis["available"]) == (False, False)
    assert "미배선" in axis["detail"]


# ── 4. 패인 분포 건전성 ─────────────────────────────────────────────────


def test_judgment_fault_share_is_measured_from_the_ledger(conn) -> None:
    """진입 직후 역행(holding_bars<=1)은 판단 결함(entry_too_late)으로 분류된다."""
    for index in range(3):
        _closed_trade(conn, f"late-{index}", pnl=-1.0, holding_bars=1)
    for index in range(7):
        _closed_trade(conn, f"fine-{index}", pnl=-1.0, holding_bars=10)

    health = gate.loss_health(conn)

    assert health["total"] == 10
    assert health["judgment_fault_share_pct"] == 30.0
    assert health["met"] is True


def test_judgment_fault_over_ceiling_fails_the_axis(conn) -> None:
    for index in range(6):
        _closed_trade(conn, f"late-{index}", pnl=-1.0, holding_bars=1)
    for index in range(4):
        _closed_trade(conn, f"fine-{index}", pnl=-1.0, holding_bars=10)

    health = gate.loss_health(conn)

    assert health["judgment_fault_share_pct"] == 60.0
    assert health["met"] is False
    assert "상한" in health["reason"]


def test_zero_classified_losses_is_not_treated_as_healthy(conn) -> None:
    """분류 대상 손실이 없는 것과 건전한 것은 다르다."""
    health = gate.loss_health(conn)

    assert health["total"] == 0
    assert health["met"] is False
    assert "판정 불가" in health["reason"]


# ── 5. 운영 안정성 ──────────────────────────────────────────────────────


def test_stability_axis_passes_on_a_clean_window(conn) -> None:
    days = [(NOW.date() - timedelta(days=offset)).isoformat() for offset in range(1, 11)]
    _coverage(conn, "crypto", days, coverage_pct=99.0, valid=1)

    result = gate.stability(conn, "crypto", now=NOW)

    assert result["met"] is True
    assert result["lost_days"] == 0


def test_stability_axis_fails_when_lost_days_pile_up(conn) -> None:
    """관측이 끊기면 판단 근거가 끊긴 것이다 — 호스트 문제 재발은 게이트를 막는다."""
    good = [(NOW.date() - timedelta(days=offset)).isoformat() for offset in range(1, 8)]
    bad = [(NOW.date() - timedelta(days=offset)).isoformat() for offset in range(8, 11)]
    _coverage(conn, "crypto", good, coverage_pct=99.0, valid=1)
    _coverage(conn, "crypto", bad, coverage_pct=40.0, valid=0)

    result = gate.stability(conn, "crypto", now=NOW)

    assert result["met"] is False
    assert result["lost_days"] == 3


def test_stability_without_records_is_unmet(conn) -> None:
    assert gate.stability(conn, "crypto", now=NOW)["met"] is False


# ── 6. 트랙 독립 (C4) ───────────────────────────────────────────────────


def test_progress_counts_measured_axes_only(conn) -> None:
    """정책 선언(트랙 독립)을 분모에 넣으면 진행도가 공짜로 올라간다."""
    result = gate.live_trading_readiness(conn, "crypto", now=NOW)

    assert result["measured_total"] == len(gate.MEASURED_AXES) == 4
    policy = next(item for item in result["axes"] if item["axis"] == gate.AXIS_TRACK_SCOPE)
    assert policy["kind"] == "policy"


def test_report_judges_every_track_independently(conn) -> None:
    days = [(NOW.date() - timedelta(days=offset)).isoformat() for offset in range(1, 11)]
    _coverage(conn, "crypto", days, coverage_pct=99.0, valid=1)

    report = gate.live_trading_gate_report(conn, now=NOW)

    assert set(report["tracks"]) == {"crypto", "stock_kr", "stock_us", "poly"}
    assert report["ready_tracks"] == []
    # 크립토가 한 축을 통과해도 다른 트랙의 진행도는 그대로다.
    assert report["tracks"]["poly"]["measured_met"] == 0


def test_every_axis_from_the_document_is_present(conn) -> None:
    axes = {item["axis"] for item in gate.live_trading_readiness(conn, "crypto", now=NOW)["axes"]}

    assert axes == {gate.AXIS_VALIDATION, gate.AXIS_EDGE, gate.AXIS_LOSS_HEALTH, gate.AXIS_STABILITY, gate.AXIS_TRACK_SCOPE, gate.AXIS_HUMAN}


def test_gate_document_exists_and_has_a_signature_block() -> None:
    """서명란 없는 게이트 문서는 승인 근거가 되지 않는다."""
    document = Path(__file__).resolve().parents[2] / "docs" / "validation" / "LIVE_TRADING_GATE.md"

    text = document.read_text(encoding="utf-8")

    assert "사용자 승인 서명란" in text
    assert "킬 스위치" in text
    for axis_label in ("검증 완료", "성과 우위", "패인 분포 건전성", "운영 안정성", "트랙 독립", "인적 승인"):
        assert axis_label in text, f"게이트 문서에 축 '{axis_label}' 이 없다"


def test_thresholds_match_the_document_proposal() -> None:
    """코드와 문서가 갈라지면 어느 쪽이 기준인지 알 수 없게 된다."""
    document = (Path(__file__).resolve().parents[2] / "docs" / "validation" / "LIVE_TRADING_GATE.md").read_text(encoding="utf-8")

    assert gate.MAX_JUDGMENT_FAULT_SHARE_PCT == 40.0 and "40%" in document
    assert gate.MIN_STABILITY_COVERAGE_PCT == 95.0 and "95%" in document
    assert gate.STABILITY_WINDOW_DAYS == 14 and "14일" in document
    assert gate.MAX_STABILITY_LOST_DAYS == 1
