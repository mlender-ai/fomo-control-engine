"""Daily scoring and proposal-gated lifecycle review for candidate signatures."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from uuid import UUID

from app.analyst.alignment import build_full_alignment
from app.analyst.briefing import hysteresis_params_from_settings
from app.analyst.confluence import build_confluence
from app.analyst.signature_registry import current_state, record_transition
from app.backtest.costs import roundtrip_cost_pct
from app.backtest.data_quality import assess_candles
from app.backtest.outcomes import aggregate_outcomes, atr, score_event_outcome
from app.backtest.regimes import label_regime
from app.backtest.replay import replay_candles
from app.backtest.signatures import SetupSignature, signature_key, signature_label
from app.backtest.statistics import DISCLAIMER_NET, enrich_signature_stat
from app.db.models import BacktestStat, JudgmentLedgerEntry, MarketCandle, MarketSnapshot, utc_now
from app.exchange.bitget.trades import timeframe_seconds
from app.marketdata.assets import classify_asset_class
from app.marketdata.money_flow import classify_money_flow, coinglass_flow_observation, observations_from_metrics
from app.positions.chart_analysis import MIN_CHART_CANDLES, build_chart_analysis
from app.structure.levels.engine import detect_structure_levels


CandidateCandleLoader = Callable[[str, str], list[MarketCandle]]

CANDIDATE_SCORE_TARGETS: dict[str, dict[str, Any]] = {
    "fvg": {"label": "FVG", "event_type": "gap_formed", "replay": True},
    "order_block": {"label": "OB", "event_type": "retest", "replay": True},
    "vcp": {"label": "VCP", "event_type": "contraction", "replay": True},
    "full_alignment": {"label": "Full alignment", "event_type": "unanimous", "replay": "special"},
    "money_flow": {"label": "Futures-led rally", "event_type": "futures_led_rally", "replay": "special"},
}

# These contracts are exercised in test_candidate_scoring.py and the detector replay tests. The scorer
# refuses to run if a newly registered target is absent from this audited set.
LOOKAHEAD_AUDITED_ENGINES = frozenset({"fvg", "order_block", "vcp", "full_alignment", "money_flow"})
LIVE_LEDGER_POSITION_ID = UUID(int=0)
PROMOTION_VETO_HOURS = 48
_REVIEW_CACHE: dict[int, tuple[str, dict[str, Any]]] = {}


def score_candidates(
    repo: Any,
    settings: Any,
    *,
    targets: Iterable[tuple[str, str]],
    candle_loader: CandidateCandleLoader,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Replay all tracked pairs, merge live observations, persist stats, and review."""

    now = _aware(now or utc_now())
    audit = candidate_lookahead_audit()
    if not audit["passed"]:
        raise RuntimeError("candidate scoring blocked: lookahead audit incomplete")

    validated_history = _frozen_validated_history(repo, settings)
    judgments = [item for item in repo.list_judgments(LIVE_LEDGER_POSITION_ID, limit=5000) if item.type == "candidate_signature"]
    scored_pairs: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    saved_count = 0
    for symbol, timeframe in sorted({(symbol.upper(), timeframe) for symbol, timeframe in targets}):
        try:
            candles = _confirmed_candles(candle_loader(symbol, timeframe), timeframe, now)
            quality = assess_candles(candles, timeframe)
            quality_floor = int(getattr(settings, "backtest_data_quality_floor", 70))
            if quality["score"] < quality_floor:
                scored_pairs.append({"symbol": symbol, "timeframe": timeframe, "status": "data_quality_blocked", "score": quality["score"]})
                continue
            clean = quality["valid_candles"]
            min_window = max(30, int(getattr(settings, "backtest_min_window_candles", 60)))
            if len(clean) < min_window + 5:
                scored_pairs.append({"symbol": symbol, "timeframe": timeframe, "status": "insufficient_candles", "candles": len(clean)})
                continue
            asset_class = classify_asset_class(symbol)
            lookahead = max(5, int(getattr(settings, "backtest_lookahead_bars", 48)))
            cost_pct = roundtrip_cost_pct(settings, asset_class=asset_class)
            replay_cases = [
                {**case, "source": "backtest"}
                for case in replay_candles(
                    symbol,
                    timeframe,
                    clean,
                    asset_class=asset_class,
                    min_window=min_window,
                    lookahead_bars=lookahead,
                    cost_pct=cost_pct,
                    regime_params=_regime_params(settings),
                )
                if _target_case(case)
            ]
            replay_cases.extend(
                _special_replay_cases(
                    repo,
                    settings,
                    symbol=symbol,
                    timeframe=timeframe,
                    candles=clean,
                    asset_class=asset_class,
                    min_window=min_window,
                    lookahead_bars=lookahead,
                    cost_pct=cost_pct,
                    validated_history=validated_history,
                )
            )
            live_cases = _live_cases_for_pair(
                judgments,
                symbol=symbol,
                timeframe=timeframe,
                candles=clean,
                asset_class=asset_class,
                lookahead_bars=lookahead,
                cost_pct=cost_pct,
                settings=settings,
            )
            combined = _dedupe_cases([*live_cases, *replay_cases])
            stats = _stats_by_signature(combined, settings)
            observed_engines = {str(_signature(stat).get("engine") or "") for stat in stats}
            stats.extend(_empty_stat(engine, asset_class, timeframe) for engine in CANDIDATE_SCORE_TARGETS if engine not in observed_engines)
            for stat in stats:
                _save_stat(repo, symbol, timeframe, asset_class, stat, audit)
                saved_count += 1
            scored_pairs.append(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "status": "scored",
                    "backtest_cases": len(replay_cases),
                    "live_cases": len(live_cases),
                    "unique_cases": len(combined),
                    "stats": len(stats),
                }
            )
        except Exception as exc:
            errors.append({"symbol": symbol, "timeframe": timeframe, "error": f"{type(exc).__name__}: {exc}"})

    proposals = evaluate_candidate_promotions(repo, settings, now=now)
    status = candidate_review_status(repo, settings, now=now)
    return {
        "as_of": now.isoformat(),
        "pairs": scored_pairs,
        "saved_stats": saved_count,
        "proposals": proposals,
        "candidate_review": status,
        "lookahead_audit": audit,
        "errors": errors,
    }


