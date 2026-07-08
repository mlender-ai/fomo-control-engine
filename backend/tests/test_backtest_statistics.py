"""WO-FCE-36 시그니처 통계 엄밀화 — 수용 기준 테스트.

CI 결정성·게이트 CI 하한·net 비용·OOS/워크포워드·레짐·overlap·데이터 품질·표기 표준.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.backtest.costs import roundtrip_cost_pct
from app.backtest.data_quality import assess_candles
from app.backtest.outcomes import score_event_outcome
from app.backtest.overlap import compute_overlap_groups, overlap_groups_payload
from app.backtest.regimes import label_regime
from app.backtest.service import historical_context_for_analysis
from app.backtest.statistics import (
    bootstrap_ci_from_counts,
    bootstrap_win_ci,
    enrich_signature_stat,
    format_stat_line,
    oos_split,
    walk_forward_curve,
)
from app.analyst.confluence import _suppress_overlaps
from app.core.config import Settings
from app.db.models import MarketCandle
from app.db.repository import MemoryRepository

BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _candle(index: int, *, open_: float, high: float, low: float, close: float, volume: float = 1000.0, step_s: int = 14400) -> MarketCandle:
    return MarketCandle(
        timestamp=BASE + timedelta(seconds=index * step_s),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _series(closes: list[float]) -> list[MarketCandle]:
    candles = []
    for i, close in enumerate(closes):
        high = close * 1.005
        low = close * 0.995
        candles.append(_candle(i, open_=close, high=high, low=low, close=close))
    return candles


def _case(index: int, *, win: bool, engine: str, event: str, direction: str, day: int) -> dict:
    return {
        "as_of": (BASE + timedelta(days=day)).isoformat(),
        "confirmation_index": index,
        "signature": {"engine": engine, "event_type": event, "direction": direction},
        "outcome": {"win_1r": win},
        "regime": "quiet_range",
        "regime_label": "저변동 횡보",
    }


# ── §3 부트스트랩 CI ────────────────────────────────────────────────

def test_bootstrap_ci_is_deterministic() -> None:
    wins = [True] * 20 + [False] * 12
    assert bootstrap_win_ci(wins) == bootstrap_win_ci(wins)
    assert bootstrap_ci_from_counts(20, 32) == bootstrap_win_ci(wins)


def test_bootstrap_ci_none_on_empty() -> None:
    assert bootstrap_win_ci([]) is None
    assert bootstrap_ci_from_counts(0, 0) is None


def test_point_estimate_passes_but_ci_low_blocks() -> None:
    # 59% 점추정은 55% 기준을 넘지만 CI 하한은 50% 미만 → 게이트 차단 근거.
    ci = bootstrap_ci_from_counts(19, 32)
    assert ci is not None
    assert ci[0] < 50.0  # 점추정 59% 이지만 하한이 낮다


# ── realized_rr 회계 일관성 (전수 감사 수정) ───────────────────────

def test_rr_stays_banked_when_stop_hits_after_1r() -> None:
    # 1R 도달(캔들1) 후 스탑(캔들2): win_1r=True와 rr이 모순되면 안 된다.
    future = [
        _candle(1, open_=100, high=102.5, low=99.5, close=102.0),  # 1R(102) 도달
        _candle(2, open_=102, high=102.2, low=97.5, close=97.6),  # 스탑(98) 히트
    ]
    outcome = score_event_outcome(future, direction="long", entry_price=100.0, invalidation_price=98.0)
    assert outcome["win_1r"] is True
    assert outcome["realized_rr"] == 1.0  # 1R 익절 기준 유지 (기존: -1.0으로 덮어씀)
    assert outcome["win_2r"] is False


def test_rr_marks_to_final_close_on_timeout() -> None:
    # 목표/스탑 미도달 타임아웃: 피크(MFE)가 아니라 최종 종가 기준.
    future = [
        _candle(1, open_=100, high=101.8, low=99.5, close=99.0),  # 피크 +0.9R, 종가 -0.5R
    ]
    outcome = score_event_outcome(future, direction="long", entry_price=100.0, invalidation_price=98.0)
    assert outcome["win_1r"] is False
    assert outcome["realized_rr"] == -0.5  # (99-100)/2 (기존: mfe 기준 +0.9)


def test_empty_outcome_stop_respects_direction() -> None:
    from app.backtest.outcomes import _empty

    long_stop = _empty(100.0, None, 2.0, direction="long")["invalidation_price"]
    short_stop = _empty(100.0, None, 2.0, direction="short")["invalidation_price"]
    assert long_stop < 100.0 < short_stop


# ── §2 거래비용 net ────────────────────────────────────────────────

def test_net_cost_flips_marginal_win_to_loss() -> None:
    future = [_candle(1, open_=100, high=102.5, low=100.0, close=101.0)]
    gross = score_event_outcome(future, direction="long", entry_price=100.0, invalidation_price=98.0, cost_pct=0.0)
    net = score_event_outcome(future, direction="long", entry_price=100.0, invalidation_price=98.0, cost_pct=1.0)
    assert gross["win_1r"] is True
    assert net["win_1r"] is False  # 비용 차감 후 net 1R 미달
    assert net["gross_debug"]["win_1r"] is True  # gross는 내부 디버그로만 남는다


def test_roundtrip_cost_shallow_liquidity_surcharge() -> None:
    settings = Settings()
    deep = roundtrip_cost_pct(settings, asset_class="crypto", quote_volume_24h=10_000_000)
    shallow = roundtrip_cost_pct(settings, asset_class="crypto", quote_volume_24h=100_000)
    assert shallow > deep


# ── §4 OOS + 워크포워드 ────────────────────────────────────────────

def test_oos_split_flags_unstable_on_gap() -> None:
    cases = [_case(i, win=True, engine="liquidity", event="sweep_low", direction="long", day=i) for i in range(14)]
    cases += [_case(i, win=False, engine="liquidity", event="sweep_low", direction="long", day=14 + i) for i in range(6)]
    result = oos_split(cases)
    assert result is not None
    assert result["unstable"] is True
    assert result["gap_pct"] > 15.0


def test_oos_split_stable_when_consistent() -> None:
    cases = [_case(i, win=(i % 2 == 0), engine="liquidity", event="sweep_low", direction="long", day=i) for i in range(20)]
    result = oos_split(cases)
    assert result is not None
    assert result["unstable"] is False


def test_walk_forward_curve_produces_windows() -> None:
    cases = [_case(i, win=(i % 3 != 0), engine="liquidity", event="sweep_low", direction="long", day=i * 20) for i in range(20)]
    curve = walk_forward_curve(cases, window_days=180, step_days=60)
    assert len(curve) > 1
    assert all("win_1r_pct" in window and "sample_size" in window for window in curve)


# ── §5 레짐 분해 ──────────────────────────────────────────────────

def test_regime_uptrend_and_downtrend() -> None:
    up = label_regime(_series([100 + i for i in range(80)]))
    down = label_regime(_series([200 - i for i in range(80)]))
    assert up["regime"] == "uptrend"
    assert down["regime"] == "downtrend"


def test_regime_ranging_is_not_trend() -> None:
    ranging = label_regime(_series([100 + (1 if i % 2 else -1) for i in range(80)]))
    assert ranging["regime"] in {"quiet_range", "volatile_range"}


def test_regime_unknown_when_insufficient() -> None:
    assert label_regime(_series([100.0] * 30))["regime"] == "unknown"


# ── §6 증거 중복 상관 (스윕 + Spring) ──────────────────────────────

def test_overlap_group_links_sweep_and_spring() -> None:
    cases = []
    for k, index in enumerate([10, 12, 14, 16, 18]):
        cases.append(_case(index, win=True, engine="liquidity", event="sweep_low", direction="long", day=k))
        cases.append(_case(index, win=True, engine="wyckoff", event="spring", direction="long", day=k))
    groups = compute_overlap_groups(cases, threshold=0.7)
    assert len(groups) == 1
    families = {tuple(f) for f in groups[0]["families"]}
    assert ("liquidity", "sweep", "long") in families
    assert ("wyckoff", "event", "long") in families


def test_prior_group_present_without_measurement() -> None:
    # 측정 표본이 없어도 도메인 사전 그룹(스윕↔이벤트)이 병기된다.
    payload = overlap_groups_payload([], threshold=0.7)
    assert any(group.get("source") == "prior" for group in payload)


def test_suppress_overlaps_keeps_only_strongest() -> None:
    evidence = [
        {"engine": "liquidity", "direction": "long", "claim": "저점 스윕", "score": 5.0},
        {"engine": "wyckoff", "direction": "long", "claim": "Spring 확인", "score": 3.0},
    ]
    groups = [{"group_id": "g1", "families": [["liquidity", "sweep", "long"], ["wyckoff", "event", "long"]]}]
    result, suppressed = _suppress_overlaps(evidence, groups)
    scores = {item["claim"]: item["score"] for item in result}
    assert scores["저점 스윕"] == 5.0  # 최강 유지
    assert scores["Spring 확인"] == 0.0  # 이중 가점 차단
    assert suppressed[0]["overlap_note"] == "동근원 확인"


# ── §1 데이터 무결성 ──────────────────────────────────────────────

def test_data_quality_flags_ohlc_violations() -> None:
    clean = _series([100 + i * 0.1 for i in range(50)])
    assert assess_candles(clean, "4h")["score"] == 100
    broken = list(clean)
    for i in range(20):
        c = broken[i]
        broken[i] = MarketCandle(timestamp=c.timestamp, open=c.open, high=c.low, low=c.high, close=c.close, volume=c.volume)
    report = assess_candles(broken, "4h")
    assert report["score"] < 70
    assert len(report["valid_candles"]) < len(broken)


def test_service_blocks_stats_below_quality_floor() -> None:
    repo = MemoryRepository()
    settings = Settings()
    candles = _series([100 + i * 0.1 for i in range(80)])
    for i in range(30):  # OHLC 위반 다수 → 품질 하한 미달
        c = candles[i]
        candles[i] = MarketCandle(timestamp=c.timestamp, open=c.open, high=c.low, low=c.high, close=c.close, volume=c.volume)
    payload = historical_context_for_analysis(
        repo,
        settings,
        symbol="BADUSDT",
        timeframe="4h",
        analysis={"asset_class": "crypto"},
        candles=candles,
    )
    assert payload["source"] == "data_quality_below_floor"
    assert payload["stats"] == []


# ── §7 표기 표준 ──────────────────────────────────────────────────

def test_format_stat_line_full() -> None:
    stat = {"label": "스윕 롱", "sample_size": 32, "win_1r_pct": 64.0, "win_1r_ci": [51.0, 76.0]}
    line = format_stat_line(stat)
    assert "net 1R 64.0%" in line
    assert "CI 51.0~76.0%" in line
    assert "N=32" in line


def test_format_stat_line_sample_floor() -> None:
    assert "결론 유보" in format_stat_line({"label": "X", "sample_size": 4, "win_1r_pct": 80.0, "win_1r_ci": [40.0, 95.0]})


def test_format_stat_line_missing_ci_holds_publication() -> None:
    # CI 없는 승률 표기 금지 — 발행 보류로 낮춰 표기.
    assert "발행 보류" in format_stat_line({"label": "X", "sample_size": 32, "win_1r_pct": 64.0, "win_1r_ci": None})


def test_format_stat_line_unstable_tail() -> None:
    stat = {"label": "X", "sample_size": 32, "win_1r_pct": 64.0, "win_1r_ci": [51.0, 76.0], "unstable": True}
    assert format_stat_line(stat).endswith("· OOS 불안정")


def test_format_stat_line_prefers_current_regime() -> None:
    stat = {
        "label": "스윕 롱",
        "sample_size": 40,
        "win_1r_pct": 55.0,
        "win_1r_ci": [45.0, 65.0],
        "regimes": {
            "quiet_range": {"sample_size": 20, "win_1r_pct": 70.0, "win_1r_ci": [55.0, 82.0], "regime_label": "저변동 횡보"},
        },
    }
    line = format_stat_line(stat, current_regime="quiet_range")
    assert "70.0%" in line  # 현재 레짐 슬라이스 우선
    assert "저변동 횡보" in line


def test_enrich_signature_stat_attaches_ci_oos_regimes() -> None:
    cases = [_case(i, win=(i % 2 == 0), engine="liquidity", event="sweep_low", direction="long", day=i) for i in range(20)]
    enriched = enrich_signature_stat({"sample_size": 20}, cases)
    assert enriched["win_1r_ci"] is not None
    assert enriched["oos"] is not None
    assert "quiet_range" in enriched["regimes"]
    assert enriched["period"] is not None
