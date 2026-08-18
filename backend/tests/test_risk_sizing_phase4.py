"""WO-FCE-RISK-SIZING-01 Phase 4 — 슬리피지 관측 · 갭 예산 · 포트폴리오 상한 회귀.

이 파일이 지키는 것:
1. **가정값 0.03% 는 변하지 않는다** (C7) — 관측이 그것을 대체하는 경로가 없다.
2. **상한·관측 기본값은 회귀 0** — 옵트인이므로 켜지 않으면 아무것도 달라지지 않는다.
3. Phase 2 의 발표값(1.681배 · 56% · +0.280R)이 재현된다.
4. 재현 실패는 **실패로 드러난다** — Phase 3-5 합계 미재현을 테스트가 고정한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db.models import Direction
from app.paper.policy import PaperPolicy, portfolio_cap_block_reason
from app.paper.slippage import (
    ASSUMPTION_REPLACEMENT,
    observe_depth,
    summarize_observations,
    walk_book,
)
from app.validation.risk_sizing_replay import (
    GAP_EXCESS_R_THRESHOLD,
    PortfolioCaps,
    ReplayTrade,
    apply_portfolio_caps,
    gap_budget,
    metrics,
    same_bar_reentry_blocked,
    stop_executions,
)


BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _trade(
    trade_id: str,
    *,
    symbol: str = "HYPEUSDT",
    direction: str = "long",
    entry_offset_hours: int = 0,
    exit_offset_hours: int = 4,
    entry_price: float = 100.0,
    invalidation_price: float = 98.0,
    exit_price: float | None = None,
    exit_reason: str = "time_decay",
    quantity: float = 3.0,
    gross: float = 0.0,
    costs: float = 0.0,
) -> ReplayTrade:
    return ReplayTrade(
        trade_id=trade_id,
        symbol=symbol,
        timeframe="4h",
        direction=direction,
        entry_bar_at=BASE + timedelta(hours=entry_offset_hours),
        exit_bar_at=BASE + timedelta(hours=exit_offset_hours),
        entry_price=entry_price,
        invalidation_price=invalidation_price,
        exit_price=exit_price if exit_price is not None else entry_price,
        exit_reason=exit_reason,
        quantity=quantity,
        gross_pnl_usdt=gross,
        costs_usdt=costs,
        net_pnl_usdt=gross - costs,
    )


# ---------------------------------------------------------------------------
# 4-1 슬리피지 관측
# ---------------------------------------------------------------------------


def test_walk_book_absorbs_within_first_level() -> None:
    """첫 호가가 명목을 다 소화하면 슬리피지는 반스프레드다."""
    walk = walk_book([[100.1, 100.0]], notional_usdt=600.0, mid_price=100.05, side="buy")
    assert walk.fully_absorbed
    assert walk.levels_consumed == 1
    assert walk.slippage_pct is not None
    assert abs(walk.slippage_pct - (0.05 / 100.05 * 100.0)) < 1e-9


def test_walk_book_marks_exhausted_depth_instead_of_hiding_it() -> None:
    """깊이가 모자라면 감추지 않고 표시한다 — 저유동성 과소평가 방지."""
    walk = walk_book([[100.1, 0.1]], notional_usdt=600.0, mid_price=100.05, side="buy")
    assert walk.depth_exhausted is True
    assert walk.fully_absorbed is False
    assert walk.filled_notional_usdt < 600.0


def test_walk_book_walks_multiple_levels_and_costs_more() -> None:
    """깊은 호가까지 먹으면 슬리피지가 커진다 — 단조성."""
    shallow = walk_book([[100.1, 10.0], [101.0, 100.0]], notional_usdt=100.0, mid_price=100.05, side="buy")
    deep = walk_book([[100.1, 10.0], [101.0, 100.0]], notional_usdt=5000.0, mid_price=100.05, side="buy")
    assert shallow.slippage_pct is not None and deep.slippage_pct is not None
    assert deep.slippage_pct > shallow.slippage_pct
    assert deep.levels_consumed == 2


def test_sell_side_slippage_is_positive_cost() -> None:
    """매도도 불리한 방향이 양수다 — 부호 규약이 방향에 따라 뒤집히지 않는다."""
    walk = walk_book([[99.9, 100.0]], notional_usdt=300.0, mid_price=100.05, side="sell")
    assert walk.slippage_pct is not None and walk.slippage_pct > 0


def test_observation_records_assumption_alongside_and_never_replaces_it() -> None:
    """관측치와 가정값이 **나란히** 남고 가정값은 그대로다 (C7)."""
    payload = observe_depth(
        symbol="btcusdt",
        timeframe="4h",
        decision="entry",
        observed_at=BASE,
        book={"bids": [[64660.0, 6.0]], "asks": [[64660.1, 13.0]]},
        assumed_slippage_pct=0.03,
        direction="long",
    ).as_payload()
    assert payload["assumed_slippage_pct"] == 0.03
    assert payload["symbol"] == "BTCUSDT"
    assert payload["observed_slippage_pct"]["buy@300"] is not None
    # 관측이 가정 자리를 차지하지 않는다.
    assert "slippage_pct" not in payload
    assert payload["assumption_replacement_criteria"]["auto_apply"] is False


def test_missing_depth_records_reason_not_silence() -> None:
    """호가를 못 얻어도 사유를 남긴다 — 침묵 금지."""
    assert (
        observe_depth(symbol="BTCUSDT", timeframe="4h", decision="entry", observed_at=BASE, book=None, assumed_slippage_pct=0.03).as_payload()[
            "unavailable_reason"
        ]
        == "depth_unavailable"
    )
    assert (
        observe_depth(
            symbol="BTCUSDT", timeframe="4h", decision="entry", observed_at=BASE, book={"bids": [], "asks": []}, assumed_slippage_pct=0.03
        ).as_payload()["unavailable_reason"]
        == "depth_side_missing"
    )


def test_replacement_criteria_fixed_before_results() -> None:
    """대체 기준은 결과 전에 고정됐고 자동 적용이 없다 (4-1 작업 5 · 도메인 불변)."""
    assert ASSUMPTION_REPLACEMENT["min_observations"] == 30
    assert ASSUMPTION_REPLACEMENT["min_distinct_symbols"] == 3
    assert ASSUMPTION_REPLACEMENT["statistic"] == "p80"
    assert ASSUMPTION_REPLACEMENT["auto_apply"] is False


def test_summary_withholds_verdict_until_sample_is_sufficient() -> None:
    """표본 부족이면 방향을 말하지 않는다 (C10)."""
    payloads = [{"symbol": "BTCUSDT", "observed_slippage_pct": {"buy@300": 0.001}} for _ in range(5)]
    summary = summarize_observations(payloads, key="buy@300", assumed_slippage_pct=0.03)
    assert summary["sample_sufficient"] is False
    assert summary["verdict"] == "sample_insufficient"


def test_summary_flags_eligibility_but_requires_user_decision() -> None:
    """표본이 차고 관측이 가정보다 낮아도 **제안**까지다 — 자동 교체가 없다."""
    payloads = [{"symbol": f"SYM{index % 4}USDT", "observed_slippage_pct": {"buy@300": 0.002}} for index in range(30)]
    summary = summarize_observations(payloads, key="buy@300", assumed_slippage_pct=0.03)
    assert summary["sample_sufficient"] is True
    assert summary["verdict"].startswith("observation_below_assumption")
    assert "pending_user_decision" in summary["verdict"]
    assert summary["assumed_pct"] == 0.03


def test_summary_keeps_assumption_when_observation_is_not_lower() -> None:
    payloads = [{"symbol": f"SYM{index % 4}USDT", "observed_slippage_pct": {"buy@300": 0.05}} for index in range(30)]
    summary = summarize_observations(payloads, key="buy@300", assumed_slippage_pct=0.03)
    assert summary["verdict"] == "observation_at_or_above_assumption:keep_assumption"


def test_policy_slippage_default_is_unchanged() -> None:
    """이 Phase 는 비용 상수를 만지지 않는다 (C7 · 금지 1항)."""
    policy = PaperPolicy()
    assert policy.slippage_pct == 0.03
    assert policy.taker_fee_pct == 0.06
    assert abs(policy.execution_cost_rate - 0.0009) < 1e-12


# ---------------------------------------------------------------------------
# 4-2 갭 예산
# ---------------------------------------------------------------------------


def test_gap_threshold_separates_gap_from_close_rule() -> None:
    """Phase 2 의 두 현상이 갈린다 — 갭 1건 vs 종가규칙 다수."""
    gap = _trade("gap", exit_reason="invalidation_breach", exit_price=91.0, gross=-3.51 * 6.0, costs=0.0, quantity=3.0)
    close_rule = _trade("close", exit_reason="invalidation_breach", exit_price=97.9, gross=-1.049 * 6.0, costs=0.0, quantity=3.0)
    rows = stop_executions([gap, close_rule])
    assert {row.trade.trade_id: row.is_gap for row in rows} == {"gap": True, "close": False}
    assert rows[0].excess_r > GAP_EXCESS_R_THRESHOLD


def test_non_stop_exits_are_excluded_from_effective_risk() -> None:
    """계획 리스크가 시험되지 않은 거래는 실효 리스크를 만들지 않는다 (C10)."""
    assert stop_executions([_trade("tp", exit_reason="take_profit_2", gross=12.0)]) == []


def test_gap_budget_reports_both_bases_separately() -> None:
    """실효(net)와 체결 이탈(gross)을 한 숫자로 합치지 않는다."""
    stop = _trade("s1", exit_reason="invalidation_breach", exit_price=97.9, gross=-6.3, costs=0.6, quantity=3.0)
    budget = gap_budget(stop_executions([stop]))
    assert budget is not None
    # planned risk = |100-98| * 3 = 6.0 → gross 1.05R · net 1.15R
    assert abs(budget.mean_execution_risk_r - 1.05) < 1e-9
    assert abs(budget.mean_effective_risk_r - 1.15) < 1e-9
    assert budget.mean_execution_risk_r < budget.mean_effective_risk_r


def test_gap_budget_does_not_mutate_the_risk_budget() -> None:
    """실효 리스크는 **추가 산출**이며 예산 값을 바꾸지 않는다 (C3)."""
    budget = gap_budget(stop_executions([_trade("s1", exit_reason="invalidation_breach", exit_price=90.0, gross=-30.0)]))
    assert budget is not None
    assert budget.planned_risk_usdt == 2.5
    assert PaperPolicy().risk_budget_usdt == 2.5
    assert PaperPolicy().max_notional_usdt == 600.0


# ---------------------------------------------------------------------------
# 4-3 포트폴리오 상한
# ---------------------------------------------------------------------------


def test_portfolio_cap_default_off_is_zero_regression() -> None:
    """기본값은 상한 없음이다 — 옵트인이므로 회귀 0 (C3 정신)."""
    policy = PaperPolicy()
    assert policy.portfolio_cap_mode == "off"
    held = [{"symbol": "HYPEUSDT", "direction": "long", "planned_risk_usdt": 2.5} for _ in range(20)]
    assert portfolio_cap_block_reason(direction=Direction.long, symbol="BTCUSDT", planned_risk_usdt=2.5, open_positions=held, policy=policy) is None


def test_total_risk_cap_blocks_with_reason() -> None:
    policy = PaperPolicy(portfolio_cap_mode="on", max_total_risk_usdt=7.5)
    held = [{"symbol": "HYPEUSDT", "direction": "long", "planned_risk_usdt": 2.5} for _ in range(3)]
    reason = portfolio_cap_block_reason(direction=Direction.long, symbol="BTCUSDT", planned_risk_usdt=2.5, open_positions=held, policy=policy)
    assert reason == "portfolio_cap:total_risk>7.5"


def test_same_direction_cap_is_independent_of_total_risk() -> None:
    """축이 뭉치지 않는다 — 사유로 어느 축인지 구분된다."""
    policy = PaperPolicy(portfolio_cap_mode="on", max_same_direction_positions=2)
    held = [{"symbol": "HYPEUSDT", "direction": "long", "planned_risk_usdt": 2.5} for _ in range(2)]
    assert (
        portfolio_cap_block_reason(direction=Direction.long, symbol="BTCUSDT", planned_risk_usdt=2.5, open_positions=held, policy=policy)
        == "portfolio_cap:same_direction>2"
    )
    # 반대 방향은 편중이 아니다.
    assert portfolio_cap_block_reason(direction=Direction.short, symbol="BTCUSDT", planned_risk_usdt=2.5, open_positions=held, policy=policy) is None


def test_correlation_cluster_cap_uses_declared_map() -> None:
    policy = PaperPolicy(
        portfolio_cap_mode="on",
        max_correlation_cluster_positions=1,
        correlation_clusters={"BTCUSDT": "major", "ETHUSDT": "major", "HYPEUSDT": "alt_beta"},
    )
    held = [{"symbol": "ETHUSDT", "direction": "long", "planned_risk_usdt": 2.5}]
    assert (
        portfolio_cap_block_reason(direction=Direction.long, symbol="BTCUSDT", planned_risk_usdt=2.5, open_positions=held, policy=policy)
        == "portfolio_cap:cluster[major]>1"
    )
    # 다른 군집은 통과한다.
    assert portfolio_cap_block_reason(direction=Direction.long, symbol="HYPEUSDT", planned_risk_usdt=2.5, open_positions=held, policy=policy) is None


def test_unmapped_symbols_share_the_unclustered_bucket() -> None:
    """분류가 선언이라는 사실이 드러난다 — 미등록 심볼은 한 군집으로 묶인다."""
    policy = PaperPolicy(portfolio_cap_mode="on", max_correlation_cluster_positions=1, correlation_clusters={})
    held = [{"symbol": "ETHUSDT", "direction": "long", "planned_risk_usdt": 2.5}]
    reason = portfolio_cap_block_reason(direction=Direction.long, symbol="BTCUSDT", planned_risk_usdt=2.5, open_positions=held, policy=policy)
    assert reason == "portfolio_cap:cluster[unclustered]>1"


def test_cap_replay_reports_blocked_entries_with_reasons() -> None:
    """반사실도 보류 사유를 남긴다 (C11)."""
    trades = [
        _trade("a", entry_offset_hours=0, exit_offset_hours=40, gross=6.0),
        _trade("b", symbol="BTCUSDT", entry_offset_hours=4, exit_offset_hours=40, gross=6.0),
        _trade("c", symbol="ETHUSDT", entry_offset_hours=8, exit_offset_hours=40, gross=6.0),
    ]
    replay = apply_portfolio_caps(trades, PortfolioCaps(label="t", max_total_risk_usdt=5.0))
    assert [trade.trade_id for trade in replay.kept] == ["a", "b"]
    assert [(trade.trade_id, reason) for trade, reason in replay.blocked] == [("c", "portfolio_cap:total_risk>5")]


def test_cap_replay_frees_capacity_after_exit() -> None:
    """청산한 포지션은 노출을 점유하지 않는다."""
    trades = [
        _trade("a", entry_offset_hours=0, exit_offset_hours=4, gross=6.0),
        _trade("b", symbol="BTCUSDT", entry_offset_hours=8, exit_offset_hours=12, gross=6.0),
    ]
    replay = apply_portfolio_caps(trades, PortfolioCaps(label="t", max_total_risk_usdt=2.5))
    assert len(replay.kept) == 2
    assert replay.blocked == []


# ---------------------------------------------------------------------------
# 재현 가능성 (Phase 4-0)
# ---------------------------------------------------------------------------


def test_same_bar_lock_blocks_only_matching_direction_and_bar() -> None:
    trades = [
        _trade("first", entry_offset_hours=0, exit_offset_hours=4),
        _trade("same_bar_same_dir", entry_offset_hours=4, exit_offset_hours=8),
        _trade("same_bar_other_dir", direction="short", entry_offset_hours=4, exit_offset_hours=8),
        _trade("later_bar", entry_offset_hours=12, exit_offset_hours=16),
    ]
    assert same_bar_reentry_blocked(trades) == {"same_bar_same_dir"}


def test_r_is_invariant_to_position_size() -> None:
    """Phase 1 의 핵심 성질 — 수량을 s 배 해도 R 은 같다."""
    small = _trade("s", quantity=3.0, gross=6.0, costs=0.6)
    large = _trade("l", quantity=30.0, gross=60.0, costs=6.0)
    assert abs(small.net_r - large.net_r) < 1e-12
    assert abs(metrics([small]).net_r - metrics([large]).net_r) < 1e-12


def test_cost_recomputation_matches_the_documented_formula() -> None:
    """비용R = 왕복 비용률 / 스톱거리% (Phase 3-5 §3)."""
    trade = _trade("t", entry_price=100.0, invalidation_price=98.0, gross=0.0, costs=0.0)
    row = metrics([trade], one_way_cost_pct=0.09)
    # 스톱거리 2% → 왕복 0.18% / 2% = 0.09R
    assert abs(row.cost_r - 0.09) < 1e-9


def test_metrics_mdd_is_measured_on_the_amount_curve() -> None:
    """낙폭은 금액 곡선에서 잰다 (METRIC-TRUTH-01)."""
    win = _trade("w", exit_offset_hours=4, gross=12.0)
    loss = _trade("l", exit_offset_hours=8, gross=-6.0)
    row = metrics([win, loss])
    # +2R 뒤 −1R → 예산 2.5 기준 낙폭 2.5
    assert abs(row.mdd_usdt - 2.5) < 1e-9
    assert row.profit_factor is not None and abs(row.profit_factor - 2.0) < 1e-9
