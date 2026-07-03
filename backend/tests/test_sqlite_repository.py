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

