from app.db.models import MarketSnapshot
from app.exchange.base import MarketDataProvider


class BitgetReadOnlyClient(MarketDataProvider):
    """Read-only Bitget boundary.

    The concrete HTTP implementation is intentionally left out of v0.1 so API
    credentials can be added without touching scoring, reports, or dashboard code.
    """

    def __init__(self, api_key: str = "", api_secret: str = "", passphrase: str = "") -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase

    def get_snapshot(self, symbol: str, timeframe: str = "4h") -> MarketSnapshot:
        raise NotImplementedError("Bitget API wiring is pending read-only credential setup.")

