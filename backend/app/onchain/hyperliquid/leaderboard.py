from __future__ import annotations

import math
import re
from typing import Any

import httpx

from app.db.models import WhaleWallet, utc_now

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
DISCOVERY_CACHE_KEY = "hyperliquid_leaderboard_discovery"


class HyperliquidLeaderboardClient:
    """Read-only client for the public leaderboard dataset used by Hyperliquid."""

    def __init__(self, url: str, *, timeout_seconds: float = 20.0) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def leaderboard(self) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.get(self.url, headers={"Accept": "application/json"})
            response.raise_for_status()
            payload = response.json()
        return payload if isinstance(payload, dict) else {}


def discover_leaderboard_wallets(repo: Any, settings: Any, client: Any | None = None) -> dict[str, Any]:
    if not bool(settings.hyperliquid_whale_discovery_enabled):
        return {"enabled": False, "status": "disabled", "selected_count": 0}

    leaderboard_client = client or HyperliquidLeaderboardClient(
        settings.hyperliquid_leaderboard_url,
        timeout_seconds=max(10.0, float(settings.hyperliquid_request_timeout_seconds) * 2),
    )
    now = utc_now()
    payload = leaderboard_client.leaderboard()
    rows = payload.get("leaderboardRows") or []
    criteria = _criteria(settings)
    eligible = select_candidates(rows, criteria)

    all_wallets = repo.list_whale_wallets(limit=1000)
    manual_wallets = [wallet for wallet in all_wallets if wallet.active and wallet.source != "discovery"]
    discovery_slots = max(0, int(settings.hyperliquid_whale_max_wallets) - len(manual_wallets))
    selected = eligible[:discovery_slots]
    selected_addresses = {str(item["address"]) for item in selected}

    for wallet in all_wallets:
        if wallet.source == "discovery" and wallet.address.lower() not in selected_addresses and wallet.active:
            repo.upsert_whale_wallet(wallet.model_copy(update={"active": False, "updated_at": now}))

    for rank, item in enumerate(selected, start=1):
        address = str(item["address"])
        existing = repo.get_whale_wallet(address)
        display_name = str(item.get("display_name") or "").strip()
        label = display_name[:40] if display_name else f"리더보드 고래 #{rank}"
        wallet = WhaleWallet(
            address=address,
            label=existing.label if existing and existing.source != "discovery" else label,
            source=existing.source if existing and existing.source != "discovery" else "discovery",
            active=True,
            added_at=existing.added_at if existing else now,
            updated_at=now,
            last_polled_at=existing.last_polled_at if existing else None,
            last_fill_at=existing.last_fill_at if existing else None,
            payload={
                **(existing.payload if existing else {}),
                "discovery": {**item, "selection_rank": rank, "as_of": now.isoformat()},
            },
        )
        repo.upsert_whale_wallet(wallet)

    result = {
        "enabled": True,
        "status": "ok",
        "as_of": now.isoformat(),
        "rows_scanned": len(rows),
        "eligible_count": len(eligible),
        "selected_count": len(selected),
        "manual_count": len(manual_wallets),
        "criteria": criteria,
        "selected": selected,
        "source": "Hyperliquid public leaderboard",
    }
    repo.upsert_calibration_report_cache(DISCOVERY_CACHE_KEY, result)
    return result


def select_candidates(rows: list[Any], criteria: dict[str, float]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        address = str(row.get("ethAddress") or "").lower()
        if not ADDRESS_RE.fullmatch(address):
            continue
        account_value = _float(row.get("accountValue"))
        month = _window(row, "month")
        pnl = _float(month.get("pnl"))
        roi = _float(month.get("roi"))
        volume = _float(month.get("vlm"))
        turnover = volume / account_value if account_value > 0 else math.inf
        if (
            account_value < criteria["min_account_usd"]
            or pnl < criteria["min_month_pnl_usd"]
            or roi < criteria["min_month_roi"]
            or volume < criteria["min_month_volume_usd"]
            or turnover > criteria["max_turnover"]
        ):
            continue
        quality_score = math.log10(max(1.0, pnl)) * 30 + min(roi, 1.0) * 100 + math.log10(max(1.0, account_value)) * 8
        candidates.append(
            {
                "address": address,
                "display_name": row.get("displayName"),
                "account_value_usd": round(account_value, 2),
                "month_pnl_usd": round(pnl, 2),
                "month_roi": roi,
                "month_volume_usd": round(volume, 2),
                "turnover": round(turnover, 2),
                "quality_score": round(quality_score, 4),
            }
        )
    return sorted(candidates, key=lambda item: (float(item["quality_score"]), float(item["month_pnl_usd"])), reverse=True)


def cached_discovery(repo: Any) -> dict[str, Any]:
    cached = repo.get_calibration_report_cache(DISCOVERY_CACHE_KEY)
    if not isinstance(cached, dict):
        return {"enabled": True, "status": "pending", "selected_count": 0}
    payload = cached.get("payload")
    return dict(payload) if isinstance(payload, dict) else {"enabled": True, "status": "pending", "selected_count": 0}


def _window(row: dict[str, Any], name: str) -> dict[str, Any]:
    for item in row.get("windowPerformances") or []:
        if isinstance(item, list) and len(item) == 2 and item[0] == name and isinstance(item[1], dict):
            return item[1]
    return {}


def _criteria(settings: Any) -> dict[str, float]:
    return {
        "min_account_usd": float(settings.hyperliquid_whale_discovery_min_account_usd),
        "min_month_pnl_usd": float(settings.hyperliquid_whale_discovery_min_month_pnl_usd),
        "min_month_roi": float(settings.hyperliquid_whale_discovery_min_month_roi),
        "min_month_volume_usd": float(settings.hyperliquid_whale_discovery_min_month_volume_usd),
        "max_turnover": float(settings.hyperliquid_whale_discovery_max_turnover),
    }


def _float(value: Any) -> float:
    try:
        result = float(value or 0)
        return result if math.isfinite(result) else 0.0
    except (TypeError, ValueError):
        return 0.0
