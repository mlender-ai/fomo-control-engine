from app.db.models import Direction, Position, Report
from app.db.repository import create_repository
from app.exchange.mock import MockMarketDataProvider
from app.report.engine import generate_report


def test_sqlite_repository_persists_reports_and_positions(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'fomo-test.db'}"
    provider = MockMarketDataProvider()
    report = generate_report(provider.get_snapshot("ETHUSDT"))

    repo = create_repository(database_url)
    repo.add_report(report)
    repo.add_position(
        Position(
            symbol="ETHUSDT",
            direction=Direction.long,
            entry_price=report.price,
            quantity=0.5,
            entry_report_id=report.id,
            entry_score=report.entry_score,
        )
    )

    reopened = create_repository(database_url)
    latest = reopened.latest_report("ETHUSDT")
    positions = reopened.list_positions()

    assert isinstance(latest, Report)
    assert latest.id == report.id
    assert len(positions) == 1
    assert positions[0].entry_report_id == report.id


def test_sqlite_repository_persists_entry_block_logs(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'entry-blocks.db'}"
    repo = create_repository(database_url)
    record = {
        "id": "block-1",
        "symbol": "BTCUSDT",
        "timeframe": "4h",
        "bar_at": "2026-07-15T00:00:00+00:00",
        "direction": "long",
        "failed_gate": "checklist",
        "detail": "체크리스트 3/6",
        "created_at": "2026-07-15T00:01:00+00:00",
    }

    assert repo.upsert_entry_block_log(record) is True
    assert repo.upsert_entry_block_log(record) is False
    reopened = create_repository(database_url)
    assert reopened.list_entry_block_logs(symbol="BTCUSDT") == [record]
