from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import TossApiError, TossAuthenticationError, TossEdgeBlocked, TossMaintenance, TossPathNotAllowed
from .rate_limit import TokenBucket

logger = logging.getLogger("toss.readonly")
_SHARED_BUCKETS: dict[tuple[str, int, str], TokenBucket] = {}
_SHARED_TOKENS: dict[tuple[str, int], _Token] = {}
_SHARED_TOKEN_LOCKS: dict[tuple[str, int], asyncio.Lock] = {}

TOKEN_PATH = "/oauth2/token"
ALLOWED_PATHS = (
    re.compile(r"^/api/v1/prices$"),
    re.compile(r"^/api/v1/orderbook$"),
    re.compile(r"^/api/v1/trades$"),
    re.compile(r"^/api/v1/price-limits$"),
    re.compile(r"^/api/v1/candles$"),
    re.compile(r"^/api/v1/stocks$"),
    re.compile(r"^/api/v1/stocks/[^/]+/warnings$"),
    re.compile(r"^/api/v1/exchange-rate$"),
    re.compile(r"^/api/v1/market-calendar/(KR|US)$"),
    re.compile(r"^/api/v1/rankings$"),
    re.compile(r"^/api/v1/market-indicators/.+$"),
)

PATH_GROUPS = {
    "/api/v1/prices": "MARKET_DATA",
    "/api/v1/orderbook": "MARKET_DATA",
    "/api/v1/trades": "MARKET_DATA",
    "/api/v1/price-limits": "MARKET_DATA",
    "/api/v1/candles": "MARKET_DATA_CHART",
    "/api/v1/stocks": "STOCK",
    "/api/v1/rankings": "RANKING",
    "/api/v1/exchange-rate": "MARKET_INFO",
}


def assert_allowed_path(path: str) -> None:
    if path == TOKEN_PATH or any(pattern.fullmatch(path) for pattern in ALLOWED_PATHS):
        return
    raise TossPathNotAllowed(f"Toss read-only path is not allowed: {path}")


def _group_for(path: str) -> str:
    if path == TOKEN_PATH:
        return "AUTH"
    if path.startswith("/api/v1/stocks/"):
        return "STOCK"
    if path.startswith("/api/v1/market-calendar/"):
        return "MARKET_INFO"
    if path == "/api/v1/market-indicators/prices":
        return "MARKET_INDICATOR_PRICE"
    if path.endswith("/candles") and path.startswith("/api/v1/market-indicators/"):
        return "MARKET_INDICATOR_CHART"
    if path.startswith("/api/v1/market-indicators/"):
        return "MARKET_INDICATOR"
    return PATH_GROUPS[path]


@dataclass
class _Token:
    value: str
    expires_at: float


class TossReadOnlyClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        *,
        base_url: str = "https://openapi.tossinvest.com",
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.client_id = client_id
        self._secret = client_secret
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_seconds, transport=transport)
        self._buckets = {
            "AUTH": TokenBucket(5, 5),
            "MARKET_DATA": TokenBucket(10, 10),
            "MARKET_DATA_CHART": TokenBucket(5, 5),
            "STOCK": TokenBucket(5, 5),
            "RANKING": TokenBucket(5, 5),
            "MARKET_INDICATOR": TokenBucket(5, 5),
            "MARKET_INDICATOR_PRICE": TokenBucket(10, 10),
            "MARKET_INDICATOR_CHART": TokenBucket(5, 5),
            "MARKET_INFO": TokenBucket(3, 3),
        }

    def _bucket(self, group: str) -> TokenBucket:
        """Share TPS budgets across KR/US clients using the same credential.

        Toss limits are client × API group, not Python-object local. The event
        loop id keeps test/application loops isolated while concurrent KR and US
        collectors consume one production budget.
        """
        key = (self.client_id, id(asyncio.get_running_loop()), group)
        bucket = _SHARED_BUCKETS.get(key)
        if bucket is None:
            template = self._buckets[group]
            bucket = TokenBucket(template.rate, template.capacity)
            _SHARED_BUCKETS[key] = bucket
        return bucket

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self._secret)

    async def close(self) -> None:
        await self._http.aclose()

    async def verify_access_token(self) -> None:
        """Verify issuance without exposing the token to diagnostic callers."""
        await self._access_token()

    async def _access_token(self, *, force: bool = False, rejected_token: str | None = None) -> str:
        if not self.configured:
            raise TossAuthenticationError("토스 API 인증값이 설정되지 않았습니다.")
        key = (self.client_id, id(asyncio.get_running_loop()))
        token = _SHARED_TOKENS.get(key)
        if not force and token and token.expires_at - time.time() > 60:
            return token.value
        lock = _SHARED_TOKEN_LOCKS.setdefault(key, asyncio.Lock())
        async with lock:
            token = _SHARED_TOKENS.get(key)
            # Another market may have refreshed while this request was in flight.
            # Reuse that newer token instead of issuing again and invalidating it.
            if token and token.expires_at - time.time() > 60 and (not force or (rejected_token is not None and token.value != rejected_token)):
                return token.value
            response: httpx.Response | None = None
            for attempt in range(5):
                await self._bucket("AUTH").acquire()
                response = await self._http.post(
                    TOKEN_PATH,
                    data={"grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self._secret},
                )
                if response.status_code != 429 and response.status_code < 500:
                    break
                if attempt < 4:
                    retry_after = float(response.headers.get("Retry-After") or 0)
                    await asyncio.sleep(max(retry_after, min(30.0, 2**attempt + random.random())))
            assert response is not None
            if response.status_code >= 400:
                details = _error_details(response)
                raise TossAuthenticationError(
                    "토스 인증 토큰 발급에 실패했습니다.",
                    status_code=response.status_code,
                    request_id=details["request_id"],
                    error_code=details["error_code"],
                    error_message=details["error_message"],
                )
            payload = response.json()
            value = str(payload.get("access_token") or payload.get("accessToken") or "")
            if not value:
                raise TossAuthenticationError("토스 인증 응답에 access token이 없습니다.")
            expires_in = max(60, int(payload.get("expires_in") or payload.get("expiresIn") or 3600))
            _SHARED_TOKENS[key] = _Token(value=value, expires_at=time.time() + expires_in)
            return value

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert_allowed_path(path)
        return await self._request(path, params=params, auth_retry=True, attempt=0)

    async def _request(self, path: str, *, params: dict[str, Any] | None, auth_retry: bool, attempt: int) -> dict[str, Any]:
        group = _group_for(path)
        bucket = self._bucket(group)
        await bucket.acquire()
        token = await self._access_token()
        response = await self._http.get(path, params=params, headers={"Authorization": f"Bearer {token}"})
        request_id = _request_id(response)
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining is not None:
            try:
                if float(remaining) <= bucket.capacity * 0.2:
                    bucket.slow(0.8)
            except ValueError:
                pass
        if response.status_code >= 400:
            logger.warning("toss request failed path=%s status=%s request_id=%s", path, response.status_code, request_id)
        if response.status_code == 401 and auth_retry:
            await self._access_token(force=True, rejected_token=token)
            return await self._request(path, params=params, auth_retry=False, attempt=attempt)
        if response.status_code == 401:
            details = _error_details(response)
            raise TossAuthenticationError(
                "토스 인증이 반복 실패해 수집을 중지합니다.",
                status_code=401,
                request_id=request_id,
                error_code=details["error_code"],
                error_message=details["error_message"],
            )
        if response.status_code == 429 and attempt < 5:
            retry_after = float(response.headers.get("Retry-After") or 0)
            await asyncio.sleep(max(retry_after, min(30.0, 2**attempt + random.random())))
            return await self._request(path, params=params, auth_retry=auth_retry, attempt=attempt + 1)
        if response.status_code == 403:
            raise TossEdgeBlocked("토스 API 허용 IP를 확인하세요.", status_code=403, request_id=request_id)
        if response.status_code >= 500:
            raise TossMaintenance("토스 API 점검으로 수집을 15분 중지합니다.", status_code=response.status_code, request_id=request_id)
        if response.status_code >= 400:
            raise TossApiError(f"토스 API 요청 실패 ({response.status_code})", status_code=response.status_code, request_id=request_id)
        logger.info("toss request ok path=%s request_id=%s", path, request_id)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}


def _request_id(response: httpx.Response) -> str | None:
    header = response.headers.get("requestId") or response.headers.get("X-Request-Id")
    if header:
        return header
    if response.status_code < 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    value = error.get("requestId") if isinstance(error, dict) else None
    return str(value) if value else None


def _error_details(response: httpx.Response) -> dict[str, str | None]:
    """Extract only server diagnostic fields; credentials/tokens are never returned."""
    request_id = _request_id(response)
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return {"request_id": request_id, "error_code": None, "error_message": text[:500] or None}
    error = payload.get("error") if isinstance(payload, dict) else None
    source = error if isinstance(error, dict) else payload if isinstance(payload, dict) else {}
    code = source.get("code") or source.get("errorCode") or source.get("error_code")
    message = source.get("message") or source.get("errorMessage") or source.get("error_description")
    nested_request_id = source.get("requestId") or source.get("request_id")
    return {
        "request_id": str(nested_request_id or request_id) if nested_request_id or request_id else None,
        "error_code": str(code) if code is not None else None,
        "error_message": str(message)[:500] if message is not None else None,
    }
