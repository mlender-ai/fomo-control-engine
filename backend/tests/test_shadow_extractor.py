from app.db.models import Direction, ShadowExtractRequest, Trade
from app.shadow.engine import ShadowSampleError, extract_shadow_profile


def test_shadow_sample_shortage_raises_clear_error() -> None:
    try:
        extract_shadow_profile([], ShadowExtractRequest())
    except ShadowSampleError as exc:
        assert "샘플이 부족합니다" in str(exc)
    else:
        raise AssertionError("expected ShadowSampleError")


def test_shadow_rule_extraction() -> None:
    trades = [
        Trade(
            position_id=f"00000000-0000-0000-0000-00000000000{i % 10}",
            symbol="BTCUSDT",
            direction=Direction.long,
            entry_price=100,
            exit_price=110,
            quantity=1,
            pnl_percent=5,
            pnl_amount=5,
            entry_score=80,
            exit_score=82,
            holding_minutes=60,
            exit_reason="test",
            review_text="",
        )
        for i in range(10)
    ]

    profile = extract_shadow_profile(trades, ShadowExtractRequest(min_trades=10, min_profitable_trades=5))

    assert profile.total_trades == 10
    assert profile.rules