def candidate_lookahead_audit() -> dict[str, Any]:
    required = set(CANDIDATE_SCORE_TARGETS)
    audited = set(LOOKAHEAD_AUDITED_ENGINES)
    return {
        "passed": required == audited,
        "required": sorted(required),
        "audited": sorted(audited),
        "contracts": {
            "fvg": "closed_prefix_only",
            "order_block": "closed_prefix_and_two_bar_swing_delay",
            "vcp": "closed_prefix_and_two_bar_swing_delay",
            "full_alignment": "closed_prefix_sequential_stance_and_frozen_validated_model_set",
            "money_flow": "confirmed_metric_prefix_only",
        },
    }


def evaluate_candidate_promotions(repo: Any, settings: Any, *, now: datetime | None = None) -> list[dict[str, Any]]:
    now = _aware(now or utc_now())
    proposals: list[dict[str, Any]] = []
    for stat in _signature_review_stats(repo, settings):
        key = str(stat["signature_key"])
        state = current_state(repo, key, stat=stat, settings=settings)
        gate = _promotion_gate(stat, settings)
        if state != "candidate":
            continue
        if gate["degrade"]:
            if not _same_latest_transition(repo, key, "degrade", gate["fingerprint"]):
                record_transition(
                    repo,
                    signature_key=key,
                    previous="candidate",
                    new="degraded",
                    transition="degrade",
                    reason="candidate_ci_upper_below_threshold",
                    evidence=gate,
                    autonomous=True,
                )
            continue
        if not gate["eligible"]:
            continue
        if _pending_promotion(repo, key) is not None:
            continue
        if _same_latest_transition(repo, key, "promotion_vetoed", gate["fingerprint"]):
            continue
        if _same_latest_transition(repo, key, "promotion_proposed", gate["fingerprint"]):
            continue
        evidence = {
            **gate,
            "veto_deadline_at": (now + timedelta(hours=PROMOTION_VETO_HOURS)).isoformat(),
            "proposal_only": True,
            "automatic_application": False,
        }
        record_transition(
            repo,
            signature_key=key,
            previous="candidate",
            new="candidate",
            transition="promotion_proposed",
            reason="candidate_regime_gate_passed",
            evidence=evidence,
            autonomous=False,
        )
        proposals.append({"signature_key": key, **evidence})
    return proposals


def approve_candidate_promotion(repo: Any, signature_key_value: str, *, approved_by: str = "manual") -> Any:
    proposal = _pending_promotion(repo, signature_key_value)
    if proposal is None:
        raise ValueError("candidate promotion proposal not found")
    if current_state(repo, signature_key_value) != "candidate":
        raise ValueError("candidate promotion proposal is no longer applicable")
    return record_transition(
        repo,
        signature_key=signature_key_value,
        previous="candidate",
        new="validated",
        transition="promotion_applied",
        reason=f"approved_by:{approved_by}",
        evidence={**proposal.evidence, "approved_by": approved_by, "approved_at": utc_now().isoformat()},
        autonomous=False,
    )


