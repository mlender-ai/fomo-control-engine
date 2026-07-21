from __future__ import annotations

import re
from collections import defaultdict
from datetime import timedelta
from typing import Any

from app.analyst.signature_registry import state_map
from app.backtest.candidate_scoring import CANDIDATE_SENTINEL_POSITION_ID, WHALE_VALIDATION_DAYS
from app.backtest.statistics import bootstrap_ci_from_counts
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


def discover(
    repo: Any,
    settings: Any,
    *,
    client: Any | None = None,
    position_client: Any | None = None,
) -> dict[str, Any]:
    info_client = position_client
    if info_client is None and client is None:
        info_client = HyperliquidInfoClient(
            settings.hyperliquid_info_url,
            timeout_seconds=float(settings.hyperliquid_request_timeout_seconds),
        )
    return discover_leaderboard_wallets(repo, settings, client, info_client)


def whale_dashboard(repo: Any, settings: Any) -> dict[str, Any]:
    wallets = repo.list_whale_wallets(active=True, limit=max(1, int(settings.hyperliquid_whale_max_wallets)))
    states = repo.list_whale_position_states(limit=1000)
    raw_events = repo.list_whale_events(limit=2000)
    review_context = _wallet_review_context(repo)
    by_wallet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for state in states:
        by_wallet[str(state.get("wallet_address") or "").lower()].append(state)
    rows = []
    for wallet in wallets:
        review = _wallet_review(wallet.address, review_context)
        rows.append(
            {
                **wallet.model_dump(mode="json"),
                "address_short": f"{wallet.address[:6]}…{wallet.address[-4:]}",
                "alias_disclaimer": "공식 리더보드 공개 계정 · 신원 확정 아님" if wallet.source == "discovery" else "사용자 지정 추정 별칭 · 신원 확정 아님",
                "leaderboard": wallet.payload.get("discovery") if isinstance(wallet.payload.get("discovery"), dict) else None,
                "positions": sorted(by_wallet.get(wallet.address.lower(), []), key=lambda item: float(item.get("size_usd") or 0), reverse=True),
                "review": review,
                "marker_emphasis": review["trust_status"] == "trusted",
                "direction_eligible": review["trust_status"] == "trusted",
            }
        )
    poll_interval = max(30, int(settings.hyperliquid_whale_poll_interval_seconds))
    position_weight_per_wallet = 2
    fill_base_weight_per_wallet = 20
    official_ip_limit = 1200
    polls_per_minute = max(1, (60 + poll_interval - 1) // poll_interval)
    configured_wallet_limit = max(1, int(settings.hyperliquid_whale_max_wallets))
    estimated_max_weight = polls_per_minute * configured_wallet_limit * (position_weight_per_wallet + fill_base_weight_per_wallet)
    flow = _flow_dashboard(repo, wallets, states, source_events=raw_events)
    flow_by_instrument = {
        item["symbol"]: _flow_dashboard(repo, wallets, states, source_events=raw_events, instrument=item["symbol"]) for item in flow["symbols"]
    }
    active_addresses = {wallet.address.lower() for wallet in wallets}
    event_bursts = _event_burst_payloads(raw_events, active_addresses, review_context)
    recent_events_by_instrument: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for payload in event_bursts:
        instrument = str(payload["instrument"])
        if len(recent_events_by_instrument[instrument]) < 10:
            recent_events_by_instrument[instrument].append(payload)
    return {
        "enabled": bool(settings.hyperliquid_whale_tracking_enabled),
        "wallet_count": len(wallets),
        "max_wallets": int(settings.hyperliquid_whale_max_wallets),
        "minimum_event_size_usd": float(settings.hyperliquid_whale_min_size_usd),
        "wallets": rows,
        "recent_events": _round_robin_event_feed(event_bursts),
        "recent_events_by_instrument": dict(recent_events_by_instrument),
        "discovery": cached_discovery(repo),
        "flow": flow,
        "flow_by_instrument": flow_by_instrument,
        "symbol_activity": _symbol_activity(repo, wallets, states, review_context),
        "rate_budget": {
            "poll_interval_seconds": poll_interval,
            "position_weight_per_wallet": position_weight_per_wallet,
            "fill_base_weight_per_wallet": fill_base_weight_per_wallet,
            "estimated_max_weight_per_minute": estimated_max_weight,
            "official_ip_limit_weight_per_minute": official_ip_limit,
            "within_official_budget": estimated_max_weight <= official_ip_limit,
            "policy": "포지션 매 틱 + 마지막 체결 이후 증분 조회 · 실패 시 워커 공통 지수 백오프",
        },
        "policy": "미검증 고래는 관측·실측만 표시하며 방향 판정과 자동 진입에 사용하지 않습니다.",
    }


def _symbol_activity(
    repo: Any,
    wallets: list[WhaleWallet],
    states: list[dict[str, Any]],
    review_context: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    active = {wallet.address.lower(): wallet for wallet in wallets}
    positions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for state in states:
        address = str(state.get("wallet_address") or "").lower()
        symbol = _state_instrument(state)
        wallet = active.get(address)
        if wallet is None or not symbol:
            continue
        leaderboard = wallet.payload.get("discovery") if isinstance(wallet.payload.get("discovery"), dict) else {}
        positions[symbol].append(
            {
                "wallet_address": wallet.address,
                "address_short": f"{wallet.address[:6]}…{wallet.address[-4:]}",
                "wallet_label": wallet.label,
                "leaderboard_rank": leaderboard.get("leaderboard_rank"),
                "selection_rank": leaderboard.get("selection_rank"),
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
        symbol = _event_instrument(event)
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
            "recent_events": [_event_payload(event, review_context) for event in events.get(symbol, [])],
            "as_of": max(as_of_values, default=None),
        }
    return result


def _flow_dashboard(
    repo: Any,
    wallets: list[WhaleWallet],
    states: list[dict[str, Any]],
    *,
    source_events: list[WhaleEvent] | None = None,
    instrument: str | None = None,
) -> dict[str, Any]:
    now = utc_now()
    window_hours = 72
    bucket_hours = 2
    start = now - timedelta(hours=window_hours)
    active_addresses = {wallet.address.lower() for wallet in wallets}
    normalized_instrument = instrument.upper() if instrument else None
    events = [
        event
        for event in (source_events if source_events is not None else repo.list_whale_events(limit=2000))
        if event.wallet_address.lower() in active_addresses
        and event.event_at >= start
        and (normalized_instrument is None or _event_instrument(event) == normalized_instrument)
    ]

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

    active_states = [
        state
        for state in states
        if str(state.get("wallet_address") or "").lower() in active_addresses
        and (normalized_instrument is None or _state_instrument(state) == normalized_instrument)
    ]
    symbol_rows: dict[str, dict[str, Any]] = defaultdict(lambda: {"long_usd": 0.0, "short_usd": 0.0, "event_volume_24h_usd": 0.0, "wallets": set()})
    for state in active_states:
        symbol = _state_instrument(state)
        if not symbol:
            continue
        side = "long" if state.get("side") == "long" else "short"
        symbol_rows[symbol][f"{side}_usd"] += float(state.get("size_usd") or 0)
        symbol_rows[symbol]["wallets"].add(str(state.get("wallet_address") or "").lower())
    event_cutoff = now - timedelta(hours=24)
    event_counts: dict[str, int] = defaultdict(int)
    for event in events:
        if event.event_at >= event_cutoff:
            event_counts[_event_instrument(event)] += 1
            symbol_rows[_event_instrument(event)]["event_volume_24h_usd"] += event.size_usd
        symbol_rows[_event_instrument(event)]
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
                "event_volume_24h_usd": round(float(values["event_volume_24h_usd"]), 2),
            }
        )
    symbols.sort(key=lambda item: float(item["long_usd"]) + float(item["short_usd"]) + float(item["event_volume_24h_usd"]), reverse=True)
    current_long = sum(float(item.get("size_usd") or 0) for item in active_states if item.get("side") == "long")
    current_short = sum(float(item.get("size_usd") or 0) for item in active_states if item.get("side") == "short")
    flow_24h = sum(_event_signed_flow(event) for event in events if event.event_at >= event_cutoff)
    return {
        "instrument": normalized_instrument,
        "window_hours": window_hours,
        "bucket_hours": bucket_hours,
        "current_long_usd": round(current_long, 2),
        "current_short_usd": round(current_short, 2),
        "current_net_usd": round(current_long - current_short, 2),
        "flow_24h_usd": round(flow_24h, 2),
        "event_count_24h": sum(1 for event in events if event.event_at >= event_cutoff),
        "timeline": [{**point, **{key: round(float(value), 2) for key, value in point.items() if key.endswith("_usd")}} for point in buckets.values()],
        "symbols": symbols,
    }


def _event_instrument(event: WhaleEvent) -> str:
    return str(event.symbol or event.coin or "UNKNOWN").upper()


def _state_instrument(state: dict[str, Any]) -> str:
    return str(state.get("symbol") or state.get("coin") or "UNKNOWN").upper()


def _event_action_label(event: WhaleEvent) -> str:
    if event.event == "flip":
        return "숏→롱 전환" if event.side == "long" else "롱→숏 전환"
    labels = {
        "open": "롱 신규" if event.side == "long" else "숏 신규",
        "increase": "롱 증액" if event.side == "long" else "숏 증액",
        "reduce": "롱 감액" if event.side == "long" else "숏 감액",
        "close": "롱 청산" if event.side == "long" else "숏 청산",
    }
    return labels.get(event.event, event.event)


def _recent_event_feed(
    events: list[WhaleEvent],
    active_addresses: set[str],
    review_context: dict[str, Any],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Compact display bursts and round-robin instruments without mutating the raw ledger."""
    return _round_robin_event_feed(_event_burst_payloads(events, active_addresses, review_context), limit=limit)


def _event_burst_payloads(
    events: list[WhaleEvent],
    active_addresses: set[str],
    review_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build newest-first display bursts while preserving every raw ledger event."""
    cutoff = utc_now() - timedelta(hours=72)
    bursts: list[list[WhaleEvent]] = []
    open_bursts: dict[tuple[str, str, str, str], tuple[Any, list[WhaleEvent]]] = {}
    for event in sorted(events, key=lambda item: item.event_at, reverse=True):
        if event.wallet_address.lower() not in active_addresses or event.event_at < cutoff:
            continue
        key = (event.wallet_address.lower(), _event_instrument(event), event.event, event.side)
        current = open_bursts.get(key)
        if current is not None and (current[0] - event.event_at).total_seconds() <= 60:
            current[1].append(event)
            continue
        group = [event]
        bursts.append(group)
        open_bursts[key] = (event.event_at, group)

    payloads: list[dict[str, Any]] = []
    for group in bursts:
        latest = group[0]
        payload = _event_payload(latest, review_context)
        total_size = sum(item.size_usd for item in group)
        priced = [(item.entry_px, item.size_usd) for item in group if item.entry_px is not None and item.size_usd > 0]
        entry_px = sum(float(value) * weight for value, weight in priced) / sum(weight for _, weight in priced) if priced else latest.entry_px
        payload.update(
            {
                "id": f"burst:{latest.id}",
                "instrument": _event_instrument(latest),
                "action_label": _event_action_label(latest),
                "size_usd": round(total_size, 2),
                "entry_px": round(entry_px, 8) if entry_px is not None else None,
                "fill_count": len(group),
                "raw_event_ids": [str(item.id) for item in group],
                "burst_started_at": min(item.event_at for item in group).isoformat(),
            }
        )
        payloads.append(payload)

    return sorted(payloads, key=lambda item: str(item["event_at"]), reverse=True)


def _round_robin_event_feed(payloads: list[dict[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
    """Balance the global tape across instruments without truncating per-instrument history."""
    by_instrument: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for payload in payloads:
        by_instrument[str(payload["instrument"])].append(payload)
    ordered_instruments = sorted(by_instrument, key=lambda key: str(by_instrument[key][0]["event_at"]), reverse=True)
    result: list[dict[str, Any]] = []
    index = 0
    while len(result) < limit:
        added = False
        for key in ordered_instruments:
            rows = by_instrument[key]
            if index < len(rows):
                result.append(rows[index])
                added = True
                if len(result) >= limit:
                    break
        if not added:
            break
        index += 1
    return result


def _event_signed_flow(event: WhaleEvent) -> float:
    entering = event.event in {"open", "increase", "flip"}
    return event.size_usd if (entering and event.side == "long") or (not entering and event.side == "short") else -event.size_usd


def chart_onchain_context(repo: Any, symbol: str, timeframe: str, candles: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = symbol.upper()
    events = repo.list_whale_events(symbol=normalized, limit=500)
    catalog_cache = repo.get_calibration_report_cache("hyperliquid_symbol_catalog")
    catalog_payload = catalog_cache.get("payload") if isinstance(catalog_cache, dict) and isinstance(catalog_cache.get("payload"), dict) else {}
    catalog = {str(item).upper() for item in catalog_payload.get("symbols") or []}
    observed_symbols = {_state_instrument(state) for state in repo.list_whale_position_states(limit=1000)}
    observed_symbols.update(_event_instrument(event) for event in events)
    supported = normalized in catalog or normalized in observed_symbols
    duration = _timeframe_seconds(timeframe)
    review_context = _wallet_review_context(repo)
    now_s = int(utc_now().timestamp())
    candle_times = sorted(int(candle.get("time") or 0) for candle in candles if int(candle.get("time") or 0) > 0)
    closed_candle_times = [value for value in candle_times if value + duration <= now_s]
    latest_closed = closed_candle_times[-1] if closed_candle_times else None
    groups: dict[tuple[int, str, str, bool], list[WhaleEvent]] = defaultdict(list)
    for event in events:
        event_s = int(event.event_at.timestamp())
        anchor = max((value for value in closed_candle_times if value <= event_s < value + duration), default=None)
        live = False
        if anchor is None:
            in_current_window = latest_closed is not None and latest_closed + duration <= event_s < latest_closed + (2 * duration)
            if not in_current_window or event_s > now_s:
                continue
            anchor = latest_closed
            live = True
        kind = "entry" if event.event in {"open", "increase", "flip"} else "exit"
        groups[(anchor, kind, event.side, live)].append(event)
    markers = []
    for (anchor, kind, side, live), grouped in groups.items():
        ranked = sorted(grouped, key=lambda item: item.size_usd, reverse=True)
        items = [_event_payload(item, review_context) for item in ranked]
        size_usd = sum(item.size_usd for item in ranked)
        emphasized = any(item["validation_state"] == "validated" for item in items)
        latest_event = max(ranked, key=lambda item: item.event_at)
        markers.append(
            {
                "time": anchor,
                "event_time": int(latest_event.event_at.timestamp()),
                "kind": kind,
                "side": side,
                "event": "flip" if any(item.event == "flip" for item in ranked) else ranked[0].event,
                "count": len(ranked),
                "size_usd": size_usd,
                "price": latest_event.entry_px or latest_event.mark_px,
                "size_tier": _size_tier(size_usd),
                "label": _marker_label(ranked[0], len(ranked), size_usd),
                "emphasized": emphasized,
                "live": live,
                "items": items,
            }
        )
    markers = sorted(markers, key=lambda item: (item["event_time"], item["time"]), reverse=True)[:8]
    validated_evidence = _validated_consensus(repo, normalized, review_context)
    return {
        "supported": supported,
        "unsupported_reason": None if supported else "Hyperliquid perp 메타에 없는 심볼",
        "symbol": normalized,
        "markers": sorted(markers, key=lambda item: (item["event_time"], item["time"])),
        "validated_evidence": validated_evidence,
        "policy": "확정 체결 최근 8그룹 · 미완성 봉은 우측 LIVE 표시 · 미검증은 관측 표시만",
    }


def _validated_consensus(repo: Any, symbol: str, review_context: dict[str, Any]) -> list[dict[str, Any]]:
    states = [state for state in repo.list_whale_position_states(limit=1000) if _state_instrument(state) == symbol]
    rows = []
    for state in states:
        review = _wallet_review(str(state.get("wallet_address") or ""), review_context)
        if review["trust_status"] != "trusted":
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


def _event_payload(event: WhaleEvent, review_context: dict[str, Any]) -> dict[str, Any]:
    review = _wallet_review(event.wallet_address, review_context)
    return {
        **event.model_dump(mode="json"),
        "validation_state": review["state"],
        "trust_status": review["trust_status"],
        "sample_size": review["sample_size"],
        "win_1r_pct": review["win_1r_pct"],
        "win_1r_ci": review["win_1r_ci"],
        "accuracy_label": f"과거 적중 {review['win_1r_pct']}% (N={review['sample_size']})"
        if review["sample_size"] >= 30 and review["win_1r_pct"] is not None
        else f"적중률 축적 중 (N={review['sample_size']})",
        "alias_disclaimer": "사용자 지정 추정 별칭 · 신원 확정 아님",
        "instrument": _event_instrument(event),
        "action_label": _event_action_label(event),
        "fill_count": 1,
        "raw_event_ids": [str(event.id)],
    }


def _wallet_review_context(repo: Any) -> dict[str, Any]:
    judgments_by_key: dict[str, list[Any]] = defaultdict(list)
    for judgment in repo.list_judgments(CANDIDATE_SENTINEL_POSITION_ID, limit=10000):
        if judgment.type != "candidate_signature":
            continue
        signature_key = str(judgment.claim.get("signature_key") or "")
        if signature_key.startswith("whale_entry_"):
            judgments_by_key[signature_key].append(judgment)
    scores_by_judgment = {
        score.judgment_id: score
        for score in repo.list_judgment_scores(position_id=CANDIDATE_SENTINEL_POSITION_ID, limit=10000)
        if score.judgment_type == "candidate_signature" and score.outcome != "untested"
    }
    review_payloads = {
        stat.signature_key: stat.payload
        for stat in repo.list_backtest_stats(limit=5000)
        if stat.payload.get("candidate_review") and stat.signature_key.startswith("whale_entry_")
    }
    return {
        "judgments_by_key": judgments_by_key,
        "scores_by_judgment": scores_by_judgment,
        "review_payloads": review_payloads,
        "states": state_map(repo),
    }


def _wallet_review(address: str, context: dict[str, Any]) -> dict[str, Any]:
    key = whale_signature_key(address)
    payload = context["review_payloads"].get(key, {})
    judgments = context["judgments_by_key"].get(key, [])
    scores = [context["scores_by_judgment"][judgment.judgment_id] for judgment in judgments if judgment.judgment_id in context["scores_by_judgment"]]
    sample_size = len(scores)
    wins = sum(1 for score in scores if score.outcome == "correct")
    win_1r_pct = round(wins / sample_size * 100, 1) if sample_size else None
    win_1r_ci = list(bootstrap_ci_from_counts(wins, sample_size)) if sample_size else None
    rr_values = [float(score.metrics.get("realized_rr") or 0.0) for score in scores]
    positive_r = sum(value for value in rr_values if value > 0)
    negative_r = abs(sum(value for value in rr_values if value < 0))
    started_at = min((judgment.as_of for judgment in judgments), default=None)
    elapsed_days = max(0, int((utc_now() - started_at).total_seconds() // 86400)) if started_at else 0
    calendar_complete = elapsed_days >= WHALE_VALIDATION_DAYS
    state = context["states"].get(key, "candidate")
    ci_low = float(win_1r_ci[0]) if win_1r_ci else None
    if state == "validated":
        trust_status = "trusted"
    elif state in {"degraded", "quarantined"}:
        trust_status = "excluded"
    elif calendar_complete and (sample_size < 30 or ci_low is None or ci_low < 55.0):
        trust_status = "excluded"
    elif calendar_complete and sample_size >= 30 and ci_low is not None and ci_low >= 55.0:
        trust_status = "review_ready"
    else:
        trust_status = "validating"
    return {
        "signature_key": key,
        "state": state,
        "trust_status": trust_status,
        "sample_size": sample_size,
        "observed_count": len(judgments),
        "win_1r_pct": win_1r_pct,
        "win_1r_ci": win_1r_ci,
        "remaining_samples": max(0, 30 - sample_size),
        "cumulative_return_r": round(sum(rr_values), 2),
        "average_return_r": round(sum(rr_values) / sample_size, 2) if sample_size else None,
        "profit_factor_r": round(positive_r / negative_r, 2) if negative_r > 0 else (None if positive_r <= 0 else 99.0),
        "validation_started_at": started_at.isoformat() if started_at else None,
        "validation_days": elapsed_days,
        "validation_required_days": WHALE_VALIDATION_DAYS,
        "validation_remaining_days": max(0, WHALE_VALIDATION_DAYS - elapsed_days),
        "validation_progress_pct": min(100.0, round(elapsed_days / WHALE_VALIDATION_DAYS * 100, 1)),
        "validation_calendar_complete": calendar_complete,
        "promotion_eligible": trust_status == "review_ready" or bool(payload.get("promotion_eligible")),
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
