from __future__ import annotations

import asyncio
from urllib.parse import urlencode

import httpx

from app.exchange.bitget.errors import (
    BitgetAPIError,
    BitgetNotConfiguredError,
    BitgetPermissionError,
)
from app.exchange.bitget.signer import get_timestamp_ms, sign_bitget_request


PRIVATE_PERMISSION_CODES = {
    "40037",
    "40038",
    "40039",
    "40040",
    "40041",
    "40043",
    "40044",
    "40045",
    "40046",
    "40047",
}


class BitgetClient:
    def __init__(
        self,
        base_url: str = "https://api.bitget.com",
        api_key: str = "",
        api_secret: str = "",
        passphrase: str = "",
        locale: str = "en-US",
        timeout: float = 10.0,
        retries: int = 1,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.locale = locale
        self.timeout = timeout
        self.retries = retries

    @property
    def private_configured(self) -> bool:
        return bool(self.api_key and self.api_secret and self.passphrase)

    async def public_get(self, path: str, params: dict | None = None) -> dict:
        return await self._request("GET", path, params=params, private=False)

    async def private_get(self, path: str, params: dict | None = None) -> dict:
        if not self.private_configured:
            raise BitgetNotConfiguredError()
        return await self._request("GET", path, params=params, private=True)

    async def _request(self, method: str, path: str, params: dict | None = None, private: bool = False) -> dict:
        params = {key: value for key, value in (params or {}).items() if value is not None}
        headers = {"locale": self.locale, "Content-Type": "application/json"}
        if private:
            query_string = urlencode(params)
            timestamp = get_timestamp_ms()
            headers.update(
                {
                    "ACCESS-KEY": self.api_key,
                    "ACCESS-TIMESTAMP": timestamp,
                    "ACCESS-PASSPHRASE": self.passphrase,
                    "ACCESS-SIGN": sign_bitget_request(
                        self.api_secret,
                        timestamp,
                        method,
                        path,
                        query_string=query_string or None,
                    ),
                }
            )

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
                    response = await client.request(method, path, params=params, headers=headers)
                    response.raise_for_status()
                payload = response.json()
                code = str(payload.get("code", ""))
                if code != "00000":
                    message = str(payload.get("msg", "unknown Bitget error"))
                    if private and code in PRIVATE_PERMISSION_CODES:
                        raise BitgetPermissionError(code, _safe_message(message), payload=_safe_payload(payload))
                    raise BitgetAPIError(
                        code or "unknown",
                        _safe_message(message),
                        payload=_safe_payload(payload),
                    )
                return payload
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt < self.retries:
                    await asyncio.sleep(0.25 * (2**attempt))
                    continue
                raise BitgetAPIError("network_error", "Bitget API temporarily unavailable.") from exc
            except httpx.HTTPStatusError as exc:
                raise BitgetAPIError(str(exc.response.status_code), "Bitget HTTP request failed.") from exc

        raise BitgetAPIError("unknown", "Bitget request failed.") from last_error


def _safe_message(message: str) -> str:
    lowered = message.lower()
    if "passphrase" in lowered:
        return "Bitget authentication failed. Check API key, secret, and passphrase."
    if "signature" in lowered or "sign" in lowered:
        return "Bitget authentication failed. Check API key, secret, and passphrase."
    return message


def _safe_payload(payload: dict | None) -> dict | None:
    if payload is None:
        return None
    return {key: value for key, value in payload.items() if key.lower() not in {"access-key", "access-sign", "access-passphrase"}}
