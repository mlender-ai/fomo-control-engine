"""WO-FCE-ALERT-WHITELIST-02 회귀 가드.

C1: **거부는 텔레그램 금지 — 예외 없음.** 개별·집계·전이 어떤 형태로도 발송하지 않는다.
선행 WO들이 빈도를 계속 조였지만 오염된 입력 앞에서 반복 실패했으므로, 이제 발송 경로에서
원천 제외한다. 이 파일의 첫 테스트가 그 증명이다.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.notify.paper_events import (
    SUPPRESSIBLE_KINDS,
    TELEGRAM_SENDABLE_KINDS,
    is_telegram_sendable,
    track_event,
)
from app.poly_paper.service import (
    CAPACITY_REASONS,
    OUT_OF_SCOPE_REASONS,
    UNIVERSE_EXIT_EXPIRED,
    UNIVERSE_EXIT_REASONS,
    _apply_market_gates,
    classify_exclusion,
)


# ══════════════════════════════════════════════════════════════
# 작업 1 — 화이트리스트 (C1 · 예외 없음)
# ══════════════════════════════════════════════════════════════


def test_whitelist_allows_only_opened_and_closed() -> None:
    assert TELEGRAM_SENDABLE_KINDS == frozenset({"opened", "closed"})


@pytest.mark.parametrize("kind", ["rejected_summary", "skipped", "error", "gate_diagnostic", "unknown"])
def test_non_whitelisted_kinds_never_send(kind: str) -> None:
    """거부·미발생·오류는 어떤 형태로도 발송되지 않는다."""
    assert is_telegram_sendable(track_event("poly", kind, "*", detail={"reason": "x"})) is False


@pytest.mark.parametrize("kind", ["opened", "closed"])
def test_whitelisted_kinds_send(kind: str) -> None:
    assert is_telegram_sendable(track_event("stock", kind, "AAPL", detail={})) is True


def test_hundred_rejections_produce_zero_telegram_sends() -> None:
    """수용 기준: 거부 100회 발생 시 텔레그램 0건."""
    events = [track_event("poly", "rejected_summary", "*", detail={"rejected": index, "top_reject_gate": f"gate_{index % 7}"}) for index in range(100)]
    assert sum(1 for event in events if is_telegram_sendable(event)) == 0


def test_changing_top_gate_still_produces_zero_sends() -> None:
    """최다 거부 게이트가 매번 바뀌어도(=전이 반복) 0건이어야 한다.

    이것이 억제 방식으로는 못 막던 실제 스팸 패턴이다(2026-08-01 실측).
    """
    gates = ["unsupported_crypto_question", "마켓 한도", "resolution_time_invalid"] * 40
    events = [track_event("poly", "rejected_summary", "*", detail={"top_reject_gate": gate}) for gate in gates]
    assert sum(1 for event in events if is_telegram_sendable(event)) == 0


def test_suppressible_kinds_no_longer_gate_rejections() -> None:
    """억제 목록은 비었다 — 화이트리스트가 대체했으므로 억제로 새는 경로가 없다."""
    assert SUPPRESSIBLE_KINDS == frozenset()


@pytest.mark.asyncio
async def test_worker_send_path_drops_rejections(tmp_path) -> None:
    """워커 실제 발송 경로에서 거부가 0건임을 end-to-end로 확인한다."""
    from app.core.config import Settings
    from app.worker.manager import WorkerManager

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'w.db'}",
        telegram_alerts_enabled=True,
        paper_telegram_alerts_enabled=True,
        background_worker_enabled=False,
    )
    manager = WorkerManager(settings)
    sent: list[str] = []

    async def _capture(text: str) -> int:
        sent.append(text)
        return 1

    setattr(manager.sender, "send_to_all", _capture)
    result = {
        "effective_run": True,
        "events": [track_event("poly", "rejected_summary", "*", detail={"rejected": 73, "top_reject_gate": "마켓 한도"}) for _ in range(100)]
        + [track_event("stock", "skipped", "*", detail={"reason": "disabled"})]
        + [track_event("stock", "opened", "AAPL", detail={"market": "US"})],
    }
    delivered = await manager._send_paper_events(result)

    assert delivered == 1, "opened 1건만 도달해야 한다"
    assert len(sent) == 1
    assert "주식 페이퍼" in sent[0]
    assert not any("평가 요약" in text or "미실행" in text for text in sent)


# ══════════════════════════════════════════════════════════════
# 작업 2 — 폴리 유니버스 위생 (C3 분류)
# ══════════════════════════════════════════════════════════════


def _market(**overrides):
    from app.poly_paper.models import PolyMarket

    base = {
        "id": "0xmkt",
        "question": "Will BTC exceed $100k?",
        "slug": "btc-100k",
        "category": "crypto",
        "liquidity": 50_000.0,
        "active": True,
        "closed": False,
        "end_at": datetime(2026, 12, 31, tzinfo=timezone.utc),
        "yes_token_id": "y",
        "no_token_id": "n",
        "yes_price": 0.5,
        "no_price": 0.5,
        "taker_fee_rate": 0.0,
        "resolution_source": "official",
        "trade_eligible": True,
        "exclusion_reason": None,
        "observed_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "description": "BTC price question",
    }
    base.update(overrides)
    fields = {key: base[key] for key in base if key in PolyMarket.__dataclass_fields__}
    return PolyMarket(**fields)


def test_expired_market_leaves_universe() -> None:
    """만료 시장은 유니버스 이탈이다 — 거부가 아니다."""
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    expired = _market(end_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    gated = _apply_market_gates(expired, now=now)
    assert gated.trade_eligible is False
    assert gated.exclusion_reason == UNIVERSE_EXIT_EXPIRED
    assert classify_exclusion(gated.exclusion_reason) == "universe_exit"


def test_closed_market_leaves_universe() -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    gated = _apply_market_gates(_market(closed=True), now=now)
    assert gated.exclusion_reason == UNIVERSE_EXIT_EXPIRED


def test_live_market_stays_eligible() -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    gated = _apply_market_gates(_market(), now=now)
    assert gated.trade_eligible is True
    assert gated.exclusion_reason is None


def test_capacity_is_not_a_rejection() -> None:
    """한도 도달은 설계된 정상 동작 — 거부 지표에서 분리된다."""
    for reason in CAPACITY_REASONS:
        assert classify_exclusion(reason) == "capacity_full"


def test_out_of_scope_is_not_a_rejection() -> None:
    for reason in OUT_OF_SCOPE_REASONS:
        assert classify_exclusion(reason) == "out_of_scope"


def test_universe_exit_reasons_classified() -> None:
    for reason in UNIVERSE_EXIT_REASONS:
        assert classify_exclusion(reason) == "universe_exit"


def test_true_rejection_stays_rejection() -> None:
    """실제 판정 미달만 거부다."""
    assert classify_exclusion("after_cost_edge_low") == "rejected"
    assert classify_exclusion("liquidity_below_minimum") == "rejected"


# ══════════════════════════════════════════════════════════════
# 작업 3 — 주식 커버리지 단계별 차단 카운터
# ══════════════════════════════════════════════════════════════


def test_coverage_blocks_reported_in_payload(tmp_path) -> None:
    """진입 0의 원인을 추측이 아니라 카운터로 특정할 수 있어야 한다."""
    import sqlite3

    from app.core.config import Settings
    from app.db.migrations import run_migrations
    from app.stock_paper.service import run_stock_paper_engine

    path = tmp_path / "cov.db"
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    run_migrations(connection)
    connection.close()
    settings = Settings(
        database_url=f"sqlite:///{path}",
        stock_paper_engine_enabled=True,
        toss_stock_scout_enabled=True,
        toss_client_id="cid",
        toss_client_secret="secret",
    )
    result = run_stock_paper_engine(settings, {"KR": {"market_state": "closed"}, "US": {"market_state": "closed"}})
    assert "coverage_blocks" in result
    assert isinstance(result["coverage_blocks"], dict)


# ══════════════════════════════════════════════════════════════
# C2 — 게이트·파라미터 무변경
# ══════════════════════════════════════════════════════════════


def test_parameters_unchanged() -> None:
    from app.poly_paper.parameters import load_poly_parameters
    from app.stock_paper.exit_policy import load_exit_parameters
    from app.stock_paper.parameters import load_stock_parameters

    entry = load_stock_parameters()
    assert (entry.min_rr, entry.min_evidence, entry.min_entry_score) == (1.5, 4, 75)
    assert load_exit_parameters().max_holding_days == 10
    # max_open_markets 를 늘려 "진입이 나오게" 하지 않았다(금지 항목).
    assert load_poly_parameters().max_open_markets in {5, 8}


def test_liveness_alerts_remain_enabled() -> None:
    """C4: 생존·사망·복구 알림은 이 WO의 영향을 받지 않는다."""
    from app.core.config import Settings

    enabled = Settings().alert_enabled_rule_set
    for rule in ("engine_liveness", "job_backoff_stuck", "infra_capacity", "process_restarted"):
        assert rule in enabled
