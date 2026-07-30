"""WO-FCE-PERFORMANCE-REPORT-01 — 성과 리포트 규격 회귀.

핵심 방어선: **표본 0에서 숫자를 만들지 않는다**(C3). 계산부가 None 을 강제하고
표시부는 "표본 없음"만 말해야 한다.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.notify.performance_report import (
    format_paper_performance,
    format_track_record_suffix,
    format_weekly_performance,
)
from app.review.paper_performance import (
    SAMPLE_MIN_N,
    closed_metrics,
    paper_performance_report,
    realized_r,
    sample_state,
)


def _trade(**overrides):
    base = {
        "status": "closed",
        "exit_reason": "take_profit",
        "net_pnl_usdt": 10.0,
        "quantity": 1.0,
        "entry_price": 100.0,
        "invalidation_price": 90.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_sample_state_thresholds() -> None:
    assert sample_state(0) == "none"
    assert sample_state(1) == "insufficient"
    assert sample_state(SAMPLE_MIN_N - 1) == "insufficient"
    assert sample_state(SAMPLE_MIN_N) == "sufficient"


def test_closed_metrics_makes_no_number_on_empty_sample() -> None:
    metrics = closed_metrics([])
    assert metrics["n"] == 0
    assert metrics["state"] == "none"
    # C3 핵심: 0% 같은 숫자를 만들어선 안 된다.
    assert metrics["win_rate_pct"] is None
    assert metrics["avg_r"] is None


def test_closed_metrics_win_rate_is_r_positive_ratio() -> None:
    metrics = closed_metrics([1.0, -0.5, 2.0, -1.0])
    assert metrics["n"] == 4
    assert metrics["wins"] == 2
    assert metrics["win_rate_pct"] == 50.0
    assert metrics["avg_r"] == 0.375
    assert metrics["state"] == "insufficient"


def test_realized_r_uses_invalidation_distance_not_stop() -> None:
    # 손익 +10, 리스크 = |100-90| * 1 = 10 → +1.0R
    assert realized_r(10.0, 1.0, 100.0, 90.0) == 1.0
    # 무효화가가 진입가와 같으면 분모가 0 — 숫자를 만들지 않는다.
    assert realized_r(10.0, 1.0, 100.0, 100.0) is None


def test_realized_r_survives_partial_exit_below_entry() -> None:
    """부분청산으로 최종 청산가가 진입가보다 불리해도 실현 손익이 양수면 R>0 이다.

    종가 기준 R 은 이 경우를 손실로 오판한다 — 원장의 실현 손익이 진실이다.
    """
    assert realized_r(1.19, 1.0, 58.623, 57.711) > 0


def test_report_includes_all_four_tracks_even_with_zero_entries() -> None:
    report = paper_performance_report(
        crypto_trades=[],
        stock_dashboard={
            "tracks": [
                {"market": "KR", "currency": "KRW", "elapsed_days": 1, "calendar_days": 8, "lost_days": 7},
                {"market": "US", "currency": "USD", "elapsed_days": 4, "calendar_days": 7, "lost_days": 3},
            ],
            "positions": [],
            "entry_rejection_distribution": {"period_days": 7, "gates": [{"market": "US", "gate": "entry_score", "count": 8}]},
        },
        poly_dashboard={"track": {"elapsed_days": 9}, "positions": [], "resolution_count": 0},
    )

    keys = [track["key"] for track in report["tracks"]]
    # 작업 4: 진입이 0이어도 4트랙 전부 등장해야 한다(주식 KR·US 분리).
    assert keys == ["crypto", "stock_kr", "stock_us", "poly"]
    for track in report["tracks"]:
        assert track["closed"]["win_rate_pct"] is None
        assert track["closed"]["state"] == "none"


def test_daily_report_says_no_sample_instead_of_zero_percent() -> None:
    report = paper_performance_report(
        crypto_trades=[],
        stock_dashboard={"tracks": [{"market": "KR", "currency": "KRW", "elapsed_days": 1, "lost_days": 7}], "positions": []},
        poly_dashboard={"track": {}, "positions": [], "resolution_count": 0},
    )
    text = "\n".join(format_paper_performance(report, date_label="07-30"))

    assert "표본 없음" in text
    assert "승률 0%" not in text
    # 유실일 제외 기준으로 검증 경과일이 표기된다.
    assert "검증 D+1, 유실 7일 제외" in text
    # 4트랙 전부 등장.
    for label in ("크립토", "주식 KR", "폴리"):
        assert label in text


def test_daily_report_always_pairs_win_rate_with_n() -> None:
    trades = [_trade(net_pnl_usdt=10.0), _trade(net_pnl_usdt=-5.0)]
    report = paper_performance_report(
        crypto_trades=trades,
        stock_dashboard={"tracks": [], "positions": []},
        poly_dashboard={"track": {}, "positions": [], "resolution_count": 0},
    )
    text = "\n".join(format_paper_performance(report, date_label="07-30"))

    assert "승률 50% (1/2)" in text
    assert f"표본 부족 (N<{SAMPLE_MIN_N}) — 판정 유보" in text


def test_non_trade_exit_reasons_excluded_from_win_rate() -> None:
    trades = [_trade(net_pnl_usdt=10.0), _trade(net_pnl_usdt=0.0, exit_reason="duplicate_bootstrap_suppressed")]
    report = paper_performance_report(
        crypto_trades=trades,
        stock_dashboard={"tracks": [], "positions": []},
        poly_dashboard={"track": {}, "positions": [], "resolution_count": 0},
    )
    crypto = report["tracks"][0]

    assert crypto["closed"]["n"] == 1
    assert crypto["excluded"]["non_trade"] == 1


def test_poly_headline_brier_is_settled_positions_not_trivial_estimates() -> None:
    """자명 시장이 섞인 추정 브라이어를 대표 지표로 올리면 예측력을 과장한다."""
    report = paper_performance_report(
        crypto_trades=[],
        stock_dashboard={"tracks": [], "positions": []},
        poly_dashboard={
            "track": {"elapsed_days": 9},
            "positions": [{"status": "open"}] * 8,
            "resolution_count": 1789,
            "calibration": {
                "mean_brier_score": 0.0005,
                "curve": [{"bucket": "0–10%", "n": 1184}, {"bucket": "90–100%", "n": 594}],
            },
        },
    )
    poly = report["tracks"][-1]
    text = "\n".join(format_paper_performance(report, date_label="07-30"))

    assert poly["brier"]["settled_positions"]["avg"] is None
    assert poly["brier"]["estimates"]["trivial_share_pct"] == 99.4
    assert "브라이어 표본 없음 — 정산 완료 포지션 0건" in text
    # 추정 기준 숫자는 참고로만, 자명 시장 비중을 밝혀서.
    assert "자명 시장 99% 포함" in text


def test_weekly_report_reports_min_sample_reachability() -> None:
    report = paper_performance_report(
        crypto_trades=[],
        stock_dashboard={"tracks": [{"market": "KR", "currency": "KRW", "elapsed_days": 1, "lost_days": 7}], "positions": []},
        poly_dashboard={"track": {}, "positions": [], "resolution_count": 0},
    )
    text = format_weekly_performance(report)

    assert "검증 진행 1/28일 (유실 7일 제외)" in text
    assert f"N≥{SAMPLE_MIN_N} 도달 불가" in text


def test_track_key_mapping_splits_stock_by_market() -> None:
    from app.worker.manager import _track_key_for_event

    assert _track_key_for_event({"kind": "closed"}) == "crypto"
    assert _track_key_for_event({"track": "poly", "kind": "closed"}) == "poly"
    assert _track_key_for_event({"track": "stock", "detail": {"market": "KR"}}) == "stock_kr"
    assert _track_key_for_event({"track": "stock", "detail": {"market": "US"}}) == "stock_us"
    # 시장을 모르면 어느 트랙 누적인지 단정하지 않는다 — 접미를 생략한다.
    assert _track_key_for_event({"track": "stock", "detail": {}}) == ""


def test_track_record_suffix_for_immediate_alerts() -> None:
    assert format_track_record_suffix({"n": 0, "state": "none"}) == "트랙 누적: 표본 없음"
    suffix = format_track_record_suffix({"n": 12, "state": "insufficient", "win_rate_pct": 75.0})
    assert "승률 75% (N=12)" in suffix
    assert f"표본 부족 (N<{SAMPLE_MIN_N})" in suffix
