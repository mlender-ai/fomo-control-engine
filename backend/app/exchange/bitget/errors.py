from app.exchange.errors import MarketDataError


class BitgetAPIError(MarketDataError):
    def __init__(self, code: str, message: str, payload: dict | None = None) -> None:
        self.code = code
        self.message = message
        self.payload = payload
        super().__init__(f"Bitget API Error {code}: {message}")


class BitgetAuthError(BitgetAPIError):
    pass


class BitgetNotConfiguredError(BitgetAuthError):
    def __init__(self) -> None:
        super().__init__("not_configured", "Bitget private API is not configured.")


class BitgetPermissionError(BitgetAuthError):
    pass