def veto_candidate_promotion(repo: Any, signature_key_value: str, *, vetoed_by: str = "manual") -> Any:
    proposal = _pending_promotion(repo, signature_key_value)
    if proposal is None:
        raise ValueError("candidate promotion proposal not found")
    deadline = _parse_datetime(proposal.evidence.get("veto_deadline_at")) if isinstance(proposal.evidence, dict) else None
    if deadline is not None and utc_now() > deadline:
        raise ValueError("candidate promotion veto window expired")
    return record_transition(
        repo,
        signature_key=signature_key_value,
        previous="candidate",
        new="candidate",
        transition="promotion_vetoed",
        reason=f"vetoed_by:{vetoed_by}",
        evidence={**proposal.evidence, "vetoed_by": vetoed_by, "vetoed_at": utc_now().isoformat()},
        autonomous=False,
    )


def candidate_review_status(repo: Any, settings: Any, *, now: datetime | None = None) -> dict[str, Any]:
    now = _aware(now or utc_now())
    fingerprint = _review_fingerprint(repo, settings)
    cached = _REVIEW_CACHE.get(id(repo))
    if cached is not None and cached[0] == fingerprint:
        return deepcopy(cached[1])
    rows = _signature_review_stats(repo, settings)
    by_engine: dict[str, list[dict[str, Any]]] = {engine: [] for engine in CANDIDATE_SCORE_TARGETS}
    for row in rows:
        engine = str(_signature(row).get("engine") or row.get("engine") or "")
        if engine in by_engine:
            by_engine[engine].append(row)
    items: list[dict[str, Any]] = []
    min_sample = int(getattr(settings, "signature_validated_min_sample", 30))
    for engine, definition in CANDIDATE_SCORE_TARGETS.items():
        cases = _dedupe_cases([case for row in by_engine[engine] for case in _cases(row)])
        aggregate = _engine_stat(engine, cases, settings)
        signatures = by_engine[engine]
        pending = [row for row in signatures if _pending_promotion(repo, str(row["signature_key"])) is not None]
        states = [current_state(repo, str(row["signature_key"]), stat=row, settings=settings) for row in signatures]
        source_counts = {source: len([case for case in cases if case.get("source") == source]) for source in ("backtest", "live")}
        ci = aggregate.get("win_1r_ci")
        ci_low = float(ci[0]) if isinstance(ci, (list, tuple)) and len(ci) == 2 else None
        sample_size = int(aggregate.get("sample_size") or 0)
        items.append(
            {
                "engine": engine,
                "event_type": definition["event_type"],
                "label": definition["label"],
                "sample_size": sample_size,
                "win_1r_pct": aggregate.get("win_1r_pct"),
                "win_2r_pct": aggregate.get("win_2r_pct"),
                "win_1r_ci": ci,
                "regimes": aggregate.get("regimes") or {},
                "remaining_samples": max(0, min_sample - _best_regime_sample(aggregate)),
                "source_counts": source_counts,
                "status": "validated" if "validated" in states else "promotion_proposed" if pending else "degraded" if "degraded" in states else "candidate",
                "promotion_signature_keys": [str(row["signature_key"]) for row in pending],
                "predictive_warning": bool(
                    sample_size >= min_sample and ci_low is not None and ci_low < float(getattr(settings, "universe_backtest_min_ci_low_pct", 50.0))
                ),
                "lookahead_audit": "passed" if engine in LOOKAHEAD_AUDITED_ENGINES else "blocked",
            }
        )
    result = {
        "generated_at": now.isoformat(),
        "policy": "overall and each sufficiently sampled regime: N>=30 and win@1R CI lower>=50%; promotion is proposal-only",
        "veto_window_hours": PROMOTION_VETO_HOURS,
        "items": items,
        "pending_promotions": len(
            {str(row["signature_key"]) for rows in by_engine.values() for row in rows if _pending_promotion(repo, str(row["signature_key"])) is not None}
        ),
        "lookahead_audit": candidate_lookahead_audit(),
    }
    _REVIEW_CACHE[id(repo)] = (fingerprint, deepcopy(result))
    return result


def candidate_engine_review(repo: Any, settings: Any, engine: str) -> dict[str, Any] | None:
    return next((item for item in candidate_review_status(repo, settings)["items"] if item["engine"] == engine), None)


def _confirmed_candles(candles: list[MarketCandle], timeframe: str, now: datetime) -> list[MarketCandle]:
    duration = timedelta(seconds=timeframe_seconds(timeframe))
    return sorted(
        [candle for candle in candles if _aware(candle.timestamp) + duration <= now],
        key=lambda candle: candle.timestamp,
    )


