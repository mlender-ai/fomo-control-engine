class TossApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, request_id: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class TossPathNotAllowed(TossApiError):
    pass


class TossEdgeBlocked(TossApiError):
    pass


class TossMaintenance(TossApiError):
    pass


class TossAuthenticationError(TossApiError):
    pass
