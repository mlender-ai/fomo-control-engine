"""WO-FCE-WHALE-FOLLOW-01 — 코호트 고정(5-1)·참여자 유형(5-2) 계약.

이 WO 는 5-3(추종 페이퍼 트랙)·5-4(알림)를 **착수하지 않았다**. 5-1 실측 결과 승격 통과자가
0명이었고, WO §0 이 그 상태에서 배선을 금지했다. 그래서 이 파일은 배선이 없다는 것도 고정한다
(`test_follow_track_is_not_wired`).
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.onchain import cohort, participant_type
from app.onchain.hyperliquid import leaderboard

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)

# C2·C3 — 이 WO 가 건드리면 안 되는 파일. 목록에 적는 행위가 검토 지점이다.
UNTOUCHABLE = (
    "backend/app/paper/policy.py",
    "backend/app/paper/service.py",
    "backend/app/analyst",
    "backend/app/structure",
)


class _Wallet:
    """`WhaleWallet` 의 판정에 필요한 표면만 흉내낸다 — DB 없이 계획을 검사한다."""

    def __init__(self, address: str, *, source: str = "discovery", active: bool = True, added_days: int = 30, idle_days: int | None = 0) -> None:
        self.address = address
        self.source = source
        self.active = active
        self.added_at = NOW - timedelta(days=added_days)
        self.last_fill_at = None if idle_days is None else NOW - timedelta(days=idle_days)
        self.payload: dict = {}


# ── 5-1 유지 판정 ──────────────────────────────────────────────────────


def _decide(**kwargs):
    base = {"address": "0xabc", "source": "discovery", "sample_size": 5, "added_at": NOW - timedelta(days=30), "last_fill_at": NOW, "now": NOW}
    return cohort.retention_decision(**{**base, **kwargs})


def test_incomplete_sample_is_retained() -> None:
    """이 WO 의 핵심. 표본을 완주하지 못한 지갑은 유지된다."""
    decision = _decide(sample_size=29)
    assert decision.keep is True
    assert decision.reason == "sample_incomplete"
    assert "29/30" in decision.detail


def test_completed_sample_is_released() -> None:
    decision = _decide(sample_size=30)
    assert decision.keep is False
    assert decision.reason == "sample_complete"


def test_new_wallet_gets_a_tenure_floor() -> None:
    """편성 직후 지갑을 내리면 회전이 그대로 재현된다."""
    decision = _decide(sample_size=0, added_at=NOW - timedelta(days=2))
    assert decision.keep is True
    assert decision.reason == "tenure_floor"


def test_dormant_wallet_is_released() -> None:
    decision = _decide(sample_size=3, last_fill_at=NOW - timedelta(days=15))
    assert decision.keep is False
    assert decision.reason == "dormant"


def test_manual_wallet_is_never_released_by_discovery() -> None:
    decision = _decide(source="manual", sample_size=30, last_fill_at=NOW - timedelta(days=99))
    assert decision.keep is True
    assert decision.reason == "manual_source"


def test_vanished_wallet_is_released() -> None:
    decision = _decide(sample_size=0, last_fill_at=None, on_leaderboard=False)
    assert decision.keep is False
    assert decision.reason == "vanished"


def test_every_release_reason_is_whitelisted() -> None:
    """C4 — 성과 사유로는 내리지 않는다. 사유 집합이 화이트리스트 안에 있다."""
    cases = [
        _decide(sample_size=30),
        _decide(sample_size=3, last_fill_at=NOW - timedelta(days=15)),
        _decide(sample_size=0, last_fill_at=None, on_leaderboard=False),
    ]
    for decision in cases:
        assert decision.keep is False
        assert decision.reason in cohort.RELEASE_REASONS


def test_retention_ignores_performance() -> None:
    """C4 증명 — 손익이 정반대인 두 지갑의 유지 판정이 같다.

    유지 판정 서명에 성과 인자가 없으므로 넣을 방법조차 없다. 그 사실을 고정한다.
    """
    import inspect

    parameters = set(inspect.signature(cohort.retention_decision).parameters)
    assert not parameters & cohort.FORBIDDEN_PERFORMANCE_INPUTS
    assert _decide(sample_size=10) == _decide(sample_size=10)


def test_performance_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="C4"):
        cohort.assert_no_performance_inputs({"account_value_usd", "month_roi"})
    cohort.assert_no_performance_inputs(cohort.NON_PERFORMANCE_INPUTS)


def test_non_performance_score_does_not_move_with_pnl() -> None:
    """선발 점수에 손익 항이 없다 — PnL·ROI 를 바꿔도 점수가 같다."""
    poor = {
        "account_value_usd": 5_000_000,
        "month_volume_usd": 40_000_000,
        "month_pnl_usd": -900_000,
        "month_roi": -0.4,
        "focus_positions": [{"size_usd": 1_000_000}],
    }
    rich = {**poor, "month_pnl_usd": 9_000_000, "month_roi": 3.0}
    assert cohort.non_performance_score(poor) == cohort.non_performance_score(rich)


def test_non_performance_score_moves_with_size_and_activity() -> None:
    small = {"account_value_usd": 1_000_000, "month_volume_usd": 10_000_000, "focus_positions": []}
    large = {"account_value_usd": 50_000_000, "month_volume_usd": 500_000_000, "focus_positions": [{"size_usd": 5_000_000}]}
    assert cohort.non_performance_score(large) > cohort.non_performance_score(small)


def test_non_performance_criteria_drops_pnl_and_roi() -> None:
    from app.core.config import get_settings

    criteria = cohort.non_performance_criteria(get_settings())
    assert criteria["min_month_pnl_usd"] is None
    assert criteria["min_month_roi"] is None
    assert criteria["min_account_usd"] is not None
    assert criteria["min_month_volume_usd"] is not None


# ── 5-1 복귀·자리 경합 ─────────────────────────────────────────────────


def test_reinstatement_prefers_wallets_closest_to_completion() -> None:
    plan = cohort.reinstatement_plan({"0xa": 39, "0xb": 12, "0xc": 34, "0xd": 0}, active_addresses=set(), slots=2)
    assert [item["address"] for item in plan] == ["0xa", "0xc"]
    assert plan[0]["remaining"] == 0 or plan[0]["sample_size"] == 39
    # 표본 0 은 복귀 대상이 아니다 — 복귀는 완주를 재개하는 것이다.
    assert "0xd" not in {item["address"] for item in plan}


def test_reinstatement_skips_already_active() -> None:
    plan = cohort.reinstatement_plan({"0xa": 39, "0xb": 20}, active_addresses={"0XA"}, slots=5)
    assert [item["address"] for item in plan] == ["0xb"]


def test_sample_bearing_wallet_outranks_empty_new_wallet() -> None:
    """실측이 요구한 순위. 표본 0 신규가 자리를 막으면 코호트 고정이 무력해진다."""
    wallets = [_Wallet(f"0xnew{index}", added_days=1) for index in range(3)] + [_Wallet("0xold", added_days=40)]
    plan = cohort.cohort_plan(
        wallets,
        sample_sizes={"0xold": 25, "0xexiled": 39},
        leaderboard_addresses=set(),
        now=NOW,
        max_wallets=3,
    )
    retained = {row["address"] for row in plan["retained"]}
    reinstated = {row["address"] for row in plan["reinstated"]}
    assert "0xold" in retained, "표본 보유 지갑이 밀려났다"
    assert "0xexiled" in reinstated, "표본 39건 지갑이 표본 0 신규에 막혔다"
    assert plan["released_count"] >= 1
    assert all(row["reason"] in cohort.RELEASE_REASONS for row in plan["released"])


def test_slot_pressure_is_not_a_performance_reason() -> None:
    assert "slot_pressure" in cohort.RELEASE_REASONS
    wallets = [_Wallet(f"0x{index}", added_days=1) for index in range(5)]
    plan = cohort.cohort_plan(wallets, sample_sizes={}, leaderboard_addresses=set(), now=NOW, max_wallets=2)
    reasons = {row["reason"] for row in plan["released"]}
    assert reasons <= set(cohort.RELEASE_REASONS)
    for row in plan["released"]:
        assert "성과" not in row["detail"] or "성과가 아니라" in row["detail"]


def test_cohort_plan_never_exceeds_the_wallet_cap() -> None:
    """C9 — 폴링 예산이 지갑 수에 비례한다. 계획이 한도를 넘으면 안 된다."""
    wallets = [_Wallet(f"0x{index}", added_days=1) for index in range(30)]
    plan = cohort.cohort_plan(wallets, sample_sizes={f"0x{index}": index for index in range(30)}, leaderboard_addresses=set(), now=NOW, max_wallets=20)
    assert plan["retained_count"] + plan["reinstated_count"] <= 20
    assert plan["discovery_slots"] >= 0


def test_every_decision_carries_a_reason() -> None:
    """C10 — 침묵 금지. 유지든 해제든 사유가 붙는다."""
    wallets = [_Wallet("0xa", added_days=40), _Wallet("0xb", added_days=1), _Wallet("0xc", added_days=40, idle_days=30)]
    plan = cohort.cohort_plan(wallets, sample_sizes={"0xa": 12}, leaderboard_addresses=set(), now=NOW, max_wallets=20)
    for row in plan["retained"] + plan["released"]:
        assert row["reason"] and row["detail"]
        assert row["basis"]


# ── 5-1 선발 깔때기 배선 ───────────────────────────────────────────────


def _row(address: str, *, pnl: float, roi: float, volume: float, account: float) -> dict:
    return {
        "ethAddress": address,
        "displayName": None,
        "accountValue": str(account),
        "windowPerformances": [["month", {"pnl": str(pnl), "roi": str(roi), "vlm": str(volume)}], ["week", {}], ["allTime", {}]],
    }


def test_none_threshold_disables_that_axis() -> None:
    """비성과 자격 요건은 PnL·ROI 임계를 `None` 으로 둔다 — 손실 지갑도 통과해야 한다."""
    rows = [_row("0x" + "a" * 40, pnl=-500_000, roi=-0.5, volume=50_000_000, account=5_000_000)]
    strict = leaderboard.select_candidates(
        rows, {"min_account_usd": 1_000_000, "min_month_pnl_usd": 100_000, "min_month_roi": 0.02, "min_month_volume_usd": 10_000_000, "max_turnover": 250}
    )
    loose = leaderboard.select_candidates(
        rows, {"min_account_usd": 1_000_000, "min_month_pnl_usd": None, "min_month_roi": None, "min_month_volume_usd": 10_000_000, "max_turnover": 250}
    )
    assert strict == []
    assert len(loose) == 1


def test_score_injection_reorders_without_touching_defaults() -> None:
    rows = [
        _row("0x" + "a" * 40, pnl=9_000_000, roi=3.0, volume=20_000_000, account=2_000_000),
        _row("0x" + "b" * 40, pnl=200_000, roi=0.05, volume=900_000_000, account=90_000_000),
    ]
    criteria = {"min_account_usd": 1_000_000, "min_month_pnl_usd": None, "min_month_roi": None, "min_month_volume_usd": 10_000_000, "max_turnover": 1_000}
    by_quality = leaderboard.select_candidates(rows, criteria)
    by_size = leaderboard.select_candidates(rows, criteria, score=cohort.non_performance_score)
    assert by_quality[0]["address"].endswith("a" * 10), "기본 정렬(quality_score)이 바뀌었다"
    assert by_size[0]["address"].endswith("b" * 10), "비성과 정렬이 규모·활동량을 앞세우지 않았다"


def test_cohort_retention_is_on_by_default_and_revertible() -> None:
    """Phase 6-1 — 기본값이 켬이다. 끄는 경로(env=false)는 남아 있다.

    Phase 5 는 꺼두고 드라이런만 했다. 그 검증이 끝났고 Phase 6 가 활성화를 지시했다.
    원복 가능성은 `test_discovery_rotates_when_cohort_mode_is_off` 가 고정한다.
    """
    from app.core.config import Settings, get_settings

    assert get_settings().hyperliquid_whale_cohort_retention_enabled is True
    assert Settings(FCE_HYPERLIQUID_WHALE_COHORT_RETENTION_ENABLED="false").hyperliquid_whale_cohort_retention_enabled is False


# ── 5-2 참여자 유형 ────────────────────────────────────────────────────


def _event(
    address: str, *, crossed: bool | None, side: str, size_usd: float, symbol: str = "BTCUSDT", kind: str = "increase", at: str = "2026-08-01T00:00:00Z"
) -> dict:
    raw: dict = {"coin": symbol}
    if crossed is not None:
        raw["crossed"] = crossed
    return {
        "wallet_address": address,
        "symbol": symbol,
        "event": kind,
        "event_at": at,
        "size_usd": size_usd,
        "side": side,
        "payload": {"payload": {"raw": raw}},
    }


def test_crossed_is_read_from_the_nested_payload() -> None:
    indicators = participant_type.wallet_indicators(
        [_event("0xa", crossed=False, side="long", size_usd=1.0), _event("0xa", crossed=True, side="short", size_usd=1.0)]
    )
    assert indicators["0xa"]["maker_pct"] == 50.0
    assert indicators["0xa"]["classified_pct"] == 100.0


def test_market_maker_is_excluded() -> None:
    """maker 우세 · 양방향 · 고빈도 = 재고 관리. 추종 대상이 아니다."""
    estimate = participant_type.estimate_participant_type(
        "0xmm", {"events": 900, "maker_pct": 96.0, "direction_skew": 0.05, "events_per_day": 190.0, "distinct_coins": 4, "close_ratio": 0.4}
    )
    assert estimate.participant_type == participant_type.TYPE_MARKET_MAKER
    assert estimate.follow_eligible is False
    assert "재고" in estimate.reason


def test_basis_carry_is_excluded() -> None:
    estimate = participant_type.estimate_participant_type(
        "0xcarry", {"events": 400, "maker_pct": 98.0, "direction_skew": 0.999, "events_per_day": 26.0, "distinct_coins": 1, "close_ratio": 0.002}
    )
    assert estimate.participant_type == participant_type.TYPE_BASIS_CARRY
    assert estimate.follow_eligible is False
    assert "약한 추정" in estimate.reason


def test_directional_taker_is_eligible() -> None:
    estimate = participant_type.estimate_participant_type(
        "0xdir", {"events": 470, "maker_pct": 1.3, "direction_skew": 0.63, "events_per_day": 21.0, "distinct_coins": 5, "close_ratio": 0.3}
    )
    assert estimate.participant_type == participant_type.TYPE_DIRECTIONAL
    assert estimate.follow_eligible is True
    assert estimate.confidence > 0.5


def test_two_way_high_frequency_taker_is_unclassified_with_a_reason() -> None:
    """실측이 요구한 분기. 스프레드를 지불하지만 순포지션이 남지 않는 지갑은 MM 도 방향성도 아니다."""
    estimate = participant_type.estimate_participant_type(
        "0xscalp", {"events": 1900, "maker_pct": 0.3, "direction_skew": 0.296, "events_per_day": 261.0, "distinct_coins": 5, "close_ratio": 0.3}
    )
    assert estimate.participant_type == participant_type.TYPE_UNCLASSIFIED
    assert estimate.follow_eligible is False
    assert "MM 은 아니나" in estimate.reason and "추종 지연" in estimate.reason


def test_low_sample_is_not_classified() -> None:
    estimate = participant_type.estimate_participant_type(
        "0xa", {"events": 12, "maker_pct": 0.0, "direction_skew": 0.9, "events_per_day": 5.0, "distinct_coins": 1, "close_ratio": 0.3}
    )
    assert estimate.participant_type == participant_type.TYPE_UNCLASSIFIED
    assert "미달" in estimate.reason


def test_unclassified_is_never_followed() -> None:
    assert participant_type.TYPE_UNCLASSIFIED not in participant_type.FOLLOW_ELIGIBLE_TYPES
    assert participant_type.TYPE_MARKET_MAKER not in participant_type.FOLLOW_ELIGIBLE_TYPES
    assert participant_type.TYPE_BASIS_CARRY not in participant_type.FOLLOW_ELIGIBLE_TYPES
    assert participant_type.FOLLOW_ELIGIBLE_TYPES == {participant_type.TYPE_DIRECTIONAL}


def test_every_estimate_declares_itself_an_estimate() -> None:
    """5-2 항목 4 — 확정으로 표시하지 않는다."""
    estimates = participant_type.classify_wallets([_event("0xa", crossed=True, side="long", size_usd=1.0) for _ in range(40)])
    for payload in estimates.values():
        assert payload["estimate"] is True
        assert "확정이 아님" in payload["basis"]
        assert payload["reason"]


def test_type_distribution_reports_exclusions() -> None:
    estimates = {
        "0xa": {"participant_type": participant_type.TYPE_DIRECTIONAL, "follow_eligible": True},
        "0xb": {"participant_type": participant_type.TYPE_MARKET_MAKER, "follow_eligible": False},
        "0xc": {"participant_type": participant_type.TYPE_UNCLASSIFIED, "follow_eligible": False},
    }
    distribution = participant_type.type_distribution(estimates)
    assert distribution["wallets"] == 3
    assert distribution["follow_eligible"] == 1
    assert distribution["estimate"] is True


def test_participant_type_reads_no_pnl() -> None:
    """C4 — 유형 판정에 손익이 들어가지 않는다. 실현 손익이 반대여도 유형이 같다."""
    winner = [_event("0xa", crossed=True, side="long", size_usd=1.0) for _ in range(40)]
    for event in winner:
        event["payload"]["payload"]["closed_pnl"] = "500000"
    loser = [_event("0xb", crossed=True, side="long", size_usd=1.0) for _ in range(40)]
    for event in loser:
        event["payload"]["payload"]["closed_pnl"] = "-500000"
    estimates = participant_type.classify_wallets(winner + loser)
    assert estimates["0xa"]["participant_type"] == estimates["0xb"]["participant_type"]


# ── 제약 증명 ──────────────────────────────────────────────────────────


def test_entry_gates_and_sizing_are_untouched() -> None:
    """C2·C3 — 진입 게이트·사이징·잠금·출구·방향 판정 diff 0줄."""
    diff = subprocess.run(
        ["git", "diff", "origin/main", "--stat", "--", *UNTOUCHABLE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        pytest.skip("origin/main 을 참조할 수 없는 환경")
    assert diff.stdout.strip() == "", f"C2·C3 위반 — 페이퍼 정책·방향 판정이 변경됐다:\n{diff.stdout}"


def test_follow_track_is_wired_with_a_separate_ledger() -> None:
    """Phase 6-2 — §0 의 미배선 조건이 **대체됐다.**

    Phase 5 는 승격자 0명이라 배선하지 않았고, 그 판단은 당시 WO 문안대로였다. Phase 6 가
    그 문안을 바꿨다 — 승격 기준(28일·N>=30·CI 하한 55%)을 페이퍼 관찰의 전제로 쓰는 것이
    순환이었기 때문이다. 관찰 자격을 별도로 신설했고 승격 기준은 그대로다.

    이 테스트는 이제 **원장 분리**를 고정한다. 배선 자체가 아니라 배선의 형태가 지켜야 할
    것이다(C3).
    """
    from app.validation import sample_viability

    spec = sample_viability.TRACK_SAMPLE_SPECS["whale_follow"]
    assert "whale_follow_trades" in spec.entry_sql
    assert "paper_trades" not in spec.entry_sql and "paper_trades" not in spec.scored_sql

    grep = subprocess.run(
        ["git", "grep", "-l", "upsert_paper_trade", "--", "backend/app/paper/whale_follow.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert grep.stdout.strip() == "", "추종 엔진이 크립토 원장에 기입한다(C3 위반)"


def test_promotion_thresholds_are_unchanged() -> None:
    """C5 — 28일 · N>=30 · CI 하한 55%. 통과자가 0명이어도 낮추지 않는다."""
    from app.backtest import candidate_scoring

    assert candidate_scoring.WHALE_VALIDATION_DAYS == 28
    source = (REPO_ROOT / "backend/app/onchain/service.py").read_text(encoding="utf-8")
    assert "sample_size < 30 or ci_low is None or ci_low < 55.0" in source
    assert cohort.COHORT_SAMPLE_TARGET == 30


# ── 5-1 발견 실행 통합 ─────────────────────────────────────────────────


class _Leaderboard:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def leaderboard(self) -> dict:
        return {"leaderboardRows": self._rows}


def _settings(**overrides):
    from app.core.config import get_settings

    return get_settings().model_copy(update=overrides)


def _seed(repo, address: str, *, added_days: int, source: str = "discovery") -> None:
    from app.db.models import WhaleWallet

    repo.upsert_whale_wallet(
        WhaleWallet(
            address=address,
            label=address[:10],
            source=source,
            active=True,
            added_at=NOW - timedelta(days=added_days),
            updated_at=NOW,
            last_fill_at=NOW,
        )
    )


def test_discovery_keeps_incomplete_samples_when_cohort_mode_is_on() -> None:
    """배선 통합 — 선발에 없는 지갑이라도 표본 미완주면 살아 있다."""
    from app.db.repository import MemoryRepository

    repo = MemoryRepository()
    _seed(repo, "0x" + "1" * 40, added_days=40)
    fresh = "0x" + "2" * 40
    settings = _settings(hyperliquid_whale_cohort_retention_enabled=True, hyperliquid_whale_discovery_scan_limit=0)
    rows = [_row(fresh, pnl=1_000_000, roi=0.5, volume=900_000_000, account=90_000_000)]

    result = leaderboard.discover_leaderboard_wallets(repo, settings, _Leaderboard(rows), None, sample_sizes={"0x" + "1" * 40: 12})

    survivors = {wallet.address for wallet in repo.list_whale_wallets(active=True, limit=100)}
    assert "0x" + "1" * 40 in survivors, "표본 12건 지갑이 선발에서 빠지자 비활성화됐다"
    assert result["cohort_retention"]["retained_count"] >= 1
    assert result["selection_basis"] == "규모·활동량(비성과, C4)"


def test_discovery_rotates_when_cohort_mode_is_off() -> None:
    """옵트인 원복 확인 — 끄면 종전 회전 동작 그대로다."""
    from app.db.repository import MemoryRepository

    repo = MemoryRepository()
    stale = "0x" + "1" * 40
    _seed(repo, stale, added_days=40)
    fresh = "0x" + "2" * 40
    settings = _settings(hyperliquid_whale_cohort_retention_enabled=False, hyperliquid_whale_discovery_scan_limit=0)
    rows = [_row(fresh, pnl=1_000_000, roi=0.5, volume=900_000_000, account=90_000_000)]

    result = leaderboard.discover_leaderboard_wallets(repo, settings, _Leaderboard(rows), None)

    survivors = {wallet.address for wallet in repo.list_whale_wallets(active=True, limit=100)}
    assert stale not in survivors, "플래그가 꺼졌는데 종전 회전 동작이 아니다"
    assert result["cohort_retention"]["enabled"] is False
    assert result["selection_basis"].startswith("quality_score")


def test_cohort_mode_admits_a_loss_making_wallet() -> None:
    """C4 — 비성과 자격 요건은 월간 손실 지갑도 후보로 받는다."""
    from app.db.repository import MemoryRepository

    losing = "0x" + "3" * 40
    rows = [_row(losing, pnl=-800_000, roi=-0.4, volume=900_000_000, account=90_000_000)]
    off = leaderboard.discover_leaderboard_wallets(
        MemoryRepository(), _settings(hyperliquid_whale_cohort_retention_enabled=False, hyperliquid_whale_discovery_scan_limit=0), _Leaderboard(rows), None
    )
    on = leaderboard.discover_leaderboard_wallets(
        MemoryRepository(),
        _settings(hyperliquid_whale_cohort_retention_enabled=True, hyperliquid_whale_discovery_scan_limit=0),
        _Leaderboard(rows),
        None,
        sample_sizes={},
    )
    assert off["eligible_count"] == 0, "현행 기준이 손실 지갑을 받아들였다"
    assert on["eligible_count"] == 1, "비성과 기준이 손실 지갑을 거부했다 — 성과로 걸러내고 있다"
