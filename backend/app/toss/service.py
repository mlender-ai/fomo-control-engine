from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import time
from typing import Any

import logging

from app.core.config import Settings

from . import blocks
from .client import TossReadOnlyClient
from .errors import TossAuthenticationError, TossEdgeBlocked, TossMaintenance
from .signals import (
    build_candidate,
    group_candidates,
    investor_flow_signal,
    momentum_signal,
    orderbook_change_signal,
    price_limit_signal,
    resample_candles,
)
from .store import TossStockStore
from app.stock_paper.models import Market
from app.stock_paper.parameters import load_stock_parameters
from app.stock_paper.store import StockPaperStore
from app.stock_paper.universe import load_universe

_RANKING_TYPES = (
    "MARKET_TRADING_AMOUNT",
    "MARKET_TRADING_VOLUME",
    "TOP_GAINERS",
    "TOSS_SECURITIES_TRADING_AMOUNT",
    "TOSS_SECURITIES_TRADING_VOLUME",
    "TOP_LOSERS",
)
_ranking_cache: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}
_investor_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_warning_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_candidate_evidence_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
_orderbook_imbalance: dict[tuple[str, str], float] = {}
_latest_market: dict[str, dict[str, Any]] = {}
_BENCHMARK_PROXY = {"KR": "237350", "US": "QQQ"}
_MAX_CANDIDATES_PER_MARKET = 18
_daily_backfill_cursor = {"KR": 0, "US": 0}
_coverage_cursor = {"KR": 0, "US": 0}
_daily_backfilled_on: dict[tuple[str, str], str] = {}


logger = logging.getLogger("toss.service")


def clear_authentication_blocks() -> None:
    """진단 API 가 사람 개입으로 차단을 즉시 푸는 경로.

    주의: 이건 **보조** 경로다. 자동 복구는 `blocks` 의 백오프 재시도가 담당한다.
    2026-07-28 사고 때는 이 수동 경로가 유일한 해제 수단이라 20.8시간이 날아갔다.
    """
    blocks.clear_all()


