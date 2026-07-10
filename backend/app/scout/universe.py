from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from html import escape
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

from app.analyst.signature_registry import current_state, gate_excluded, state_map
from app.backtest.signatures import signature_key, signature_label, signatures_from_analysis
from app.backtest.statistics import bootstrap_ci_from_counts, format_stat_line
from app.core.config import Settings
from app.db.models import BacktestStat, JudgmentLedgerEntry, UniverseDiscovery, utc_now
from app.marketdata.assets import base_ticker
from app.notify.rules import AlertCandidate
from app.review.params import engine_param_snapshot
from app.scout.monitor import SCOUT_SENTINEL_POSITION_ID


AnalysisLoader = Callable[[str, str], dict[str, Any]]

_ROUND_ROBIN_CURSOR = 0


@dataclass(frozen=True)
class GateResult:
    quality_passed: bool
    dispatch_allowed: bool
    reasons: list[dict[str, Any]]


def run_universe_scan(
    repo: Any,
    settings: Settings,
    *,
    analysis_loader: AnalysisLoader,
    timeframe: str = "4h",
    ticker_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not settings.universe_scanner_enabled:
        return {"enabled": False, "symbols": [], "discoveries": [], "alert_candidates": [], "_alert_candidate_objects": []}

    universe = build_universe(repo, settings, ticker_rows=ticker_rows)
    selected = _round_robin(universe["symbols"], settings.universe_round_robin_batch_size)
    discoveries: list[UniverseDiscovery] = []
    candidates: list[AlertCandidate] = []
    errors: list[dict[str, str]] = []
    alerted_today = _alerted_today(repo)
    now = utc_now()
    signature_states = state_map(repo)

    for item in selected:
        symbol = item["symbol"]
        try:
            entry = analysis_loader(symbol, timeframe)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
            continue
        analysis = entry.get("analysis") if isinstance(entry.get("analysis"), dict) else {}
        summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
        historical = entry.get("historical_backtest") if isinstance(entry.get("historical_backtest"), dict) else analysis.get("historical_backtest")
        active_signatures = _active_signatures(analysis, historical)
        if not active_signatures:
            continue
        quote_volume = _float(summary.get("quote_volume_24h"))
        current_price = _float(summary.get("mark_price") or analysis.get("mark_price"))
        asset_class = str(summary.get("asset_class") or analysis.get("asset_class") or item.get("asset_class") or "unknown")
        for signature in active_signatures:
            key = str(signature.get("key") or signature_key(signature))
            signature = {**signature, "key": key, "label": signature.get("label") or signature_label(signature)}
            stat = class_backtest_stat(repo, signature)
            confidence = signal_confidence(analysis, signature)
            cooldown_active = _symbol_cooldown_active(repo, symbol, settings, now=now)
            daily_room = alerted_today < max(0, settings.universe_daily_alert_limit)
            sig_state = signature_states.get(key) or current_state(repo, key, stat=stat, settings=settings)
            gate = evaluate_discovery_gate(
                settings,
                confidence=confidence,
                stat=stat,
                quote_volume_24h=quote_volume,
                asset_class=asset_class,
                earnings_blocked=False,
                daily_room=daily_room,
                cooldown_active=cooldown_active,
                signature_state=sig_state,
            )
            status = "rejected"
            alerted_at = None
            if gate.quality_passed:
                status = "alerted" if gate.dispatch_allowed else "stored"
                alerted_at = now if gate.dispatch_allowed else None
            discovery_id = uuid5(NAMESPACE_URL, f"fce:universe:{symbol}:{timeframe}:{key}:{now.date().isoformat()}")
            judgment_id = f"universe_discovery:{discovery_id}" if gate.quality_passed else None
            message = discovery_message(symbol, asset_class, signature, confidence, stat)
            discovery = UniverseDiscovery(
                id=discovery_id,
                symbol=symbol,
                timeframe=timeframe,
                asset_class=asset_class,
                signature_key=key,
                signature=signature,
                status=status,  # type: ignore[arg-type]
                gate_passed=gate.quality_passed,
                gate_reasons=gate.reasons,
                confidence=confidence,
                win_1r_pct=_float(stat.get("win_1r_pct")) if stat else None,
                win_1r_ci=_stat_ci(stat) if stat else None,
                sample_size=int(stat.get("sample_size") or 0) if stat else None,
                quote_volume_24h=quote_volume,
                current_price=current_price,
                message=message,
                payload={
                    "source": "universe_scan",
                    "backtest_stat": stat,
                    "summary": summary,
                    "rate_budget": universe["rate_budget"],
                },
                judgment_id=judgment_id,
                alerted_at=alerted_at,
                created_at=now,
                updated_at=now,
            )
            saved = repo.upsert_universe_discovery(discovery)
            discoveries.append(saved)
            if gate.quality_passed:
                _record_discovery_judgment(repo, saved)
            if gate.dispatch_allowed:
                alerted_today += 1
                candidates.append(_alert_candidate(saved))

    return {
        "enabled": True,
        "symbols": selected,
        "discoveries": [item.model_dump(mode="json") for item in discoveries],
        "alert_candidates": [candidate.payload for candidate in candidates],
        "_alert_candidate_objects": candidates,
        "errors": errors,
        "rate_budget": universe["rate_budget"],
        "excluded": universe["excluded"],
    }


def build_universe(repo: Any, settings: Settings, *, ticker_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    enabled_classes = settings.universe_enabled_class_set or {"crypto", "stock", "index"}
    blacklist = settings.universe_blacklist_set
    # 유니버스 큐레이션(2026-07-10): 거래량 순위만으로는 마이크로캡 잡주가 올라온다.
    # 코인=시총 10위권, 주식=미증 시총 상위+핫 종목 허용 리스트(base ticker). 빈 리스트면 비활성.
    crypto_allow = settings.universe_crypto_allowlist_set
    stock_allow = settings.universe_stock_allowlist_set
    excluded_existing = _existing_symbols(repo)
    quote_volume_by_symbol = _ticker_quote_volume_map(ticker_rows or [])
    catalog = repo.search_symbols("", limit=1000)
    rows: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for item in catalog:
        symbol = item.symbol.upper()
        asset_class = str(item.asset_class or "unknown")
        ticker = base_ticker(symbol, str(getattr(item, "base_coin", "") or ""))
        reason = ""
        if symbol in blacklist:
            reason = "blacklist"
        elif symbol in excluded_existing:
            reason = "already_tracked"
        elif asset_class not in enabled_classes:
            reason = "asset_class_disabled"
        elif str(item.status or "").lower() in {"off", "delisted", "offline"}:
            reason = "contract_inactive"
        elif asset_class == "crypto" and crypto_allow and ticker not in crypto_allow:
            reason = "not_in_allowlist"
        elif asset_class == "stock" and stock_allow and ticker not in stock_allow:
            reason = "not_in_allowlist"
        if reason:
            excluded.append({"symbol": symbol, "reason": reason})
            continue
        rows.append(
            {
                "symbol": symbol,
                "asset_class": asset_class,
                "quote_volume_24h": quote_volume_by_symbol.get(symbol),
            }
        )

    crypto = sorted([row for row in rows if row["asset_class"] == "crypto"], key=_volume_rank_key)[: settings.universe_crypto_symbol_limit]
    stock_like = sorted([row for row in rows if row["asset_class"] in {"stock", "index"}], key=_volume_rank_key)[: settings.universe_stock_symbol_limit]
    unknown = sorted([row for row in rows if row["asset_class"] not in {"crypto", "stock", "index"}], key=_volume_rank_key)[:5]
    symbols = crypto + stock_like + unknown
    return {"symbols": symbols, "excluded": excluded, "rate_budget": universe_rate_budget(settings, len(symbols))}


def evaluate_discovery_gate(
    settings: Settings,
    *,
    confidence: int | None,
    stat: dict[str, Any] | None,
    quote_volume_24h: float | None,
    asset_class: str,
    earnings_blocked: bool,
    daily_room: bool,
    cooldown_active: bool,
    signature_state: str = "candidate",
) -> GateResult:
    sample = int(stat.get("sample_size") or 0) if stat else 0
    ci_low = _stat_ci_low(stat)
    divergence_flag = bool((stat or {}).get("live_backtest_divergence") or ((stat or {}).get("payload") or {}).get("live_backtest_divergence"))
    ci_threshold = float(getattr(settings, "universe_backtest_min_ci_low_pct", 50.0))
    reasons = [
        _reason("confidence", (confidence or 0) >= settings.universe_min_confidence, confidence, f">= {settings.universe_min_confidence}"),
        _reason("backtest_sample", sample >= settings.universe_backtest_min_sample, sample, f">= {settings.universe_backtest_min_sample}"),
        # WO-37: 자율 강등/격리된 시그니처는 발견 게이트에서 제외 (강등의 실효).
        _reason(
            "signature_lifecycle_state",
            not gate_excluded(signature_state),
            signature_state,
            "not degraded/quarantined",
        ),
        # WO-36 §3: 점추정이 아니라 부트스트랩 CI 하한 기준 — 표본 부족 시그니처가
        # 운 좋은 점추정으로 게이트를 통과하는 것을 구조적으로 차단한다.
        # CI가 없는(구버전) 통계는 검증 불가이므로 보수적으로 불통과.
        _reason(
            "backtest_win_1r_ci_low",
            ci_low is not None and ci_low >= ci_threshold,
            ci_low,
            f"CI 하한 >= {ci_threshold}%",
        ),
        _reason("live_backtest_divergence", not divergence_flag, divergence_flag, "false"),
        _reason(
            "liquidity_floor",
            (quote_volume_24h or 0) >= settings.universe_min_quote_volume_24h,
            quote_volume_24h,
            f">= {settings.universe_min_quote_volume_24h}",
        ),
        _reason("earnings_window", not (asset_class in {"stock", "index"} and earnings_blocked), earnings_blocked, "not D-1~D+1"),
    ]
    quality_passed = all(item["passed"] for item in reasons)
    dispatch_reasons = [
        _reason("daily_alert_limit", daily_room, daily_room, f"max {settings.universe_daily_alert_limit}/day"),
        _reason("symbol_cooldown", not cooldown_active, cooldown_active, f"{settings.universe_symbol_cooldown_hours}h"),
    ]
    return GateResult(quality_passed, quality_passed and all(item["passed"] for item in dispatch_reasons), [*reasons, *dispatch_reasons])


def _stat_ci(stat: dict[str, Any] | None) -> list[float] | None:
    if not isinstance(stat, dict):
        return None
    ci = stat.get("win_1r_ci")
    if not isinstance(ci, (list, tuple)) or len(ci) != 2:
        inner = stat.get("payload") if isinstance(stat.get("payload"), dict) else {}
        ci = inner.get("win_1r_ci")
    if isinstance(ci, (list, tuple)) and len(ci) == 2:
        low, high = _float(ci[0]), _float(ci[1])
        if low is not None and high is not None:
            return [low, high]
    return None


def _stat_ci_low(stat: dict[str, Any] | None) -> float | None:
    ci = _stat_ci(stat)
    return ci[0] if ci else None


def class_backtest_stat(repo: Any, signature: dict[str, Any]) -> dict[str, Any] | None:
    key = str(signature.get("key") or signature_key(signature))
    asset_class = str(signature.get("asset_class") or "unknown")
    stats = repo.list_backtest_stats(signature_key=key, limit=500)
    direct = [stat for stat in stats if stat.scope == "asset_class" and stat.asset_class == asset_class]
    if direct:
        return _stat_payload(direct[0], scope="asset_class")
    matching = [stat for stat in stats if stat.asset_class == asset_class and stat.scope == "symbol"]
    if not matching:
        return None
    latest_by_symbol: dict[str, BacktestStat] = {}
    for stat in matching:
        current = latest_by_symbol.get(stat.symbol)
        if current is None or stat.generated_at > current.generated_at:
            latest_by_symbol[stat.symbol] = stat
    return _aggregate_stats(list(latest_by_symbol.values()), asset_class=asset_class, key=key, signature=signature)


def signal_confidence(analysis: dict[str, Any], signature: dict[str, Any]) -> int | None:
    engine = str(signature.get("engine") or "")
    event = str(signature.get("event_type") or "")
    direction = str(signature.get("direction") or "")
    if engine == "liquidity":
        liquidity = analysis.get("liquidity") if isinstance(analysis.get("liquidity"), dict) else {}
        values: list[int] = []
        for sweep in _dicts(liquidity.get("sweeps")) + _dicts(liquidity.get("htf_range_sweeps")):
            side_direction = "long" if sweep.get("side") == "sell_side" else "short" if sweep.get("side") == "buy_side" else "neutral"
            if side_direction != direction:
                continue
            if event.startswith("htf_") != (sweep.get("type") == "htf_range_sweep"):
                continue
            values.append(int(_float(sweep.get("confidence")) or 0))
        return max(values) if values else None
    if engine == "wyckoff":
        values = []
        for marker in _dicts(analysis.get("wyckoff_markers")):
            label = str(marker.get("label") or marker.get("type") or "").lower()
            marker_direction = (
                "long"
                if any(token in label for token in ("spring", "sos", "lps"))
                else "short"
                if any(token in label for token in ("utad", "sow", "lpsy"))
                else "neutral"
            )
            if marker_direction == direction:
                values.append(int(_float(marker.get("confidence")) or 0))
        return max(values) if values else None
    if engine == "harmonic":
        values = []
        for pattern in _dicts(analysis.get("harmonic_patterns")):
            pattern_direction = "long" if pattern.get("direction") == "bullish" else "short" if pattern.get("direction") == "bearish" else "neutral"
            if pattern_direction == direction:
                values.append(int(_float(pattern.get("confidence")) or 0))
        return max(values) if values else None
    if engine == "levels":
        levels = analysis.get("price_levels") if isinstance(analysis.get("price_levels"), dict) else {}
        side = "support" if direction == "long" else "resistance" if direction == "short" else ""
        scores = [int(_float(level.get("score")) or 0) for level in _dicts(levels.get(side))]
        return max(scores) if scores else None
    return None


def discovery_message(
    symbol: str,
    asset_class: str,
    signature: dict[str, Any],
    confidence: int | None,
    stat: dict[str, Any] | None,
) -> str:
    class_label = {"crypto": "코인", "stock": "주식", "index": "지수"}.get(asset_class, asset_class)
    label = signature.get("label") or signature_label(signature)
    rr = (stat or {}).get("median_rr")
    # WO-36 §7: 승률 발행은 표기 표준(format_stat_line) 경유 — CI 없는 승률 표기 금지.
    stat_line = format_stat_line(stat or {}, label=f"동일 시그니처 ({class_label} 클래스)")
    if rr is not None:
        stat_line += f" · 중앙 {rr}R"
    return "\n".join(
        [
            f"🔎 <b>발견 · {escape(symbol)}</b> ({escape(class_label)})",
            f"{escape(str(label))} 확정 · 신뢰도 {confidence if confidence is not None else '-'}",
            escape(stat_line),
            "→ 셋업 발견 통보입니다. 판단 전 브리핑 확인 권장.",
        ]
    )


def universe_rate_budget(settings: Settings, symbol_count: int) -> dict[str, Any]:
    per_symbol = 3
    interval_minutes = max(1, settings.worker_universe_scan_interval_seconds / 60)
    batch = max(1, settings.universe_round_robin_batch_size)
    requests_per_tick = batch * per_symbol
    full_cycle_ticks = (symbol_count + batch - 1) // batch if symbol_count else 0
    return {
        "job": "universe_scan",
        "interval_minutes": interval_minutes,
        "requests_per_symbol": per_symbol,
        "symbols": symbol_count,
        "batch_size": batch,
        "requests_per_tick": requests_per_tick,
        "full_cycle_minutes": round(full_cycle_ticks * interval_minutes, 1),
        "round_robin_required": symbol_count > batch,
        "policy": "관심종목 스캔보다 낮은 우선순위이며 배치 초과분은 라운드로빈으로 다음 틱에 처리합니다.",
    }


def _alert_candidate(discovery: UniverseDiscovery) -> AlertCandidate:
    direction = str(discovery.signature.get("direction") or "neutral")
    return AlertCandidate(
        rule_id="universe_discovery",
        severity="info",
        position_id=None,
        symbol=discovery.symbol,
        identity=f"{discovery.symbol}:{discovery.signature_key}",
        title="유니버스 발견",
        message=discovery.message,
        payload={
            "kind": "universe_discovery",
            "discovery_id": str(discovery.id),
            "symbol": discovery.symbol,
            "timeframe": discovery.timeframe,
            "direction": direction if direction in {"long", "short"} else None,
            "signature_key": discovery.signature_key,
            "signature": discovery.signature,
            "confidence": discovery.confidence,
            "sample_size": discovery.sample_size,
            "win_1r_pct": discovery.win_1r_pct,
            "win_1r_ci": discovery.win_1r_ci,
            "current_price": discovery.current_price,
            "quote_volume_24h": discovery.quote_volume_24h,
            "gate_reasons": discovery.gate_reasons,
            "number_sources": [
                {"label": "confidence", "value": discovery.confidence, "source": "analysis_signal"},
                {"label": "sample_size", "value": discovery.sample_size, "source": "backtest_stats"},
                {"label": "win_1r_pct", "value": discovery.win_1r_pct, "source": "backtest_stats"},
            ],
        },
    )


def _record_discovery_judgment(repo: Any, discovery: UniverseDiscovery) -> None:
    if not discovery.judgment_id:
        return
    repo.add_judgment(
        JudgmentLedgerEntry(
            judgment_id=discovery.judgment_id,
            position_id=SCOUT_SENTINEL_POSITION_ID,
            source_type="universe_discovery",
            source_id=str(discovery.id),
            as_of=discovery.created_at,
            type="universe_discovery",
            claim={
                "symbol": discovery.symbol,
                "timeframe": discovery.timeframe,
                "signature_key": discovery.signature_key,
                "signature": discovery.signature,
                "status": discovery.status,
                "current_price": discovery.current_price,
                "gate_reasons": discovery.gate_reasons,
            },
            confidence=discovery.confidence,
            param_version=engine_param_snapshot(repo),
        )
    )


def _active_signatures(analysis: dict[str, Any], historical: Any) -> list[dict[str, Any]]:
    if isinstance(historical, dict) and isinstance(historical.get("active_signatures"), list):
        return [item for item in historical["active_signatures"] if isinstance(item, dict)]
    return signatures_from_analysis(analysis)


def _existing_symbols(repo: Any) -> set[str]:
    symbols = {item.symbol.upper() for item in repo.list_watchlist()}
    symbols.update(position.symbol.upper() for position in repo.list_positions())
    symbols.update(intent.symbol.upper() for intent in repo.list_entry_intents(status="active", limit=1000))
    symbols.update(setup.symbol.upper() for setup in repo.list_armed_setups(status="armed", limit=1000))
    return symbols


def _ticker_quote_volume_map(ticker_rows: list[dict[str, Any]]) -> dict[str, float]:
    volumes: dict[str, float] = {}
    for row in ticker_rows:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        quote_volume = _float(row.get("quote_volume_24h"))
        if quote_volume is None:
            quote_volume = _float(row.get("quoteVolume") or row.get("usdtVolume") or row.get("turnover24h") or row.get("turnover"))
        if quote_volume is None:
            base_volume = _float(row.get("base_volume_24h") or row.get("baseVolume") or row.get("volume24h") or row.get("volume"))
            last_price = _float(row.get("last_price") or row.get("lastPr") or row.get("last") or row.get("close"))
            if base_volume is not None and last_price is not None:
                quote_volume = base_volume * last_price
        if quote_volume is not None:
            volumes[symbol] = quote_volume
    return volumes


def _volume_rank_key(row: dict[str, Any]) -> tuple[int, float, str]:
    volume = _float(row.get("quote_volume_24h"))
    if volume is None:
        return (1, 0.0, str(row["symbol"]))
    return (0, -volume, str(row["symbol"]))


def _round_robin(symbols: list[dict[str, Any]], batch_size: int) -> list[dict[str, Any]]:
    global _ROUND_ROBIN_CURSOR
    if not symbols:
        return []
    batch = max(1, int(batch_size))
    if len(symbols) <= batch:
        _ROUND_ROBIN_CURSOR = 0
        return symbols
    selected = [symbols[(_ROUND_ROBIN_CURSOR + offset) % len(symbols)] for offset in range(batch)]
    _ROUND_ROBIN_CURSOR = (_ROUND_ROBIN_CURSOR + batch) % len(symbols)
    return selected


def _alerted_today(repo: Any) -> int:
    now = utc_now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return len([item for item in repo.list_universe_discoveries(status="alerted", limit=500) if item.created_at >= start])


def _symbol_cooldown_active(repo: Any, symbol: str, settings: Settings, *, now: datetime) -> bool:
    cutoff = now - timedelta(hours=max(1, settings.universe_symbol_cooldown_hours))
    return any(item.created_at >= cutoff for item in repo.list_universe_discoveries(symbol=symbol, status="alerted", limit=20))


def _stat_payload(stat: BacktestStat, *, scope: str) -> dict[str, Any]:
    payload = stat.model_dump(mode="json")
    signature = stat.payload.get("signature") if isinstance(stat.payload, dict) and isinstance(stat.payload.get("signature"), dict) else {}
    payload["signature"] = signature
    payload["label"] = stat.payload.get("label") if isinstance(stat.payload, dict) else signature.get("label")
    payload["scope"] = scope
    if isinstance(stat.payload, dict):
        for key in ("win_1r_ci", "oos", "unstable", "walk_forward", "regimes", "period"):
            if key not in payload or payload.get(key) is None:
                payload[key] = stat.payload.get(key)
    return payload


def _aggregate_stats(stats: list[BacktestStat], *, asset_class: str, key: str, signature: dict[str, Any]) -> dict[str, Any] | None:
    sample = sum(max(0, int(stat.sample_size)) for stat in stats)
    if sample <= 0:
        return None
    win_1r = _weighted(stats, "win_1r_pct", sample)
    win_2r = _weighted(stats, "win_2r_pct", sample)
    rr = _weighted(stats, "median_rr", sample)
    # 클래스 풀링 CI: 심볼별 (승수, 표본)을 합산해 부트스트랩 (WO-36 §3)
    pooled_wins = 0
    pooled_n = 0
    for stat in stats:
        n = max(0, int(stat.sample_size))
        if n <= 0 or stat.win_1r_pct is None:
            continue
        pooled_wins += int(round(float(stat.win_1r_pct) / 100 * n))
        pooled_n += n
    ci = bootstrap_ci_from_counts(pooled_wins, pooled_n) if pooled_n > 0 else None
    return {
        "signature_key": key,
        "symbol": "*",
        "timeframe": str(signature.get("timeframe") or "4h"),
        "asset_class": asset_class,
        "scope": "asset_class_aggregate",
        "engine": signature.get("engine"),
        "event_type": signature.get("event_type"),
        "strength_class": signature.get("strength_class"),
        "direction": signature.get("direction"),
        "sample_size": sample,
        "win_1r_pct": round(win_1r, 1) if win_1r is not None else None,
        "win_2r_pct": round(win_2r, 1) if win_2r is not None else None,
        "median_rr": round(rr, 2) if rr is not None else None,
        "win_1r_ci": list(ci) if ci else None,
        "signature": signature,
        "label": signature.get("label") or signature_label(signature),
        "source_symbols": sorted({stat.symbol for stat in stats}),
    }


def _weighted(stats: list[BacktestStat], attr: str, total_sample: int) -> float | None:
    numerator = 0.0
    denominator = 0
    for stat in stats:
        value = getattr(stat, attr)
        sample = max(0, int(stat.sample_size))
        if value is None or sample <= 0:
            continue
        numerator += float(value) * sample
        denominator += sample
    if denominator <= 0:
        return None
    return numerator / denominator


def _reason(code: str, passed: bool, value: Any, threshold: Any) -> dict[str, Any]:
    return {"code": code, "passed": bool(passed), "value": value, "threshold": threshold}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
