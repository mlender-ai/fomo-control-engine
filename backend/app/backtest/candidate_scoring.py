from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.analyst.signature_registry import current_state, record_transition
from app.backtest.outcomes import atr, score_event_outcome
from app.backtest.statistics import DISCLAIMER_NET, bootstrap_ci_from_counts
from app.db.models import BacktestStat, CalibrationSuggestion, JudgmentScore, utc_now
from app.review.autonomy import VETO_WINDOW_HOURS

CANDIDATE_SENTINEL_POSITION_ID = UUID(int=0)
CANDIDATE_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("fvg", "gap_formed", "FVG"),
    ("order_block", "retest", "Order Block"),
    ("vcp", "contraction", "VCP"),
    ("full_alignment", "unanimous", "Full Alignment"),
    ("money_flow", "futures_led_rally", "Futures-led Rally"),
)
CANDIDATE_ENGINES = frozenset({*(item[0] for item in CANDIDATE_DEFINITIONS), "whale"})
LOOKAHEAD_AUDIT = {
    "fvg": "closed prefix i-2..i; confirmation at i",
    "order_block": "BOS prefix and first retest use candles through confirmation only",
    "vcp": "right-confirmed fractal swings and closed-candle relative volume",
    "full_alignment": "confirmed bar_at, non-transitioning stance, HTF alignment",
    "money_flow": "non-provisional derivative interval snapshot; no future price bars",
    "whale": "Hyperliquid fill time is the observation anchor; only later closed candles are scored",
}


def score_candidates(
    repo: Any,
    settings: Any,
    *,
    audit_overrides: dict[str, bool] | None = None,
    engines: set[str] | None = None,
) -> dict[str, Any]:
    selected_engines = engines or set(CANDIDATE_ENGINES)
    source_stats = [
        stat
        for stat in repo.list_backtest_stats(limit=5000)
        if stat.engine in selected_engines and not bool(stat.payload.get("candidate_review"))
    ]
    audit_engines = {*(item[0] for item in CANDIDATE_DEFINITIONS), *(stat.engine for stat in source_stats)}
    audit = candidate_lookahead_audit(audit_overrides, engines=audit_engines)
    failed = [engine for engine, result in audit.items() if not result["passed"]]
    if failed:
        raise ValueError(f"candidate lookahead audit failed: {', '.join(sorted(failed))}")

    live = _live_evidence(repo)
    grouped: dict[str, list[BacktestStat]] = defaultdict(list)
    for stat in source_stats:
        grouped[stat.signature_key].append(stat)

    reviews: list[dict[str, Any]] = []
    proposals: list[CalibrationSuggestion] = []
    transitions = []
    seen_engines: set[str] = set()
    for signature_key, stats in grouped.items():
        review = _review_signature(repo, settings, signature_key, stats, live, audit)
        reviews.append(review)
        seen_engines.add(review["engine"])
        repo.upsert_backtest_stat(_review_stat(review))
        proposal = _promotion_proposal(repo, review)
        if proposal is not None:
            repo.add_calibration_suggestion(proposal)
            proposals.append(proposal)
        transition = _degrade_if_needed(repo, review)
        if transition is not None:
            transitions.append(transition.model_dump(mode="json"))

    for engine, event_type, label in CANDIDATE_DEFINITIONS:
        if engine not in selected_engines or engine in seen_engines:
            continue
        review = _empty_review(repo, settings, engine, event_type, label, live, audit)
        reviews.append(review)
        repo.upsert_backtest_stat(_review_stat(review))

    payload = candidate_review_status(repo, reviews=reviews)
    payload.update(
        {
            "scored_signatures": len(reviews),
            "promotion_proposals_created": len(proposals),
            "degraded": len(transitions),
            "lookahead_audit": audit,
            "transitions": transitions,
        }
    )
    upsert_cache = getattr(repo, "upsert_calibration_report_cache", None)
    if callable(upsert_cache):
        upsert_cache("candidate_review", payload)
    return payload


