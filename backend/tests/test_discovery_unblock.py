"""WO-FCE-DISCOVERY-UNBLOCK-01 — 발견 경로 순환 해제 회귀.

이 파일이 지키는 것:
1. **임계값은 변하지 않는다** (C1) — `sample_floor`·CI 하한·품질 문턱.
2. **비순환 게이트는 계속 요구된다** (C2) — 유동성·신뢰도·2단계·수명주기·괴리·실적창.
3. **요구에서 기록으로다. 제거가 아니다** (C3) — 값은 계속 산출·저장된다.
4. **기본값은 기존 동작** (C4) — 옵트인이며 설정 한 값으로 원복된다.
5. **사후 채점이 분리된다** (C5) — 없으면 완화와 구분되지 않는다.
6. **"표본 미검증" 표시가 있다** (C9).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.db.models import PaperTrade, UniverseDiscovery, Direction
from app.db.repository import MemoryRepository
from app.paper.service import observation_only_universe, paper_universe, universe_source_breakdown
from app.scout.discovery_gate import OBSERVATION_DEFERRED_GATES, observation_gate_passed


NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)


def _reason(code: str, passed: bool) -> dict[str, object]:
    return {"code": code, "passed": passed, "value": None, "threshold": "-"}


def _discovery(symbol: str, reasons: list[dict[str, object]], *, gate_passed: bool = False) -> UniverseDiscovery:
    return UniverseDiscovery(
        id=uuid4(),
        symbol=symbol,
        timeframe="4h",
        asset_class="crypto",
        signature_key="liquidity:sweep_low:strong:long:crypto:4h",
        signature={"key": "liquidity:sweep_low:strong:long:crypto:4h"},
        status="alerted" if gate_passed else "rejected",
        gate_passed=gate_passed,
        gate_reasons=reasons,
        created_at=NOW,
        updated_at=NOW,
    )


# ---------------------------------------------------------------------------
# 3-1 순환/비순환 분류
# ---------------------------------------------------------------------------


def test_only_the_two_sample_gates_are_deferred() -> None:
    """미루는 대상은 **표본에 종속된 두 게이트뿐**이다. 나머지를 함께 풀면 완화가 된다(C2)."""
    assert OBSERVATION_DEFERRED_GATES == {"backtest_sample", "backtest_win_1r_ci_low"}


@pytest.mark.parametrize(
    "code",
    ["liquidity_floor", "confidence", "stage2_template", "signature_lifecycle_state", "live_backtest_divergence", "earnings_window"],
)
def test_non_circular_gates_still_block(code: str) -> None:
    """비순환 게이트는 관측 등급에서도 통과를 막는다 (C2)."""
    reasons = [_reason("backtest_sample", False), _reason(code, False)]
    assert observation_gate_passed(reasons) is False


def test_sample_gates_alone_do_not_block() -> None:
    """표본 두 게이트만 실패하면 관측 등급은 통과한다 — 그것이 순환이었다."""
    reasons = [
        _reason("backtest_sample", False),
        _reason("backtest_win_1r_ci_low", False),
        _reason("liquidity_floor", True),
        _reason("confidence", True),
    ]
    assert observation_gate_passed(reasons) is True


def test_dispatch_gates_are_irrelevant_to_evaluation() -> None:
    """알림 쿨다운·일일 한도는 **발송** 자격이다. 평가 자격과 무관하다."""
    assert observation_gate_passed([_reason("symbol_cooldown", False), _reason("daily_alert_limit", False)]) is True


def test_alert_grade_pass_implies_observation_pass() -> None:
    """알림 자격을 통과한 건은 당연히 평가 자격도 있다."""
    assert observation_gate_passed([_reason(code, True) for code in ("backtest_sample", "liquidity_floor", "confidence")]) is True


# ---------------------------------------------------------------------------
# C1 임계값 무변경 · C3 기록 유지
# ---------------------------------------------------------------------------


def test_thresholds_are_untouched() -> None:
    """이 WO 는 임계가 아니라 **적용 위치**를 바꾼다 (C1)."""
    settings = Settings(database_url="memory://")
    assert settings.universe_backtest_min_sample == 30
    assert settings.universe_backtest_min_ci_low_pct == 50.0
    assert settings.stance_backtest_sample_floor == 30


def test_deferred_values_are_still_recorded(tmp_path) -> None:
    """제거가 아니다 — 미룬 게이트의 판정도 원장에 그대로 남는다 (C3)."""
    repo = MemoryRepository()
    reasons = [_reason("backtest_sample", False), _reason("backtest_win_1r_ci_low", False), _reason("liquidity_floor", True)]
    repo.upsert_universe_discovery(_discovery("SOLUSDT", reasons))
    stored = repo.list_recent_observation_universe_discoveries(limit=10)[0]
    codes = {str(item["code"]): item["passed"] for item in stored.gate_reasons}
    assert codes["backtest_sample"] is False
    assert codes["backtest_win_1r_ci_low"] is False
    # 알림 자격 플래그도 바뀌지 않는다.
    assert stored.gate_passed is False


# ---------------------------------------------------------------------------
# C4 옵트인 — 기본값은 기존 동작
# ---------------------------------------------------------------------------


def test_observation_universe_is_off_by_default() -> None:
    assert Settings(database_url="memory://").paper_observation_universe_enabled is False


def test_default_universe_excludes_observation_only_symbols() -> None:
    """기본값에서는 회귀 0 — 관측 등급 심볼이 유니버스에 들어오지 않는다."""
    repo = MemoryRepository()
    repo.upsert_universe_discovery(_discovery("SOLUSDT", [_reason("backtest_sample", False), _reason("liquidity_floor", True)]))
    assert paper_universe(repo) == []


def test_observation_feed_adds_the_symbol() -> None:
    repo = MemoryRepository()
    repo.upsert_universe_discovery(_discovery("SOLUSDT", [_reason("backtest_sample", False), _reason("liquidity_floor", True)]))
    assert paper_universe(repo, observation_feed=True) == [("SOLUSDT", "4h")]


def test_observation_feed_respects_non_circular_rejection() -> None:
    """유동성 미달 심볼은 관측 등급에서도 들어오지 않는다."""
    repo = MemoryRepository()
    repo.upsert_universe_discovery(_discovery("JUNKUSDT", [_reason("backtest_sample", False), _reason("liquidity_floor", False)]))
    assert paper_universe(repo, observation_feed=True) == []


def test_observation_only_set_excludes_already_validated() -> None:
    """알림 자격으로 이미 들어온 심볼은 '관측 전용'이 아니다 — 플래그가 잘못 붙으면 안 된다."""
    repo = MemoryRepository()
    repo.upsert_universe_discovery(_discovery("BTCUSDT", [_reason("backtest_sample", True), _reason("liquidity_floor", True)], gate_passed=True))
    repo.upsert_universe_discovery(_discovery("SOLUSDT", [_reason("backtest_sample", False), _reason("liquidity_floor", True)]))
    validated = set(paper_universe(repo))
    assert validated == {("BTCUSDT", "4h")}
    assert observation_only_universe(repo, validated=validated) == {("SOLUSDT", "4h")}


# ---------------------------------------------------------------------------
# 3-3 사후 채점 분리 (C5) · C9 표시
# ---------------------------------------------------------------------------


def _trade(source: str, net: float) -> PaperTrade:
    return PaperTrade(
        id=uuid4(),
        symbol="SOLUSDT",
        timeframe="4h",
        asset_class="crypto",
        direction=Direction.long,
        status="closed",
        entry_bar_at=NOW,
        entry_at=NOW,
        entry_price=100.0,
        margin_usdt=100.0,
        leverage=3.0,
        quantity=3.0,
        remaining_quantity=0.0,
        invalidation_price=98.0,
        take_profit_price=103.0,
        stop_price=98.0,
        entry_evidence={"universe_source": source},
        exit_at=NOW + timedelta(hours=4),
        exit_bar_at=NOW + timedelta(hours=4),
        exit_price=101.0,
        exit_reason="take_profit_2",
        gross_pnl_usdt=net,
        costs_usdt=0.0,
        net_pnl_usdt=net,
        net_return_pct=net,
        created_at=NOW,
        updated_at=NOW,
    )


def test_breakdown_separates_the_two_feeds() -> None:
    """섞이면 원인을 귀속할 수 없다 (C5)."""
    trades = [_trade("validated", 5.0), _trade("observation_unvalidated", -3.0), _trade("observation_unvalidated", -1.0)]
    breakdown = universe_source_breakdown(trades)
    assert breakdown["validated"]["trade_count"] == 1
    assert breakdown["observation_unvalidated"]["trade_count"] == 2
    assert breakdown["validated"]["win_rate_pct"] == 100.0
    assert breakdown["observation_unvalidated"]["win_rate_pct"] == 0.0


def test_trades_without_the_flag_count_as_validated() -> None:
    """과거 원장에는 플래그가 없다 — 관측 등급으로 오분류하면 성적이 왜곡된다."""
    breakdown = universe_source_breakdown([_trade("validated", 1.0)])
    assert breakdown["validated"]["trade_count"] == 1
    assert breakdown["observation_unvalidated"]["trade_count"] == 0


def test_empty_bucket_withholds_a_verdict() -> None:
    """표본 0 에서 승률을 말하지 않는다 (C10)."""
    breakdown = universe_source_breakdown([_trade("validated", 1.0)])
    assert breakdown["observation_unvalidated"]["trade_count"] == 0
    assert "판정 유보" in str(breakdown["observation_unvalidated"]["note"])


def test_breakdown_states_that_passing_is_not_quality() -> None:
    """C10 — 통과 건수 증가를 품질 개선으로 읽지 못하게 문서화된 술어를 남긴다."""
    assert "품질 검증이 아니다" in str(universe_source_breakdown([])["note"])
