from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta
from typing import Any

from app.analyst.signature_registry import current_state
from app.db.models import WhaleEvent, WhaleWallet, utc_now
from app.onchain.hyperliquid.client import HyperliquidInfoClient
from app.onchain.hyperliquid.collector import collect_whale_positions, whale_signature_key
from app.onchain.hyperliquid.leaderboard import cached_discovery, discover_leaderboard_wallets

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


def add_whale_wallet(repo: Any, settings: Any, address: str, label: str | None, *, source: str = "manual") -> WhaleWallet:
    normalized = address.strip().lower()
    if not ADDRESS_RE.fullmatch(normalized):
        raise ValueError("Hyperliquid 지갑 주소는 0x로 시작하는 42자 주소여야 합니다.")
    existing = repo.get_whale_wallet(normalized)
    active_wallets = repo.list_whale_wallets(active=True, limit=max(1, int(settings.hyperliquid_whale_max_wallets)) + 1)
    if existing is None and len(active_wallets) >= int(settings.hyperliquid_whale_max_wallets):
        raise ValueError(f"고래 워치리스트는 최대 {settings.hyperliquid_whale_max_wallets}개입니다.")
    now = utc_now()
    wallet = WhaleWallet(
        address=normalized,
        label=(label or (existing.label if existing else "") or f"고래 {normalized[:6]}…{normalized[-4:]}").strip()[:40],
        source=source if source in {"manual", "bot", "discovery"} else "manual",
        active=True,
        added_at=existing.added_at if existing else now,
        updated_at=now,
        last_polled_at=existing.last_polled_at if existing else None,
        last_fill_at=existing.last_fill_at if existing else None,
        payload=existing.payload if existing else {},
    )
    return repo.upsert_whale_wallet(wallet)


def remove_whale_wallet(repo: Any, address: str) -> bool:
    normalized = address.strip().lower()
    for state in repo.list_whale_position_states(normalized, limit=500):
        coin = str(state.get("coin") or "")
        if coin:
            repo.delete_whale_position_state(normalized, coin)
    return repo.remove_whale_wallet(normalized)


def collect(repo: Any, settings: Any, *, client: Any | None = None) -> dict[str, Any]:
    if not bool(settings.hyperliquid_whale_tracking_enabled):
        return {"enabled": False, "wallets": 0, "positions": 0, "created": 0, "events": [], "errors": []}
    info_client = client or HyperliquidInfoClient(
        settings.hyperliquid_info_url,
        timeout_seconds=float(settings.hyperliquid_request_timeout_seconds),
    )
    return {"enabled": True, **collect_whale_positions(repo, settings, info_client)}


def discover(repo: Any, settings: Any, *, client: Any | None = None) -> dict[str, Any]:
    return discover_leaderboard_wallets(repo, settings, client)


def whale_dashboard(repo: Any, settings: Any) -> dict[str, Any]:
    wallets = repo.list_whale_wallets(active=True, limit=max(1, int(settings.hyperliquid_whale_max_wallets)))
    states = repo.list_whale_position_states(limit=1000)
    by_wallet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for state in states:
        by_wallet[str(state.get("wallet_address") or "").lower()].append(state)
    rows = []
    for wallet in wallets:
        review = _wallet_review(repo, wallet.address)
        rows.append(
            {
                **wallet.model_dump(mode="json"),
                "address_short": f"{wallet.address[:6]}…{wallet.address[-4:]}",
                "alias_disclaimer": "공식 리더보드 공개 계정 · 신원 확정 아님" if wallet.source == "discovery" else "사용자 지정 추정 별칭 · 신원 확정 아님",
                "leaderboard": wallet.payload.get("discovery") if isinstance(wallet.payload.get("discovery"), dict) else None,
                "positions": sorted(by_wallet.get(wallet.address.lower(), []), key=lambda item: float(item.get("size_usd") or 0), reverse=True),
                "review": review,
                "marker_emphasis": review["state"] == "validated",
                "direction_eligible": review["state"] == "validated",
            }
        )
    return {
        "enabled": bool(settings.hyperliquid_whale_tracking_enabled),
        "wallet_count": len(wallets),
        "max_wallets": int(settings.hyperliquid_whale_max_wallets),
        "minimum_event_size_usd": float(settings.hyperliquid_whale_min_size_usd),
        "wallets": rows,
        "recent_events": [_event_payload(repo, event) for event in repo.list_whale_events(limit=100)],
        "discovery": cached_discovery(repo),
        "flow": _flow_dashboard(repo, wallets, states),
        "symbol_activity": _symbol_activity(repo, wallets, states),
        "rate_budget": {
            "poll_interval_seconds": int(settings.hyperliquid_whale_poll_interval_seconds),
            "position_weight_per_wallet": 2,
            "fill_base_weight_per_wallet": 20,
            "official_ip_limit_weight_per_minute": 1200,
            "policy": "포지션 매 틱 + 마지막 체결 이후 증분 조회 · 실패 시 워커 공통 지수 백오프",
        },
        "policy": "미검증 고래는 관측·실측만 표시하며 방향 판정과 자동 진입에 사용하지 않습니다.",
    }


