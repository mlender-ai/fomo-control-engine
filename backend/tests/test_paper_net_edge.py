"""WO-FCE-NET-EDGE-01 — 순 우위 수리의 기전별 불변식.

고정하는 명제:

1. **RR 항등식이 실재한다** — 현행 기본 경로에서 RR 은 입력과 무관하게 1.5 다
2. **새 축은 전부 옵트인** — 기본값에서 산술이 한 줄도 바뀌지 않는다
3. **마찰 게이트는 거부만 한다** — 어떤 임계도 완화하지 않는다
4. **스톱 경계는 좁히지 않고 거부한다** — 조용한 축소가 결함의 뿌리였다
5. **손절 체결이 익절과 대칭이다** — 봉 중간 터치, 갭은 시가
6. **두 진입 경로에 같은 산술 게이트가 걸린다** — 관문이 둘이면 하나는 잊힌다
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from app.db.models import Direction, MarketCandle
from app.paper.policy import PaperPolicy, apply_exit_decision, evaluate_entry, evaluate_exit, open_trade, stop_within_bounds
from app.paper.service import (
    _crypto_policy_modes,
    _execution_risk,
    _paper_target_plan,
    _staged_reward,
    _stop_side_structure_distances,
    crypto_policy_parameters_path,
)


BASE_TIME = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
TRADE_ID = UUID("00000000-0000-0000-0000-0000000000e1")


def bar(close: float, *, high: float | None = None, low: float | None = None, open_: float | None = None, offset: int = 0) -> MarketCandle:
    return MarketCandle(
        timestamp=BASE_TIME + timedelta(hours=4 * offset),
        open=open_ if open_ is not None else close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=1_000.0,
    )


def analysis_with_levels(*, candles: list[MarketCandle], support: list[float] = [], resistance: list[float] = []) -> dict:
    return {
        "candles": [
            {"time": candle.timestamp.isoformat(), "open": candle.open, "high": candle.high, "low": candle.low, "close": candle.close, "volume": candle.volume}
            for candle in candles
        ],
        "price_levels": {
            "support": [{"price": price, "score": 70} for price in support],
            "resistance": [{"price": price, "score": 70} for price in resistance],
            "invalidation": [],
        },
    }


def _entry_kwargs(**overrides: object) -> dict:
    """모든 게이트가 통과하는 입력. 한 번에 **하나만** 뒤집어 그 게이트를 확인한다."""
    kwargs = {
        "stance_state": {"stance": "long_leaning", "flipped": True, "transitioning": False},
        "direction": Direction.long,
        "evidence_count": 4,
        "checklist_passed": 5,
        "checklist_total": 5,
        "rr_ratio": 1.5,
        "survives_to_invalidation": True,
        "validated_signature": True,
        "signature_ci_low_pct": 50.0,
        "earnings_clear": True,
        "data_fresh": True,
        "confirmed_bar": True,
    }
    kwargs.update(overrides)
    return kwargs


# ── 1. 항등식이 실재한다 ────────────────────────────────────────────────


@pytest.mark.parametrize("structural_multiple", [1.0, 1.5, 3.0, 10.0])
def test_current_rr_gate_is_an_identity_whenever_structure_is_wider_than_one_atr(structural_multiple: float) -> None:
    """구조 무효화가 1 ATR 보다 멀기만 하면 RR 은 **입력과 무관하게** 정확히 1.5 다.

    이것이 "RR 게이트가 한 번도 무언가를 거른 적이 없다"의 산술적 이유다(실측 20/24 건이
    정확히 1.5000, RR<1.5 는 0건). 게이트가 재는 값이 상수면 그것은 판정이 아니다.
    """
    policy = PaperPolicy()
    atr_value = 100.0
    risk, _source = _execution_risk(structural_risk=atr_value * structural_multiple, atr_value=atr_value, policy=policy)
    tp1, tp2, _ = _staged_reward(atr_value=atr_value, execution_risk=risk, structural_distance=None, policy=policy)
    assert risk == atr_value
    assert (tp1 * 0.5 + tp2 * 0.5) / risk == pytest.approx(1.5)


def test_cost_r_is_inversely_proportional_to_stop_distance() -> None:
    """비용R = 왕복 비용률 / 스톱거리%. 스톱을 절반으로 좁히면 마찰이 두 배가 된다.

    리스크 기준 사이징에서 1R 의 금액가치가 예산 상수이므로 이 관계는 사이즈와 무관한
    **항등식**이다. 재판정 N=427 에서 비용이 우위의 2.9배가 된 기전이 이것이다.
    """
    policy = PaperPolicy()
    close = 1_000.0
    wide = _paper_target_plan(
        analysis_with_levels(candles=[bar(close)]),
        {},
        bar=bar(close),
        direction=Direction.long,
        invalidation_price=close * 0.96,
        action_plan={},
        policy=PaperPolicy(risk_mode="structural"),
    )
    tight = _paper_target_plan(
        analysis_with_levels(candles=[bar(close)]),
        {},
        bar=bar(close),
        direction=Direction.long,
        invalidation_price=close * 0.98,
        action_plan={},
        policy=PaperPolicy(risk_mode="structural"),
    )
    assert tight["cost_r"] == pytest.approx(wide["cost_r"] * 2.0, rel=1e-6)
    assert wide["cost_r"] == pytest.approx(policy.roundtrip_cost_rate / 0.04, rel=1e-6)


# ── 2. 새 축은 전부 옵트인 ──────────────────────────────────────────────


def test_every_new_axis_defaults_to_the_legacy_behaviour() -> None:
    policy = PaperPolicy()
    assert policy.risk_mode == "atr_capped"
    assert policy.reward_mode == "atr_ladder"
    assert policy.stop_fill_mode == "close"
    assert policy.max_entry_cost_r is None
    assert policy.min_stop_atr_multiple is None
    assert policy.max_stop_atr_multiple is None
    assert policy.min_net_rr is None
    assert policy.htf_conflict_blocks is False
    # 켜지 않은 축은 게이트에서 **항상 통과**로 나와야 한다 — 꺼진 축이 탈락으로 위장하면
    # 퍼널이 거짓 병목을 가리킨다.
    decision = evaluate_entry(**_entry_kwargs(), policy=policy, cost_r=99.0, stop_atr_multiple=99.0, htf_conflict=True)
    assert decision.enter is True
    assert decision.gates["cost_efficiency"] is True
    assert decision.gates["stop_bounds"] is True
    assert decision.gates["htf_alignment"] is True


def test_observations_are_recorded_even_when_the_gate_is_off() -> None:
    """조건에서 뺐다고 관측까지 버리면 임계를 정할 근거가 영영 안 생긴다."""
    decision = evaluate_entry(**_entry_kwargs(), policy=PaperPolicy(), cost_r=0.137, stop_atr_multiple=2.4, htf_conflict=True)
    assert decision.observations["cost_r_observed"] == 0.137
    assert decision.observations["stop_atr_multiple_observed"] == 2.4
    assert decision.observations["htf_conflict_observed"] is True
    assert decision.observations["risk_mode"] == "atr_capped"


def test_shipped_policy_file_is_the_highest_version_and_rolls_back_by_deletion(tmp_path) -> None:
    shipped = crypto_policy_parameters_path()
    assert shipped is not None and shipped.name == "crypto-v3.json"
    payload = json.loads(shipped.read_text(encoding="utf-8"))
    # v3 는 v2 의 전 키를 승계한다 — 승계를 빠뜨리면 v3 를 켜는 순간 사이징·잠금이 꺼진다.
    v2 = json.loads((shipped.parent / "crypto-v2.json").read_text(encoding="utf-8"))
    for key, value in v2.items():
        if key.startswith("_"):
            continue
        assert key in payload, f"v3 가 v2 의 {key} 를 승계하지 않는다"
        if key != "version":
            assert payload[key] == value, f"v3 의 {key} 가 근거 없이 v2 와 다르다"

    (tmp_path / "crypto-v2.json").write_text(json.dumps({"version": "crypto-v2", "rr_basis": "gross"}), encoding="utf-8")
    (tmp_path / "crypto-v9.json").write_text(json.dumps({"version": "crypto-v9", "rr_basis": "net"}), encoding="utf-8")
    assert crypto_policy_parameters_path(tmp_path).name == "crypto-v9.json"
    (tmp_path / "crypto-v9.json").unlink()
    assert crypto_policy_parameters_path(tmp_path).name == "crypto-v2.json"


def test_shipped_policy_file_loads_every_declared_axis() -> None:
    """파일에 적었는데 로더가 읽지 않는 키가 없어야 한다.

    오타 난 키가 조용히 기본값으로 지나가면 "켰다고 생각했는데 안 켜진" 상태가 되고,
    그것이 가장 진단하기 어려운 종류의 결함이다.
    """
    shipped = crypto_policy_parameters_path()
    assert shipped is not None
    declared = {key for key in json.loads(shipped.read_text(encoding="utf-8")) if not key.startswith("_")}
    loaded = set(_crypto_policy_modes())
    # `observation_universe_enabled` 는 정책 필드가 아니라 유니버스 급유 스위치다.
    assert declared - loaded - {"observation_universe_enabled"} == set()


# ── 3. 마찰 게이트는 거부만 한다 ─────────────────────────────────────────


def test_cost_gate_rejects_and_never_relaxes() -> None:
    policy = PaperPolicy(max_entry_cost_r=0.16)
    assert evaluate_entry(**_entry_kwargs(), policy=policy, cost_r=0.12).gates["cost_efficiency"] is True
    blocked = evaluate_entry(**_entry_kwargs(), policy=policy, cost_r=0.30)
    assert blocked.enter is False
    assert blocked.rejection_reasons == ("cost_efficiency",)
    # 마찰 게이트가 RR·근거·체크리스트 임계를 대신 통과시키는 일은 없어야 한다.
    still_blocked = evaluate_entry(**_entry_kwargs(rr_ratio=1.0), policy=policy, cost_r=0.01)
    assert still_blocked.enter is False
    assert "risk_reward" in still_blocked.rejection_reasons


def test_cost_gate_without_a_measurement_does_not_pass_silently() -> None:
    """비용R 을 못 잰 자리는 통과가 아니다 — 못 잼을 통과로 세면 게이트가 침묵한다."""
    blocked = evaluate_entry(**_entry_kwargs(), policy=PaperPolicy(max_entry_cost_r=0.16), cost_r=None)
    assert blocked.gates["cost_efficiency"] is False


# ── 4. 스톱 경계는 좁히지 않고 거부한다 ──────────────────────────────────


def test_stop_ceiling_rejects_instead_of_silently_tightening() -> None:
    policy = PaperPolicy(risk_mode="structural", max_stop_atr_multiple=3.0)
    assert stop_within_bounds(2.9, policy) is True
    assert stop_within_bounds(3.1, policy) is False
    # 거부지 축소가 아니다 — 리스크 거리는 구조가 준 값 그대로 남는다.
    risk, source = _execution_risk(structural_risk=500.0, atr_value=100.0, policy=policy)
    assert (risk, source) == (500.0, "structural")
    assert evaluate_entry(**_entry_kwargs(), policy=policy, stop_atr_multiple=5.0).rejection_reasons == ("stop_bounds",)


def test_stop_floor_widens_and_is_never_its_own_rejection() -> None:
    """하한은 거부 사유가 아니라 넓히는 지시다. 넓힌 값이 스스로를 탈락시키면 안 된다."""
    policy = PaperPolicy(risk_mode="structural", min_stop_atr_multiple=1.0, max_stop_atr_multiple=3.0)
    risk, source = _execution_risk(structural_risk=40.0, atr_value=100.0, policy=policy)
    assert (risk, source) == (100.0, "atr_floor")
    assert stop_within_bounds(risk / 100.0, policy) is True


def test_nearest_structure_picks_the_closest_level_beyond_the_floor() -> None:
    """가장 가까운 레벨을 쓰되 노이즈 범위(하한 미만)는 건너뛴다.

    `action_plan` 의 무효화는 `invalidation_levels[0]` 또는 `support[0]` 이며 **근접 순이
    아니다.** 픽스처 실측에서 그 거리가 6~19 ATR 로 나온다 — 그것이 1 ATR 캡이라는 반창고를
    부른 원인이다.
    """
    close = 1_000.0
    analysis = analysis_with_levels(candles=[bar(close)], support=[998.0, 970.0, 940.0, 500.0], resistance=[1_100.0])
    distances = _stop_side_structure_distances(analysis, entry_price=close, direction=Direction.long)
    assert distances == (2.0, 30.0, 60.0, 500.0)
    # 저항(위쪽)은 롱의 손절 자리가 될 수 없으므로 섞이지 않는다.
    assert 100.0 not in distances

    policy = PaperPolicy(risk_mode="nearest_structure", min_stop_atr_multiple=1.0)
    risk, source = _execution_risk(structural_risk=500.0, structure_distances=distances, atr_value=20.0, policy=policy)
    assert (risk, source) == (30.0, "nearest_structure")


def test_nearest_structure_falls_back_to_the_floor_when_every_level_is_noise_close() -> None:
    policy = PaperPolicy(risk_mode="nearest_structure", min_stop_atr_multiple=1.0)
    risk, source = _execution_risk(structural_risk=5.0, structure_distances=(1.0, 2.0), atr_value=20.0, policy=policy)
    assert (risk, source) == (20.0, "atr_floor")


# ── 5. 손절 체결이 익절과 대칭이다 ───────────────────────────────────────


def _open_long(policy: PaperPolicy, *, entry: float = 100.0, stop: float = 98.0) -> object:
    return open_trade(
        trade_id=TRADE_ID,
        symbol="NETUSDT",
        timeframe="4h",
        asset_class="crypto",
        direction=Direction.long,
        bar=bar(entry),
        invalidation_price=stop,
        take_profit_price=entry + 3.0,
        evidence={},
        checklist={},
        stance_snapshot={},
        signature_snapshot={},
        policy=policy,
        take_profit_2_price=entry + 6.0,
    )


def test_close_mode_waits_for_the_close_and_fills_there() -> None:
    policy = PaperPolicy()
    trade = _open_long(policy)
    # 봉 중간에 스톱을 관통했다가 되돌아온 봉 — 현행은 손절하지 않는다.
    survived = evaluate_exit(
        trade, bar=bar(99.5, low=97.0, offset=1), stance_state={"stance": "long"}, take_profit_pressure=None, prior_high_pressure_streak=0, policy=policy
    )
    assert survived.action == "hold"
    # 종가가 스톱 아래면 **종가에** 체결된다 — 무효화선 너머다.
    breached = evaluate_exit(
        trade, bar=bar(96.0, low=95.0, offset=2), stance_state={"stance": "long"}, take_profit_pressure=None, prior_high_pressure_streak=0, policy=policy
    )
    assert breached.action == "close"
    assert breached.execution_price == pytest.approx(96.0)


def test_intrabar_mode_fills_at_the_stop_price() -> None:
    policy = PaperPolicy(stop_fill_mode="intrabar")
    trade = _open_long(policy)
    decision = evaluate_exit(
        trade, bar=bar(99.5, low=97.0, offset=1), stance_state={"stance": "long"}, take_profit_pressure=None, prior_high_pressure_streak=0, policy=policy
    )
    assert decision.action == "close"
    assert decision.execution_price == pytest.approx(98.0), "무효화 임계는 그대로다 — 같은 가격에서 더 일찍 체결될 뿐이다"
    closed = apply_exit_decision(trade, decision=decision, bar=bar(99.5, low=97.0, offset=1), policy=policy)
    planned_risk = (100.0 - 98.0) * trade.quantity
    assert closed.gross_pnl_usdt == pytest.approx(-planned_risk), "계획 −1.000R 을 초과하지 않는다"


def test_intrabar_mode_fills_at_the_open_on_a_gap() -> None:
    """봉이 이미 무효화가를 넘어서 열렸으면 그 가격에 체결될 수 없다.

    시가로 적지 않으면 갭 손실이 원장에서 사라진다 — 실측 SPCXUSDT 1건이 8건 초과분의
    56% 였다. 시가는 봉 시작에 알 수 있으므로 룩어헤드가 아니다.
    """
    policy = PaperPolicy(stop_fill_mode="intrabar")
    trade = _open_long(policy)
    gap_bar = bar(94.0, open_=95.0, high=95.5, low=93.0, offset=1)
    decision = evaluate_exit(trade, bar=gap_bar, stance_state={"stance": "long"}, take_profit_pressure=None, prior_high_pressure_streak=0, policy=policy)
    assert decision.execution_price == pytest.approx(95.0)
    assert decision.execution_price < trade.stop_price, "갭 체결은 무효화가보다 나쁘다 — 그 사실을 지우지 않는다"


def test_intrabar_short_is_symmetric() -> None:
    policy = PaperPolicy(stop_fill_mode="intrabar")
    trade = open_trade(
        trade_id=TRADE_ID,
        symbol="NETUSDT",
        timeframe="4h",
        asset_class="crypto",
        direction=Direction.short,
        bar=bar(100.0),
        invalidation_price=102.0,
        take_profit_price=97.0,
        evidence={},
        checklist={},
        stance_snapshot={},
        signature_snapshot={},
        policy=policy,
    )
    touched = evaluate_exit(
        trade, bar=bar(100.5, high=103.0, offset=1), stance_state={"stance": "short"}, take_profit_pressure=None, prior_high_pressure_streak=0, policy=policy
    )
    assert touched.execution_price == pytest.approx(102.0)
    gapped = evaluate_exit(
        trade,
        bar=bar(106.0, open_=105.0, high=107.0, low=104.0, offset=2),
        stance_state={"stance": "short"},
        take_profit_pressure=None,
        prior_high_pressure_streak=0,
        policy=policy,
    )
    assert gapped.execution_price == pytest.approx(105.0)


# ── 6. 보상 사다리 ──────────────────────────────────────────────────────


def test_structural_extend_only_lengthens_never_shortens() -> None:
    policy = PaperPolicy(reward_mode="structural_extend", max_reward_atr_multiple=3.5)
    atr_value = 100.0
    # 구조 목표가 TP2 안쪽이면 **무시한다** — 당겨오면 추세 구간이 계획에서 빠진다.
    _tp1, tp2, source = _staged_reward(atr_value=atr_value, execution_risk=atr_value, structural_distance=150.0, policy=policy)
    assert (tp2, source) == (200.0, "atr")
    # 바깥이면 상한까지 늘린다.
    _tp1, tp2, source = _staged_reward(atr_value=atr_value, execution_risk=atr_value, structural_distance=300.0, policy=policy)
    assert (tp2, source) == (300.0, "action_plan_extended")
    _tp1, tp2, source = _staged_reward(atr_value=atr_value, execution_risk=atr_value, structural_distance=900.0, policy=policy)
    assert (tp2, source) == (350.0, "reward_ceiling")


def test_risk_multiple_reward_has_no_lower_clamp() -> None:
    """최소 배수를 깔면 RR 이 그 배수에 고정돼 **항등식이 자리를 옮길 뿐**이다."""
    policy = PaperPolicy(reward_mode="risk_multiple", take_profit_2_r_max=3.5)
    _tp1, tp2, source = _staged_reward(atr_value=100.0, execution_risk=50.0, structural_distance=60.0, policy=policy)
    assert (tp2, source) == (60.0, "action_plan")
    # 목표가 TP1 보다 가까우면 TP1 까지만 — 그 셋업은 RR 이 낮게 나오고 게이트가 판정한다.
    _tp1, tp2, source = _staged_reward(atr_value=100.0, execution_risk=50.0, structural_distance=20.0, policy=policy)
    assert (tp2, source) == (50.0, "action_plan_floored_to_tp1")


def test_structural_extend_breaks_the_rr_identity() -> None:
    """같은 ATR·같은 스톱인데 구조 목표에 따라 RR 이 **달라져야** 한다."""
    close = 1_000.0
    candles = [bar(close)]
    plans = [
        _paper_target_plan(
            analysis_with_levels(candles=candles),
            {},
            bar=bar(close),
            direction=Direction.long,
            invalidation_price=close * 0.97,
            action_plan={"take_profit": [{"price": target}]},
            policy=PaperPolicy(reward_mode="structural_extend"),
        )
        for target in (close * 1.01, close * 1.06, close * 1.20)
    ]
    ratios = [plan["rr_ratio"] for plan in plans]
    assert len(set(ratios)) > 1, "구조가 달라도 RR 이 같으면 그것은 판정이 아니라 상수다"
    assert all(plan["risk_distance"] == plans[0]["risk_distance"] for plan in plans), "리스크는 안 바뀌었는데 RR 만 움직여야 한다"


def test_net_rr_basis_uses_its_own_floor() -> None:
    """총 기준 임계를 순 기준에 그대로 쓰면 두 축이 한 숫자에 묶여 따로 풀 수 없다."""
    policy = PaperPolicy(rr_basis="net", min_net_rr=1.2)
    assert evaluate_entry(**_entry_kwargs(rr_ratio=1.25), policy=policy).gates["risk_reward"] is True
    assert evaluate_entry(**_entry_kwargs(rr_ratio=1.15), policy=policy).gates["risk_reward"] is False
    assert PaperPolicy(rr_basis="net").effective_min_net_rr == PaperPolicy().min_rr


# ── 7. 관문이 둘이면 하나는 잊힌다 ───────────────────────────────────────


def test_both_entry_paths_apply_the_same_arithmetic_gates() -> None:
    """정규 경로와 검증 부트스트랩 경로가 **같은** 산술 게이트를 건다.

    품질 임계는 부트스트랩에서 완화돼 있지만 마찰은 완화할 수 있는 종류가 아니다 —
    비용이 1R 을 먹는 자리에서 만든 표본은 시그니처를 채점하지 못하고 계좌만 깎는다.
    """
    source = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "paper" / "service.py").read_text(encoding="utf-8")
    bootstrap_block = source[source.index('"confirmed_stance": True') : source.index('"rank": (checklist_passed')]
    for gate in ("stop_bounds", "cost_efficiency"):
        assert gate in bootstrap_block, f"부트스트랩 경로가 {gate} 를 걸지 않는다"


def test_replay_harness_passes_the_new_gate_inputs() -> None:
    """재판정이 라이브보다 관대해지면 반사실이 아무것도 증명하지 못한다."""
    source = (__import__("pathlib").Path(__file__).resolve().parents[1] / "app" / "validation" / "paper_replay.py").read_text(encoding="utf-8")
    for argument in ("cost_r=", "stop_atr_multiple=", "htf_conflict="):
        assert argument in source, f"하네스가 {argument} 를 넘기지 않는다"
