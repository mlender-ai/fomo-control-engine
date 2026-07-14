from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.analyst.signature_registry import current_state
from app.backtest.signatures import SetupSignature, signature_key
from app.core.config import Settings
from app.db.models import BacktestStat, DerivativeMetric, JudgmentLedgerEntry, MarketCandle
from app.db.repository import MemoryRepository
from app.notify.bot.formatters import format_weekly_calibration
from app.validation import candidates as subject


def test_score_candidates_persists_all_five_targets_with_sources(monkeypatch) -> None:
    repo = MemoryRepository()
    settings = Settings(backtest_min_window_candles=30, backtest_lookahead_bars=8, backtest_bootstrap_iterations=100)
    candles = _candles(90)

    replay_cases = [
        _case("fvg", "gap_formed", "long", candles, 50, source="backtest"),
        _case("order_block", "retest", "long", candles, 51, source="backtest"),
        _case("vcp", "contraction", "long", candles, 52, source="backtest"),
    ]
    monkeypatch.setattr(subject, "replay_candles", lambda *args, **kwargs: replay_cases)
    repo.add_judgment(_judgment("full_alignment", "unanimous", candles[60].timestamp, candles[60].close, agreeing=4))
    repo.add_judgment(_judgment("money_flow", "futures_led_rally", candles[60].timestamp + timedelta(hours=1), candles[60].close))

    result = subject.score_candidates(
        repo,
        settings,
        targets=[("BTCUSDT", "1h")],
        candle_loader=lambda _symbol, _timeframe: candles,
        now=candles[-1].timestamp + timedelta(hours=2),
    )

    stats = repo.list_backtest_stats(limit=20)
    assert {stat.engine for stat in stats} == {"fvg", "order_block", "vcp", "full_alignment", "money_flow"}
    assert result["lookahead_audit"]["passed"] is True
    assert result["errors"] == []
    assert next(stat for stat in stats if stat.engine == "fvg").payload["sources"]["backtest"]["sample_size"] == 1
    assert next(stat for stat in stats if stat.engine == "full_alignment").payload["sources"]["live"]["sample_size"] == 1
    assert next(stat for stat in stats if stat.engine == "money_flow").payload["sources"]["live"]["sample_size"] == 1


def test_candidate_gate_creates_proposal_only_and_supports_veto_or_manual_approval() -> None:
    settings = Settings(signature_validated_min_sample=30, universe_backtest_min_ci_low_pct=50.0, backtest_bootstrap_iterations=100)
    key, stat = _winning_stat(30)

    veto_repo = MemoryRepository()
    veto_repo.upsert_backtest_stat(stat)
    proposals = subject.evaluate_candidate_promotions(veto_repo, settings)

    assert [item["signature_key"] for item in proposals] == [key]
    assert current_state(veto_repo, key, settings=settings) == "candidate"
    proposal_log = veto_repo.list_autonomy_logs(signature_key=key, limit=1)[0]
    assert proposal_log.transition == "promotion_proposed"
    assert proposal_log.autonomous is False
    assert proposal_log.evidence["automatic_application"] is False
    assert proposal_log.evidence["veto_deadline_at"]

    subject.veto_candidate_promotion(veto_repo, key)
    assert current_state(veto_repo, key, settings=settings) == "candidate"
    assert veto_repo.list_autonomy_logs(signature_key=key, limit=1)[0].transition == "promotion_vetoed"

    approve_repo = MemoryRepository()
    approve_repo.upsert_backtest_stat(stat)
    subject.evaluate_candidate_promotions(approve_repo, settings)
    subject.approve_candidate_promotion(approve_repo, key)
    assert current_state(approve_repo, key, settings=settings) == "validated"
    assert approve_repo.list_autonomy_logs(signature_key=key, limit=1)[0].autonomous is False


def test_score_candidates_records_zero_sample_audit_rows_for_all_targets(monkeypatch) -> None:
    repo = MemoryRepository()
    settings = Settings(backtest_min_window_candles=30, backtest_bootstrap_iterations=100)
    candles = _candles(90)
    monkeypatch.setattr(subject, "replay_candles", lambda *args, **kwargs: [])

    result = subject.score_candidates(
        repo,
        settings,
        targets=[("BTCUSDT", "1h")],
        candle_loader=lambda _symbol, _timeframe: candles,
        now=candles[-1].timestamp + timedelta(hours=2),
    )

    stats = repo.list_backtest_stats(limit=20)
    assert {stat.engine for stat in stats} == set(subject.CANDIDATE_SCORE_TARGETS)
    assert all(stat.sample_size == 0 for stat in stats)
    assert all(stat.payload["audit_state"] == "scored_no_occurrences" for stat in stats)
    assert result["saved_stats"] == 5


