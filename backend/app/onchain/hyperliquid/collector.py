from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.backtest.candidate_scoring import CANDIDATE_SENTINEL_POSITION_ID
from app.db.models import BacktestStat, JudgmentLedgerEntry, WhaleEvent, WhaleWallet, utc_now


def collect_whale_positions(repo: Any, settings: Any, client: Any) -> dict[str, Any]:
    wallets = repo.list_whale_wallets(active=True, limit=max(1, int(settings.hyperliquid_whale_max_wallets)))
    now = utc_now()
    created_events: list[WhaleEvent] = []
    errors: list[dict[str, str]] = []
    position_count = 0
    catalog_error: str | None = None
    if hasattr(client, "meta"):
        try:
            universe = client.meta().get("universe") or []
            symbols = sorted({coin_to_symbol(str(item.get("name") or "")) for item in universe if isinstance(item, dict)} - {""})
            repo.upsert_calibration_report_cache("hyperliquid_symbol_catalog", {"symbols": symbols, "as_of": now.isoformat()})
        except Exception as exc:
            catalog_error = f"{type(exc).__name__}: {exc}"
    for wallet in wallets:
        baseline = wallet.last_polled_at is None
        try:
            state = client.clearinghouse_state(wallet.address)
            positions = _positions(state, wallet, now)
            current_coins = {str(position["coin"]).upper() for position in positions}
            for previous in repo.list_whale_position_states(wallet.address, limit=500):
                previous_coin = str(previous.get("coin") or "").upper()
                if previous_coin and previous_coin not in current_coins:
                    repo.delete_whale_position_state(wallet.address, previous_coin)
            for position in positions:
                repo.upsert_whale_position_state(wallet.address, str(position["coin"]), position)
            position_count += len(positions)

            start = wallet.last_fill_at or (now - timedelta(hours=max(1, int(settings.hyperliquid_whale_initial_lookback_hours))))
            fills = client.user_fills_by_time(
                wallet.address,
                start_time_ms=max(0, int(start.timestamp() * 1000) + (1 if wallet.last_fill_at else 0)),
                end_time_ms=int(now.timestamp() * 1000),
            )
            latest_fill = wallet.last_fill_at
            _ensure_candidate_stat(repo, wallet)
            for fill in sorted(fills, key=lambda item: int(item.get("time") or 0)):
                fill_at = _datetime_ms(fill.get("time"))
                if fill_at is not None:
                    latest_fill = max(latest_fill or fill_at, fill_at)
                event = event_from_fill(wallet, fill, baseline=baseline)
                if event is None or event.size_usd < float(settings.hyperliquid_whale_min_size_usd):
                    continue
                if repo.add_whale_event(event):
                    created_events.append(event)
                    if event.event in {"open", "flip"}:
                        _record_candidate_observation(repo, event)
            repo.upsert_whale_wallet(
                wallet.model_copy(update={"last_polled_at": now, "last_fill_at": latest_fill, "updated_at": now})
            )
        except Exception as exc:
            errors.append({"address": wallet.address, "error": f"{type(exc).__name__}: {exc}"})
    return {
        "wallets": len(wallets),
        "positions": position_count,
        "created": len(created_events),
        "events": [event.model_dump(mode="json") for event in created_events],
        "errors": errors,
        "catalog_error": catalog_error,
        "rate_budget": _rate_budget(len(wallets)),
    }


def event_from_fill(wallet: WhaleWallet, fill: dict[str, Any], *, baseline: bool = False) -> WhaleEvent | None:
    coin = str(fill.get("coin") or "").upper()
    price = _float(fill.get("px"))
    size = abs(_float(fill.get("sz")))
    start = _float(fill.get("startPosition"))
    side_code = str(fill.get("side") or "").upper()
    event_at = _datetime_ms(fill.get("time"))
    if not coin or price <= 0 or size <= 0 or side_code not in {"A", "B"} or event_at is None:
        return None
    delta = size if side_code == "B" else -size
    end = start + delta
    event, side = _classify_position_change(start, end)
    fill_id = str(fill.get("tid") or fill.get("oid") or fill.get("hash") or f"{coin}:{int(event_at.timestamp() * 1000)}")
    event_id = uuid5(NAMESPACE_URL, f"fce:hyperliquid:{wallet.address.lower()}:{fill_id}:{coin}:{event}")
    return WhaleEvent(
        id=event_id,
        wallet_address=wallet.address.lower(),
        wallet_label=wallet.label,
        coin=coin,
        symbol=coin_to_symbol(coin),
        side=side,
        event=event,
        size=size,
        size_usd=round(size * price, 2),
        entry_px=price,
        mark_px=price,
        event_at=event_at,
        fill_id=fill_id,
        payload={"baseline": baseline, "dir": fill.get("dir"), "closed_pnl": fill.get("closedPnl"), "raw": fill},
    )


