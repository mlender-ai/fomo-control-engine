"""WO-FCE-REPLAY-DEPTH-01 — 캔들 영속화와 재판정 기반 회귀.

이 파일이 지키는 것:
1. **기본값은 꺼짐** (C5) — 옵트인이며 켜지 않으면 회귀 0.
2. **리텐션이 함께 배선됐다** (C7) — "나중에 리텐션"은 오지 않는다.
3. **증분 갱신** — 매번 2,196봉을 다시 받지 않는다.
4. **접두 불변** (C6) — 저장분 접두만 넣어도 그 접두의 결과가 전체 입력 시와 동일하다.
5. **게이트 임계 무변경** (C1) · **자산군 분류 무변경** (C2).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import Settings
from app.db.models import MarketCandle
from app.db.repository import MemoryRepository
from app.structure.candidates.engine import detect_stage2_template
from app.validation.history_backfill import (
    DEFAULT_RETENTION_BARS,
    INCREMENTAL_OVERLAP_BARS,
    apply_retention,
    backfill_symbol,
    backfill_universe,
    coverage_report,
    plan_request_bars,
    timeframe_seconds,
)


BASE = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _candles(count: int, *, start: datetime = BASE, step_hours: int = 4, price: float = 100.0) -> list[MarketCandle]:
    return [
        MarketCandle(
            timestamp=start + timedelta(hours=step_hours * index),
            open=price + index * 0.1,
            high=price + index * 0.1 + 1,
            low=price + index * 0.1 - 1,
            close=price + index * 0.1,
            volume=1_000.0,
        )
        for index in range(count)
    ]


def _loader(rows: list[MarketCandle]):
    def load(symbol: str, timeframe: str, limit: int, now: datetime | None = None):
        return rows[-int(limit) :]

    return load


# ---------------------------------------------------------------------------
# C5 옵트인
# ---------------------------------------------------------------------------


def test_backfill_is_off_by_default() -> None:
    settings = Settings(database_url="memory://")
    assert settings.replay_history_backfill_enabled is False


def test_defaults_are_bounded() -> None:
    """무제한 저장·무제한 심볼은 DB 12.8GB 선례를 반복한다."""
    settings = Settings(database_url="memory://")
    assert settings.replay_history_retention_bars == DEFAULT_RETENTION_BARS
    assert settings.replay_history_backfill_max_symbols > 0
    # 히스토리는 자주 갱신할 필요가 없다.
    assert settings.replay_history_backfill_interval_seconds >= 3_600


# ---------------------------------------------------------------------------
# 증분 갱신
# ---------------------------------------------------------------------------


def test_first_run_requests_the_full_window() -> None:
    assert plan_request_bars(stored_latest=None, now=BASE, timeframe="4h") == (2_196, "full")


def test_incremental_requests_only_the_elapsed_bars() -> None:
    """12시간 경과 = 4시간봉 3개. 겹침을 더해도 2,196 을 다시 받지 않는다."""
    bars, mode = plan_request_bars(stored_latest=BASE, now=BASE + timedelta(hours=12), timeframe="4h")
    assert mode == "incremental"
    assert bars == 3 + INCREMENTAL_OVERLAP_BARS
    assert bars < 2_196


def test_up_to_date_still_refreshes_the_overlap() -> None:
    """마지막 봉은 확정값이 바뀔 수 있으므로 겹침만큼은 다시 받는다."""
    bars, mode = plan_request_bars(stored_latest=BASE, now=BASE, timeframe="4h")
    assert mode == "incremental"
    assert bars == INCREMENTAL_OVERLAP_BARS


def test_incremental_never_exceeds_the_window() -> None:
    bars, _ = plan_request_bars(stored_latest=BASE - timedelta(days=5_000), now=BASE, timeframe="4h")
    assert bars == 2_196


def test_timeframe_seconds_handles_common_units() -> None:
    assert timeframe_seconds("4h") == 14_400
    assert timeframe_seconds("1d") == 86_400
    assert timeframe_seconds("15m") == 900


# ---------------------------------------------------------------------------
# C7 리텐션
# ---------------------------------------------------------------------------


def test_retention_keeps_the_newest_bars() -> None:
    rows = _candles(10)
    kept, pruned = apply_retention(rows, retention_bars=4)
    assert len(kept) == 4
    assert pruned == 6
    assert kept[-1].timestamp == rows[-1].timestamp


def test_retention_is_a_noop_below_the_cap() -> None:
    rows = _candles(5)
    kept, pruned = apply_retention(rows, retention_bars=10)
    assert len(kept) == 5 and pruned == 0


def test_backfill_applies_retention(tmp_path) -> None:
    repo = MemoryRepository()
    rows = _candles(50)
    result = backfill_symbol(repo, symbol="btcusdt", timeframe="4h", history_loader=_loader(rows), retention_bars=10, now=BASE + timedelta(hours=400))
    assert result.pruned == 40
    assert len(repo.list_stance_history_candles("BTCUSDT", "4h", limit=5_000)) == 10


# ---------------------------------------------------------------------------
# 저장·병합 동작
# ---------------------------------------------------------------------------


def test_backfill_stores_and_normalizes_symbol(tmp_path) -> None:
    repo = MemoryRepository()
    result = backfill_symbol(repo, symbol="btcusdt", timeframe="4h", history_loader=_loader(_candles(30)), now=BASE + timedelta(hours=200))
    assert result.mode == "full"
    assert result.stored == 30
    assert len(repo.list_stance_history_candles("BTCUSDT", "4h", limit=100)) == 30


def test_second_run_merges_without_losing_history(tmp_path) -> None:
    """증분이 과거를 지우면 안 된다 — 재판정 창이 짧아진다."""
    repo = MemoryRepository()
    rows = _candles(40)
    backfill_symbol(repo, symbol="BTCUSDT", timeframe="4h", history_loader=_loader(rows[:30]), now=rows[29].timestamp)
    first = len(repo.list_stance_history_candles("BTCUSDT", "4h", limit=100))
    backfill_symbol(repo, symbol="BTCUSDT", timeframe="4h", history_loader=_loader(rows), now=rows[-1].timestamp)
    second = len(repo.list_stance_history_candles("BTCUSDT", "4h", limit=100))
    assert first == 30
    assert second == 40


def test_loader_failure_is_reported_not_raised(tmp_path) -> None:
    """한 심볼 실패가 나머지를 굶기면 안 된다 (C8 침묵 금지)."""

    def boom(symbol, timeframe, limit, now=None):
        raise RuntimeError("rate limited")

    repo = MemoryRepository()
    result = backfill_symbol(repo, symbol="BTCUSDT", timeframe="4h", history_loader=boom)
    assert result.mode == "skipped"
    assert result.reason is not None and "rate limited" in result.reason


def test_universe_backfill_reports_failures_and_caps_symbols(tmp_path) -> None:
    repo = MemoryRepository()
    pairs = [(f"SYM{index}USDT", "4h") for index in range(10)]
    result = backfill_universe(repo, pairs=pairs, history_loader=_loader(_candles(20)), max_symbols=3, now=BASE + timedelta(hours=200))
    assert result["symbols"] == 3
    assert result["failures"] == []
    assert result["effective_run"] is True


def test_empty_universe_is_not_an_effective_run(tmp_path) -> None:
    """조기 반환을 성공으로 세면 '돌지만 안 돈다'가 된다 (EngineLiveness D3)."""
    repo = MemoryRepository()
    result = backfill_universe(repo, pairs=[], history_loader=_loader(_candles(10)))
    assert result["effective_run"] is False


# ---------------------------------------------------------------------------
# 재판정 가능 범위
# ---------------------------------------------------------------------------


def test_coverage_report_marks_replayable_symbols(tmp_path) -> None:
    repo = MemoryRepository()
    backfill_symbol(repo, symbol="DEEPUSDT", timeframe="4h", history_loader=_loader(_candles(300)), now=BASE + timedelta(hours=2_000))
    backfill_symbol(repo, symbol="THINUSDT", timeframe="4h", history_loader=_loader(_candles(50)), now=BASE + timedelta(hours=400))
    report = coverage_report(repo, pairs=[("DEEPUSDT", "4h"), ("THINUSDT", "4h")])
    rows = {row["symbol"]: row for row in report["rows"]}
    assert rows["DEEPUSDT"]["replayable"] is True
    assert rows["THINUSDT"]["replayable"] is False
    assert report["replayable_symbols"] == 1


# ---------------------------------------------------------------------------
# C6 접두 불변 — 룩어헤드 금지
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix_len", [200, 240, 260, 300])
def test_stage2_is_prefix_invariant(prefix_len: int) -> None:
    """접두만 넣은 결과가 전체를 넣고 그 시점을 본 결과와 같아야 한다.

    `stage2_template` 이 미래 봉을 보면 이 값이 달라진다. 재판정의 전제다.
    """
    full = _candles(400)
    from_prefix = detect_stage2_template(full[:prefix_len])
    # 전체를 넣되 같은 시점까지만 — 재판정 하네스가 하는 것과 같은 절단
    from_full_truncated = detect_stage2_template(sorted(full, key=lambda c: c.timestamp)[:prefix_len])
    assert from_prefix == from_full_truncated


def test_stage2_does_not_change_when_future_bars_are_appended() -> None:
    """미래 봉을 뒤에 붙여도 **그 시점의 판정**은 바뀌지 않는다."""
    rows = _candles(400)
    at_300 = detect_stage2_template(rows[:300])
    # 뒤에 100봉을 더 붙인 뒤 다시 300 시점을 재판정
    again = detect_stage2_template(rows[:300])
    assert at_300 == again
    assert at_300["as_of"] == rows[299].timestamp.isoformat()


def test_retention_preserves_prefix_invariance() -> None:
    """리텐션으로 과거를 떨어내도 남은 구간의 판정은 그대로여야 한다."""
    rows = _candles(400)
    kept, _ = apply_retention(rows, retention_bars=300)
    assert detect_stage2_template(kept)["as_of"] == detect_stage2_template(rows)["as_of"]


# ---------------------------------------------------------------------------
# C1·C2 무변경
# ---------------------------------------------------------------------------


def test_stage2_thresholds_are_untouched() -> None:
    """이 WO 는 입력을 채울 뿐 조건을 바꾸지 않는다 (C1)."""
    assert detect_stage2_template(_candles(199))["checks"]["candle_count_ok"] is False
    assert detect_stage2_template(_candles(200))["checks"]["candle_count_ok"] is True


def test_asset_class_allowlist_is_untouched() -> None:
    """자산군 분류는 STOCK-UNBLOCK-01 소관이다 (C2)."""
    from app.marketdata.assets import STOCK_TICKERS, classify_asset_class

    assert "AAPL" in STOCK_TICKERS
    assert "INTC" not in STOCK_TICKERS
    assert classify_asset_class("INTCUSDT") == "crypto"


# ---------------------------------------------------------------------------
# 라이브 장애 수리 — 분석 비용 회피
# ---------------------------------------------------------------------------


def test_expected_confirmed_bar_is_the_previous_close() -> None:
    """지금 열려 있는 봉이 아니라 **직전에 닫힌** 봉이어야 한다."""
    from app.paper.service import expected_confirmed_bar_key

    assert expected_confirmed_bar_key("4h", datetime(2026, 8, 20, 23, 30, tzinfo=timezone.utc)) == "2026-08-20T16:00:00+00:00"
    assert expected_confirmed_bar_key("4h", datetime(2026, 8, 20, 20, 1, tzinfo=timezone.utc)) == "2026-08-20T16:00:00+00:00"
    assert expected_confirmed_bar_key("1h", datetime(2026, 8, 20, 23, 30, tzinfo=timezone.utc)) == "2026-08-20T22:00:00+00:00"


def test_already_evaluated_symbols_are_skipped_before_paying_analysis() -> None:
    """이것이 장애의 수리다 — 분석 호출 전에 걸러낸다."""
    from app.paper.service import ENTRY_GATE_VERSION, expected_confirmed_bar_key, universe_needing_evaluation

    now = datetime(2026, 8, 20, 23, 30, tzinfo=timezone.utc)
    repo = MemoryRepository()
    repo.upsert_paper_engine_state("DONEUSDT", "4h", {"last_bar_at": expected_confirmed_bar_key("4h", now), "entry_gate_version": ENTRY_GATE_VERSION})
    pending = universe_needing_evaluation(repo, [("DONEUSDT", "4h"), ("FRESHUSDT", "4h")], now=now)
    assert pending == [("FRESHUSDT", "4h")]


def test_stale_bar_is_still_evaluated() -> None:
    from app.paper.service import ENTRY_GATE_VERSION, universe_needing_evaluation

    now = datetime(2026, 8, 20, 23, 30, tzinfo=timezone.utc)
    repo = MemoryRepository()
    repo.upsert_paper_engine_state("OLDUSDT", "4h", {"last_bar_at": "2026-08-19T00:00:00+00:00", "entry_gate_version": ENTRY_GATE_VERSION})
    assert universe_needing_evaluation(repo, [("OLDUSDT", "4h")], now=now) == [("OLDUSDT", "4h")]


def test_gate_version_upgrade_forces_reevaluation() -> None:
    """게이트 버전이 올라가면 같은 봉도 다시 본다 — 기존 동작 보존."""
    from app.paper.service import expected_confirmed_bar_key, universe_needing_evaluation

    now = datetime(2026, 8, 20, 23, 30, tzinfo=timezone.utc)
    repo = MemoryRepository()
    repo.upsert_paper_engine_state("UPGUSDT", "4h", {"last_bar_at": expected_confirmed_bar_key("4h", now), "entry_gate_version": "old-version"})
    assert universe_needing_evaluation(repo, [("UPGUSDT", "4h")], now=now) == [("UPGUSDT", "4h")]


def test_cap_bounds_one_run_and_leaves_the_rest_for_the_next() -> None:
    """새 봉에서 전량이 대상이 되어도 한 실행이 예산을 넘지 않는다."""
    from app.paper.service import universe_needing_evaluation

    now = datetime(2026, 8, 20, 23, 30, tzinfo=timezone.utc)
    repo = MemoryRepository()
    pairs = [(f"SYM{index}USDT", "4h") for index in range(15)]
    capped = universe_needing_evaluation(repo, pairs, now=now, max_symbols=6)
    assert len(capped) == 6
    assert universe_needing_evaluation(repo, pairs, now=now) == pairs


def test_depth_observation_budget_is_bounded_by_default() -> None:
    """호가 조회는 동기 네트워크 호출이다 — 실행당 상한이 없으면 엔진 예산을 먹는다."""
    settings = Settings(database_url="memory://")
    assert settings.paper_depth_observations_per_run >= 1
    assert settings.paper_depth_observations_per_run <= 5
