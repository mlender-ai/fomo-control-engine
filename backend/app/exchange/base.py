from abc import ABC, abstractmethod

from app.db.models import MarketSnapshot


class MarketDataProvider(ABC):
    @abstractmethod
    def get_snapshot(self, symbol: str, timeframe: str = "4h") -> MarketSnapshot:
        raise NotImplementedError