def public_status(settings: Settings, market: str, store: TossStockStore) -> dict[str, Any]:
    configured = bool(settings.toss_client_id and settings.toss_client_secret)
    return {
        "market": market,
        "configured": configured,
        "collector_enabled": bool(settings.toss_stock_scout_enabled),
        "status": "ready" if configured and settings.toss_stock_scout_enabled else "credentials_required" if not configured else "paused",
        "message": "토스 API 인증값을 로컬 서버에 설정하세요." if not configured else None,
        "read_only_label": "Toss 데이터 · 주문 실행 없음",
        "source": "Toss Securities Open API",
        "groups": {},
        "performance": store.performance("stock_kr" if market == "KR" else "stock_us"),
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def latest_status(settings: Settings, market: str, store: TossStockStore) -> dict[str, Any]:
    return _latest_market.get(market) or public_status(settings, market, store)


def _early_return(
    settings: Settings,
    market: str,
    store: TossStockStore,
    status: str,
    *,
    reason: str | None = None,
    extra: dict[str, Any] | None = None,
    block: blocks.MarketBlock | None = None,
) -> dict[str, Any]:
    """C3: 산출물 없는 반환은 **전부** 사유와 함께 기록한다.

    조기 반환 6개 지점(auth/maintenance/세션 비개장/empty_universe/edge_blocked)은 모두
    정상 반환이라 잡 하트비트·오류 카운터에 흔적이 남지 않는다. 그래서
    "5.5시간째 authentication_failed" 를 아무도 보고받지 못했다(D4).
    """
    outcome = blocks.record_outcome(market, status, reason=reason, block=block)
    response = {
        **public_status(settings, market, store),
        "status": status,
        "reason": reason,
        "outcome": outcome,
        **(extra or {}),
    }
    if block is not None:
        response.update(
            {
                "retry_in_seconds": block.retry_in_seconds(),
                "attempt_count": block.attempt_count,
                "blocked_since": block.first_blocked_at,
                **block.last_error,
            }
        )
    _latest_market[market] = response
    return response


async def collect_market(settings: Settings, market: str) -> dict[str, Any]:
    store = TossStockStore(settings.database_url)
    # C2(자기잠금 금지): 차단돼 있어도 재시도 시각이 지나면 여기서 막지 않고 **실제로 호출한다**.
    blocked = blocks.active_block(market)
    if blocked is not None:
        return _early_return(
            settings,
            market,
            store,
            blocked.status,
            reason=blocked.reason,
            block=blocked,
            extra={"diagnosis_url": "/api/system/toss/auth-diagnosis"} if blocked.status == "authentication_failed" else None,
        )
    retrying = blocks.pending_block(market)
    client = TossReadOnlyClient(
        settings.toss_client_id,
        settings.toss_client_secret,
        base_url=settings.toss_base_url,
        timeout_seconds=settings.toss_timeout_seconds,
    )
    now = datetime.now(timezone.utc).isoformat()
    if retrying is not None:
        # 차단 해제 재시도. 인증 차단이면 폐기된 토큰으로 재시도해봐야 즉시 재차단이다.
        logger.info(
            "toss %s 차단 해제 재시도 — %s (%d회차 · %d초 지속)",
            market,
            retrying.status,
            retrying.attempt_count,
            retrying.blocked_seconds(),
        )
        if retrying.status == "authentication_failed":
            client.invalidate_cached_token()
    try:
        calendar = await client.get(f"/api/v1/market-calendar/{market}")
        session = _session_state(calendar, market)
        if session != "open":
            # 호출이 성공했으므로 차단은 실제로 풀렸다 — 장이 닫혔을 뿐이다.
            _note_recovery(market, blocks.clear_block(market))
            return _early_return(
                settings,
                market,
                store,
                session,
                reason=_session_reason(calendar, market, session),
                extra={"market_state": session, "observed_at": now, "coverage_candidates": []},
            )
        rankings = await _load_rankings(client, store, market, now)
        investor_payloads = await _load_investor_flow(client, store, market, now)
        ranked_symbols = {symbol for payload in rankings.values() for symbol in _ranking_rows(payload)}
        configured_symbols = list(settings.toss_kr_watchlist if market == "KR" else settings.toss_us_watchlist)
        universe_symbols = [item.symbol for item in load_universe().for_market(Market(market))]
        symbols = list(dict.fromkeys([*configured_symbols, *universe_symbols, _BENCHMARK_PROXY[market]]))
        symbols = list(dict.fromkeys([*symbols, *sorted(ranked_symbols)]))[:400]
        if not symbols:
            _note_recovery(market, blocks.clear_block(market))
            return _early_return(
                settings,
                market,
                store,
                "empty_universe",
                reason="수집 대상 심볼이 0개 — 유니버스·워치리스트 설정을 확인하세요.",
                extra={"market_state": "open", "observed_at": now},
            )
        price_rows: list[dict[str, Any]] = []
        stock_rows: list[dict[str, Any]] = []
        for offset in range(0, len(symbols), 200):
            batch = symbols[offset : offset + 200]
            prices_payload = await client.get("/api/v1/prices", params={"symbols": ",".join(batch)})
            stocks_payload = await client.get("/api/v1/stocks", params={"symbols": ",".join(batch)})
            store.append_raw("toss_quotes", {"market": market, "symbol": "*", "observed_at": now}, prices_payload)
            price_rows.extend(_result_list(prices_payload))
            stock_rows.extend(_result_list(stocks_payload))
        if settings.stock_paper_engine_enabled:
            stock_store = StockPaperStore(settings.database_url)
            stock_store.update_marks(
                Market(market),
                {str(row.get("symbol") or "").upper(): value for row in price_rows if (value := _float(row.get("lastPrice"))) is not None},
                datetime.fromisoformat(now),
            )
            benchmark_row = next((row for row in price_rows if str(row.get("symbol") or "").upper() == _BENCHMARK_PROXY[market]), None)
            benchmark_price = _float(benchmark_row.get("lastPrice")) if benchmark_row else None
            if benchmark_price is not None:
                stock_store.update_benchmark(Market(market), benchmark_price, datetime.fromisoformat(now))
            if market == "US":
                try:
                    exchange_rate = await client.get(
                        "/api/v1/exchange-rate",
                        params={"baseCurrency": "USD", "quoteCurrency": "KRW"},
                    )
                except Exception:
                    exchange_rate = None
                if exchange_rate:
                    stock_store.record_fx(exchange_rate, datetime.fromisoformat(now))
        candidates = await _build_ranked_candidates(client, store, market, rankings, investor_payloads, price_rows, stock_rows, now)
        coverage_candidates = []
        if settings.stock_paper_engine_enabled:
            stock_store = StockPaperStore(settings.database_url)
            candidate_symbols = {str(item["symbol"]).upper() for item in candidates}
            coverage_candidates = await _build_coverage_candidates(
                client,
                store,
                market,
                rankings,
                investor_payloads,
                price_rows,
                stock_rows,
                now,
                excluded_symbols=candidate_symbols | set(stock_store.position_symbols(Market(market))),
                batch_size=load_stock_parameters().coverage_scan_batch_size,
            )
            candidate_symbols.update(str(item["symbol"]).upper() for item in coverage_candidates)
            for symbol in stock_store.position_symbols(Market(market)):
                if symbol not in candidate_symbols:
                    await _load_candidate_evidence(client, store, market, symbol, now)
            await _backfill_next_daily_candle(
                client,
                store,
                market,
                [symbol for symbol in universe_symbols if symbol not in candidate_symbols],
                now,
            )
        prices = {str(row.get("symbol")): float(row["lastPrice"]) for row in price_rows if row.get("symbol") and row.get("lastPrice") is not None}
        store.record_due_outcomes(prices)
        response = {
            **public_status(settings, market, store),
            "status": "observed",
            "market_state": "open",
            "observed_at": now,
            "groups": group_candidates(candidates),
            "trade_groups": group_candidates(item for item in candidates if item.get("tradable") is True),
            "coverage_candidates": coverage_candidates,
        }
        _latest_market[market] = response
        blocks.record_outcome(market, "observed", reason=None)
        _note_recovery(market, blocks.clear_block(market))
        return response
    except TossEdgeBlocked as exc:
        # 과거엔 래치가 없어 매 틱(10초) 무의미하게 재호출했다 — IP 미등록은 사람이 고쳐야 한다(C4).
        block = blocks.register_block(
            market,
            "edge_blocked",
            "토스 API 허용 IP 미등록 — 콘솔에서 이 서버 IP를 등록하세요.",
            {"message": str(exc), "request_id": exc.request_id},
        )
        return _early_return(settings, market, store, "edge_blocked", reason=block.reason, block=block)
    except TossAuthenticationError as exc:
        block = blocks.register_block(
            market,
            "authentication_failed",
            "토스 인증 실패 — 토큰 재발급 후 자동 재시도합니다.",
            {
                "message": str(exc),
                "request_id": exc.request_id,
                "error_code": exc.error_code,
                "error_message": exc.error_message,
            },
        )
        logger.warning(
            "toss %s 인증 차단 — %d초 후 자동 재시도 (%d회차). 재시작 없이 스스로 풀려야 한다.",
            market,
            block.retry_in_seconds(),
            block.attempt_count,
        )
        return _early_return(
            settings,
            market,
            store,
            "authentication_failed",
            reason=block.reason,
            block=block,
            extra={"diagnosis_url": "/api/system/toss/auth-diagnosis"},
        )
    except TossMaintenance as exc:
        block = blocks.register_block(
            market,
            "maintenance",
            "토스 API 점검 — 수집을 일시 중지했습니다.",
            {"message": str(exc), "request_id": exc.request_id},
        )
        return _early_return(
            settings,
            market,
            store,
            "maintenance",
            reason=block.reason,
            block=block,
            extra={"pause_seconds": block.retry_in_seconds()},
        )
    finally:
        await client.close()


def _note_recovery(market: str, cleared: blocks.MarketBlock | None) -> None:
    """차단이 풀린 사실은 조용히 넘기지 않는다 — 자동 복구도 관측 대상이다."""
    if cleared is None:
        return
    logger.info(
        "toss %s 수집 복구 — %s 차단 해제 (%d회 재시도 · %d초 지속)",
        market,
        cleared.status,
        cleared.attempt_count,
        cleared.blocked_seconds(),
    )


def _session_reason(payload: dict[str, Any], market: str, session: str) -> str:
    """장이 닫혔다는 판정의 **근거**(파싱된 정규장 창)를 함께 남긴다(작업 4).

    미국 정규장인데 holiday 가 나오는 상황을 진단 API 에서 즉시 식별할 수 있어야 한다.
    """
    window = _session_window(_regular_market(payload, market))
    if window is None:
        return f"{market} 정규장 창을 응답에서 찾지 못했습니다(휴장 또는 응답 구조 변경)."
    return f"{market} 정규장 {window[0].isoformat()} ~ {window[1].isoformat()} (UTC) 기준 {session}"


def _regular_market(payload: dict[str, Any], market: str) -> Any:
    """정규장 창을 응답에서 꺼낸다. **KR·US 구조가 실제로 다르다** (작업 4 실측 2026-07-29).

    비대칭은 버그가 아니라 토스 응답 스펙이다. 지우면 KR 이 즉시 영구 holiday 가 된다.

        KR: {"date", "integrated": {"preMarket", "regularMarket", "afterMarket"}}
        US: {"date", "dayMarket", "preMarket", "regularMarket", "afterMarket"}   ← 평면

    검증: 같은 시각 반대 분기를 적용하면 양쪽 모두 `holiday` 로 오판했다.
    """
    result = payload.get("result") or payload.get("data") or payload
    today = result.get("today", result) if isinstance(result, dict) else {}
    if not isinstance(today, dict):
        return None
    if market == "US":
        return today.get("regularMarket")
    return (today.get("integrated") or {}).get("regularMarket")


def _session_state(payload: dict[str, Any], market: str, *, now: datetime | None = None) -> str:
    window = _session_window(_regular_market(payload, market))
    if window is None:
        return "holiday"
    current = now or datetime.now(timezone.utc)
    return "open" if window[0] <= current <= window[1] else "closed"


async def _load_rankings(client: TossReadOnlyClient, store: TossStockStore, market: str, observed_at: str) -> dict[str, dict[str, Any]]:
    cached = _ranking_cache.get(market)
    if cached and time.monotonic() - cached[0] < 60:
        return cached[1]
    payloads: dict[str, dict[str, Any]] = {}
    for ranking_type in _RANKING_TYPES:
        duration = "1d" if ranking_type in {"TOP_GAINERS", "TOP_LOSERS"} else "realtime"
        payload = await client.get(
            "/api/v1/rankings",
            params={"type": ranking_type, "marketCountry": market, "duration": duration, "count": 30},
        )
        payloads[ranking_type] = payload
        store.append_raw(
            "toss_rankings_snapshot",
            {"market": market, "ranking_kind": ranking_type, "ranking_basis": duration, "observed_at": observed_at},
            payload,
        )
    _ranking_cache[market] = (time.monotonic(), payloads)
    return payloads


async def _load_investor_flow(
    client: TossReadOnlyClient,
    store: TossStockStore,
    market: str,
    observed_at: str,
) -> list[dict[str, Any]]:
    if market != "KR":
        return []
    cached = _investor_cache.get(market)
    if cached and time.monotonic() - cached[0] < 300:
        return cached[1]
    payloads = []
    for index_code in ("KOSPI", "KOSDAQ"):
        payload = await client.get(
            f"/api/v1/market-indicators/{index_code}/investor-trading",
            params={"interval": "1d", "count": 1},
        )
        payloads.append(payload)
        store.append_raw(
            "toss_investor_flow",
            {"market": market, "index_code": index_code, "observed_at": observed_at},
            payload,
        )
    _investor_cache[market] = (time.monotonic(), payloads)
    return payloads


async def _build_ranked_candidates(
    client: TossReadOnlyClient,
    store: TossStockStore,
    market: str,
    rankings: dict[str, dict[str, Any]],
    investor_payloads: list[dict[str, Any]],
    price_rows: list[dict[str, Any]],
    stock_rows: list[dict[str, Any]],
    observed_at: str,
) -> list[dict[str, Any]]:
    universe = load_universe()
    market_rows = _ranking_rows(rankings.get("MARKET_TRADING_AMOUNT", {}))
    retail_rows = _ranking_rows(rankings.get("TOSS_SECURITIES_TRADING_AMOUNT", {}))
    price_by_symbol = {str(row.get("symbol")): row for row in price_rows}
    stock_by_symbol = {str(row.get("symbol")): row for row in stock_rows}
    symbols = (set(market_rows) | set(retail_rows)) & set(price_by_symbol)
    preliminary = []
    for symbol in symbols:
        tradable, role = universe.classify(Market(market), symbol)
        market_row = market_rows.get(symbol, {})
        retail_row = retail_rows.get(symbol, {})
        price_row = price_by_symbol.get(symbol, {})
        raw_ranked_price = market_row.get("price")
        ranked_price: dict[str, Any] = raw_ranked_price if isinstance(raw_ranked_price, dict) else {}
        candidate = build_candidate(
            market=market,
            symbol=symbol,
            name=str(stock_by_symbol.get(symbol, {}).get("name") or symbol),
            price=_float(price_row.get("lastPrice") or ranked_price.get("lastPrice")),
            observed_at=observed_at,
            market_rank=_int(market_row.get("rank")),
            retail_rank=_int(retail_row.get("rank")),
            warnings=[],
            tradable=tradable,
            role=role,
        )
        if candidate:
            preliminary.append(candidate)
    preliminary.sort(key=lambda item: (item.get("market_rank") is None, item.get("market_rank") or 10_000, item["symbol"]))
    observation_only = [item for item in preliminary if item.get("tradable") is not True][:_MAX_CANDIDATES_PER_MARKET]
    for candidate in observation_only:
        for signal in candidate["signals"]:
            if signal.get("tone") == "candidate":
                store.record_judgment(candidate, signal)
    result = list(observation_only)
    tradable_candidates = [item for item in preliminary if item.get("tradable") is True][:_MAX_CANDIDATES_PER_MARKET]
    for candidate in tradable_candidates:
        warnings_payload = await _load_warnings(client, store, market, candidate["symbol"], observed_at)
        warnings = [str(item.get("warningType") or item.get("type") or "") for item in _result_list(warnings_payload)]
        evidence = await _load_candidate_evidence(client, store, market, candidate["symbol"], observed_at)
        extra_signals = [
            investor_flow_signal(investor_payloads, candidate["market_rank"]),
            momentum_signal(evidence["candles"]),
            evidence.get("orderbook_signal"),
            price_limit_signal(candidate["price"], evidence.get("upper_limit")),
        ]
        checked = build_candidate(
            market=market,
            symbol=candidate["symbol"],
            name=candidate["name"],
            price=candidate["price"],
            observed_at=observed_at,
            market_rank=candidate["market_rank"],
            retail_rank=candidate["retail_rank"],
            warnings=warnings,
            tradable=bool(candidate.get("tradable")),
            role=str(candidate.get("role") or "observation_only"),
            extra_signals=[signal for signal in extra_signals if signal],
        )
        if checked:
            result.append(checked)
            for signal in checked["signals"]:
                if signal.get("tone") == "candidate":
                    store.record_judgment(checked, signal)
    return result


async def _build_coverage_candidates(
    client: TossReadOnlyClient,
    store: TossStockStore,
    market: str,
    rankings: dict[str, dict[str, Any]],
    investor_payloads: list[dict[str, Any]],
    price_rows: list[dict[str, Any]],
    stock_rows: list[dict[str, Any]],
    observed_at: str,
    *,
    excluded_symbols: set[str],
    batch_size: int,
) -> list[dict[str, Any]]:
    """Rotate the versioned universe through real execution observations.

    This candidate lane is deliberately separate from Scout attention-gap
    signals. It creates paper execution coverage without relabeling a weak
    signal as a strict strategy entry.
    """
    if batch_size <= 0:
        return []
    universe = load_universe()
    members = {item.symbol for item in universe.for_market(Market(market))}
    price_by_symbol = {str(row.get("symbol") or "").upper(): row for row in price_rows}
    stock_by_symbol = {str(row.get("symbol") or "").upper(): row for row in stock_rows}
    market_rows = _ranking_rows(rankings.get("MARKET_TRADING_AMOUNT", {}))
    retail_rows = _ranking_rows(rankings.get("TOSS_SECURITIES_TRADING_AMOUNT", {}))
    ranked = sorted(
        (symbol for symbol in members if symbol in price_by_symbol and symbol not in excluded_symbols),
        key=lambda symbol: (_int((market_rows.get(symbol) or {}).get("rank")) is None, _int((market_rows.get(symbol) or {}).get("rank")) or 10_000, symbol),
    )
    if not ranked:
        return []
    start = _coverage_cursor[market] % len(ranked)
    selected = [ranked[(start + offset) % len(ranked)] for offset in range(min(batch_size, len(ranked)))]
    _coverage_cursor[market] = (start + len(selected)) % len(ranked)
    result: list[dict[str, Any]] = []
    for symbol in selected:
        warnings_payload = await _load_warnings(client, store, market, symbol, observed_at)
        warnings = [str(item.get("warningType") or item.get("type") or "") for item in _result_list(warnings_payload)]
        evidence = await _load_candidate_evidence(client, store, market, symbol, observed_at)
        market_rank = _int((market_rows.get(symbol) or {}).get("rank"))
        retail_rank = _int((retail_rows.get(symbol) or {}).get("rank"))
        coverage_signal = {
            "type": "universe_coverage",
            "label": "버전 유니버스 순환 체결 검증",
            "tone": "observation",
        }
        candidate = build_candidate(
            market=market,
            symbol=symbol,
            name=str((stock_by_symbol.get(symbol) or {}).get("name") or symbol),
            price=_float((price_by_symbol.get(symbol) or {}).get("lastPrice")),
            observed_at=observed_at,
            market_rank=market_rank,
            retail_rank=retail_rank,
            warnings=warnings,
            tradable=True,
            role="universe_member",
            extra_signals=[
                coverage_signal,
                *[
                    signal
                    for signal in (
                        investor_flow_signal(investor_payloads, market_rank),
                        momentum_signal(evidence["candles"]),
                        evidence.get("orderbook_signal"),
                        price_limit_signal(
                            _float((price_by_symbol.get(symbol) or {}).get("lastPrice")),
                            evidence.get("upper_limit"),
                        ),
                    )
                    if signal
                ],
            ],
        )
        if candidate is not None:
            result.append(candidate)
    return result


async def _load_warnings(
    client: TossReadOnlyClient,
    store: TossStockStore,
    market: str,
    symbol: str,
    observed_at: str,
) -> dict[str, Any]:
    key = (market, symbol)
    cached = _warning_cache.get(key)
    if cached and time.monotonic() - cached[0] < 86400:
        return cached[1]
    payload = await client.get(f"/api/v1/stocks/{symbol}/warnings")
    store.upsert_warning(market, symbol, observed_at, payload)
    _warning_cache[key] = (time.monotonic(), payload)
    return payload


async def _load_candidate_evidence(
    client: TossReadOnlyClient,
    store: TossStockStore,
    market: str,
    symbol: str,
    observed_at: str,
) -> dict[str, Any]:
    key = (market, symbol)
    cached = _candidate_evidence_cache.get(key)
    if cached and time.monotonic() - cached[0] < 15:
        return cached[1]
    orderbook_payload, trades_payload, limits_payload, candles_payload = await asyncio.gather(
        client.get("/api/v1/orderbook", params={"symbol": symbol}),
        client.get("/api/v1/trades", params={"symbol": symbol, "count": 50}),
        client.get("/api/v1/price-limits", params={"symbol": symbol}),
        client.get(
            "/api/v1/candles",
            params={"symbol": symbol, "interval": "1m", "count": 200, "adjusted": "true"},
        ),
    )
    for kind, payload in (
        ("orderbook", orderbook_payload),
        ("trades", trades_payload),
        ("price_limits", limits_payload),
    ):
        store.append_raw(
            "toss_quotes",
            {"market": market, "symbol": symbol, "observed_at": observed_at},
            {"kind": kind, "response": payload},
        )
    result = candles_payload.get("result") or {}
    raw_rows = (result.get("candles") or []) if isinstance(result, dict) else []
    one_minute = _normalize_candles(raw_rows)
    if one_minute:
        store.upsert_candles(market, symbol, "1m", "toss_1m", observed_at, one_minute)
        for minutes, timeframe in ((5, "5m"), (15, "15m"), (60, "1h"), (240, "4h")):
            store.upsert_candles(
                market,
                symbol,
                timeframe,
                "resampled_from_toss_1m",
                observed_at,
                resample_candles(one_minute, minutes),
            )
    await _backfill_candidate_daily(client, store, market, symbol, observed_at)
    current_imbalance = _orderbook_ratio(orderbook_payload)
    orderbook_signal = orderbook_change_signal(_orderbook_imbalance.get(key), current_imbalance)
    if current_imbalance is not None:
        _orderbook_imbalance[key] = current_imbalance
    limits = limits_payload.get("result") or {}
    evidence = {
        "candles": one_minute,
        "orderbook_signal": orderbook_signal,
        "upper_limit": _float(limits.get("upperLimitPrice")) if isinstance(limits, dict) else None,
    }
    _candidate_evidence_cache[key] = (time.monotonic(), evidence)
    return evidence


async def _backfill_candidate_daily(
    client: TossReadOnlyClient,
    store: TossStockStore,
    market: str,
    symbol: str,
    observed_at: str,
) -> bool:
    day = observed_at[:10]
    if _daily_backfilled_on.get((market, symbol)) == day:
        return False
    payload = await client.get(
        "/api/v1/candles",
        params={"symbol": symbol, "interval": "1d", "count": 200, "adjusted": "true"},
    )
    result = payload.get("result") or {}
    rows = (result.get("candles") or []) if isinstance(result, dict) else []
    candles = _normalize_candles(rows)
    if not candles:
        return False
    store.upsert_candles(market, symbol, "1d", "toss_1d", observed_at, candles)
    _daily_backfilled_on[(market, symbol)] = day
    return True


async def _backfill_next_daily_candle(
    client: TossReadOnlyClient,
    store: TossStockStore,
    market: str,
    symbols: list[str],
    observed_at: str,
) -> str | None:
    """Rotate one non-hot universe member per cycle to stay inside the candle budget."""
    if not symbols:
        return None
    day = observed_at[:10]
    start = _daily_backfill_cursor[market] % len(symbols)
    symbol = None
    for offset in range(len(symbols)):
        candidate = symbols[(start + offset) % len(symbols)]
        if _daily_backfilled_on.get((market, candidate)) != day:
            symbol = candidate
            _daily_backfill_cursor[market] = (start + offset + 1) % len(symbols)
            break
    if symbol is None:
        return None
    payload = await client.get(
        "/api/v1/candles",
        params={"symbol": symbol, "interval": "1d", "count": 200, "adjusted": "true"},
    )
    result = payload.get("result") or {}
    raw_rows = (result.get("candles") or []) if isinstance(result, dict) else []
    candles = _normalize_candles(raw_rows)
    if candles:
        store.upsert_candles(market, symbol, "1d", "toss_1d_backfill", observed_at, candles)
        _daily_backfilled_on[(market, symbol)] = day
        return symbol
    return None


def _normalize_candles(raw_rows: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "opened_at": row["timestamp"],
            "open": float(row["openPrice"]),
            "high": float(row["highPrice"]),
            "low": float(row["lowPrice"]),
            "close": float(row["closePrice"]),
            "volume": float(row.get("volume") or 0),
        }
        for row in raw_rows
        if isinstance(row, dict) and all(row.get(key) is not None for key in ("timestamp", "openPrice", "highPrice", "lowPrice", "closePrice"))
    ]