def coin_to_symbol(coin: str) -> str:
    value = coin.strip().upper()
    if not value or value.startswith("@") or "/" in value or ":" in value:
        return ""
    return f"{value}USDT"


def whale_signature_key(address: str) -> str:
    return f"whale_entry_{address.lower()}"


def _positions(state: dict[str, Any], wallet: WhaleWallet, now: datetime) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in state.get("assetPositions") or []:
        position = item.get("position") if isinstance(item, dict) and isinstance(item.get("position"), dict) else {}
        size = _float(position.get("szi"))
        coin = str(position.get("coin") or "").upper()
        if not coin or abs(size) <= 1e-12:
            continue
        entry_px = _optional_float(position.get("entryPx"))
        position_value = abs(_float(position.get("positionValue")))
        mark_px = position_value / abs(size) if position_value > 0 else entry_px
        result.append(
            {
                "wallet_address": wallet.address.lower(),
                "wallet_label": wallet.label,
                "coin": coin,
                "symbol": coin_to_symbol(coin),
                "side": "long" if size > 0 else "short",
                "size": abs(size),
                "size_usd": position_value,
                "entry_px": entry_px,
                "mark_px": mark_px,
                "unrealized_pnl": _optional_float(position.get("unrealizedPnl")),
                "liquidation_px": _optional_float(position.get("liquidationPx")),
                "leverage": position.get("leverage"),
                "as_of": now.isoformat(),
            }
        )
    return result


def _classify_position_change(start: float, end: float) -> tuple[str, str]:
    epsilon = 1e-12
    if abs(start) <= epsilon:
        return "open", "long" if end > 0 else "short"
    if abs(end) <= epsilon:
        return "close", "long" if start > 0 else "short"
    if (start > 0) != (end > 0):
        return "flip", "long" if end > 0 else "short"
    if abs(end) > abs(start):
        return "increase", "long" if end > 0 else "short"
    return "reduce", "long" if start > 0 else "short"


def _ensure_candidate_stat(repo: Any, wallet: WhaleWallet) -> None:
    key = whale_signature_key(wallet.address)
    if repo.list_backtest_stats(signature_key=key, limit=1):
        return
    repo.upsert_backtest_stat(
        BacktestStat(
            signature_key=key,
            symbol="__WHALE__",
            timeframe="4h",
            asset_class="crypto",
            scope="all",
            engine="whale",
            event_type="whale_entry",
            strength_class="candidate",
            direction="neutral",
            payload={
                "label": f"{wallet.label} 고래 진입",
                "signature": {"wallet_address": wallet.address.lower(), "wallet_label": wallet.label},
                "source": "live_observation",
            },
        )
    )


def _record_candidate_observation(repo: Any, event: WhaleEvent) -> None:
    judgment_id = f"whale:{event.id}"
    existing = repo.list_judgments(CANDIDATE_SENTINEL_POSITION_ID, limit=10000)
    if any(item.judgment_id == judgment_id for item in existing):
        return
    repo.add_judgment(
        JudgmentLedgerEntry(
            id=uuid5(NAMESPACE_URL, f"fce:whale-judgment:{event.id}"),
            judgment_id=judgment_id,
            position_id=CANDIDATE_SENTINEL_POSITION_ID,
            source_type="hyperliquid_fill",
            source_id=str(event.id),
            as_of=event.event_at,
            type="candidate_signature",
            claim={
                "signature_key": whale_signature_key(event.wallet_address),
                "engine": "whale",
                "event_type": "whale_entry",
                "wallet_address": event.wallet_address,
                "wallet_label": event.wallet_label,
                "symbol": event.symbol,
                "timeframe": "4h",
                "direction": event.side,
                "price": event.entry_px,
                "detected_after_fill": True,
            },
            confidence=50,
            param_version={"source": "hyperliquid_info", "lookahead": "fill confirmed before observation"},
        )
    )


def _rate_budget(wallet_count: int) -> dict[str, Any]:
    return {
        "wallets": wallet_count,
        "per_poll_weight_estimate": wallet_count * (2 + 20),
        "polls_per_minute_at_120s": 0.5,
        "estimated_weight_per_minute": wallet_count * 11,
        "official_ip_limit_weight_per_minute": 1200,
        "note": "체결 반환 건수 추가 가중치는 별도이며 마지막 체결 시각 이후만 증분 조회합니다.",
    }


def _datetime_ms(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return _float(value)