def test_candidate_gate_requires_a_sufficient_regime_slice() -> None:
    settings = Settings(signature_validated_min_sample=30, universe_backtest_min_ci_low_pct=50.0, backtest_bootstrap_iterations=100)
    repo = MemoryRepository()
    key, stat = _winning_stat(30, split_regimes=True)
    repo.upsert_backtest_stat(stat)

    assert subject.evaluate_candidate_promotions(repo, settings) == []
    review = subject.candidate_review_status(repo, settings)
    fvg = next(item for item in review["items"] if item["engine"] == "fvg")
    assert fvg["sample_size"] == 30
    assert fvg["remaining_samples"] == 15
    assert current_state(repo, key, settings=settings) == "candidate"


def test_candidate_lookahead_audit_covers_exact_scoring_target_set() -> None:
    audit = subject.candidate_lookahead_audit()

    assert audit["passed"] is True
    assert set(audit["audited"]) == {"fvg", "order_block", "vcp", "full_alignment", "money_flow"}
    assert "two_bar_swing_delay" in audit["contracts"]["vcp"]
    assert audit["contracts"]["money_flow"] == "confirmed_metric_prefix_only"


def test_full_alignment_replay_rebuilds_only_closed_candle_prefixes(monkeypatch) -> None:
    candles = _candles(110)
    seen_lengths: list[int] = []

    def fake_analysis(snapshot, *_args):
        seen_lengths.append(len(snapshot.candles))
        return {"prefix_size": len(snapshot.candles)}

    def fake_confluence(**kwargs):
        size = int(kwargs["analysis"]["prefix_size"])
        return {"prefix_size": size, "stance_state": {"stance": "long_leaning", "transitioning": False}}

    def fake_alignment(confluence, _historical):
        return {
            "unanimous": confluence["prefix_size"] == 100,
            "transitioning": False,
            "direction": "long",
            "agreeing": 4,
        }

    monkeypatch.setattr(subject, "build_chart_analysis", fake_analysis)
    monkeypatch.setattr(subject, "build_confluence", fake_confluence)
    monkeypatch.setattr(subject, "build_full_alignment", fake_alignment)
    monkeypatch.setattr(subject, "_frozen_validated_history", lambda *_args: {"stats": [{}, {}, {}, {}]})

    cases = subject._full_alignment_replay_cases(
        MemoryRepository(),
        Settings(backtest_bootstrap_iterations=100),
        symbol="BTCUSDT",
        timeframe="1h",
        candles=candles,
        asset_class="crypto",
        min_window=30,
        lookahead_bars=8,
        cost_pct=0.0,
    )

    assert seen_lengths[0] == 100
    assert seen_lengths[-1] == 109
    assert len(cases) == 1
    assert cases[0]["confirmation_index"] == 99
    assert cases[0]["event"]["input_contract"] == "candles[:confirmation_index+1]"


def test_money_flow_replay_classifies_chronological_metric_prefixes() -> None:
    candles = _candles(40)
    repo = MemoryRepository()
    for index in range(12):
        futures_led = index == 10
        observation = {
            "as_of": (candles[index].timestamp + timedelta(hours=1)).isoformat(),
            "price_change_pct": 2.0 if futures_led else 1.0,
            "spot_cvd_delta_ratio": 0.0 if futures_led else 1.0,
            "futures_cvd_delta_ratio": 2.0 if futures_led else 1.0,
            "oi_change_pct": 2.0 if futures_led else 1.0,
            "confirmed": True,
            "source": "bitget_spot",
            "coverage": {"spot_available": True, "futures_available": True},
        }
        repo.add_derivative_metric(
            DerivativeMetric(
                symbol="BTCUSDT",
                source="bitget",
                tier="bitget_public",
                as_of=candles[index].timestamp + timedelta(hours=1),
                raw_json={"money_flow_observation": observation},
            )
        )

    cases = subject._money_flow_replay_cases(
        repo,
        Settings(backtest_bootstrap_iterations=100),
        symbol="BTCUSDT",
        timeframe="1h",
        candles=candles,
        asset_class="crypto",
        lookahead_bars=8,
        cost_pct=0.0,
    )

    assert len(cases) == 1
    assert cases[0]["confirmation_index"] == 10
    assert cases[0]["signature"]["direction"] == "short"
    assert cases[0]["event"]["metric_prefix_size"] == 11
    assert cases[0]["event"]["input_contract"] == "metrics[:observation_as_of]"