def _orderbook_ratio(payload: dict[str, Any]) -> float | None:
    result = payload.get("result") or {}
    if not isinstance(result, dict):
        return None
    try:
        bid_volume = sum(float(row.get("volume") or 0) for row in result.get("bids") or [])
        ask_volume = sum(float(row.get("volume") or 0) for row in result.get("asks") or [])
    except (AttributeError, TypeError, ValueError):
        return None
    total = bid_volume + ask_volume
    return bid_volume / total if total > 0 else None


def _ranking_rows(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = payload.get("result") or {}
    rows = (result.get("rankings") or []) if isinstance(result, dict) else []
    return {str(row.get("symbol")): row for row in rows if isinstance(row, dict) and row.get("symbol")}


def _result_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result") or payload.get("data") or []
    return [row for row in result if isinstance(row, dict)] if isinstance(result, list) else []


def _session_window(value: Any) -> tuple[datetime, datetime] | None:
    if not isinstance(value, dict):
        return None
    start = value.get("startTime")
    end = value.get("endTime")
    if start and end:
        try:
            return (
                datetime.fromisoformat(str(start).replace("Z", "+00:00")).astimezone(timezone.utc),
                datetime.fromisoformat(str(end).replace("Z", "+00:00")).astimezone(timezone.utc),
            )
        except ValueError:
            return None
    return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
