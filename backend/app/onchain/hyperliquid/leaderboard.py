from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

import httpx

from app.db.models import WhaleWallet, utc_now
from app.onchain import cohort

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


def discover_leaderboard_wallets(
    repo: Any,
    settings: Any,
    client: Any | None = None,
    position_client: Any | None = None,
    *,
    sample_sizes: dict[str, int] | None = None,
) -> dict[str, Any]:
    if not bool(settings.hyperliquid_whale_discovery_enabled):
        return {"enabled": False, "status": "disabled", "selected_count": 0}
    # 5-1 코호트 고정. 옵트인이고 끄면 종전 동작으로 되돌아간다(5-1 항목 5).
    cohort_mode = bool(getattr(settings, "hyperliquid_whale_cohort_retention_enabled", False))

    leaderboard_client = client or HyperliquidLeaderboardClient(
        settings.hyperliquid_leaderboard_url,
        timeout_seconds=max(10.0, float(settings.hyperliquid_request_timeout_seconds) * 2),
    )
    now = utc_now()
    payload = leaderboard_client.leaderboard()
    rows = payload.get("leaderboardRows") or []
    criteria = cohort.non_performance_criteria(settings) if cohort_mode else _criteria(settings)
    selection_score = cohort.non_performance_score if cohort_mode else None
    if cohort_mode:
        # C4 증명 — 선발 점수 입력에 성과 키가 없다는 것을 실행 시점에 확인한다.
        cohort.assert_no_performance_inputs(cohort.NON_PERFORMANCE_INPUTS)
        cohort.assert_no_performance_inputs({key for key, value in criteria.items() if value is not None})
    eligible = select_candidates(rows, criteria, score=selection_score)
    focus_symbols = _focus_symbols(settings)
    scan_limit = max(0, int(settings.hyperliquid_whale_discovery_scan_limit))
    position_scan = scan_candidate_positions(
        eligible,
        position_client,
        scan_limit=scan_limit,
        focus_symbols=focus_symbols,
        minimum_size_usd=float(settings.hyperliquid_whale_min_size_usd),
    )

    all_wallets = repo.list_whale_wallets(limit=1000)
    manual_wallets = [wallet for wallet in all_wallets if wallet.active and wallet.source != "discovery"]
    max_wallets = max(1, int(settings.hyperliquid_whale_max_wallets))

    plan: dict[str, Any] | None = None
    if cohort_mode:
        # 유지가 선발보다 앞선다. 표본을 완주하지 못한 지갑은 자리를 먼저 차지한다.
        plan = cohort.cohort_plan(
            all_wallets,
            sample_sizes=sample_sizes or {},
            leaderboard_addresses={str(item["address"]) for item in eligible},
            now=now,
            max_wallets=max_wallets,
            sample_target=max(1, int(getattr(settings, "hyperliquid_whale_cohort_sample_target", cohort.COHORT_SAMPLE_TARGET))),
            min_tenure_days=max(0, int(getattr(settings, "hyperliquid_whale_cohort_min_tenure_days", cohort.COHORT_MIN_TENURE_DAYS))),
            dormant_days=max(1, int(getattr(settings, "hyperliquid_whale_cohort_dormant_days", cohort.COHORT_DORMANT_DAYS))),
        )
        discovery_slots = int(plan["discovery_slots"])
    else:
        discovery_slots = max(0, max_wallets - len(manual_wallets))

    selected = select_directional_cohort(
        eligible,
        discovery_slots,
        directional_slots=int(settings.hyperliquid_whale_directional_slots),
        focus_symbols=focus_symbols,
        score=selection_score,
    )
    selected_addresses = {str(item["address"]) for item in selected}

    if plan is None:
        for wallet in all_wallets:
            if wallet.source == "discovery" and wallet.address.lower() not in selected_addresses and wallet.active:
                repo.upsert_whale_wallet(wallet.model_copy(update={"active": False, "updated_at": now}))
    else:
        # 해제는 `RELEASE_REASONS` 에 해당하는 지갑만. 선발에서 빠졌다는 것은 사유가 아니다.
        released = {str(item["address"]).lower(): item for item in plan["released"]}
        by_address = {wallet.address.lower(): wallet for wallet in all_wallets}
        for address, decision in released.items():
            wallet = by_address.get(address)
            if wallet is None or not wallet.active:
                continue
            repo.upsert_whale_wallet(
                wallet.model_copy(
                    update={
                        "active": False,
                        "updated_at": now,
                        "payload": {**wallet.payload, "cohort_release": decision},
                    }
                )
            )
        for item in plan["reinstated"]:
            wallet = by_address.get(str(item["address"]).lower())
            if wallet is None:
                continue
            repo.upsert_whale_wallet(
                wallet.model_copy(
                    update={
                        "active": True,
                        "updated_at": now,
                        "payload": {**wallet.payload, "cohort_reinstatement": {**item, "as_of": now.isoformat()}},
                    }
                )
            )
        for item in plan["retained"]:
            wallet = by_address.get(str(item["address"]).lower())
            if wallet is None:
                continue
            repo.upsert_whale_wallet(wallet.model_copy(update={"payload": {**wallet.payload, "cohort_retention": item}, "updated_at": now}))

    for rank, item in enumerate(selected, start=1):
        address = str(item["address"])
        existing = repo.get_whale_wallet(address)
        display_name = str(item.get("display_name") or "").strip()
        leaderboard_rank = int(item.get("leaderboard_rank") or rank)
        label = display_name[:40] if display_name else f"리더보드 고래 #{leaderboard_rank}"
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
        "position_scan": position_scan,
        "selection_policy": {
            "focus_symbols": focus_symbols,
            "scan_limit": scan_limit,
            "directional_slots": min(discovery_slots, max(0, int(settings.hyperliquid_whale_directional_slots))),
            "quality_slots": max(0, discovery_slots - max(0, int(settings.hyperliquid_whale_directional_slots))),
            "minimum_position_usd": float(settings.hyperliquid_whale_min_size_usd),
        },
        "selected_coverage": position_coverage(selected, focus_symbols),
        "source": "Hyperliquid public leaderboard",
        "cohort_retention": plan
        if plan is not None
        else {"enabled": False, "note": "코호트 고정 미적용 — 선발에서 빠진 discovery 지갑은 비활성화된다(종전 동작)"},
        "selection_basis": "규모·활동량(비성과, C4)" if cohort_mode else "quality_score(월간 PnL·ROI·계좌규모)",
    }
    repo.upsert_calibration_report_cache(DISCOVERY_CACHE_KEY, result)
    return result


