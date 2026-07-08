from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.db.models import Direction, Trade
from app.notify.rules import evaluate_performance_alerts
from app.performance.metrics import (
    PerformanceConfig,
    build_performance_report,
    kelly_reference_from_historical,
    performance_metrics,
)
from app.core.config import Settings


def test_performance_metrics_match_fixed_fixture() -> None:
    trades = [
        _trade(100, days=0),
        _trade(-50, days=1),
        _trade(200, days=2),
        _trade(-100, days=3),
        _trade(150, days=4),
        _trade(-75, days=5),
        _trade(125, days=6),
        _trade(-25, days=7),
        _trade(50, days=8),
        _trade(-50, days=9),
    ]

    report = build_performance_report(trades, config=PerformanceConfig(capital_base_usdt=1000))
    metrics = report["overall"]

    assert metrics["sample_sufficient"] is True
    assert metrics["gross_profit_usdt"] == 625
    assert metrics["gross_loss_usdt"] == 300
    assert metrics["net_profit_usdt"] == 325
    assert metrics["profit_factor"] == 2.083
    assert metrics["win_rate_pct"] == 50
    assert metrics["max_drawdown_pct"] == -8.0
    assert metrics["recovery_factor"] == 3.25


def test_performance_metrics_withhold_when_sample_is_small() -> None:
    metrics = performance_metrics([_trade(100), _trade(-50)], config=PerformanceConfig(capital_base_usdt=1000))

    assert metrics["sample_sufficient"] is False
    assert metrics["profit_factor"] is None
    assert metrics["sharpe"] is None
    assert metrics["sample_warning"] == "표본 부족 — 결론 유보 (N=2)"


def test_kelly_reference_uses_ci_lower_bound_and_hides_candidates() -> None:
    reference = kelly_reference_from_historical(
        {
            "stats": [
                {
                    "signature_key": "candidate",
                    "signature_state": "candidate",
                    "sample_size": 100,
                    "win_1r_ci": [90, 95],
                    "median_rr": 3,
                    "signature": {"direction": "long"},
                },
                {
                    "signature_key": "validated",
                    "signature_state": "validated",
                    "label": "저점 Strong 스윕",
                    "sample_size": 30,
                    "win_1r_ci": [55, 70],
                    "median_rr": 2,
                    "signature": {"direction": "long"},
                },
            ]
        },
        "long",
    )

    assert reference["available"] is True
    assert reference["signature_key"] == "validated"
    assert reference["win_rate_ci_low_pct"] == 55
    assert reference["half_kelly_fraction_pct"] == 16.25


def test_mdd_guard_alert_candidates() -> None:
    settings = Settings(
        FCE_ALERT_RULES_ENABLED="mdd_limit_warn,mdd_limit_critical",
        FCE_PERFORMANCE_MONTHLY_MDD_LIMIT_PCT=10,
    )
    payload = build_performance_report(
        [_trade(-500, days=0), _trade(-400, days=1), _trade(100, days=2)],
        config=PerformanceConfig(capital_base_usdt=1000, monthly_mdd_limit_pct=10),
    )

    candidates = evaluate_performance_alerts(payload, settings)

    assert len(candidates) == 1
    assert candidates[0].rule_id == "mdd_limit_critical"
    assert candidates[0].symbol == "ACCOUNT"


def _trade(pnl_amount: float, *, days: int = 0) -> Trade:
    return Trade(
        position_id=uuid4(),
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100,
        exit_price=101 if pnl_amount >= 0 else 99,
        quantity=1,
        pnl_percent=pnl_amount / 10,
        pnl_amount=pnl_amount,
        entry_score=70,
        exit_score=60,
        holding_minutes=240,
        exit_reason="fixture",
        review_text="fixture",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=days),
    )
