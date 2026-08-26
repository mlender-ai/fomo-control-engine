from __future__ import annotations

import sqlite3

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from app.core.config import Settings

from .broker import PaperBroker, taker_fee_per_share
from .client import PolymarketPublicClient, resolved_outcome
from .estimator import attach_execution_cost, estimate_market_probability, kelly_fraction, quality_at_least
from app.notify.paper_events import track_event

from app.validation import track_scope

from . import track_status as track_status_module
from .models import FillInvariantViolation, OrderBook, PaperOrder, PolyMarket
from .parameters import load_poly_parameters
from .store import PolyPaperStore

TRACK = "poly"

# ── 제외 사유 분류 (WO-FCE-ALERT-WHITELIST-02 작업 2, C3) ────────────────────
# 거부 지표의 분모는 "실제 판정 대상"이어야 한다. 아래 셋은 거부가 아니다.
UNIVERSE_EXIT_EXPIRED = "resolved_or_expired"
# 만료·종료 — 유니버스에서 나간다. 매 사이클 재평가하지 않는다.
UNIVERSE_EXIT_REASONS = frozenset({UNIVERSE_EXIT_EXPIRED, "resolution_time_invalid", "market_inactive"})
# 범위 외 — 애초에 파싱·판정 대상이 아니다.
OUT_OF_SCOPE_REASONS = frozenset({"unsupported_crypto_question", "clob_token_missing"})
# 한도 도달 — 설계된 정상 동작이다. 5개 보유 중이면 6번째를 안 잡는 게 맞다.
CAPACITY_REASONS = frozenset({"position_capacity", "coverage_capacity", "insufficient_cash"})
# 채점 창 밖 만기 — 판정 기준 미달이 아니라 **표본이 될 수 없는 시장**이다(PHASE 2-3).
# "거부"에 섞으면 엔진이 기준 미달로 떨어뜨린 것처럼 보인다. 필터가 몇 건을 걸렀는지를
# 따로 세야 수정 전후 대조가 된다(규칙 5).
RESOLUTION_BEYOND_SCORING_WINDOW = "resolution_beyond_scoring_window"
WINDOW_FILTER_REASONS = frozenset({RESOLUTION_BEYOND_SCORING_WINDOW})
# 마지막 정산 시도의 미정산 사유 집계 — 진단 표면·리포트에서 (a)/(b) 판별에 쓴다.
_LAST_SETTLEMENT_SKIPS: dict[str, int] = {}


def last_settlement_skips() -> dict[str, int]:
    """직전 정산 사이클에서 정산되지 않은 사유 분포."""
    return dict(_LAST_SETTLEMENT_SKIPS)


class PublicMarketClient(Protocol):
    async def list_markets(self, *, limit: int = 100) -> list[PolyMarket]: ...

    async def get_market(self, market_id: str) -> PolyMarket | None: ...

    async def get_order_book(self, token_id: str) -> OrderBook: ...