def _target_case(case: dict[str, Any]) -> bool:
    signature = _signature(case)
    engine = str(signature.get("engine") or "")
    return engine in CANDIDATE_SCORE_TARGETS and CANDIDATE_SCORE_TARGETS[engine]["replay"] is True


def _special_replay_cases(
    repo: Any,
    settings: Any,
    *,
    symbol: str,
    timeframe: str,
    candles: list[MarketCandle],
    asset_class: str,
    min_window: int,
    lookahead_bars: int,
    cost_pct: float,
    validated_history: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return [
        *_full_alignment_replay_cases(
            repo,
            settings,
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            asset_class=asset_class,
            min_window=min_window,
            lookahead_bars=lookahead_bars,
            cost_pct=cost_pct,
            validated_history=validated_history,
        ),
        *_money_flow_replay_cases(
            repo,
            settings,
            symbol=symbol,
            timeframe=timeframe,
            candles=candles,
            asset_class=asset_class,
            lookahead_bars=lookahead_bars,
            cost_pct=cost_pct,
        ),
    ]


def _full_alignment_replay_cases(
    repo: Any,
    settings: Any,
    *,
    symbol: str,
    timeframe: str,
    candles: list[MarketCandle],
    asset_class: str,
    min_window: int,
    lookahead_bars: int,
    cost_pct: float,
    validated_history: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    historical = validated_history or _frozen_validated_history(repo, settings)
    if len(historical["stats"]) < 4:
        return []
    first_index = max(MIN_CHART_CANDLES - 1, min_window)
    prior_state: dict[str, Any] | None = None
    result: list[dict[str, Any]] = []
    duration = timedelta(seconds=timeframe_seconds(timeframe))
    for index in range(first_index, len(candles) - 1):
        past = candles[: index + 1]
        current = past[-1]
        snapshot = MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            price=current.close,
            change_24h=0.0,
            funding_rate=0.0,
            open_interest_change=0.0,
            candles=past,
            provider="candidate_replay",
        )
        try:
            analysis = build_chart_analysis(snapshot, None, None)
        except ValueError:
            continue
        generated_at = _aware(current.timestamp) + duration + timedelta(seconds=1)
        confluence = build_confluence(
            symbol=symbol,
            timeframe=timeframe,
            analysis=analysis,
            generated_at=generated_at,
            prior_state=prior_state,
            hysteresis_params=hysteresis_params_from_settings(settings),
        )
        state = confluence.get("stance_state")
        prior_state = dict(state) if isinstance(state, dict) else prior_state
        alignment = build_full_alignment(confluence, historical)
        if not alignment.get("unanimous") or alignment.get("transitioning"):
            continue
        direction = str(alignment.get("direction") or "neutral")
        if direction not in {"long", "short"}:
            continue
        case = _replayed_case(
            engine="full_alignment",
            event_type="unanimous",
            strength_class=f"{int(alignment.get('agreeing') or 0)}_modules",
            direction=direction,
            symbol=symbol,
            timeframe=timeframe,
            asset_class=asset_class,
            candles=candles,
            confirmation_index=index,
            lookahead_bars=lookahead_bars,
            cost_pct=cost_pct,
            settings=settings,
            event={
                "alignment": alignment,
                "input_contract": "candles[:confirmation_index+1]",
                "model_configuration": "validated set frozen at score job start",
            },
        )
        if case is not None:
            result.append(case)
    return result


def _money_flow_replay_cases(
    repo: Any,
    settings: Any,
    *,
    symbol: str,
    timeframe: str,
    candles: list[MarketCandle],
    asset_class: str,
    lookahead_bars: int,
    cost_pct: float,
) -> list[dict[str, Any]]:
    metrics = sorted(repo.list_derivative_metrics(symbol=symbol, limit=5000), key=lambda item: item.as_of)
    result: list[dict[str, Any]] = []
    seen_observations: set[str] = set()
    for end in range(1, len(metrics) + 1):
        prefix = metrics[:end]
        current, history = _flow_prefix(prefix)
        if current is None:
            continue
        as_of = _parse_datetime(current.get("as_of"))
        if as_of is None or as_of > _aware(prefix[-1].as_of):
            continue
        identity = as_of.isoformat()
        if identity in seen_observations:
            continue
        seen_observations.add(identity)
        flow = classify_money_flow(current, history, now=as_of)
        if flow.get("state") != "futures_led" or flow.get("provisional") or not flow.get("available"):
            continue
        index = _closed_candle_index(candles, timeframe, as_of)
        if index is None:
            continue
        case = _replayed_case(
            engine="money_flow",
            event_type="futures_led_rally",
            strength_class="candidate",
            direction="short",
            symbol=symbol,
            timeframe=timeframe,
            asset_class=asset_class,
            candles=candles,
            confirmation_index=index,
            lookahead_bars=lookahead_bars,
            cost_pct=cost_pct,
            settings=settings,
            event={
                "money_flow": flow,
                "metric_prefix_size": end,
                "input_contract": "metrics[:observation_as_of]",
            },
        )
        if case is not None:
            result.append(case)
    return result


def _flow_prefix(metrics: list[Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    observations = observations_from_metrics(metrics)
    current = next(
        (
            observation
            for metric in reversed(metrics)
            if metric.source == "bitget"
            if isinstance(metric.raw_json, dict)
            if isinstance((observation := metric.raw_json.get("money_flow_observation")), dict)
        ),
        observations[-1] if observations else None,
    )
    coinglass = next(
        (observation for metric in reversed(metrics) if (observation := coinglass_flow_observation(metric)) is not None),
        None,
    )
    if coinglass is not None:
        bitget = current or {}
        current = {
            **bitget,
            **coinglass,
            "price_change_pct": coinglass.get("price_change_pct") if coinglass.get("price_change_pct") is not None else bitget.get("price_change_pct"),
            "oi_change_pct": coinglass.get("oi_change_pct") if coinglass.get("oi_change_pct") is not None else bitget.get("oi_change_pct"),
            "coverage": {**(bitget.get("coverage") or {}), **(coinglass.get("coverage") or {})},
        }
    source = current.get("source") if isinstance(current, dict) else None
    source_history = [item for item in observations if item.get("source") == source] if source else observations
    current_as_of = _parse_datetime(current.get("as_of")) if isinstance(current, dict) else None
    if current_as_of is not None:
        source_history = [item for item in source_history if (item_as_of := _parse_datetime(item.get("as_of"))) is not None and item_as_of <= current_as_of]
    return current, source_history


def _closed_candle_index(candles: list[MarketCandle], timeframe: str, as_of: datetime) -> int | None:
    duration = timedelta(seconds=timeframe_seconds(timeframe))
    matches = [index for index, candle in enumerate(candles) if _aware(candle.timestamp) + duration <= _aware(as_of)]
    return matches[-1] if matches else None


def _replayed_case(
    *,
    engine: str,
    event_type: str,
    strength_class: str,
    direction: str,
    symbol: str,
    timeframe: str,
    asset_class: str,
    candles: list[MarketCandle],
    confirmation_index: int,
    lookahead_bars: int,
    cost_pct: float,
    settings: Any,
    event: dict[str, Any],
) -> dict[str, Any] | None:
    if confirmation_index < 0 or confirmation_index >= len(candles) - 1:
        return None
    past = candles[: confirmation_index + 1]
    future = candles[confirmation_index + 1 : confirmation_index + 1 + lookahead_bars]
    if not future:
        return None
    levels = detect_structure_levels(past, past[-1].close)
    signature = SetupSignature(
        engine=engine,
        event_type=event_type,
        strength_class=strength_class,
        direction=direction,
        asset_class=asset_class,
        timeframe=timeframe,
    ).model_dump()
    signature["key"] = signature_key(signature)
    signature["label"] = signature_label(signature)
    regime = label_regime(past, **_regime_params(settings))
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "asset_class": asset_class,
        "as_of": past[-1].timestamp.isoformat(),
        "confirmation_index": confirmation_index,
        "entry_price": round(past[-1].close, 8),
        "signature": signature,
        "signature_key": signature["key"],
        "event": event,
        "outcome": score_event_outcome(
            future,
            direction=direction,
            entry_price=past[-1].close,
            invalidation_price=_invalidation_price(direction, past[-1].close, levels),
            atr_value=atr(past),
            max_bars=lookahead_bars,
            cost_pct=cost_pct,
        ),
        "regime": regime.get("regime"),
        "regime_label": regime.get("regime_label"),
        "source": "backtest",
        "disclaimer": DISCLAIMER_NET,
    }


def _frozen_validated_history(repo: Any, settings: Any) -> dict[str, Any]:
    stats: list[dict[str, Any]] = []
    for model in repo.list_backtest_stats(limit=5000):
        if model.engine in CANDIDATE_SCORE_TARGETS:
            continue
        payload = model.model_dump(mode="json")
        inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        merged = {**payload, **inner}
        merged["lifecycle_state"] = current_state(repo, model.signature_key, stat=merged, settings=settings)
        if merged["lifecycle_state"] == "validated":
            stats.append(merged)
    return {
        "stats": stats,
        "event_stats": [],
        "configuration_policy": "validated model set frozen at score job start",
    }


def _live_cases_for_pair(
    judgments: list[JudgmentLedgerEntry],
    *,
    symbol: str,
    timeframe: str,
    candles: list[MarketCandle],
    asset_class: str,
    lookahead_bars: int,
    cost_pct: float,
    settings: Any,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for judgment in judgments:
        claim = judgment.claim if isinstance(judgment.claim, dict) else {}
        engine = str(claim.get("engine") or "")
        event_type = str(claim.get("event_type") or "")
        if engine not in CANDIDATE_SCORE_TARGETS or event_type != CANDIDATE_SCORE_TARGETS[engine]["event_type"]:
            continue
        if str(claim.get("symbol") or "").upper() != symbol or str(claim.get("timeframe") or "4h") != timeframe:
            continue
        direction = str(claim.get("direction") or "neutral")
        if direction not in {"long", "short"}:
            continue
        index = _confirmation_index(candles, judgment.as_of, engine, timeframe)
        if index is None or index >= len(candles) - 1:
            continue
        past = candles[: index + 1]
        future = candles[index + 1 : index + 1 + lookahead_bars]
        if not future:
            continue
        levels = detect_structure_levels(past, past[-1].close)
        invalidation = _invalidation_price(direction, past[-1].close, levels)
        outcome = score_event_outcome(
            future,
            direction=direction,
            entry_price=past[-1].close,
            invalidation_price=invalidation,
            atr_value=atr(past),
            max_bars=lookahead_bars,
            cost_pct=cost_pct,
        )
        agreeing = claim.get("agreeing_modules") if isinstance(claim.get("agreeing_modules"), list) else []
        strength = f"{len(agreeing)}_modules" if engine == "full_alignment" and agreeing else "candidate"
        signature = SetupSignature(
            engine=engine,
            event_type=event_type,
            strength_class=strength,
            direction=direction,
            asset_class=asset_class,
            timeframe=timeframe,
        ).model_dump()
        signature["key"] = signature_key(signature)
        signature["label"] = signature_label(signature)
        regime = label_regime(past, **_regime_params(settings))
        result.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "asset_class": asset_class,
                "as_of": past[-1].timestamp.isoformat(),
                "confirmation_index": index,
                "entry_price": round(past[-1].close, 8),
                "signature": signature,
                "signature_key": signature["key"],
                "event": {"judgment_id": judgment.judgment_id, "claim": claim},
                "outcome": outcome,
                "regime": regime.get("regime"),
                "regime_label": regime.get("regime_label"),
                "source": "live",
                "disclaimer": DISCLAIMER_NET,
            }
        )
    return result


def _confirmation_index(candles: list[MarketCandle], as_of: datetime, engine: str, timeframe: str) -> int | None:
    target = _aware(as_of)
    if engine == "money_flow":
        target -= timedelta(seconds=timeframe_seconds(timeframe))
    matches = [index for index, candle in enumerate(candles) if _aware(candle.timestamp) <= target]
    return matches[-1] if matches else None


def _stats_by_signature(cases: list[dict[str, Any]], settings: Any) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        grouped.setdefault(str(case["signature_key"]), []).append(case)
    return [_build_stat(items, settings) for _, items in sorted(grouped.items())]


def _build_stat(cases: list[dict[str, Any]], settings: Any) -> dict[str, Any]:
    signature = _signature(cases[0])
    aggregate = aggregate_outcomes(cases)
    stat = {
        "signature_key": str(signature.get("key") or signature_key(signature)),
        "signature": signature,
        "label": signature_label(signature),
        "scope": "symbol",
        **aggregate,
        "cases": cases,
        "sample_warning": "표본 부족 — 결론 유보" if aggregate["sample_size"] < 10 else None,
        "sources": {source: _source_summary(cases, source) for source in ("backtest", "live")},
        "dedupe_policy": "same signature/symbol/timeframe/as_of counted once; live ledger wins",
    }
    return enrich_signature_stat(stat, cases, **_statistics_params(settings))


def _empty_stat(engine: str, asset_class: str, timeframe: str) -> dict[str, Any]:
    definition = CANDIDATE_SCORE_TARGETS[engine]
    signature = SetupSignature(
        engine=engine,
        event_type=str(definition["event_type"]),
        strength_class="candidate",
        direction="neutral",
        asset_class=asset_class,
        timeframe=timeframe,
    ).model_dump()
    signature["key"] = signature_key(signature)
    signature["label"] = signature_label(signature)
    return {
        "signature_key": signature["key"],
        "signature": signature,
        "label": signature["label"],
        "scope": "symbol",
        "sample_size": 0,
        "win_1r_pct": None,
        "win_2r_pct": None,
        "median_rr": None,
        "avg_mfe_r": None,
        "avg_mae_r": None,
        "avg_resolution_bars": None,
        "cases": [],
        "win_1r_ci": None,
        "oos": None,
        "unstable": False,
        "walk_forward": [],
        "regimes": {},
        "period": None,
        "sources": {"backtest": _source_summary([], "backtest"), "live": _source_summary([], "live")},
        "sample_warning": "표본 없음 — 결론 유보",
        "audit_state": "scored_no_occurrences",
        "dedupe_policy": "same signature/symbol/timeframe/as_of counted once; live ledger wins",
    }


def _engine_stat(engine: str, cases: list[dict[str, Any]], settings: Any) -> dict[str, Any]:
    if not cases:
        return {"sample_size": 0, "win_1r_pct": None, "win_2r_pct": None, "win_1r_ci": None, "regimes": {}}
    return _build_stat(cases, settings)


def _source_summary(cases: list[dict[str, Any]], source: str) -> dict[str, Any]:
    selected = [case for case in cases if case.get("source") == source]
    return aggregate_outcomes(selected)


def _save_stat(repo: Any, symbol: str, timeframe: str, asset_class: str, stat: dict[str, Any], audit: dict[str, Any]) -> BacktestStat:
    signature = _signature(stat)
    payload = {key: value for key, value in stat.items() if key != "cases"}
    payload["lookahead_audit"] = audit
    model = BacktestStat(
        signature_key=str(stat["signature_key"]),
        symbol=symbol,
        timeframe=timeframe,
        asset_class=asset_class,
        scope="symbol",
        engine=str(signature.get("engine") or "unknown"),
        event_type=str(signature.get("event_type") or "unknown"),
        strength_class=str(signature.get("strength_class") or "candidate"),
        direction=str(signature.get("direction") or "neutral"),
        sample_size=int(stat.get("sample_size") or 0),
        win_1r_pct=stat.get("win_1r_pct"),
        win_2r_pct=stat.get("win_2r_pct"),
        median_rr=stat.get("median_rr"),
        avg_mfe_r=stat.get("avg_mfe_r"),
        avg_mae_r=stat.get("avg_mae_r"),
        avg_resolution_bars=stat.get("avg_resolution_bars"),
        cases=stat.get("cases") or [],
        disclaimer=DISCLAIMER_NET,
        payload=payload,
        generated_at=utc_now(),
    )
    return repo.upsert_backtest_stat(model)


def _signature_review_stats(repo: Any, settings: Any) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for model in repo.list_backtest_stats(limit=5000):
        if model.engine not in CANDIDATE_SCORE_TARGETS:
            continue
        payload = model.model_dump(mode="json")
        inner = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
        merged = {**payload, **inner}
        grouped.setdefault(model.signature_key, []).extend(_cases(merged))
    result: list[dict[str, Any]] = []
    for key, cases in sorted(grouped.items()):
        unique = _dedupe_cases(cases)
        if unique:
            stat = _build_stat(unique, settings)
            stat["signature_key"] = key
            result.append(stat)
    return result


def _promotion_gate(stat: dict[str, Any], settings: Any) -> dict[str, Any]:
    min_sample = int(getattr(settings, "signature_validated_min_sample", 30))
    min_ci = float(getattr(settings, "universe_backtest_min_ci_low_pct", 50.0))
    overall_ci = stat.get("win_1r_ci")
    overall_n = int(stat.get("sample_size") or 0)
    regimes = stat.get("regimes") if isinstance(stat.get("regimes"), dict) else {}
    sufficient = {name: row for name, row in regimes.items() if isinstance(row, dict) and int(row.get("sample_size") or 0) >= min_sample}
    passed = {name: row for name, row in sufficient.items() if _ci_bound(row.get("win_1r_ci"), 0) is not None and _ci_bound(row.get("win_1r_ci"), 0) >= min_ci}
    overall_pass = overall_n >= min_sample and _ci_bound(overall_ci, 0) is not None and _ci_bound(overall_ci, 0) >= min_ci
    degrade = overall_n >= min_sample and _ci_bound(overall_ci, 1) is not None and _ci_bound(overall_ci, 1) < min_ci
    fingerprint = (
        f"{overall_n}:{stat.get('win_1r_pct')}:{overall_ci}:{sorted((name, row.get('sample_size'), row.get('win_1r_ci')) for name, row in sufficient.items())}"
    )
    return {
        "eligible": bool(overall_pass and sufficient and len(passed) == len(sufficient)),
        "degrade": bool(degrade),
        "sample_size": overall_n,
        "win_1r_pct": stat.get("win_1r_pct"),
        "win_1r_ci": overall_ci,
        "qualified_regimes": sorted(passed),
        "sufficient_regimes": sorted(sufficient),
        "regimes": regimes,
        "min_sample": min_sample,
        "min_ci_low_pct": min_ci,
        "fingerprint": fingerprint,
        "signature": stat.get("signature"),
    }


def _same_latest_transition(repo: Any, key: str, transition: str, fingerprint: str) -> bool:
    logs = repo.list_autonomy_logs(signature_key=key, limit=1)
    if not logs or logs[0].transition != transition:
        return False
    evidence = logs[0].evidence if isinstance(logs[0].evidence, dict) else {}
    return evidence.get("fingerprint") == fingerprint


def _pending_promotion(repo: Any, key: str) -> Any | None:
    logs = repo.list_autonomy_logs(signature_key=key, limit=50)
    for log in logs:
        if log.transition in {"promotion_applied", "promotion_vetoed", "degrade", "quarantine"}:
            return None
        if log.transition == "promotion_proposed":
            return log
    return None


def _review_fingerprint(repo: Any, settings: Any) -> str:
    stats = repo.list_backtest_stats(limit=5000)
    logs = repo.list_autonomy_logs(limit=5000)
    latest_stat = stats[0].generated_at.isoformat() if stats else "none"
    latest_log = logs[0].created_at.isoformat() if logs else "none"
    return ":".join(
        (
            str(len(stats)),
            latest_stat,
            str(len(logs)),
            latest_log,
            str(getattr(settings, "signature_validated_min_sample", 30)),
            str(getattr(settings, "universe_backtest_min_ci_low_pct", 50.0)),
        )
    )


def _dedupe_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for case in cases:
        identity = (
            str(case.get("signature_key") or ""),
            str(case.get("symbol") or "").upper(),
            str(case.get("timeframe") or ""),
            str(case.get("as_of") or ""),
        )
        if identity not in result or case.get("source") == "live":
            result[identity] = case
    return sorted(result.values(), key=lambda case: str(case.get("as_of") or ""))


def _cases(stat: dict[str, Any]) -> list[dict[str, Any]]:
    value = stat.get("cases")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _signature(value: dict[str, Any]) -> dict[str, Any]:
    signature = value.get("signature")
    return signature if isinstance(signature, dict) else {}


def _best_regime_sample(stat: dict[str, Any]) -> int:
    regimes = stat.get("regimes") if isinstance(stat.get("regimes"), dict) else {}
    samples = [int(row.get("sample_size") or 0) for row in regimes.values() if isinstance(row, dict)]
    return max(samples, default=0)


def _ci_bound(value: Any, index: int) -> float | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return float(value[index])
    except (TypeError, ValueError):
        return None


def _invalidation_price(direction: str, entry: float, levels: dict[str, Any]) -> float | None:
    if direction == "long":
        below = [level for level in levels.get("support", []) if level.price < entry]
        return below[0].price if below else None
    above = [level for level in levels.get("resistance", []) if level.price > entry]
    return above[0].price if above else None


def _regime_params(settings: Any) -> dict[str, Any]:
    return {
        "ma_period": int(getattr(settings, "regime_ma_period", 200)),
        "slope_window": int(getattr(settings, "regime_ma_slope_window", 20)),
        "slope_threshold_pct": float(getattr(settings, "regime_ma_slope_threshold_pct", 1.0)),
        "atr_lookback": int(getattr(settings, "regime_atr_lookback", 120)),
        "atr_high_percentile": float(getattr(settings, "regime_atr_high_percentile", 70.0)),
    }


def _statistics_params(settings: Any) -> dict[str, Any]:
    return {
        "iterations": int(getattr(settings, "backtest_bootstrap_iterations", 1000)),
        "confidence": float(getattr(settings, "backtest_ci_confidence", 0.95)),
        "validation_ratio": float(getattr(settings, "backtest_oos_validation_ratio", 0.30)),
        "unstable_gap_pct": float(getattr(settings, "backtest_oos_unstable_gap_pct", 15.0)),
        "walk_forward_window_days": int(getattr(settings, "backtest_walk_forward_window_days", 180)),
        "walk_forward_step_days": int(getattr(settings, "backtest_walk_forward_step_days", 60)),
    }


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None