def candidate_lookahead_audit(
    overrides: dict[str, bool] | None = None,
    *,
    engines: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    overrides = overrides or {}
    return {
        engine: {
            "passed": bool(overrides.get(engine, True)),
            "policy": LOOKAHEAD_AUDIT[engine],
            "audited_at": utc_now().isoformat(),
        }
        for engine in (engines or {item[0] for item in CANDIDATE_DEFINITIONS})
    }


def score_live_candidate_judgments(repo: Any, provider: Any, settings: Any) -> dict[str, Any]:
    """Score confirmed candidate observations against later closed candles."""
    existing = {
        score.judgment_id
        for score in repo.list_judgment_scores(position_id=CANDIDATE_SENTINEL_POSITION_ID, limit=10000)
        if score.judgment_type == "candidate_signature"
    }
    snapshots: dict[tuple[str, str], Any] = {}
    created = 0
    pending = 0
    errors: list[dict[str, str]] = []
    lookahead = max(5, int(getattr(settings, "backtest_lookahead_bars", 48)))
    now = utc_now()
    for judgment in repo.list_judgments(CANDIDATE_SENTINEL_POSITION_ID, limit=10000):
        if judgment.type != "candidate_signature" or judgment.judgment_id in existing:
            continue
        symbol = str(judgment.claim.get("symbol") or "").upper()
        timeframe = str(judgment.claim.get("timeframe") or "4h")
        direction = str(judgment.claim.get("direction") or "")
        if not symbol or direction not in {"long", "short"}:
            pending += 1
            continue
        try:
            key = (symbol, timeframe)
            if key not in snapshots:
                snapshots[key] = provider.get_snapshot(symbol, timeframe)
            duration = timedelta(seconds=_timeframe_seconds(timeframe))
            candles = sorted(
                (
                    candle
                    for candle in snapshots[key].candles
                    if candle.timestamp + duration <= now
                ),
                key=lambda candle: candle.timestamp,
            )
            past = [candle for candle in candles if candle.timestamp <= judgment.as_of]
            future = [candle for candle in candles if candle.timestamp > judgment.as_of][:lookahead]
            if not past or not future:
                pending += 1
                continue
            entry_price = _float(judgment.claim.get("price")) or float(past[-1].close)
            result = score_event_outcome(
                future,
                direction=direction,
                entry_price=entry_price,
                atr_value=atr(past),
                max_bars=lookahead,
            )
            resolved = bool(result.get("win_1r")) or float(result.get("realized_rr") or 0.0) <= -1.0 or len(future) >= lookahead
            if not resolved:
                pending += 1
                continue
            won = bool(result.get("win_1r"))
            repo.add_judgment_score(
                JudgmentScore(
                    id=uuid5(NAMESPACE_URL, f"fce:candidate-live-score:{judgment.judgment_id}"),
                    judgment_id=judgment.judgment_id,
                    position_id=CANDIDATE_SENTINEL_POSITION_ID,
                    judgment_type="candidate_signature",
                    claim=judgment.claim,
                    confidence=judgment.confidence,
                    outcome="correct" if won else "wrong",
                    detail="확정 이후 net 1R 도달" if won else "확정 이후 net 1R 미도달",
                    metrics={**result, "source": "live_validation", "bars_evaluated": len(future)},
                    param_version=judgment.param_version,
                )
            )
            created += 1
        except Exception as exc:
            errors.append({"judgment_id": judgment.judgment_id, "error": f"{type(exc).__name__}: {exc}"})
    return {"created": created, "pending": pending, "errors": errors}


def candidate_review_status(repo: Any, *, reviews: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if reviews is None:
        reviews = []
        for stat in repo.list_backtest_stats(limit=5000):
            if stat.payload.get("candidate_review"):
                reviews.append({**stat.payload, "signature_key": stat.signature_key})
    latest: dict[str, dict[str, Any]] = {}
    for review in reviews:
        key = str(review.get("signature_key") or "")
        if key and key not in latest:
            latest[key] = review
    ordered = sorted(latest.values(), key=lambda item: (str(item.get("engine")), str(item.get("signature_key"))))
    return {
        "generated_at": utc_now().isoformat(),
        "candidate_count": len(ordered),
        "promotion_ready": sum(1 for item in ordered if item.get("promotion_eligible")),
        "signatures": ordered,
        "sample_warning": "백테스트는 사전확률, 라이브 원장 채점은 검증 표본으로 분리 집계합니다.",
    }


def apply_signature_promotion(repo: Any, suggestion: CalibrationSuggestion) -> Any:
    change = suggestion.proposed_change if isinstance(suggestion.proposed_change, dict) else {}
    signature_key = str(change.get("signature_key") or "")
    evidence = change.get("evidence") if isinstance(change.get("evidence"), dict) else {}
    if suggestion.suggestion_type != "signature_promotion" or not signature_key or not evidence:
        raise ValueError("invalid signature promotion suggestion")
    previous = current_state(repo, signature_key, stat=evidence)
    return record_transition(
        repo,
        signature_key=signature_key,
        previous=previous,
        new="validated",
        transition="validate",
        reason="candidate scoring promotion approved after veto window review",
        evidence=evidence,
        autonomous=False,
        regime=str(change.get("regime")) if change.get("regime") else None,
    )


def _review_signature(
    repo: Any,
    settings: Any,
    signature_key: str,
    stats: list[BacktestStat],
    live: dict[str, dict[str, int]],
    audit: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    first = stats[0]
    backtest_n = sum(max(0, stat.sample_size) for stat in stats)
    backtest_wins = sum(_wins(stat.sample_size, stat.win_1r_pct) for stat in stats)
    backtest_wins_2r = sum(_wins(stat.sample_size, stat.win_2r_pct) for stat in stats)
    live_bucket = live.get(signature_key, live.get(first.engine, {}))
    live_n = int(live_bucket.get("scored") or 0)
    live_wins = int(live_bucket.get("wins") or 0)
    live_wins_2r = int(live_bucket.get("wins_2r") or 0)
    total_n = backtest_n + live_n
    total_wins = backtest_wins + live_wins
    total_wins_2r = backtest_wins_2r + live_wins_2r
    win_pct = round(total_wins / total_n * 100, 1) if total_n else None
    win_2r_pct = round(total_wins_2r / total_n * 100, 1) if total_n else None
    ci = bootstrap_ci_from_counts(total_wins, total_n) if total_n else None
    regimes = _merge_regimes(stats)
    signature = _signature_payload(first)
    state = current_state(
        repo,
        signature_key,
        stat={"signature": signature, "sample_size": total_n, "win_1r_ci": ci},
        settings=settings,
    )
    minimum_n = int(getattr(settings, "signature_validated_min_sample", 30))
    minimum_ci = 55.0 if first.engine == "whale" else float(getattr(settings, "universe_backtest_min_ci_low_pct", 50.0))
    ci_low = float(ci[0]) if ci else None
    sampled_regimes = {
        regime: row
        for regime, row in regimes.items()
        if int(row.get("sample_size") or 0) >= minimum_n
    }
    qualifying_regimes = [
        regime
        for regime, row in sampled_regimes.items()
        if isinstance(row.get("win_1r_ci"), list)
        and row["win_1r_ci"]
        and float(row["win_1r_ci"][0]) >= minimum_ci
    ]
    blocked_regimes = sorted(set(sampled_regimes) - set(qualifying_regimes))
    regime_gate_passed = not regimes or (bool(qualifying_regimes) and not blocked_regimes)
    promotion_eligible = bool(
        state == "candidate"
        and total_n >= minimum_n
        and ci_low is not None
        and ci_low >= minimum_ci
        and regime_gate_passed
    )
    warning = _prediction_warning(first.engine, total_n, win_pct, ci_low, minimum_n, minimum_ci)
    return {
        "candidate_review": True,
        "signature_key": signature_key,
        "signature": signature,
        "engine": first.engine,
        "event_type": first.event_type,
        "label": str(first.payload.get("label") or first.engine),
        "state": state,
        "sample_size": total_n,
        "win_1r_pct": win_pct,
        "win_2r_pct": win_2r_pct,
        "win_1r_ci": list(ci) if ci else None,
        "regimes": regimes,
        "qualifying_regimes": qualifying_regimes,
        "blocked_regimes": blocked_regimes,
        "sources": {
            "backtest": {"sample_size": backtest_n, "wins": backtest_wins, "wins_2r": backtest_wins_2r},
            "live": {
                "observed": int(live_bucket.get("observed") or 0),
                "sample_size": live_n,
                "wins": live_wins,
                "wins_2r": live_wins_2r,
            },
        },
        "remaining_samples": max(0, minimum_n - total_n),
        "promotion_eligible": promotion_eligible,
        "prediction_warning": warning,
        "thresholds": {"min_sample_size": minimum_n, "min_ci_low_pct": minimum_ci},
        "lookahead_audit": audit[first.engine],
        "generated_at": utc_now().isoformat(),
    }


def _empty_review(
    repo: Any,
    settings: Any,
    engine: str,
    event_type: str,
    label: str,
    live: dict[str, dict[str, int]],
    audit: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    minimum_n = int(getattr(settings, "signature_validated_min_sample", 30))
    live_bucket = live.get(engine, {})
    signature_key = f"candidate-review:{engine}:{event_type}"
    return {
        "candidate_review": True,
        "signature_key": signature_key,
        "signature": {"engine": engine, "event_type": event_type, "strength_class": "candidate", "direction": "neutral"},
        "engine": engine,
        "event_type": event_type,
        "label": label,
        "state": current_state(repo, signature_key, settings=settings),
        "sample_size": 0,
        "win_1r_pct": None,
        "win_2r_pct": None,
        "win_1r_ci": None,
        "regimes": {},
        "sources": {
            "backtest": {"sample_size": 0, "wins": 0, "wins_2r": 0},
            "live": {"observed": int(live_bucket.get("observed") or 0), "sample_size": 0, "wins": 0, "wins_2r": 0},
        },
        "remaining_samples": minimum_n,
        "promotion_eligible": False,
        "prediction_warning": "예측력 미검증",
        "thresholds": {"min_sample_size": minimum_n, "min_ci_low_pct": float(getattr(settings, "universe_backtest_min_ci_low_pct", 50.0))},
        "lookahead_audit": audit[engine],
        "generated_at": utc_now().isoformat(),
    }


def _review_stat(review: dict[str, Any]) -> BacktestStat:
    signature = review["signature"]
    return BacktestStat(
        signature_key=review["signature_key"],
        symbol="__CANDIDATE__",
        timeframe=str(signature.get("timeframe") or "mixed"),
        asset_class=str(signature.get("asset_class") or "unknown"),
        scope="all",
        engine=review["engine"],
        event_type=review["event_type"],
        strength_class=str(signature.get("strength_class") or "candidate"),
        direction=str(signature.get("direction") or "neutral"),
        sample_size=int(review["sample_size"]),
        win_1r_pct=review.get("win_1r_pct"),
        win_2r_pct=review.get("win_2r_pct"),
        payload=review,
        disclaimer=DISCLAIMER_NET,
    )


def _promotion_proposal(repo: Any, review: dict[str, Any]) -> CalibrationSuggestion | None:
    if not review.get("promotion_eligible"):
        return None
    suggestion_id = uuid5(NAMESPACE_URL, f"fce:signature-promotion:{review['signature_key']}")
    if repo.get_calibration_suggestion(suggestion_id) is not None:
        return None
    now = utc_now()
    return CalibrationSuggestion(
        id=suggestion_id,
        suggestion_type="signature_promotion",
        title=f"{review['label']} validated 승격 제안",
        rationale=(
            f"candidate N={review['sample_size']} · net 1R {review['win_1r_pct']}% · "
            f"CI 하한 {review['win_1r_ci'][0]}%"
        ),
        proposed_change={
            "signature_key": review["signature_key"],
            "from": "candidate",
            "to": "validated",
            "evidence": review,
        },
        sample_size=int(review["sample_size"]),
        oos_validation={"regimes": review.get("regimes") or {}, "source_separation": review.get("sources") or {}},
        autonomy={
            "mode": "proposal_veto_only",
            "veto_window_hours": VETO_WINDOW_HOURS,
            "veto_deadline_at": (now + timedelta(hours=VETO_WINDOW_HOURS)).isoformat(),
        },
        created_at=now,
        updated_at=now,
    )


def _degrade_if_needed(repo: Any, review: dict[str, Any]) -> Any | None:
    if review.get("state") != "validated" or int(review.get("sample_size") or 0) < 30:
        return None
    ci = review.get("win_1r_ci")
    degrade_floor = 55.0 if review.get("engine") == "whale" else 50.0
    if not isinstance(ci, list) or not ci or float(ci[0]) >= degrade_floor:
        return None
    return record_transition(
        repo,
        signature_key=review["signature_key"],
        previous="validated",
        new="degraded",
        transition="degrade",
        reason=f"candidate/live combined CI lower bound fell below {degrade_floor}%",
        evidence=review,
        autonomous=True,
    )


def _live_evidence(repo: Any) -> dict[str, dict[str, int]]:
    scores: list[JudgmentScore] = repo.list_judgment_scores(position_id=CANDIDATE_SENTINEL_POSITION_ID, limit=10000)
    score_by_judgment = {score.judgment_id: score for score in scores if score.judgment_type == "candidate_signature"}
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"observed": 0, "scored": 0, "wins": 0, "wins_2r": 0})
    for judgment in repo.list_judgments(CANDIDATE_SENTINEL_POSITION_ID, limit=10000):
        if judgment.type != "candidate_signature":
            continue
        engine = str(judgment.claim.get("engine") or "")
        if engine not in CANDIDATE_ENGINES:
            continue
        signature_key = str(judgment.claim.get("signature_key") or engine)
        bucket = buckets[signature_key]
        bucket["observed"] += 1
        score = score_by_judgment.get(judgment.judgment_id)
        if score is not None and score.outcome != "untested":
            bucket["scored"] += 1
            bucket["wins"] += 1 if score.outcome == "correct" else 0
            bucket["wins_2r"] += 1 if bool(score.metrics.get("win_2r")) else 0
    return dict(buckets)


def _merge_regimes(stats: list[BacktestStat]) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, int]] = defaultdict(lambda: {"sample_size": 0, "wins": 0})
    for stat in stats:
        regimes = stat.payload.get("regimes") if isinstance(stat.payload.get("regimes"), dict) else {}
        for regime, row in regimes.items():
            if not isinstance(row, dict):
                continue
            n = int(row.get("sample_size") or 0)
            totals[str(regime)]["sample_size"] += n
            totals[str(regime)]["wins"] += _wins(n, row.get("win_1r_pct"))
    result: dict[str, dict[str, Any]] = {}
    for regime, row in totals.items():
        n = row["sample_size"]
        wins = row["wins"]
        ci = bootstrap_ci_from_counts(wins, n) if n else None
        result[regime] = {
            "sample_size": n,
            "win_1r_pct": round(wins / n * 100, 1) if n else None,
            "win_1r_ci": list(ci) if ci else None,
        }
    return result


