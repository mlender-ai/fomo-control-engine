"""WO-FCE-MAKE-IT-RUN-01 — 트랙이 실제로 돌게 만든다.

계측이 아니라 **동작**이 목표다. 이 파일이 고정하는 명제:

1. **가격을 만든 봉과 검증하는 봉이 같다** (Phase 1) — US 정지의 원인
2. **invariant 는 완화되지 않았다** (C1) — 모순된 관측에서는 여전히 발화한다
3. **큐 적체가 근원에서 막힌다** — 같은 청산 주문을 매 틱 새로 만들지 않는다
4. **재제출에 배치 상한이 있다** (C4) — 개장 순간 일괄 체결이 예산을 먹지 않는다
5. **MDD 서명값 초과가 표시된다** (Phase 4) — 임계는 그대로다(C2)
6. **451 판정은 호스트에서만 나온다** — 컨테이너 이그레스 실패를 지역 차단으로 적지 않는다
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.stock_paper.execution import execute_order
from app.stock_paper.models import (
    FillInvariantViolation,
    Market,
    MarketObservation,
    OrderStatus,
    Side,
    StockOrder,
)
from app.validation import mdd_watch

REPO_ROOT = Path(__file__).resolve().parents[2]
T = datetime(2026, 8, 31, 13, 31, tzinfo=timezone.utc)

# C1·C5 — invariant·게이트 임계·방향 판정은 한 줄도 바뀌지 않는다.
UNTOUCHABLE = ("backend/app/analyst", "backend/app/structure", "backend/app/stock_paper/policy.py")


def _obs(**kw) -> MarketObservation:
    base = dict(
        symbol="AAPL",
        market=Market.US,
        observed_at=T,
        session_open=True,
        session_open_price=200.0,
        minute_open=210.0,
        minute_high=211.0,
        minute_low=209.0,
        minute_close=210.0,
        minute_volume=100_000.0,
        bid=209.9,
        ask=210.1,
        warnings=[],
        halted=False,
        vi_active=False,
        upper_locked=False,
        lower_locked=False,
        fx_rate_to_krw=1300.0,
        fx_observed_at=None,
    )
    base.update(kw)
    return MarketObservation(**base)


def _order(reason: str | None = None, side: Side = Side.BUY) -> StockOrder:
    return StockOrder(
        id=uuid4(),
        symbol="AAPL",
        market=Market.US,
        currency="USD",
        side=side,
        quantity=10,
        remaining_quantity=10,
        status=OrderStatus.QUEUED,
        reason=reason,
        signal_at=T,
        entry_mode="strict_signal",
    )


# ── Phase 1 · 가격 산출 봉 == 검증 봉 ───────────────────────────────────


def test_queued_order_no_longer_prices_off_a_different_bar() -> None:
    """**US 정지의 직접 원인.**

    시가 200 으로 체결가를 만들고 현재 분봉 209~211 로 검사했다. 두 봉이 다르므로
    갭이 있는 날이면 **반드시** 터진다 — 실측에서 그렇게 정지했다.
    """
    result = execute_order(_order("session_closed"), _obs())

    assert result.fill is not None, "큐 주문이 여전히 invariant 로 죽는다"
    assert 209.0 <= result.fill.price <= 211.0


def test_close_at_the_high_no_longer_escapes_via_the_spread() -> None:
    """**두 번째 결함.** 첫 결함만 고치면 이것이 계속 정지를 만든다.

    분봉 종가가 고가와 같은 것은 흔하고(상승 마감), 매수는 반 스프레드를 더한 뒤
    틱을 **올림**한다 — 합쳐서 고가를 넘는다.
    """
    result = execute_order(_order(), _obs(minute_close=211.0, bid=210.9, ask=211.1))

    assert result.fill is not None
    assert result.fill.price <= 211.0


def test_sell_at_the_low_is_bounded_too() -> None:
    result = execute_order(_order(side=Side.SELL), _obs(minute_close=209.0, bid=208.9, ask=209.1))

    assert result.fill is not None
    assert result.fill.price >= 209.0


def test_normal_fill_is_unchanged() -> None:
    """수리가 정상 경로를 건드리지 않았다 — 종가가 봉 중간이면 예전 값 그대로다."""
    result = execute_order(_order(), _obs(minute_close=210.0, bid=209.9, ask=210.1))

    assert result.fill is not None
    assert result.fill.price == pytest.approx(210.1)


def test_invariant_still_fires_on_a_contradictory_observation() -> None:
    """**C1 — 완화하지 않았다.** 종가가 자기 봉 범위 밖이면 데이터 결함이고 덮지 않는다."""
    with pytest.raises(FillInvariantViolation):
        execute_order(_order(), _obs(minute_close=500.0))


def test_invariant_source_line_is_intact() -> None:
    """검사 자체를 지우거나 느슨하게 하지 않았다."""
    source = (REPO_ROOT / "backend/app/stock_paper/execution.py").read_text(encoding="utf-8")

    assert "raise FillInvariantViolation" in source
    assert "observation.minute_low <= fill_price <= observation.minute_high" in source
    # 시가로 가격을 만드는 분기가 사라졌다 — 그것이 봉 불일치의 원인이었다.
    assert 'observation.session_open_price if order.reason == "session_closed"' not in source


# ── Phase 1 · 큐 적체와 배치 상한 ───────────────────────────────────────


def test_duplicate_exit_orders_are_not_created() -> None:
    """**13,934건이 쌓인 기전.** 마감 중 청산 신호가 매 틱 새 주문을 만들었다.

    상한이 아니라 중복 제거다 — 상한만 두면 상한만큼은 여전히 중복이 쌓인다.
    """
    source = (REPO_ROOT / "backend/app/stock_paper/service.py").read_text(encoding="utf-8")
    block = source.split("def _process_exits")[1].split("\ndef ")[0]

    assert "_pending_order_keys" in block, "대기 주문을 보지 않고 새로 만든다"
    assert 'counters["deduped"]' in block


def test_requeue_has_a_batch_limit() -> None:
    """C4 — 개장 순간 큐 전체가 한꺼번에 나가면 잡 실행 시간이 큐 길이에 비례한다."""
    from app.core.config import Settings
    from app.stock_paper import service as stock_service

    assert Settings().stock_paper_requeue_batch_limit >= 1
    block = (REPO_ROOT / "backend/app/stock_paper/service.py").read_text(encoding="utf-8").split("def _process_pending_orders")[1]
    assert "batch_limit" in block
    # 남은 건은 **버리지 않는다** — 다음 실행이 이어받는다.
    assert "deferred" in block
    assert stock_service.DEFAULT_REQUEUE_BATCH_LIMIT > 0


# ── Phase 4 · MDD 서명값 초과 ───────────────────────────────────────────


class _Trade:
    def __init__(self, *, net: float, entry: datetime, exit_at: datetime, symbol: str = "BTCUSDT") -> None:
        self.id = uuid4()
        self.symbol = symbol
        self.status = "closed"
        self.net_pnl_usdt = net
        self.entry_at = entry
        self.exit_at = exit_at
        self.exit_bar_at = exit_at
        self.exit_reason = "invalidation_breach"


def test_breach_is_labelled_not_silently_numeric() -> None:
    """**실측 20.62%.** 숫자만 있으면 서명값을 넘겼다는 사실이 어디에도 안 나온다."""
    status = mdd_watch.mdd_status(20.62)

    assert status["state"] == "breached"
    assert "초과" in status["label"]
    assert status["ceiling_pct"] == 20.0


def test_near_threshold_warns_before_the_breach() -> None:
    assert mdd_watch.mdd_status(18.9)["state"] == "near"
    assert mdd_watch.mdd_status(4.71)["state"] == "ok"
    assert mdd_watch.mdd_status(None)["state"] == "unknown"


def test_report_prints_the_breach() -> None:
    from app.notify import daily_report

    line = daily_report.metric_line({"trade_count": 75, "win_rate_pct": 54.7, "profit_factor": 0.67, "mdd_pct": 20.62})
    assert "초과" in line


def test_drawdown_window_is_located() -> None:
    """Phase 4 항목 2 — "MDD 20.62%" 만으로는 대응할 수 없다."""
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    trades = [
        _Trade(net=+10.0, entry=base, exit_at=base + timedelta(hours=1)),
        _Trade(net=-4.0, entry=base + timedelta(hours=2), exit_at=base + timedelta(hours=3), symbol="ETHUSDT"),
        _Trade(net=-6.0, entry=base + timedelta(hours=4), exit_at=base + timedelta(hours=5), symbol="SOLUSDT"),
        _Trade(net=+2.0, entry=base + timedelta(hours=6), exit_at=base + timedelta(hours=7)),
    ]
    window = mdd_watch.drawdown_window(trades)

    assert window["drawdown_usdt"] == pytest.approx(10.0)
    assert window["trade_count"] == 3
    assert set(window["symbols"]) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    assert window["worst_trades"][0]["net_pnl_usdt"] == pytest.approx(-6.0)


def test_drawdown_window_refuses_an_empty_sample() -> None:
    """C8 — 표본이 없으면 구간을 만들지 않는다."""
    assert mdd_watch.drawdown_window([])["available"] is False


def test_concurrent_cap_counterfactual_is_labelled_not_performance() -> None:
    """Phase 4 항목 4 — 상한이 이 낙폭에 닿았는가. **성적이 아니다**(C8)."""
    base = datetime(2026, 8, 1, tzinfo=timezone.utc)
    # 셋이 동시에 열리고 전부 잃는다. 상한 1 이면 둘이 안 열린다.
    trades = [_Trade(net=-5.0, entry=base, exit_at=base + timedelta(hours=5), symbol=f"S{index}") for index in range(3)]
    result = mdd_watch.concurrent_cap_counterfactual(trades, max_concurrent=1)

    assert result["skipped_entries"] == 2
    assert result["capped_mdd_usdt"] < result["actual_mdd_usdt"]
    assert "성적으로 보고하지 않는다" in result["not_performance"]


def test_mdd_ceiling_is_display_only_not_a_gate() -> None:
    """C2 — 서명값을 게이트로 승격시키면 그것이야말로 임계를 임의로 바꾸는 것이다."""
    from app.validation import live_trading_gate

    assert mdd_watch.SIGNED_MDD_CEILING_PCT == 20.0
    gate_source = (REPO_ROOT / "backend/app/validation/live_trading_gate.py").read_text(encoding="utf-8")
    assert "mdd_watch" not in gate_source, "표시용 상수가 전환 게이트에 배선됐다"
    assert "SIGNED_MDD_CEILING_PCT" not in gate_source
    assert live_trading_gate.MEASURED_AXES


# ── Phase 2 · 451 판정은 호스트에서만 ───────────────────────────────────


def test_probe_separates_egress_failure_from_a_geo_block() -> None:
    """**컨테이너에서 연결이 안 되는 것과 폴리가 451 을 주는 것은 다른 사건이다.**

    섞어서 적으면 "차단됐다"는 결론이 근거 없이 선다.
    """
    source = (REPO_ROOT / "backend/scripts/prediction_market_probe.py").read_text(encoding="utf-8")

    assert "지역 차단(451)과 다르다" in source
    assert "호스트에서 실행한다" in source
    # C3 — 우회 경로가 없다. **문서에서 "프록시를 쓰지 않는다"고 적는 것과 쓰는 것은
    # 다르다** — 실제 배선 흔적만 본다.
    for bypass in ("ProxyHandler", "set_proxy", "socks5", "proxies=", "X-Forwarded-For"):
        assert bypass not in source, f"우회 경로가 있다: {bypass}"


def test_probe_requires_expiry_inside_the_validation_window() -> None:
    """금지 — 만기 확인 없이 채택. 폴리가 막힌 진짜 이유는 만기 2027 이었다."""
    from scripts import prediction_market_probe as probe

    assert probe.VALIDATION_WINDOW_DAYS == 28
    now = datetime(2026, 8, 31, tzinfo=timezone.utc)
    body = '[{"end_date_iso": "2026-09-10T00:00:00Z"}, {"end_date_iso": "2027-01-01T00:00:00Z"}]'
    distribution = probe.expiry_distribution(body, "end_date_iso", now=now)

    assert distribution["within_window"] == 1
    assert distribution["beyond_window"] == 1
    assert distribution["adoptable"] is True

    far_only = probe.expiry_distribution('[{"end_date_iso": "2027-01-01T00:00:00Z"}]', "end_date_iso", now=now)
    assert far_only["adoptable"] is False, "만기가 창 밖뿐인데 채택 가능으로 나온다"


# ── 제약 증명 ──────────────────────────────────────────────────────────


def test_gates_and_direction_layers_are_untouched() -> None:
    """C1·C5 — 진입 게이트 임계·방향 판정 diff 0줄."""
    diff = subprocess.run(["git", "diff", "origin/main", "--stat", "--", *UNTOUCHABLE], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")
    assert diff.stdout.strip() == "", f"C1·C5 위반:\n{diff.stdout}"


def test_no_ledger_deletion_path_was_added() -> None:
    """C6 — 폴리 종료 시에도 원장을 삭제하지 않는다."""
    for path in ("backend/scripts/prediction_market_probe.py", "backend/app/validation/mdd_watch.py"):
        source = (REPO_ROOT / path).read_text(encoding="utf-8").lower()
        for forbidden in ("drop table", "delete from", "truncate"):
            assert forbidden not in source, f"{path} 에 원장 삭제 경로가 있다"
