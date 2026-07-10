from __future__ import annotations

from app.analyst.briefing import load_directional_prior, persist_directional_state
from app.db.repository import MemoryRepository, SQLiteRepository


def _briefing(last_bar_at: str, stance: str = "long_leaning") -> dict:
    return {
        "confluence": {
            "stance_state": {
                "stance": stance,
                "since": last_bar_at,
                "last_bar_at": last_bar_at,
                "candles_in_state": 1,
                "preview": {"raw_stance": stance},
            }
        }
    }


class _SnapshotForbiddenRepository(MemoryRepository):
    def latest_scout_snapshot(self, symbol: str, timeframe: str | None = None):  # pragma: no cover - must not run
        raise AssertionError("directional state must not read scout snapshots")


def test_directional_state_prior_is_independent_from_scout_snapshot() -> None:
    repo = _SnapshotForbiddenRepository()
    first = _briefing("2026-07-10T00:00:00+00:00")

    assert load_directional_prior(repo, "POSITIONONLYUSDT", "4h") is None
    assert persist_directional_state(repo, "POSITIONONLYUSDT", "4h", first) is True

    prior = load_directional_prior(repo, "POSITIONONLYUSDT", "4h")
    assert prior is not None
    assert prior["stance"] == "long_leaning"
    assert prior["last_bar_at"] == "2026-07-10T00:00:00+00:00"


def test_same_confirmed_candle_does_not_write_again() -> None:
    repo = MemoryRepository()
    first = _briefing("2026-07-10T00:00:00+00:00")
    same_bar_with_new_preview = _briefing("2026-07-10T00:00:00+00:00", stance="short_leaning")

    assert persist_directional_state(repo, "BTCUSDT", "4h", first) is True
    assert persist_directional_state(repo, "BTCUSDT", "4h", same_bar_with_new_preview) is False
    assert load_directional_prior(repo, "BTCUSDT", "4h")["stance"] == "long_leaning"


def test_sqlite_directional_state_migration_and_reopen(tmp_path) -> None:
    database_path = tmp_path / "fce.db"
    repo = SQLiteRepository(str(database_path))
    briefing = _briefing("2026-07-10T04:00:00+00:00", stance="short_leaning")

    assert persist_directional_state(repo, "ETHUSDT", "4h", briefing) is True
    assert persist_directional_state(repo, "ETHUSDT", "4h", briefing) is False

    reopened = SQLiteRepository(str(database_path))
    state = load_directional_prior(reopened, "ETHUSDT", "4h")
    assert state is not None
    assert state["stance"] == "short_leaning"
    assert state["last_bar_at"] == "2026-07-10T04:00:00+00:00"
    assert reopened.list_directional_states() == [
        {
            "symbol": "ETHUSDT",
            "timeframe": "4h",
            "stance": "short_leaning",
            "since": "2026-07-10T04:00:00+00:00",
            "last_bar_at": "2026-07-10T04:00:00+00:00",
            "updated_at": state["updated_at"],
        }
    ]
