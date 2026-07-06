from __future__ import annotations

from datetime import timedelta

from app.db.models import Direction, Position, PositionStatus, WatchlistItem, utc_now
from app.report.engine import generate_report


DEMO_POSITIONS = [
    {
        "symbol": "BTCUSDT",
        "direction": Direction.long,
        "entry_price": 100_000.0,
        "quantity": 0.02,
        "leverage": 5,
        "planned_stop_price": 103_000.0,
        "planned_take_profit_price": 120_000.0,
        "liquidation_price": 82_000.0,
        "memo": "DEMO healthy_long: 수익 롱, 구조 유지",
    },
    {
        "symbol": "ETHUSDT",
        "direction": Direction.short,
        "entry_price": 3_400.0,
        "quantity": 0.5,
        "leverage": 10,
        "planned_stop_price": 3_650.0,
        "planned_take_profit_price": 3_000.0,
        "liquidation_price": 3_740.0,
        "memo": "DEMO critical_short: 청산 근접 숏",
    },
    {
        "symbol": "BASEDUSDT",
        "direction": Direction.long,
        "entry_price": 0.096,
        "quantity": 3_000.0,
        "leverage": 10,
        "planned_stop_price": 0.0863,
        "planned_take_profit_price": 0.1104,
        "liquidation_price": 0.074,
        "memo": "DEMO wyckoff_range: 레인지 + Spring/PRZ 검증",
    },
]


def seed_demo_data(repository, provider) -> dict:
    existing_demo = [position for position in repository.list_positions() if position.source == "demo"]
    if existing_demo:
        return {"seeded": False, "positions": len(existing_demo)}

    created = 0
    now = utc_now()
    for item in DEMO_POSITIONS:
        snapshot = provider.get_snapshot(item["symbol"], "4h")
        report = repository.add_report(generate_report(snapshot))
        mark = snapshot.price
        position = Position(
            symbol=item["symbol"],
            direction=item["direction"],
            entry_price=item["entry_price"],
            quantity=item["quantity"],
            leverage=item["leverage"],
            status=PositionStatus.open,
            entry_report_id=report.id,
            entry_score=report.entry_score,
            current_score=report.entry_score,
            current_price=mark,
            mark_price=mark,
            liquidation_price=item["liquidation_price"],
            planned_stop_price=item["planned_stop_price"],
            planned_take_profit_price=item["planned_take_profit_price"],
            source="demo",
            detected_source="demo",
            synced_at=now,
            opened_at=now - timedelta(hours=72 + created * 8),
            memo=item["memo"],
            thesis_text="FCE_DEMO_MODE 고정 시나리오입니다. 라이브 거래소 데이터가 아닙니다.",
        )
        repository.add_position(position)
        repository.upsert_watchlist_item(WatchlistItem(symbol=item["symbol"], note="FCE demo fixture", default_timeframe="4h"))
        created += 1
    return {"seeded": True, "positions": created}
