from app.db.models import Direction, Trade
from app.memory.engine import memory_from_trade


def test_memory_created_from_trade() -> None:
    trade = Trade(
        position_id="00000000-0000-0000-0000-000000000001",
        symbol="BTCUSDT",
        direction=Direction.long,
        entry_price=100,
        exit_price=95,
        quantity=1,
        pnl_percent=-5,
        pnl_amount=-5,
        entry_score=55,
        exit_score=40,
        holding_minutes=30,
        exit_reason="test",
        review_text="",
    )

    memory = memory_from_trade(trade)

    assert memory.memory_type == "fomo_mistake"
    assert memory.symbol == "BTCUSDT"
