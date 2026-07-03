from app.db.models import Direction, ShadowAttribution, ShadowExtractRequest, Trade
from app.shadow.engine import compare_shadow_profile, extract_shadow_profile


def test_shadow_attribution_breakdown_calculates_fomo_pnl() -> None:
    trades = []
    for i in range(10):
        profitable = i < 5
        trades.append(
            Trade(
                position_id=f"00000000-0000-0000-0000-00000000000{i}",
                symbol="BTCUSDT",
                direction=Direction.long,
                entry_price=100,
                exit_price=110 if profitable else 95,
                quantity=1,
                pnl_percent=4 if profitable else -5,
                pnl_amount=4 if profitable else -5,
                entry_score=80 if profitable else 50,
                exit_score=82 if profitable else 40,
                holding_minutes=60,
                exit_reason="test",
                review_text="",
            )
        )

    profile = extract_shadow_profile(trades, ShadowExtractRequest(min_trades=10, min_profitable_trades=5))
    comparison = compare_shadow_profile(profile, trades)

    assert isinstance(profile.attribution, ShadowAttribution)
    assert comparison["delta_pnl"] >= 0
    assert profile.attribution.fomo_trades_pnl < 0