def _symbol_activity(repo: Any, wallets: list[WhaleWallet], states: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    active = {wallet.address.lower(): wallet for wallet in wallets}
    positions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for state in states:
        address = str(state.get("wallet_address") or "").lower()
        symbol = str(state.get("symbol") or "").upper()
        wallet = active.get(address)
        if wallet is None or not symbol:
            continue
        leaderboard = wallet.payload.get("discovery") if isinstance(wallet.payload.get("discovery"), dict) else {}
        positions[symbol].append(
            {
                "wallet_address": wallet.address,
                "address_short": f"{wallet.address[:6]}…{wallet.address[-4:]}",
                "wallet_label": wallet.label,
                "leaderboard_rank": leaderboard.get("selection_rank"),
                "side": "long" if state.get("side") == "long" else "short",
                "size_usd": round(float(state.get("size_usd") or 0), 2),
                "entry_px": state.get("entry_px"),
                "mark_px": state.get("mark_px"),
                "unrealized_pnl": state.get("unrealized_pnl"),
                "as_of": state.get("as_of"),
            }
        )

    events: dict[str, list[WhaleEvent]] = defaultdict(list)
    for event in repo.list_whale_events(limit=2000):
        symbol = event.symbol.upper()
        if event.wallet_address.lower() in active and symbol and len(events[symbol]) < 8:
            events[symbol].append(event)

    result: dict[str, dict[str, Any]] = {}
    for symbol in sorted(set(positions) | set(events)):
        rows = sorted(positions.get(symbol, []), key=lambda item: float(item["size_usd"]), reverse=True)
        long_rows = [item for item in rows if item["side"] == "long"]
        short_rows = [item for item in rows if item["side"] == "short"]
        long_usd = sum(float(item["size_usd"]) for item in long_rows)
        short_usd = sum(float(item["size_usd"]) for item in short_rows)
        as_of_values = [str(item.get("as_of") or "") for item in rows if item.get("as_of")]
        result[symbol] = {
            "symbol": symbol,
            "long_usd": round(long_usd, 2),
            "short_usd": round(short_usd, 2),
            "net_usd": round(long_usd - short_usd, 2),
            "long_wallet_count": len({item["wallet_address"] for item in long_rows}),
            "short_wallet_count": len({item["wallet_address"] for item in short_rows}),
            "wallet_count": len({item["wallet_address"] for item in rows}),
            "positions": rows[:8],
            "recent_events": [_event_payload(repo, event) for event in events.get(symbol, [])],
            "as_of": max(as_of_values, default=None),
        }
    return result


def _flow_dashboard(repo: Any, wallets: list[WhaleWallet], states: list[dict[str, Any]]) -> dict[str, Any]:
    now = utc_now()
    window_hours = 72
    bucket_hours = 2
    start = now - timedelta(hours=window_hours)
    active_addresses = {wallet.address.lower() for wallet in wallets}
    events = [event for event in repo.list_whale_events(limit=2000) if event.wallet_address.lower() in active_addresses and event.event_at >= start]

    bucket_seconds = bucket_hours * 3600
    start_epoch = int(start.timestamp()) // bucket_seconds * bucket_seconds
    end_epoch = int(now.timestamp()) // bucket_seconds * bucket_seconds
    buckets: dict[int, dict[str, Any]] = {}
    for value in range(start_epoch, end_epoch + bucket_seconds, bucket_seconds):
        buckets[value] = {
            "time": value,
            "long_in_usd": 0.0,
            "short_in_usd": 0.0,
            "long_out_usd": 0.0,
            "short_out_usd": 0.0,
            "net_usd": 0.0,
            "event_count": 0,
        }
    for event in events:
        bucket = int(event.event_at.timestamp()) // bucket_seconds * bucket_seconds
        point = buckets.get(bucket)
        if point is None:
            continue
        entering = event.event in {"open", "increase", "flip"}
        key = f"{event.side}_{'in' if entering else 'out'}_usd"
        point[key] += event.size_usd
        signed = event.size_usd if (entering and event.side == "long") or (not entering and event.side == "short") else -event.size_usd
        point["net_usd"] += signed
        point["event_count"] += 1

    active_states = [state for state in states if str(state.get("wallet_address") or "").lower() in active_addresses]
    symbol_rows: dict[str, dict[str, Any]] = defaultdict(lambda: {"long_usd": 0.0, "short_usd": 0.0, "wallets": set()})
    for state in active_states:
        symbol = str(state.get("symbol") or state.get("coin") or "").upper()
        if not symbol:
            continue
        side = "long" if state.get("side") == "long" else "short"
        symbol_rows[symbol][f"{side}_usd"] += float(state.get("size_usd") or 0)
        symbol_rows[symbol]["wallets"].add(str(state.get("wallet_address") or "").lower())
    event_cutoff = now - timedelta(hours=24)
    event_counts: dict[str, int] = defaultdict(int)
    for event in events:
        if event.event_at >= event_cutoff:
            event_counts[event.symbol.upper()] += 1
    symbols = []
    for symbol, values in symbol_rows.items():
        long_usd = float(values["long_usd"])
        short_usd = float(values["short_usd"])
        symbols.append(
            {
                "symbol": symbol,
                "long_usd": round(long_usd, 2),
                "short_usd": round(short_usd, 2),
                "net_usd": round(long_usd - short_usd, 2),
                "wallet_count": len(values["wallets"]),
                "event_count_24h": event_counts.get(symbol, 0),
            }
        )
    symbols.sort(key=lambda item: float(item["long_usd"]) + float(item["short_usd"]), reverse=True)
    current_long = sum(float(item.get("size_usd") or 0) for item in active_states if item.get("side") == "long")
    current_short = sum(float(item.get("size_usd") or 0) for item in active_states if item.get("side") == "short")
    flow_24h = sum(_event_signed_flow(event) for event in events if event.event_at >= event_cutoff)
    return {
        "window_hours": window_hours,
        "bucket_hours": bucket_hours,
        "current_long_usd": round(current_long, 2),
        "current_short_usd": round(current_short, 2),
        "current_net_usd": round(current_long - current_short, 2),
        "flow_24h_usd": round(flow_24h, 2),
        "event_count_24h": sum(1 for event in events if event.event_at >= event_cutoff),
        "timeline": [{**point, **{key: round(float(value), 2) for key, value in point.items() if key.endswith("_usd")}} for point in buckets.values()],
        "symbols": symbols[:12],
    }


def _event_signed_flow(event: WhaleEvent) -> float:
    entering = event.event in {"open", "increase", "flip"}
    return event.size_usd if (entering and event.side == "long") or (not entering and event.side == "short") else -event.size_usd


def chart_onchain_context(repo: Any, symbol: str, timeframe: str, candles: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = symbol.upper()
    events = repo.list_whale_events(symbol=normalized, limit=500)
    catalog_cache = repo.get_calibration_report_cache("hyperliquid_symbol_catalog")
    catalog_payload = catalog_cache.get("payload") if isinstance(catalog_cache, dict) and isinstance(catalog_cache.get("payload"), dict) else {}
    catalog = {str(item).upper() for item in catalog_payload.get("symbols") or []}
    observed_symbols = {str(state.get("symbol") or "").upper() for state in repo.list_whale_position_states(limit=1000)}
    observed_symbols.update(event.symbol.upper() for event in events)
    supported = normalized in catalog if catalog else normalized in observed_symbols
    duration = _timeframe_seconds(timeframe)
    now_s = int(utc_now().timestamp())
    candle_times = sorted(int(candle.get("time") or 0) for candle in candles if int(candle.get("time") or 0) > 0)
    groups: dict[tuple[int, str, str], list[WhaleEvent]] = defaultdict(list)
    for event in events:
        event_s = int(event.event_at.timestamp())
        anchor = max((value for value in candle_times if value <= event_s and value + duration <= now_s), default=None)
        if anchor is None or event_s >= anchor + duration:
            continue
        kind = "entry" if event.event in {"open", "increase", "flip"} else "exit"
        groups[(anchor, kind, event.side)].append(event)
    markers = []
    for (anchor, kind, side), grouped in groups.items():
        ranked = sorted(grouped, key=lambda item: item.size_usd, reverse=True)
        items = [_event_payload(repo, item) for item in ranked]
        size_usd = sum(item.size_usd for item in ranked)
        emphasized = any(item["validation_state"] == "validated" for item in items)
        markers.append(
            {
                "time": anchor,
                "kind": kind,
                "side": side,
                "event": "flip" if any(item.event == "flip" for item in ranked) else ranked[0].event,
                "count": len(ranked),
                "size_usd": size_usd,
                "size_tier": _size_tier(size_usd),
                "label": _marker_label(ranked[0], len(ranked), size_usd),
                "emphasized": emphasized,
                "items": items,
            }
        )
    markers = sorted(markers, key=lambda item: (item["size_usd"], item["time"]), reverse=True)[:8]
    validated_evidence = _validated_consensus(repo, normalized)
    return {
        "supported": supported,
        "unsupported_reason": None if supported else "Hyperliquid perp 메타에 없는 심볼",
        "symbol": normalized,
        "markers": sorted(markers, key=lambda item: item["time"]),
        "validated_evidence": validated_evidence,
        "policy": "확정 캔들 앵커 · 화면 최대 8개 · 미검증은 관측 표시만",
    }


def _validated_consensus(repo: Any, symbol: str) -> list[dict[str, Any]]:
    states = [state for state in repo.list_whale_position_states(limit=1000) if str(state.get("symbol") or "").upper() == symbol]
    rows = []
    for state in states:
        review = _wallet_review(repo, str(state.get("wallet_address") or ""))
        if review["state"] != "validated":
            continue
        ci = review.get("win_1r_ci") or []
        rows.append(
            {
                "engine": "onchain",
                "claim": f"검증 고래 {state.get('wallet_label')} {state.get('side')} 보유",
                "direction": state.get("side"),
                "confidence": max(55.0, float(ci[0])) if ci else 55.0,
                "as_of": state.get("as_of"),
                "price": state.get("entry_px"),
                "wallet_address": state.get("wallet_address"),
                "sample_size": review.get("sample_size", 0),
                "win_1r_ci": ci,
            }
        )
    return rows


def _event_payload(repo: Any, event: WhaleEvent) -> dict[str, Any]:
    review = _wallet_review(repo, event.wallet_address)
    return {
        **event.model_dump(mode="json"),
        "validation_state": review["state"],
        "sample_size": review["sample_size"],
        "win_1r_pct": review["win_1r_pct"],
        "win_1r_ci": review["win_1r_ci"],
        "accuracy_label": f"과거 적중 {review['win_1r_pct']}% (N={review['sample_size']})"
        if review["sample_size"] >= 30 and review["win_1r_pct"] is not None
        else f"적중률 축적 중 (N={review['sample_size']})",
        "alias_disclaimer": "사용자 지정 추정 별칭 · 신원 확정 아님",
    }


def _wallet_review(repo: Any, address: str) -> dict[str, Any]:
    key = whale_signature_key(address)
    reviews = [stat for stat in repo.list_backtest_stats(signature_key=key, limit=20) if stat.payload.get("candidate_review")]
    payload = reviews[0].payload if reviews else {}
    state = current_state(repo, key, stat=payload)
    return {
        "signature_key": key,
        "state": state,
        "sample_size": int(payload.get("sample_size") or 0),
        "win_1r_pct": payload.get("win_1r_pct"),
        "win_1r_ci": payload.get("win_1r_ci"),
        "remaining_samples": int(payload.get("remaining_samples") or max(0, 30 - int(payload.get("sample_size") or 0))),
        "warning": payload.get("prediction_warning") or ("예측력 미검증" if state != "validated" else None),
    }


def _marker_label(event: WhaleEvent, count: int, size_usd: float) -> str:
    direction = "롱" if event.side == "long" else "숏"
    if count > 1:
        return f"{direction} ×{count} · {_compact_usd(size_usd)}"
    return f"{event.wallet_label} · {direction} {_compact_usd(size_usd)}"


def _compact_usd(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K"
    return f"{value:.0f}"


def _size_tier(value: float) -> int:
    if value >= 5_000_000:
        return 3
    if value >= 1_000_000:
        return 2
    return 1


def _timeframe_seconds(timeframe: str) -> int:
    value = timeframe.strip().lower()
    try:
        amount = int(value[:-1])
    except (TypeError, ValueError):
        return 4 * 3600
    return max(60, amount * {"m": 60, "h": 3600, "d": 86400, "w": 604800}.get(value[-1:], 14400))
