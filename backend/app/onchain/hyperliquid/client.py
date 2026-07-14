from __future__ import annotations

from typing import Any

import httpx


class HyperliquidInfoClient:
    """Small read-only client for Hyperliquid's unauthenticated info endpoint."""

    def __init__(self, base_url: str, *, timeout_seconds: float = 10.0) -> None:
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    def clearinghouse_state(self, address: str) -> dict[str, Any]:
        payload = self._post({"type": "clearinghouseState", "user": address})
        return payload if isinstance(payload, dict) else {}

    def meta(self) -> dict[str, Any]:
        payload = self._post({"type": "meta"})
        return payload if isinstance(payload, dict) else {}

    def user_fills_by_time(self, address: str, *, start_time_ms: int, end_time_ms: int) -> list[dict[str, Any]]:
        payload = self._post(
            {
                "type": "userFillsByTime",
                "user": address,
                "startTime": start_time_ms,
                "endTime": end_time_ms,
                "aggregateByTime": True,
            }
        )
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []

    def _post(self, payload: dict[str, Any]) -> Any:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(self.base_url, json=payload, headers={"Content-Type": "application/json"})
            response.raise_for_status()
            return response.json()