def select_candidates(
    rows: list[Any],
    criteria: dict[str, float | None],
    *,
    score: Callable[[dict[str, Any]], float] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for leaderboard_rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        address = str(row.get("ethAddress") or "").lower()
        if not ADDRESS_RE.fullmatch(address):
            continue
        account_value = _float(row.get("accountValue"))
        month = _window(row, "month")
        week = _window(row, "week")
        all_time = _window(row, "allTime")
        pnl = _float(month.get("pnl"))
        roi = _float(month.get("roi"))
        volume = _float(month.get("vlm"))
        turnover = volume / account_value if account_value > 0 else math.inf
        # 임계가 `None` 이면 그 축으로 걸러내지 않는다 — 코호트 모드가 PnL·ROI 임계를
        # 빼기 위해 쓴다(C4). 현행 모드는 모든 임계가 숫자라 동작이 바뀌지 않는다.
        if (
            _below(account_value, criteria.get("min_account_usd"))
            or _below(pnl, criteria.get("min_month_pnl_usd"))
            or _below(roi, criteria.get("min_month_roi"))
            or _below(volume, criteria.get("min_month_volume_usd"))
            or _above(turnover, criteria.get("max_turnover"))
        ):
            continue
        quality_score = math.log10(max(1.0, pnl)) * 30 + min(roi, 1.0) * 100 + math.log10(max(1.0, account_value)) * 8
        candidates.append(
            {
                "address": address,
                "display_name": row.get("displayName"),
                "leaderboard_rank": leaderboard_rank,
                "account_value_usd": round(account_value, 2),
                "month_pnl_usd": round(pnl, 2),
                "month_roi": roi,
                "month_volume_usd": round(volume, 2),
                "week_pnl_usd": round(_float(week.get("pnl")), 2),
                "week_roi": _float(week.get("roi")),
                "all_time_pnl_usd": round(_float(all_time.get("pnl")), 2),
                "all_time_roi": _float(all_time.get("roi")),
                "turnover": round(turnover, 2),
                "quality_score": round(quality_score, 4),
                "focus_positions": [],
            }
        )
    if score is None:
        return sorted(candidates, key=lambda item: (float(item["quality_score"]), float(item["month_pnl_usd"])), reverse=True)
    # 비성과 정렬. 동점 처리도 성과가 아닌 축(계좌 규모)으로 한다.
    return sorted(candidates, key=lambda item: (float(score(item)), float(item["account_value_usd"])), reverse=True)


def scan_candidate_positions(
    candidates: list[dict[str, Any]],
    client: Any | None,
    *,
    scan_limit: int,
    focus_symbols: list[str],
    minimum_size_usd: float,
) -> dict[str, Any]:
    targets = candidates[: max(0, scan_limit)]
    if client is None or not targets:
        return {
            "scanned_count": 0,
            "active_focus_count": 0,
            "errors": 0,
            "coverage": position_coverage([], focus_symbols),
        }

    errors = 0

    def fetch(index: int, candidate: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
        state = client.clearinghouse_state(str(candidate["address"]))
        return index, _focus_positions(state, focus_symbols, minimum_size_usd)

    with ThreadPoolExecutor(max_workers=min(12, max(1, len(targets)))) as executor:
        futures = {executor.submit(fetch, index, candidate): index for index, candidate in enumerate(targets)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                _, positions = future.result()
                candidates[index]["focus_positions"] = positions
            except Exception:
                errors += 1
                candidates[index]["focus_positions"] = []

    active = [candidate for candidate in targets if candidate.get("focus_positions")]
    return {
        "scanned_count": len(targets),
        "active_focus_count": len(active),
        "errors": errors,
        "coverage": position_coverage(active, focus_symbols),
    }


def select_directional_cohort(
    candidates: list[dict[str, Any]],
    slots: int,
    *,
    directional_slots: int,
    focus_symbols: list[str],
    score: Callable[[dict[str, Any]], float] | None = None,
) -> list[dict[str, Any]]:
    focus_symbols = focus_symbols or ["BTC", "ETH"]
    limit = max(0, slots)
    reserve = min(limit, max(0, directional_slots))
    selected: list[dict[str, Any]] = []
    selected_addresses: set[str] = set()

    short_target = (reserve + 1) // 2
    long_target = reserve - short_target
    for side, target in (("short", short_target), ("long", long_target)):
        picked = 0
        symbol_index = 0
        stalled = 0
        while picked < target and stalled < max(1, len(focus_symbols)):
            symbol = focus_symbols[symbol_index % len(focus_symbols)]
            symbol_index += 1
            matches = [
                candidate
                for candidate in candidates
                if str(candidate["address"]) not in selected_addresses
                and any(position.get("coin") == symbol and position.get("side") == side for position in candidate.get("focus_positions") or [])
            ]
            tiebreak = cohort.scored_by(score)
            match = max(
                matches,
                key=lambda candidate: (
                    _position_notional(candidate, symbol, side),
                    float(tiebreak(candidate)),
                ),
                default=None,
            )
            if match is None:
                stalled += 1
                continue
            stalled = 0
            selected.append({**match, "selection_reason": f"coverage:{symbol}:{side}"})
            selected_addresses.add(str(match["address"]))
            picked += 1

    for candidate in candidates:
        if len(selected) >= limit:
            break
        address = str(candidate["address"])
        if address in selected_addresses:
            continue
        selected.append({**candidate, "selection_reason": "quality"})
        selected_addresses.add(address)

    return [{**candidate, "selection_rank": index} for index, candidate in enumerate(selected[:limit], start=1)]


def position_coverage(candidates: list[dict[str, Any]], focus_symbols: list[str]) -> dict[str, dict[str, Any]]:
    result = {symbol: {"long_wallets": 0, "short_wallets": 0, "long_usd": 0.0, "short_usd": 0.0} for symbol in focus_symbols}
    for candidate in candidates:
        for position in candidate.get("focus_positions") or []:
            symbol = str(position.get("coin") or "").upper()
            side = str(position.get("side") or "")
            if symbol not in result or side not in {"long", "short"}:
                continue
            result[symbol][f"{side}_wallets"] += 1
            result[symbol][f"{side}_usd"] += float(position.get("size_usd") or 0.0)
    for values in result.values():
        values["long_usd"] = round(float(values["long_usd"]), 2)
        values["short_usd"] = round(float(values["short_usd"]), 2)
    return result


def _position_notional(candidate: dict[str, Any], symbol: str, side: str) -> float:
    return max(
        (
            float(position.get("size_usd") or 0.0)
            for position in candidate.get("focus_positions") or []
            if position.get("coin") == symbol and position.get("side") == side
        ),
        default=0.0,
    )


def _focus_positions(state: dict[str, Any], focus_symbols: list[str], minimum_size_usd: float) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    for item in state.get("assetPositions") or []:
        position = item.get("position") if isinstance(item, dict) and isinstance(item.get("position"), dict) else {}
        coin = str(position.get("coin") or "").upper()
        size = _float(position.get("szi"))
        size_usd = abs(_float(position.get("positionValue")))
        if coin not in focus_symbols or abs(size) <= 1e-12 or size_usd < minimum_size_usd:
            continue
        positions.append(
            {
                "coin": coin,
                "side": "long" if size > 0 else "short",
                "size_usd": round(size_usd, 2),
                "entry_px": _float(position.get("entryPx")) or None,
            }
        )
    return sorted(positions, key=lambda item: float(item["size_usd"]), reverse=True)


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


def _focus_symbols(settings: Any) -> list[str]:
    values = [value.strip().upper() for value in str(settings.hyperliquid_whale_focus_symbols).split(",")]
    return list(dict.fromkeys(value for value in values if value)) or ["BTC", "ETH"]


def _below(value: float, threshold: float | None) -> bool:
    return threshold is not None and value < float(threshold)


def _above(value: float, threshold: float | None) -> bool:
    return threshold is not None and value > float(threshold)


def _float(value: Any) -> float:
    try:
        result = float(value or 0)
        return result if math.isfinite(result) else 0.0
    except (TypeError, ValueError):
        return 0.0
