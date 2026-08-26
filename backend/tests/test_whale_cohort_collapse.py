"""WO-FCE-WHALE-COHORT-COLLAPSE-01 — 추적군 0 진단과 추종 트랙 노출.

이 WO 의 결론은 **무너지지 않았다**는 것이다. 깔때기 4단계가 세는 것이 추적군이 아니라
이번 실행의 신규 선발이고, 코호트 유지가 자리를 잡으면 0 이 되는 것이 설계된 동작이다.
아래 테스트는 그 구분을 고정한다 — 라벨이 다시 뒤섞이면 여기서 걸린다.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.db.models import Direction, PaperTrade
from app.onchain.service import _cohort_block, _flow_breakdown
from app.paper.whale_follow import performance_by_whale, rejection_summary_by_reason

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 26, 0, 0, tzinfo=timezone.utc)

# C2·C4·C5 — 이 WO 가 건드리면 안 되는 것.
UNTOUCHABLE = ("backend/app/paper/policy.py", "backend/app/analyst", "backend/app/structure")


class _Wallet:
    def __init__(self, address: str) -> None:
        self.address = address


class _Event:
    def __init__(self, side: str, event: str, size_usd: float) -> None:
        self.side = side
        self.event = event
        self.size_usd = size_usd


# ── 4-2 · 추적 20 vs 추적군 0 ──────────────────────────────────────────


def test_cohort_counts_tracked_wallets_not_new_selections() -> None:
    """`selected_count` 는 신규 선발이고 추적군이 아니다. 두 숫자는 다른 것을 센다."""
    wallets = [_Wallet(f"0x{index}") for index in range(20)]
    states = [
        {"wallet_address": "0x0", "coin": "BTC", "side": "long", "size_usd": 1_000_000},
        {"wallet_address": "0x1", "coin": "BTC", "side": "short", "size_usd": 500_000},
        {"wallet_address": "0x2", "coin": "ETH", "side": "long", "size_usd": 250_000},
    ]
    block = _cohort_block(None, wallets, states)
    assert block["tracked"] == 20
    assert block["supply_of"] == "whale_follow"
    assert "신규 선발" in block["note"]


def test_cohort_coverage_excludes_untracked_wallets() -> None:
    """추적군 밖 지갑의 포지션이 커버리지에 섞이면 공급원 규모가 부풀려진다."""
    wallets = [_Wallet("0xa")]
    states = [
        {"wallet_address": "0xa", "coin": "BTC", "side": "long", "size_usd": 100.0},
        {"wallet_address": "0xzz", "coin": "BTC", "side": "long", "size_usd": 999_999.0},
    ]
    coverage = _cohort_block(None, wallets, states)["coverage"]
    assert coverage["BTC"]["long_wallets"] == 1
    assert coverage["BTC"]["long_usd"] == 100.0


def test_cohort_coverage_is_not_empty_when_the_cohort_holds_positions() -> None:
    """화면의 '커버리지 0' 은 신규 선발분을 본 값이다 — 추적군은 비어 있지 않았다."""
    wallets = [_Wallet("0xa"), _Wallet("0xb")]
    states = [
        {"wallet_address": "0xa", "coin": "BTC", "side": "long", "size_usd": 10.0},
        {"wallet_address": "0xb", "coin": "ETH", "side": "short", "size_usd": 20.0},
    ]
    coverage = _cohort_block(None, wallets, states)["coverage"]
    assert coverage["BTC"]["long_wallets"] == 1
    assert coverage["ETH"]["short_wallets"] == 1


# ── 4-3 · 고래별 추종 성적 ─────────────────────────────────────────────


def _trade(*, address: str, net: float, status: str, legacy: bool = False, risk: float = 2.5) -> PaperTrade:
    evidence: dict = {"whale_address": address, "sample_size": 38, "win_pct": 65.8, "participant_type": "unclassified"}
    if not legacy:
        evidence["entry_price_source"] = "provider_mark_price"
    return PaperTrade(
        id=uuid4(),
        symbol="BTCUSDT",
        timeframe="4h",
        asset_class="crypto",
        direction=Direction.long,
        entry_bar_at=NOW,
        entry_at=NOW,
        entry_price=100.0,
        margin_usdt=100.0,
        leverage=5.0,
        quantity=1.0,
        remaining_quantity=1.0,
        invalidation_price=97.0,
        take_profit_price=106.0,
        stop_price=97.0,
        status=status,
        net_pnl_usdt=net,
        entry_evidence=evidence,
        target_plan={"sizing": {"planned_risk_usdt": risk}},
        created_at=NOW,
        updated_at=NOW,
    )


def test_per_whale_performance_answers_the_user_question() -> None:
    """ "이 고래를 따라가서 얼마 벌었나" — 지갑을 축으로 센다."""
    rows = performance_by_whale(
        [
            _trade(address="0xa", net=1.0, status="closed"),
            _trade(address="0xa", net=-0.5, status="closed"),
            _trade(address="0xa", net=0.0, status="open"),
            _trade(address="0xb", net=2.0, status="closed"),
        ]
    )
    by_address = {row["address"]: row for row in rows}
    assert by_address["0xa"]["entries"] == 3
    assert by_address["0xa"]["closed"] == 2
    assert by_address["0xa"]["open"] == 1
    assert by_address["0xa"]["net_usdt"] == pytest.approx(0.5)
    assert by_address["0xa"]["profit_factor"] == pytest.approx(2.0)
    assert by_address["0xa"]["follow_win_pct"] == 50.0


def test_profit_factor_is_absent_when_there_is_no_loss() -> None:
    """손실 0건에서 PF 를 내면 표본이 작을 때 거짓 확신이 된다."""
    row = performance_by_whale([_trade(address="0xa", net=1.0, status="closed")])[0]
    assert row["profit_factor"] is None
    assert "PF 를 만들지 않는다" in row["profit_factor_note"]


def test_small_sample_is_labelled_not_asserted() -> None:
    """C6 — 표본 부족을 명시한다."""
    row = performance_by_whale([_trade(address="0xa", net=1.0, status="closed")])[0]
    assert "N<30" in row["sample_note"]


def test_legacy_entries_are_counted_separately() -> None:
    """진입가 결함 기간 표본을 추종 성적으로 읽으면 안 된다."""
    rows = performance_by_whale([_trade(address="0xa", net=1.0, status="closed", legacy=True), _trade(address="0xa", net=1.0, status="closed")])
    row = rows[0]
    assert row["legacy_entries"] == 1
    assert "결함 기간" in row["legacy_note"]


def test_open_positions_do_not_enter_the_profit_factor() -> None:
    """아직 끝나지 않은 거래가 성적이 되면 안 된다."""
    rows = performance_by_whale([_trade(address="0xa", net=-9.0, status="open"), _trade(address="0xa", net=1.0, status="closed")])
    assert rows[0]["net_usdt"] == pytest.approx(1.0)
    assert rows[0]["closed"] == 1


def test_rejection_summary_groups_by_reason_code() -> None:
    """진입 0건일 때 **왜** 0인지가 여기서 갈린다 (4-3 항목 4)."""
    summary = rejection_summary_by_reason(
        [{"reason_code": "latency_exceeded"}, {"reason_code": "latency_exceeded"}, {"reason_code": "price_drift_exceeded"}, {}]
    )
    assert summary["by_reason"]["latency_exceeded"] == 2
    assert summary["by_reason"]["price_drift_exceeded"] == 1
    assert summary["by_reason"]["unknown"] == 1
    assert summary["total"] == 4


# ── 4-4 · 재고/유량 화해 ───────────────────────────────────────────────


def test_flow_breakdown_keeps_the_four_directions_apart() -> None:
    """`숏 감액`과 `롱 증액`을 합산하지 않는다."""
    breakdown = _flow_breakdown(
        [_Event("long", "increase", 100.0), _Event("long", "close", 40.0), _Event("short", "open", 30.0), _Event("short", "reduce", 10.0)],
        current_long=500.0,
        current_short=100.0,
    )
    assert breakdown["long_in_usd"] == 100.0
    assert breakdown["long_out_usd"] == 40.0
    assert breakdown["short_in_usd"] == 30.0
    assert breakdown["short_out_usd"] == 10.0
    assert breakdown["net_usd"] == pytest.approx((100 - 40) - (30 - 10))


def test_opposing_stock_and_flow_are_reconciled_without_causation() -> None:
    """C6 — 관측 서술만. 왜 그러는지는 말하지 않는다."""
    breakdown = _flow_breakdown([_Event("long", "close", 900.0), _Event("short", "open", 10.0)], current_long=5_000.0, current_short=100.0)
    assert breakdown["stock_net_usd"] > 0
    assert breakdown["net_usd"] < 0
    assert "재고는 순롱" in breakdown["reconciliation"]
    assert "이 표가 정하지 않는다" in breakdown["reconciliation"]
    for causal in ("때문에", "이므로 곧", "예상된다"):
        assert causal not in breakdown["reconciliation"]


def test_aligned_stock_and_flow_say_so() -> None:
    breakdown = _flow_breakdown([_Event("long", "increase", 100.0)], current_long=500.0, current_short=100.0)
    assert "방향이 같다" in breakdown["reconciliation"]


def test_no_events_is_stated_not_guessed() -> None:
    breakdown = _flow_breakdown([], current_long=500.0, current_short=100.0)
    assert breakdown["unwinding_share_pct"] is None
    assert "체결이 없다" in breakdown["reconciliation"]


# ── 제약 증명 ──────────────────────────────────────────────────────────


def test_promotion_thresholds_are_unchanged() -> None:
    """C2 — 승격 기준 diff 0줄."""
    source = (REPO_ROOT / "backend/app/onchain/service.py").read_text(encoding="utf-8")
    assert "sample_size < 30 or ci_low is None or ci_low < 55.0" in source


def test_follow_eligibility_thresholds_are_unchanged() -> None:
    """C1 — 0 을 채우려 자격을 낮추지 않는다."""
    from app.onchain import follow_eligibility

    assert follow_eligibility.FOLLOW_MIN_SAMPLE == 30
    assert follow_eligibility.FOLLOW_MIN_WIN_PCT == 55.0


def test_cohort_retention_stays_enabled() -> None:
    """C3 — 끄면 표본이 다시 리셋된다."""
    from app.core.config import get_settings

    assert get_settings().hyperliquid_whale_cohort_retention_enabled is True


def test_sizing_and_direction_layers_are_untouched() -> None:
    """C4·C5 — 사이징·잠금·출구·판정 로직 diff 0줄."""
    diff = subprocess.run(["git", "diff", "origin/main", "--stat", "--", *UNTOUCHABLE], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")
    assert diff.stdout.strip() == "", f"C4·C5 위반:\n{diff.stdout}"