def _prediction_warning(engine: str, n: int, win_pct: float | None, ci_low: float | None, minimum_n: int, minimum_ci: float) -> str | None:
    if engine not in {"full_alignment", "money_flow", "whale"}:
        return None
    if n < minimum_n:
        return "예측력 미검증"
    if win_pct is None or ci_low is None or ci_low < minimum_ci:
        return "예측력 미검증 · 직관 대비 성적 저조"
    return None


def _wins(sample_size: int, win_pct: Any) -> int:
    try:
        return round(max(0, sample_size) * float(win_pct) / 100.0)
    except (TypeError, ValueError):
        return 0


def _signature_payload(stat: BacktestStat) -> dict[str, Any]:
    signature = stat.payload.get("signature") if isinstance(stat.payload.get("signature"), dict) else {}
    return {
        "engine": stat.engine,
        "event_type": stat.event_type,
        "strength_class": stat.strength_class,
        "direction": stat.direction,
        "asset_class": stat.asset_class,
        "timeframe": stat.timeframe,
        **signature,
    }


def _timeframe_seconds(timeframe: str) -> int:
    value = timeframe.strip().lower()
    unit = value[-1:] if value else ""
    try:
        amount = int(value[:-1])
    except (TypeError, ValueError):
        return 4 * 60 * 60
    multipliers = {"m": 60, "h": 60 * 60, "d": 24 * 60 * 60, "w": 7 * 24 * 60 * 60}
    return max(60, amount * multipliers.get(unit, 4 * 60 * 60))


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