def test_special_candidates_raise_warning_after_sufficient_poor_sample() -> None:
    settings = Settings(signature_validated_min_sample=30, universe_backtest_min_ci_low_pct=50.0, backtest_bootstrap_iterations=100)
    repo = MemoryRepository()
    for engine, event_type, direction in (
        ("full_alignment", "unanimous", "long"),
        ("money_flow", "futures_led_rally", "short"),
    ):
        candles = _candles(35)
        cases = [_case(engine, event_type, direction, candles, index, source="live") for index in range(30)]
        for case in cases:
            case["outcome"] = {**case["outcome"], "win_1r": False, "win_2r": False, "realized_rr": -1.0}
        signature = cases[0]["signature"]
        repo.upsert_backtest_stat(
            BacktestStat(
                signature_key=str(cases[0]["signature_key"]),
                symbol="BTCUSDT",
                timeframe="1h",
                asset_class="crypto",
                engine=engine,
                event_type=event_type,
                strength_class="candidate",
                direction=direction,
                sample_size=30,
                cases=cases,
                payload={"signature": signature},
            )
        )

    review = subject.candidate_review_status(repo, settings)

    assert next(item for item in review["items"] if item["engine"] == "full_alignment")["predictive_warning"] is True
    assert next(item for item in review["items"] if item["engine"] == "money_flow")["predictive_warning"] is True


def test_weekly_report_formats_candidate_review_block() -> None:
    text = format_weekly_calibration(
        {
            "candidate_review": {
                "items": [
                    {
                        "engine": "fvg",
                        "label": "FVG",
                        "sample_size": 12,
                        "win_1r_pct": 58.3,
                        "win_1r_ci": [41.7, 75.0],
                        "remaining_samples": 18,
                        "status": "candidate",
                    }
                ]
            }
        }
    )

    assert "Candidate 심사 현황" in text
    assert "FVG: N=12" in text
    assert "승격까지 18표본" in text


def _winning_stat(sample_size: int, *, split_regimes: bool = False) -> tuple[str, BacktestStat]:
    candles = _candles(sample_size + 5)
    cases = []
    for index in range(sample_size):
        case = _case("fvg", "gap_formed", "long", candles, index, source="backtest")
        case["regime"] = "trend_low_vol" if not split_regimes or index < sample_size // 2 else "range_low_vol"
        case["regime_label"] = case["regime"]
        case["outcome"] = {**case["outcome"], "win_1r": True, "win_2r": index % 2 == 0}
        cases.append(case)
    key = str(cases[0]["signature_key"])
    signature = cases[0]["signature"]
    return key, BacktestStat(
        signature_key=key,
        symbol="BTCUSDT",
        timeframe="1h",
        asset_class="crypto",
        engine="fvg",
        event_type="gap_formed",
        strength_class="candidate",
        direction="long",
        sample_size=sample_size,
        cases=cases,
        payload={"signature": signature},
    )


def _case(engine: str, event_type: str, direction: str, candles: list[MarketCandle], index: int, *, source: str) -> dict:
    signature = SetupSignature(
        engine=engine,
        event_type=event_type,
        strength_class="candidate",
        direction=direction,
        asset_class="crypto",
        timeframe="1h",
    ).model_dump()
    signature["key"] = signature_key(signature)
    return {
        "symbol": "BTCUSDT",
        "timeframe": "1h",
        "asset_class": "crypto",
        "as_of": candles[index].timestamp.isoformat(),
        "signature": signature,
        "signature_key": signature["key"],
        "outcome": {
            "win_1r": True,
            "win_2r": False,
            "realized_rr": 1.0,
            "mfe_r": 1.2,
            "mae_r": 0.3,
            "resolved_bars": 3,
        },
        "regime": "trend_low_vol",
        "regime_label": "trend_low_vol",
        "source": source,
    }


def _judgment(engine: str, event_type: str, as_of: datetime, price: float, *, agreeing: int = 0) -> JudgmentLedgerEntry:
    return JudgmentLedgerEntry(
        judgment_id=f"candidate:{engine}:{as_of.isoformat()}",
        position_id=UUID(int=0),
        source_type="candidate_signature",
        source_id=f"{engine}:{as_of.isoformat()}",
        as_of=as_of,
        type="candidate_signature",
        claim={
            "symbol": "BTCUSDT",
            "timeframe": "1h",
            "engine": engine,
            "event_type": event_type,
            "direction": "short" if engine == "money_flow" else "long",
            "price": price,
            "expected_move": "down" if engine == "money_flow" else "up",
            "agreeing_modules": [{"engine": f"module-{index}"} for index in range(agreeing)],
        },
    )


def _candles(count: int) -> list[MarketCandle]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        MarketCandle(
            timestamp=start + timedelta(hours=index),
            open=100 + index * 0.1,
            high=101 + index * 0.1,
            low=99 + index * 0.1,
            close=100.5 + index * 0.1,
            volume=1000 + index,
        )
        for index in range(count)
    ]
