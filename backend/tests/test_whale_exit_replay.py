"""WO-FCE-WHALE-EXIT-REPLAY-01 — 출구 A/B 반사실 계약.

**출구 B 는 계산이지 포지션이 아니다**(C1). 공식 표본은 A 하나다(C2). 아래 테스트는 그
경계가 무너지는 경로를 막는다.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.paper import whale_exit_replay as wer

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 29, 0, 0, tzinfo=timezone.utc)

# C3·C4·C5·C10 — 이 WO 가 건드리면 안 되는 것.
UNTOUCHABLE = ("backend/app/analyst", "backend/app/structure", "backend/app/paper/policy.py", "backend/app/paper/whale_follow.py")


def _trade(**overrides):
    base = {
        "id": "t1",
        "symbol": "BTCUSDT",
        "direction": "long",
        "whale_address": "0xa",
        "net_pnl_usdt": -1.0,
        "exit_at": NOW,
        "exit_reason": "time_decay",
        "entry_price": 100.0,
        "quantity": 1.0,
    }
    return {**base, **overrides}


def _exit(minutes: int = 60, price: float = 105.0, kind: str = "close") -> wer.WhaleExit:
    return wer.WhaleExit(at=NOW + timedelta(minutes=minutes), price=price, kind=kind, size_usd=1000.0)


# ── C1 · 반사실이지 포지션이 아니다 ────────────────────────────────────


def test_module_never_writes_a_position() -> None:
    """출구 B 로 실제 포지션을 열면 리스크가 두 배가 되고 표본도 이중 계상된다."""
    source = (REPO_ROOT / "backend/app/paper/whale_exit_replay.py").read_text(encoding="utf-8")
    for mutation in ("upsert_whale_follow_trade", "upsert_paper_trade", "open_trade(", "apply_exit_decision("):
        assert mutation not in source, f"반사실 모듈이 원장을 쓴다: {mutation}"


def test_counterfactual_uses_the_same_cost_model_as_exit_a() -> None:
    """비용이 다르면 A/B 차이가 출구 차이인지 비용 차이인지 갈리지 않는다."""
    source = (REPO_ROOT / "backend/app/paper/whale_exit_replay.py").read_text(encoding="utf-8")
    assert "policy_from_settings(settings" in source
    assert "execution_cost_rate" in source


def test_long_and_short_counterfactuals_have_opposite_signs() -> None:
    profit = wer.counterfactual_pnl(direction="long", entry_price=100.0, quantity=1.0, exit_price=110.0, cost_rate=0.0, entry_cost=0.0)
    loss = wer.counterfactual_pnl(direction="short", entry_price=100.0, quantity=1.0, exit_price=110.0, cost_rate=0.0, entry_cost=0.0)
    assert profit == pytest.approx(10.0)
    assert loss == pytest.approx(-10.0)


def test_costs_are_subtracted_not_added() -> None:
    gross = wer.counterfactual_pnl(direction="long", entry_price=100.0, quantity=1.0, exit_price=110.0, cost_rate=0.0, entry_cost=0.0)
    net = wer.counterfactual_pnl(direction="long", entry_price=100.0, quantity=1.0, exit_price=110.0, cost_rate=0.001, entry_cost=0.1)
    assert net < gross


# ── C2 · 공식 표본은 A 하나다 ──────────────────────────────────────────


def test_summary_marks_exit_b_as_not_official() -> None:
    summary = wer.summarize([{"exit_a_net": -1.0, "exit_b_net": 2.0, "lead": wer.LEAD_OURS, "whale_address": "0xa"}])
    assert summary["official_sample"] == "exit_a"
    assert "합산하지 않는다" in summary["not_official"]


def test_official_track_sample_excludes_exit_b() -> None:
    """`whale_follow` 트랙 판정이 B 를 세면 표본이 두 배가 된다."""
    from app.validation import sample_viability

    spec = sample_viability.TRACK_SAMPLE_SPECS["whale_follow"]
    assert "exit_b" not in spec.scored_sql
    assert "whale_exit_replay" not in spec.scored_sql


# ── 2-1 · 고래 청산 매칭 ───────────────────────────────────────────────


def test_only_reduce_and_close_count_as_whale_exits() -> None:
    """증액은 진입 신호다. 청산으로 세면 방향이 뒤집힌다."""
    assert wer.EXIT_EVENTS == ("reduce", "close")
    rows = [{"event_at": NOW + timedelta(minutes=5), "event_type": "increase", "price": 1.0, "size_usd": 1.0}]
    assert wer.match_whale_exit(rows, after=NOW) is None


def test_earliest_exit_after_entry_is_matched() -> None:
    rows = [
        {"event_at": NOW + timedelta(minutes=30), "event_type": "close", "price": 3.0, "size_usd": 1.0},
        {"event_at": NOW + timedelta(minutes=10), "event_type": "reduce", "price": 2.0, "size_usd": 1.0},
        {"event_at": NOW - timedelta(minutes=10), "event_type": "close", "price": 1.0, "size_usd": 1.0},
    ]
    matched = wer.match_whale_exit(rows, after=NOW)
    assert matched is not None and matched.price == 2.0, "진입 이전 체결을 잡았거나 가장 이른 것을 놓쳤다"


def test_partial_and_full_exits_are_distinguished() -> None:
    assert wer.WhaleExit(at=NOW, price=1.0, kind="close", size_usd=1.0).full_exit is True
    assert wer.WhaleExit(at=NOW, price=1.0, kind="reduce", size_usd=1.0).full_exit is False


# ── 2-2 · 차이 원인 분해 ───────────────────────────────────────────────


def test_lead_is_attributed_to_whoever_exited_first() -> None:
    ours = wer.compare_trade(_trade(exit_at=NOW), _exit(minutes=60), cost_rate=0.0)
    whale = wer.compare_trade(_trade(exit_at=NOW + timedelta(minutes=120)), _exit(minutes=60), cost_rate=0.0)
    assert ours["lead"] == wer.LEAD_OURS
    assert whale["lead"] == wer.LEAD_WHALE


def test_open_whale_position_makes_no_counterfactual() -> None:
    """고래가 아직 안 닫았으면 추정치를 적지 않는다."""
    row = wer.compare_trade(_trade(), None, cost_rate=0.0)
    assert row["exit_b_net"] is None
    assert row["lead"] == wer.LEAD_NONE
    assert "청산하지 않았다" in row["note"]


def test_missing_whale_price_makes_no_counterfactual() -> None:
    row = wer.compare_trade(_trade(), _exit(price=None), cost_rate=0.0)
    assert row["exit_b_net"] is None
    assert "체결가를 읽을 수 없다" in row["note"]
    # 시각은 알므로 선행 판정은 남는다.
    assert row["lead"] in {wer.LEAD_OURS, wer.LEAD_WHALE}


def test_incomparable_trades_are_excluded_from_the_summary() -> None:
    summary = wer.summarize(
        [
            {"exit_a_net": -1.0, "exit_b_net": 2.0, "lead": wer.LEAD_OURS, "whale_address": "0xa"},
            {"exit_a_net": -1.0, "exit_b_net": None, "lead": wer.LEAD_NONE, "whale_address": "0xa"},
        ]
    )
    assert summary["comparable"] == 1
    assert summary["total"] == 2
    assert summary["overall"]["count"] == 1


def test_profit_factor_is_absent_without_losses() -> None:
    summary = wer.summarize([{"exit_a_net": 1.0, "exit_b_net": 2.0, "lead": wer.LEAD_OURS, "whale_address": "0xa"}])
    assert summary["overall"]["a_profit_factor"] is None


# ── 판정 ───────────────────────────────────────────────────────────────


def _summary(delta: float, count: int = 40):
    return {"overall": {"count": count, "delta_net": delta}}


def test_verdict_splits_three_ways() -> None:
    assert wer.verdict(_summary(5.0))["verdict"] == "EXIT_B_BETTER"
    assert wer.verdict(_summary(-5.0))["verdict"] == "EXIT_A_BETTER"
    assert wer.verdict(_summary(0.0))["verdict"] == "NO_DIFFERENCE"


def test_small_sample_refuses_to_decide() -> None:
    """C11 — 부족한 표본으로 방향을 정하면 다음 WO 전체가 그 위에 얹힌다."""
    result = wer.verdict(_summary(5.0, count=10))
    assert result["verdict"] == "INSUFFICIENT_SAMPLE"
    assert result["actionable"] is False


def test_exit_a_defect_blocks_the_switch_recommendation() -> None:
    """**이 WO 의 핵심 안전장치.**

    결함이 있으면 A/B 는 "우리 사다리 대 고래 청산"이 아니라 "버그 있는 사다리 대 고래
    청산"이다. 그 구분 없이 전환하면 버그를 설계로 굳힌다.
    """
    inflation = {"detected": True, "count": 36}
    result = wer.verdict(_summary(12.4), inflation=inflation)
    assert result["verdict"] == "EXIT_B_BETTER"
    assert result["actionable"] is False, "결함이 있는데 전환을 권고했다"
    assert "버그 있는 사다리" in result["caveat"]


def test_clean_comparison_stays_actionable() -> None:
    result = wer.verdict(_summary(12.4), inflation={"detected": False, "count": 0})
    assert result["actionable"] is True
    assert "caveat" not in result


# ── 출구 A 결함 탐지 ───────────────────────────────────────────────────


class _T:
    def __init__(self, bars: int, hours: float) -> None:
        self.id = "x"
        self.symbol = "BTCUSDT"
        self.holding_bars = bars
        self.entry_at = NOW
        self.exit_at = NOW + timedelta(hours=hours)
        self.exit_reason = "time_decay"


def test_inflation_is_detected_when_elapsed_time_contradicts_bars() -> None:
    """실측: 보유봉 30(=120시간)인데 실경과 0.51시간."""
    result = wer.detect_holding_bar_inflation([_T(30, 0.51)])
    assert result["detected"] is True
    assert result["sample"][0]["implied_hours"] == 120.0


def test_honest_holding_bars_are_not_flagged() -> None:
    assert wer.detect_holding_bar_inflation([_T(3, 12.0)])["detected"] is False


def test_detector_names_the_mechanism_not_just_the_symptom() -> None:
    result = wer.detect_holding_bar_inflation([_T(30, 0.5)])
    assert "진입봉만 비교" in result["mechanism"]
    assert "C4" in result["not_fixed_here"]


# ── 제약 증명 ──────────────────────────────────────────────────────────


def test_entry_and_exit_a_logic_are_untouched() -> None:
    """C3·C4·C5 — 진입·출구 A·방향 판정 diff 0줄."""
    diff = subprocess.run(["git", "diff", "origin/main", "--stat", "--", *UNTOUCHABLE], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")
    assert diff.stdout.strip() == "", f"C3·C4·C5 위반:\n{diff.stdout}"


def test_no_stance_is_wired_into_the_replay() -> None:
    """C5 — 방향 판단이 뒷문으로 들어오면 안 된다."""
    source = (REPO_ROOT / "backend/app/paper/whale_exit_replay.py").read_text(encoding="utf-8")
    for leak in ("stance_state", "confluence", "signature_gate"):
        assert leak not in source


def test_replay_makes_no_network_call() -> None:
    """C9 — 임계 경로에 네트워크를 추가하지 않는다. 저장된 체결만 읽는다."""
    source = (REPO_ROOT / "backend/app/paper/whale_exit_replay.py").read_text(encoding="utf-8")
    for network in ("httpx", "requests", "urlopen", "HyperliquidInfoClient"):
        assert network not in source
