from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.db.repository import SQLiteRepository
from app.exchange.bitget.client import BitgetClient
from app.exchange.bitget.provider import BitgetMarketDataProvider
from app.exchange.bitget.trades import BitgetAccountFill, parse_account_fill
from app.paper.service import paper_scoreboard, start_paper_benchmark, sync_user_fills
from app.paper.user_fills import reconstruct_user_trades


BASE = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _fill(
    trade_id: str,
    hours: int,
    *,
    side: str,
    trade_side: str,
    price: float,
    size: float,
    fee: float = 0.0,
    profit: float | None = None,
    symbol: str = "BTCUSDT",
) -> BitgetAccountFill:
    return BitgetAccountFill(
        trade_id=trade_id,
        symbol=symbol,
        price=price,
        size=size,
        side=side,
        trade_side=trade_side,
        position_mode="hedge_mode" if trade_side in {"open", "close"} else "one_way_mode",
        profit=profit,
        fee_usdt=fee,
        timestamp=BASE + timedelta(hours=hours),
    )


def test_parse_account_fill_keeps_private_position_fields_and_quote_fee() -> None:
    fill = parse_account_fill(
        {
            "tradeId": "t1",
            "orderId": "o1",
            "symbol": "btcusdt",
            "price": "100",
            "baseVolume": "2",
            "quoteVolume": "200",
            "side": "sell",
            "tradeSide": "close",
            "posMode": "hedge_mode",
            "profit": "12.5",
            "feeDetail": [
                {"feeCoin": "USDT", "totalFee": "-0.12"},
                {"feeCoin": "BGB", "totalFee": "-0.01"},
            ],
            "cTime": str(int(BASE.timestamp() * 1000)),
        }
    )

    assert fill.symbol == "BTCUSDT"
    assert fill.trade_side == "close"
    assert fill.profit == 12.5
    assert fill.fee_usdt == 0.12


@pytest.mark.asyncio
async def test_provider_account_fills_uses_authenticated_account_endpoint() -> None:
    client = BitgetClient(api_key="key", api_secret="secret", passphrase="pass")
    calls: list[tuple[str, dict]] = []

    async def private_get(path: str, params: dict | None = None) -> dict:
        calls.append((path, params or {}))
        return {
            "data": {
                "fillList": [
                    {
                        "tradeId": "t1",
                        "symbol": "BTCUSDT",
                        "price": "100",
                        "baseVolume": "1",
                        "side": "buy",
                        "tradeSide": "open",
                        "cTime": str(int(BASE.timestamp() * 1000)),
                    }
                ],
                "endId": "t1",
            }
        }

    client.private_get = private_get  # type: ignore[method-assign]
    provider = BitgetMarketDataProvider(client)
    fills = await provider.get_account_trade_fills(BASE - timedelta(days=1), BASE + timedelta(days=1))

    assert len(fills) == 1
    assert calls[0][0] == "/api/v2/mix/order/fills"
    assert calls[0][1]["productType"] == "USDT-FUTURES"


def test_reconstructs_multiple_entries_and_partial_closes() -> None:
    fills = [
        _fill("open-1", 0, side="buy", trade_side="open", price=100, size=1, fee=0.1),
        _fill("open-2", 1, side="buy", trade_side="open", price=102, size=0.5, fee=0.05),
        _fill("close-1", 2, side="buy", trade_side="close", price=110, size=0.5, fee=0.05, profit=4.5),
        _fill("close-2", 3, side="buy", trade_side="close", price=108, size=1, fee=0.1, profit=7.0),
    ]

    trades, diagnostics = reconstruct_user_trades(fills)

    assert len(trades) == 1
    trade = trades[0]
    assert trade.direction.value == "long"
    assert trade.quantity == 1.5
    assert trade.entry_price == pytest.approx(100.66666667)
    assert trade.exit_price == pytest.approx(108.66666667)
    assert trade.gross_pnl_usdt == pytest.approx(12.0)
    assert trade.net_pnl_usdt == pytest.approx(11.7)
    assert trade.exchange_reported_profit_usdt == 11.5
    assert trade.fill_count == 4
    assert trade.pnl_status == "reconstructed"
    assert diagnostics["unmatched_close_fills"] == 0


def test_one_way_reversal_closes_short_and_opens_long_remainder() -> None:
    fills = [
        _fill("sell-1", 0, side="sell", trade_side="sell_single", price=100, size=2),
        _fill("buy-1", 1, side="buy", trade_side="buy_single", price=90, size=0.5),
        _fill("buy-2", 2, side="buy", trade_side="buy_single", price=95, size=2),
    ]

    trades, diagnostics = reconstruct_user_trades(fills)

    assert len(trades) == 1
    assert trades[0].direction.value == "short"
    assert trades[0].quantity == 2
    assert trades[0].gross_pnl_usdt == pytest.approx(12.5)
    assert diagnostics["open_positions"] == 1


def test_sync_persists_all_closed_trades_and_splits_benchmark_from_recent_window(tmp_path) -> None:
    repo = SQLiteRepository(tmp_path / "user-fills.db")
    benchmark_at = BASE + timedelta(hours=10)
    start_paper_benchmark(repo, now=benchmark_at)
    fills = [
        _fill("old-open", 0, side="buy", trade_side="open", price=100, size=1),
        _fill("old-close", 1, side="buy", trade_side="close", price=101, size=1),
        _fill("new-open", 11, side="sell", trade_side="open", price=100, size=1),
        _fill("new-close", 12, side="sell", trade_side="close", price=90, size=1),
    ]
    provider = SimpleNamespace(
        client=SimpleNamespace(private_configured=True),
        get_account_fills=lambda start, end: fills,
        get_positions=lambda: [],
    )

    status = sync_user_fills(repo, provider, now=BASE + timedelta(hours=13))

    assert status["status"] == "ok"
    assert status["stored_fill_count"] == 4
    assert status["reconstructed_trade_count"] == 2
    assert status["benchmark_trade_count"] == 1
    assert status["diagnostics"]["live_position_reconciliation"]["status"] == "matched"
    reopened = SQLiteRepository(tmp_path / "user-fills.db")
    assert len(reopened.list_user_account_fills()) == 4
    assert len(reopened.list_user_trades()) == 2
    board = paper_scoreboard(reopened, Settings(), now=BASE + timedelta(hours=14))
    assert board["user"]["trade_count"] == 1
    assert board["recent_28d"]["user"]["trade_count"] == 2
    assert board["user"]["net_return_pct"] > 0
    assert board["user_fill_sync"]["pnl_status"] == "reconstructed"


def test_reconstructs_bitget_hedge_close_with_same_position_side() -> None:
    fills = [
        _fill("1460571240714211328", 0, side="buy", trade_side="open", price=1.9471, size=66),
        _fill("1460571244178706433", 0, side="buy", trade_side="open", price=1.9471, size=85),
        _fill("1460574175183507456", 1, side="buy", trade_side="close", price=1.9510, size=118),
        _fill("1460574175183507462", 1, side="buy", trade_side="close", price=1.9508, size=33),
    ]

    trades, diagnostics = reconstruct_user_trades(fills)

    assert len(trades) == 1
    assert trades[0].direction.value == "long"
    assert trades[0].quantity == 151
    assert trades[0].net_pnl_usdt > 0
    assert diagnostics == {
        "closed_positions": 1,
        "open_positions": 0,
        "open_position_details": [],
        "unmatched_close_fills": 0,
        "ignored_fills": 0,
    }
