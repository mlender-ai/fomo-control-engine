from __future__ import annotations

from .backtest import MemoryBacktestRepositoryMixin, SQLiteBacktestRepositoryMixin
from .base import MemoryRepositoryBase, Repository, SQLiteRepositoryBase
from .ledger import MemoryLedgerRepositoryMixin, SQLiteLedgerRepositoryMixin
from .instrument_map import MemoryInstrumentMapRepositoryMixin, SQLiteInstrumentMapRepositoryMixin
from .marketdata import MemoryMarketdataRepositoryMixin, SQLiteMarketdataRepositoryMixin
from .paper import MemoryPaperRepositoryMixin, SQLitePaperRepositoryMixin
from .positions import MemoryPositionsRepositoryMixin, SQLitePositionsRepositoryMixin
from .scout import MemoryScoutRepositoryMixin, SQLiteScoutRepositoryMixin


class MemoryRepository(
    MemoryPositionsRepositoryMixin,
    MemoryInstrumentMapRepositoryMixin,
    MemoryScoutRepositoryMixin,
    MemoryPaperRepositoryMixin,
    MemoryBacktestRepositoryMixin,
    MemoryLedgerRepositoryMixin,
    MemoryMarketdataRepositoryMixin,
    MemoryRepositoryBase,
):
    pass


class SQLiteRepository(
    SQLitePositionsRepositoryMixin,
    SQLiteInstrumentMapRepositoryMixin,
    SQLiteScoutRepositoryMixin,
    SQLitePaperRepositoryMixin,
    SQLiteBacktestRepositoryMixin,
    SQLiteLedgerRepositoryMixin,
    SQLiteMarketdataRepositoryMixin,
    SQLiteRepositoryBase,
):
    pass


def create_repository(database_url: str) -> Repository:
    if database_url == "memory://":
        return MemoryRepository()
    if database_url.startswith("sqlite:///"):
        return SQLiteRepository(database_url.removeprefix("sqlite:///"))
    raise ValueError(f"Unsupported database URL: {database_url}")


__all__ = ["MemoryRepository", "Repository", "SQLiteRepository", "create_repository"]
