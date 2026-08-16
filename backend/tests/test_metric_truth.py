"""WO-FCE-METRIC-TRUTH-01 — 계기판 정의 회귀.

## 왜 이 테스트가 있나

두 오류가 서로를 가리고 있었다.

1. **퍼센트 단순합**(`sum(returns)`) — 사이즈가 다른 거래의 %를 그냥 더했다.
   사용자 진입 명목이 11~3,021 USDT 로 267배 편차인데, 실제 손익을 부호까지 뒤집었다.
2. **창 술어 비대칭** — 엔진 `entry_at`, 사용자 `exit_at`. 앵커 이전 로직으로 진입한 거래가
   사용자 쪽에만 섞여 들어왔다.

둘을 함께 고치자 사용자 성적이 **−261.85 USDT → +151.85 USDT** 로 바뀌었다.
한쪽만 고쳤으면 여전히 틀린 숫자를 봤을 것이다.

고정하는 명제:
1. 대표 지표는 **금액**이다. 퍼센트 단순합은 강등 보존만 한다
2. 창 술어는 **하나의 함수**이고 양 계열이 같은 것을 쓴다
3. 승률·PF·수익률·낙폭은 **같은 모집단** 위에서 계산된다
4. 자본을 모르면 자본 대비 수익률은 `None` 이다 — 추정하지 않는다
5. 자본 기준이 다른 두 계열의 우열은 판정하지 않는다
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.paper import window as paper_window
from app.paper.service import _metric_payload, engine_capital_usdt

NOW = datetime(2026, 8, 16, tzinfo=timezone.utc)
ANCHOR = datetime(2026, 7, 17, tzinfo=timezone.utc)


class _Trade:
    def __init__(self, entry_at, exit_at):
        self.entry_at = entry_at
        self.exit_at = exit_at


# ── 1. 창 술어는 하나이고 양쪽을 모두 건다 (결정 2) ────────────────


def test_window_requires_both_entry_and_exit_inside() -> None:
    """둘 중 하나만 걸면 오염된다 — entry 만: 결과가 창 밖 / exit 만: 앵커 이전 로직."""
    inside = _Trade(ANCHOR + timedelta(days=1), ANCHOR + timedelta(days=2))
    entered_before = _Trade(ANCHOR - timedelta(days=5), ANCHOR + timedelta(days=1))
    not_closed = _Trade(ANCHOR + timedelta(days=1), None)

    assert paper_window.in_window(inside.entry_at, inside.exit_at, start=ANCHOR) is True
    assert paper_window.in_window(entered_before.entry_at, entered_before.exit_at, start=ANCHOR) is False
    assert paper_window.in_window(not_closed.entry_at, not_closed.exit_at, start=ANCHOR) is False


def test_window_end_bound_is_applied() -> None:
    late = _Trade(ANCHOR + timedelta(days=1), ANCHOR + timedelta(days=40))
    assert paper_window.in_window(late.entry_at, late.exit_at, start=ANCHOR, end=ANCHOR + timedelta(days=28)) is False


def test_both_series_call_the_same_filter() -> None:
    """두 곳에 각각 구현하면 다시 갈라진다 — 실제로 그래서 갈라졌다."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "app" / "paper" / "service.py").read_text(encoding="utf-8")
    assert source.count("paper_window.filter_trades") >= 4, "엔진·사용자 양 계열이 같은 필터를 써야 한다"
    assert "trade.entry_at >= effective_start" not in source, "옛 비대칭 술어가 남아 있다"


def test_window_accepts_iso_strings() -> None:
    assert paper_window.in_window("2026-07-20T00:00:00+00:00", "2026-07-21T00:00:00+00:00", start=ANCHOR) is True


# ── 2. 대표 지표는 금액 (결정 1) ────────────────────────────────────


def test_amount_is_the_headline_and_percent_sum_is_demoted() -> None:
    payload = _metric_payload([10.0, -5.0], 1, 20.0, 10.0, scored_count=2, neutral_count=0, pnl_usdt=[20.0, -10.0], capital_usdt=500.0)

    assert payload["net_pnl_usdt"] == 10.0
    assert payload["return_on_capital_pct"] == 2.0  # 10 / 500
    assert payload["legacy_return_sum_pct"] == 5.0, "옛 값은 삭제하지 않고 보존한다(C4)"


def test_percent_sum_can_disagree_with_amount() -> None:
    """이 케이스가 실제로 일어났다 — 퍼센트 합은 양수인데 금액은 음수.

    작은 포지션에서 +50%(=+5 USDT), 큰 포지션에서 −10%(=−100 USDT).
    """
    payload = _metric_payload([50.0, -10.0], 1, 5.0, 100.0, scored_count=2, neutral_count=0, pnl_usdt=[5.0, -100.0], capital_usdt=500.0)

    assert payload["legacy_return_sum_pct"] == 40.0, "퍼센트 합은 이기고 있다고 말한다"
    assert payload["net_pnl_usdt"] == -95.0, "실제로는 잃었다"
    assert payload["return_on_capital_pct"] < 0


def test_unknown_capital_yields_none_not_an_estimate() -> None:
    """C5 — 자본을 모르면 미산출이다. 추정치로 채우면 이 WO 가 고치는 오류를 다시 만든다."""
    payload = _metric_payload([1.0], 1, 10.0, 0.0, scored_count=1, neutral_count=0, pnl_usdt=[10.0], capital_usdt=None, capital_note="미산출 사유")

    assert payload["net_pnl_usdt"] == 10.0
    assert payload["return_on_capital_pct"] is None
    assert payload["capital_note"] == "미산출 사유"


def test_zero_trades_is_zero_not_unknown() -> None:
    payload = _metric_payload([], 0, 0.0, 0.0, scored_count=0, neutral_count=0, pnl_usdt=[], capital_usdt=500.0)
    assert payload["net_pnl_usdt"] == 0.0, "0건은 모르는 게 아니라 0이다"


def test_engine_capital_is_not_hardcoded() -> None:
    from app.paper.policy import PaperPolicy

    assert engine_capital_usdt(PaperPolicy(margin_usdt=200.0, max_open_positions=3)) == 600.0


# ── 3. 단일 모집단 (결정 3) ─────────────────────────────────────────


def test_win_rate_and_return_share_one_population() -> None:
    """승률만 채점군, 수익률은 전체 — 이 분리가 손익비 0.42 오류의 원인이었다."""
    payload = _metric_payload([1.0, -1.0, 2.0], 2, 30.0, 10.0, scored_count=3, neutral_count=1, pnl_usdt=[10.0, -10.0, 20.0], capital_usdt=500.0)

    assert payload["trade_count"] == payload["scored_trade_count"] == 3
    assert payload["win_rate_pct"] == round(2 / 3 * 100, 2)
    assert payload["neutral_count"] == 1, "중립은 관측 정보로 남되 분모에서 빠지지 않는다"
    assert payload["population"] == "all_closed_in_window"


def test_drawdown_is_measured_in_amount() -> None:
    """낙폭을 퍼센트 곡선에서 재면 사이즈가 다를 때 의미가 흐려진다."""
    payload = _metric_payload([1.0, -1.0], 1, 100.0, 50.0, scored_count=2, neutral_count=0, pnl_usdt=[100.0, -50.0], capital_usdt=500.0)

    assert payload["mdd_usdt"] == 50.0
    assert payload["mdd_pct"] == 10.0  # 50 / 500