async def run_poly_paper_engine(
    settings: Settings,
    market_provider: Any,
    repository: Any,
    *,
    client: PublicMarketClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    parameters = load_poly_parameters()
    store = PolyPaperStore(settings.database_url)
    store.ensure_track(initial_cash=settings.polymarket_initial_usdc, parameter_version=parameters.version, now=now)
    if not settings.polymarket_paper_enabled:
        # 조용한 스킵 금지 (D3): 비활성도 사유와 함께 관측되어야 한다.
        return {
            "enabled": False,
            "reason": "disabled",
            "live_orders_enabled": False,
            "effective_run": False,
            "events": [track_event(TRACK, "skipped", "*", detail={"reason": "disabled"})],
        }
    public = client or PolymarketPublicClient(
        gamma_base_url=settings.polymarket_gamma_base_url,
        clob_base_url=settings.polymarket_clob_base_url,
        timeout=settings.polymarket_timeout_seconds,
    )
    broker = PaperBroker(max_observed_ask_fraction=parameters.max_observed_ask_fraction)
    try:
        markets = await public.list_markets(limit=settings.polymarket_market_limit)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        store.record_collection(status="error", observed_at=now, error=message)
        return {
            "enabled": True,
            "status": "error",
            "error": message,
            "live_orders_enabled": False,
            "effective_run": False,
            "events": [track_event(TRACK, "error", "*", detail={"reason": "market_fetch_failed", "error": message})],
        }
    if markets:
        store.activate_clock(now)
    settled = await _settle_due_markets(store, public, repository, now)
    observed = estimated = entered = excluded = strict_entered = coverage_entered = 0
    events: list[dict[str, Any]] = []
    exclusion_counts: dict[str, int] = {}

    def _exclude(reason: str) -> None:
        exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1

    # PHASE 2-3: 채점 마감. 이 시각 이후 만기인 시장은 진입해도 표본이 되지 않는다.
    # 수정 전후 대조를 위해 필터 적용 전 만기 분포를 같은 사이클에서 함께 남긴다(규칙 5).
    scoring_deadline = scoring_cutoff(parameters, now=now, deadline=store.validation_ends_at())
    expiry_before = expiry_histogram(markets, now=now, scoring_deadline=scoring_deadline)
    universe_exits: dict[str, int] = {}
    for source_market in markets:
        observed += 1
        market = _apply_market_gates(source_market, now=now, scoring_deadline=scoring_deadline)
        # 작업 2-1: 만료·종료 시장은 유니버스 이탈이다. 평가하지 않고 재평가도 하지 않는다.
        # 거부 카운트에도 넣지 않는다 — 판정 대상이 아니었기 때문이다(C3).
        if market.exclusion_reason in UNIVERSE_EXIT_REASONS:
            universe_exits[str(market.exclusion_reason)] = universe_exits.get(str(market.exclusion_reason), 0) + 1
            store.save_market(market)
            continue
        latest_at = store.latest_estimate_at(market.id)
        retry_unexecuted = (
            store.latest_estimate_needs_execution_retry(market.id)
            and not store.has_open_position(market.id)
            and store.open_position_count() < parameters.max_open_markets
        )
        due = latest_at is None or now - latest_at >= timedelta(minutes=parameters.estimate_min_interval_minutes) or retry_unexecuted
        if not due:
            store.save_market(market)
            continue
        # Bitget's public provider exposes a synchronous snapshot wrapper. Run
        # probability estimation off the active worker event loop so that the
        # wrapper can safely drive its async HTTP client without blocking or
        # leaking an un-awaited coroutine.
        result = await asyncio.to_thread(estimate_market_probability, market, market_provider, now=now)
        if result.estimate is None:
            excluded += 1
            _exclude(str(result.reason or market.exclusion_reason or "estimate_unavailable"))
            store.save_market(replace(market, trade_eligible=False, exclusion_reason=result.reason or market.exclusion_reason))
            continue
        estimate = result.estimate
        book = None
        token_id = market.yes_token_id if estimate.direction.value == "YES" else market.no_token_id
        if not market.trade_eligible or token_id is None:
            estimate = replace(
                estimate,
                trade_eligible=False,
                exclusion_reason=market.exclusion_reason or "clob_token_missing",
            )
        else:
            try:
                book = await public.get_order_book(token_id)
            except Exception:
                book = None
            best_ask = book.asks[0].price if book and book.asks else None
            quality_allowed = quality_at_least(estimate.quality, parameters.min_estimate_quality)
            if best_ask is not None:
                priced = attach_execution_cost(
                    estimate,
                    effective_price=best_ask + taker_fee_per_share(best_ask, market.taker_fee_rate),
                    minimum_edge=parameters.min_edge,
                    quality_allowed=quality_allowed,
                )
                provisional_notional = max(
                    store.cash() * kelly_fraction(priced, cap=parameters.max_position_fraction),
                    store.cash() * parameters.coverage_position_fraction if parameters.coverage_entry_enabled else 0,
                )
                preview = broker.preview(book, provisional_notional, taker_fee_rate=market.taker_fee_rate) if book and provisional_notional > 0 else None
                estimate = attach_execution_cost(
                    estimate,
                    effective_price=preview.effective_price if preview else None,
                    minimum_edge=parameters.min_edge,
                    quality_allowed=quality_allowed,
                )
            else:
                estimate = attach_execution_cost(
                    estimate,
                    effective_price=None,
                    minimum_edge=parameters.min_edge,
                    quality_allowed=quality_allowed,
                )
        coverage_eligible = bool(
            parameters.coverage_entry_enabled
            and market.trade_eligible
            and estimate.exclusion_reason == "after_cost_edge_low"
            and quality_at_least(estimate.quality, parameters.min_estimate_quality)
            and estimate.effective_price is not None
            and book is not None
            and bool(book.asks)
            and estimate.base_rate
            and estimate.evidence
        )
        estimate = replace(estimate, coverage_eligible=coverage_eligible)
        store.save_market(market)
        store.save_estimate(estimate, repository, parameter_version=parameters.version)
        estimated += 1
        entry_mode = "strict_edge" if estimate.trade_eligible else "coverage_calibration" if coverage_eligible else None
        if entry_mode is None:
            excluded += 1
            _exclude(str(estimate.exclusion_reason or "not_trade_eligible"))
            continue
        if store.has_open_position(market.id) or store.open_position_count() >= parameters.max_open_markets:
            excluded += 1
            _exclude("position_capacity")
            continue
        if book is None or token_id is None:
            excluded += 1
            _exclude("orderbook_missing")
            continue
        if entry_mode == "coverage_calibration" and store.open_position_count("coverage_calibration") >= parameters.coverage_target_open_markets:
            excluded += 1
            _exclude("coverage_capacity")
            continue
        fraction = kelly_fraction(estimate, cap=parameters.max_position_fraction) if entry_mode == "strict_edge" else parameters.coverage_position_fraction
        requested_notional = store.cash() * fraction
        if requested_notional <= 0:
            excluded += 1
            _exclude("insufficient_cash")
            continue
        order = PaperOrder(
            market_id=market.id,
            estimate_id=estimate.id,
            token_id=token_id,
            direction=estimate.direction,
            requested_notional=requested_notional,
            created_at=now,
            entry_mode=entry_mode,
        )
        try:
            execution = broker.place(order, book, taker_fee_rate=market.taker_fee_rate)
        except FillInvariantViolation:
            store.stop_track("fill_price_outside_observed_orderbook", now)
            raise
        store.save_execution(order, status=execution.status, reason=execution.reason, fill=execution.fill)
        if execution.fill is not None:
            entered += 1
            if entry_mode == "strict_edge":
                strict_entered += 1
            else:
                coverage_entered += 1
            events.append(
                track_event(
                    TRACK,
                    "opened",
                    market.id,
                    detail={
                        "question": market.question,
                        "direction": estimate.direction.value,
                        "entry_mode": entry_mode,
                    },
                )
            )
    if settled:
        events.append(track_event(TRACK, "closed", "*", detail={"settled": settled}))
    # 거부 집계는 **텔레그램에 도달하지 않는다**(화이트리스트, 작업 1). 진단 표면과
    # 일 1회 요약이 읽을 수 있도록 이벤트 자체는 계속 생성한다 — 침묵 금지(C4)와
    # 거부 미발송(C1)은 양립한다: 조회는 되고 알림만 안 간다.
    # 작업 2-4: 지표 분모 정정 — 만료·범위외·한도도달을 거부에서 분리한다.
    buckets: dict[str, dict[str, int]] = {
        "rejected": {},
        "out_of_scope": {},
        "capacity_full": {},
        "window_filtered": {},
        "universe_exit": dict(universe_exits),
    }
    for reason, count in exclusion_counts.items():
        buckets[classify_exclusion(reason)][reason] = buckets[classify_exclusion(reason)].get(reason, 0) + count
    true_rejections = sum(buckets["rejected"].values())
    capacity_waiting = sum(buckets["capacity_full"].values())
    # 최다 거부는 **진짜 거부**에서만 고른다. 한도 도달이 "최다 거부"로 뜨던 오표기 수리.
    top_exclusion = max(buckets["rejected"].items(), key=lambda kv: kv[1])[0] if buckets["rejected"] else None
    if excluded > 0:
        events.append(
            track_event(
                TRACK,
                "rejected_summary",
                "*",
                detail={
                    # 정정된 분모(작업 2-4): 만료·범위외·한도도달은 거부가 아니다.
                    "evaluated": estimated,
                    "rejected": true_rejections,
                    "capacity_waiting": capacity_waiting,
                    "out_of_scope": sum(buckets["out_of_scope"].values()),
                    "universe_exits": sum(buckets["universe_exit"].values()),
                    "top_reject_gate": top_exclusion,
                    "gate_counts": buckets["rejected"],
                    "strict_entered": strict_entered,
                    "coverage_entered": coverage_entered,
                },
            )
        )
    store.record_collection(status="observed", observed_at=now)
    return {
        "enabled": True,
        "status": "observed",
        "observed": observed,
        "estimated": estimated,
        "entered": entered,
        "strict_entered": strict_entered,
        "coverage_entered": coverage_entered,
        "effective_run": True,
        "settlement_skips": last_settlement_skips(),
        # 정정된 지표: excluded 는 하위호환 총합, rejected 는 실제 판정 대상 중 미달분.
        "rejected": true_rejections,
        "capacity_waiting": capacity_waiting,
        "out_of_scope": sum(buckets["out_of_scope"].values()),
        "universe_exits": sum(buckets["universe_exit"].values()),
        "exclusion_buckets": buckets,
        # PHASE 2-4: 필터가 실제로 무엇을 걸렀는지. 후보가 크게 줄면 그것도 결과다 — 보고만 한다.
        "expiry_filter": {
            "scoring_deadline": scoring_deadline.isoformat() if scoring_deadline else None,
            "parameter_version": parameters.version,
            "max_days_to_resolution": parameters.max_days_to_resolution,
            "settlement_buffer_days": parameters.settlement_buffer_days,
            "filtered_out": sum(buckets["window_filtered"].values()),
            "expiry_distribution_before_filter": expiry_before,
            "note": "필터 적용 전 만기 분포와 걸러낸 건수. 후보가 줄어드는 것은 완화 사유가 아니다.",
        },
        "evaluable_markets": estimated,
        "top_reject_gate": top_exclusion,
        "events": events,
        "excluded": excluded,
        "settled": settled,
        "parameter_version": parameters.version,
        "live_orders_enabled": False,
    }


def poly_paper_dashboard(settings: Settings) -> dict[str, Any]:
    parameters = load_poly_parameters()
    store = PolyPaperStore(settings.database_url)
    store.ensure_track(initial_cash=settings.polymarket_initial_usdc, parameter_version=parameters.version)
    payload = store.dashboard()
    # WO-FCE-POLY-STATUS-01 — 상태를 판정된 형태로 낸다. 판정을 만들지 않고 표시만 한다(C3).
    track = payload.get("track") or {}
    expiry = payload.get("expiry") or {}
    collection = track_status_module.classify_collection(track.get("last_collection_status"), track.get("last_collection_error"))
    viability = _poly_viability(settings)
    coverage = _poly_coverage_rows(settings)
    return {
        **payload,
        "enabled": settings.polymarket_paper_enabled,
        "parameter_version": parameters.version,
        "read_only_label": "Public market data · PaperBroker only · 지갑/실주문 없음",
        "performance_gate": "대표 산출물은 수익률이 아니라 만기 Brier score와 calibration입니다.",
        "sample_note": "N<30에서는 캘리브레이션 품질 판정을 유보합니다.",
        "categories": ["crypto", "macro"],
        "live_orders_enabled": False,
        "status": track_status_module.track_status(collection=collection, viability=viability, expiry=expiry),
        # DEFAULTS-01 1-2: 검증 대상 제외(임시값)를 화면이 말한다.
        "validation_scope": track_scope.track_scope_status(track_scope.TRACK_POLY, settings),
        "sample_labels": track_status_module.sample_labels(
            resolution_count=int(payload.get("resolution_count") or 0),
            our_positions=len(payload.get("positions") or []),
            settling_within_validation=int(expiry.get("settling_within_validation") or 0),
        ),
        "clock_breakdown": track_status_module.clock_breakdown(coverage, window_start=_window_day(track.get("started_at"))),
    }


def _window_day(started_at: Any) -> str | None:
    text = str(started_at or "")
    return text[:10] if len(text) >= 10 else None


def _poly_viability(settings: Settings) -> dict[str, Any] | None:
    """이미 있는 판정을 읽는다. 새로 만들지 않는다(C3). 실패는 `None` 이다."""
    from app.validation import sample_viability

    path = str(settings.database_url).removeprefix("sqlite:///") if str(settings.database_url).startswith("sqlite:///") else ""
    if not path:
        return None
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            return sample_viability.track_sample_viability(connection, "poly")
        finally:
            connection.close()
    except Exception:
        return None


def _poly_coverage_rows(settings: Settings) -> list[dict[str, Any]]:
    """관측 커버리지 원본. 시계 0 의 사유를 분해하는 데 쓴다."""
    path = str(settings.database_url).removeprefix("sqlite:///") if str(settings.database_url).startswith("sqlite:///") else ""
    if not path:
        return []
    try:
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute("SELECT day, valid, reason FROM observation_coverage WHERE track='poly' ORDER BY day").fetchall()
        finally:
            connection.close()
    except Exception:
        return []
    return [dict(row) for row in rows]


def scoring_cutoff(parameters: Any, *, now: datetime, deadline: datetime | None) -> datetime | None:
    """이 시각까지 정산되어야 채점 표본이 된다 (PHASE 2-3).

    ```
    cutoff = min(now + max_days_to_resolution, 검증 종료일 - 안전 여유)
    ```

    둘 다 없으면 상한이 없다는 뜻이고 필터는 동작하지 않는다 — 그 사실을 감추지 않는다.
    안전 여유는 만기와 정산 확정 사이의 지연이다(만기 당일에 확정되지 않는다).
    """
    candidates: list[datetime] = []
    if parameters.max_days_to_resolution is not None:
        candidates.append(now + timedelta(days=float(parameters.max_days_to_resolution)))
    if deadline is not None:
        candidates.append(deadline - timedelta(days=float(parameters.settlement_buffer_days)))
    return min(candidates) if candidates else None


def _apply_market_gates(market: PolyMarket, *, now: datetime, scoring_deadline: datetime | None = None) -> PolyMarket:
    parameters = load_poly_parameters()
    reason = market.exclusion_reason
    # WO-FCE-ALERT-WHITELIST-02 작업 2-1: 이미 만료·종료된 시장은 유니버스에서 나간다.
    # 매 사이클 재평가하면 유니버스를 차지한 채 "거부"만 쌓여 평가 가능 시장을 밀어낸다
    # (2026-08-01 실측: resolution_time_invalid 39건이 평가 0을 만들었다).
    if reason is None and (market.closed or (market.end_at is not None and market.end_at <= now)):
        reason = UNIVERSE_EXIT_EXPIRED
    if reason is None and market.liquidity < parameters.min_liquidity:
        reason = "liquidity_below_minimum"
    if reason is None and market.end_at is not None:
        remaining_days = (market.end_at - now).total_seconds() / 86_400
        if remaining_days < parameters.min_days_to_resolution:
            reason = "resolution_too_near"
    # WO-FCE-SAMPLE-VIABILITY-01 PHASE 2-3: 채점 창 밖 만기는 **표본이 될 수 없다.**
    # 진입해도 검증 안에 정산되지 않으므로 미실현으로만 남는다. 기존 보유는 건드리지 않고
    # 신규 진입부터 적용한다.
    if reason is None and scoring_deadline is not None and market.end_at is not None and market.end_at > scoring_deadline:
        reason = RESOLUTION_BEYOND_SCORING_WINDOW
    if reason is None and not market.active:
        reason = "market_inactive"
    return replace(market, trade_eligible=reason is None, exclusion_reason=reason)


def expiry_histogram(markets: list[PolyMarket], *, now: datetime, scoring_deadline: datetime | None) -> dict[str, Any]:
    """후보 시장의 만기 분포 (PHASE 2-4 대조용).

    수정 전후를 대조하려면 **필터를 적용하지 않은 상태의 분포**가 필요하다. 그래서 이 함수는
    게이트를 거치지 않은 원본 목록을 받는다. 실측 2026-08-05 의 문제는 유니버스에 창 안
    만기가 없어서가 아니라(4,847개 존재) 상한 조건이 없어서였다 — 그 대비가 여기서 보인다.
    """
    buckets = {"<=7d": 0, "8-28d": 0, "29-90d": 0, ">90d": 0, "unknown": 0}
    within_deadline = 0
    total = 0
    for market in markets:
        total += 1
        if market.end_at is None:
            buckets["unknown"] += 1
            continue
        days = (market.end_at - now).total_seconds() / 86_400
        if days <= 7:
            buckets["<=7d"] += 1
        elif days <= 28:
            buckets["8-28d"] += 1
        elif days <= 90:
            buckets["29-90d"] += 1
        else:
            buckets[">90d"] += 1
        if scoring_deadline is not None and market.end_at <= scoring_deadline:
            within_deadline += 1
    return {
        "observed_markets": total,
        "buckets": buckets,
        "within_scoring_deadline": within_deadline,
        "beyond_scoring_deadline": total - within_deadline - buckets["unknown"] if scoring_deadline else None,
    }


def classify_exclusion(reason: str) -> str:
    """제외 사유를 지표 분류로 매핑한다 (작업 2-4, C3).

    **만료·범위외·한도도달은 "거부"가 아니다.** 거부는 "판정했으나 기준 미달"이며,
    이 셋은 애초에 판정 대상이 아니거나(만료·범위외) 설계된 정상 동작(한도도달)이다.
    분모에 섞이면 "거부 73건"처럼 엔진이 고장난 것처럼 보인다.
    """
    if reason in UNIVERSE_EXIT_REASONS:
        return "universe_exit"
    if reason in OUT_OF_SCOPE_REASONS:
        return "out_of_scope"
    if reason in CAPACITY_REASONS:
        return "capacity_full"
    if reason in WINDOW_FILTER_REASONS:
        return "window_filtered"
    return "rejected"


async def _settle_due_markets(
    store: PolyPaperStore,
    client: PublicMarketClient,
    repository: Any,
    now: datetime,
) -> int:
    scored = 0
    # WO-FCE-STOCK-EXIT-01 작업 6: 정산 0건이 (a)만기 미도래인지 (b)로직 미동작인지 구분
    # 불가능했다. 미정산 사유를 집계해 "왜 아직 정산이 없는가"를 관측 가능하게 한다(침묵 금지).
    skip_reasons: dict[str, int] = {}

    def _skip(reason: str) -> None:
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    for market_id in store.unresolved_market_ids():
        try:
            market = await client.get_market(market_id)
        except Exception:
            _skip("fetch_failed")
            continue
        if market is None:
            _skip("market_missing")
            continue
        store.save_market(market)
        outcome = resolved_outcome(market)
        if outcome is None:
            # closed=False면 만기 미도래(a). closed=True인데 여기 걸리면 가격이 확정
            # 극단값(>=0.999)에 못 미친 것이고 이는 (b) 계열 신호다.
            _skip("not_closed" if not market.closed else "closed_but_price_not_final")
            continue
        if not market.resolution_source:
            _skip("resolution_source_missing")
            continue
        scored += store.settle_market(
            market,
            outcome=outcome,
            source=market.resolution_source,
            repository=repository,
            resolved_at=now,
        )
    if skip_reasons:
        store.record_collection(status="observed", observed_at=now)
    _LAST_SETTLEMENT_SKIPS.clear()
    _LAST_SETTLEMENT_SKIPS.update(skip_reasons)
    return scored
