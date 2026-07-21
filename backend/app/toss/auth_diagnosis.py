from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.db.sqlite_utils import connect_sqlite

from .client import TossReadOnlyClient
from .errors import TossApiError


_STAGES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("market_calendar", "/api/v1/market-calendar/US", None),
    ("market_data", "/api/v1/prices", {"symbols": "QQQ"}),
    ("market_chart", "/api/v1/candles", {"symbol": "QQQ", "interval": "1d", "count": 1, "adjusted": "true"}),
    ("stock", "/api/v1/stocks", {"symbols": "QQQ"}),
    ("ranking", "/api/v1/rankings", {"type": "MARKET_TRADING_AMOUNT", "marketCountry": "US", "duration": "realtime", "count": 1}),
)


async def diagnose_toss_auth(settings: Settings) -> dict[str, Any]:
    observed_at = datetime.now(timezone.utc).isoformat()
    configured = bool(settings.toss_client_id and settings.toss_client_secret)
    result: dict[str, Any] = {
        "observed_at": observed_at,
        "configured": configured,
        "base_url": settings.toss_base_url,
        "credentials_exposed": False,
        "stages": [],
    }
    if not configured:
        result["stages"].append({"stage": "token", "status": "failed", "error_message": "credentials_not_configured"})
        _persist(settings.database_url, result)
        return result
    client = TossReadOnlyClient(
        settings.toss_client_id,
        settings.toss_client_secret,
        base_url=settings.toss_base_url,
        timeout_seconds=settings.toss_timeout_seconds,
    )
    try:
        try:
            await client.verify_access_token()
            result["stages"].append({"stage": "token", "status": "ok"})
        except TossApiError as exc:
            result["stages"].append(_failure("token", exc))
            return result
        for stage, path, params in _STAGES:
            try:
                await client.get(path, params=params)
                result["stages"].append({"stage": stage, "status": "ok"})
            except TossApiError as exc:
                result["stages"].append(_failure(stage, exc))
    finally:
        await client.close()
        _persist(settings.database_url, result)
    if result["stages"] and all(stage["status"] == "ok" for stage in result["stages"]):
        from .service import clear_authentication_blocks

        clear_authentication_blocks()
    return result


def _failure(stage: str, exc: TossApiError) -> dict[str, Any]:
    return {
        "stage": stage,
        "status": "failed",
        "status_code": exc.status_code,
        "error_code": exc.error_code,
        "error_message": exc.error_message or str(exc),
        "request_id": exc.request_id,
    }


def _persist(database_url: str, payload: dict[str, Any]) -> None:
    path = database_url.removeprefix("sqlite:///") if database_url.startswith("sqlite:///") else ""
    if not path:
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with connect_sqlite(path) as connection:
        connection.execute(
            """INSERT INTO toss_auth_diagnostics (observed_at, configured, base_url, payload)
            VALUES (?, ?, ?, ?)""",
            (payload["observed_at"], int(payload["configured"]), payload["base_url"], json.dumps(payload, ensure_ascii=False)),
        )
