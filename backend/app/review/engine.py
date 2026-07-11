from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.db.models import (
    CalibrationSuggestion,
    Direction,
    JudgmentLedgerEntry,
    JudgmentScore,
    MonitoringLog,
    Position,
    PositionSnapshot,
    Trade,
    utc_now,
)
from app.backtest.statistics import bootstrap_ci_from_counts
from app.positions.insight import call_openai_insight, validate_llm_numbers
from app.review.alert_responses import build_alert_response_summary
from app.scout.monitor import build_scout_calibration_summary
from app.review.autonomy import experiments_snapshot

PRICE_TOLERANCE = 0.005
SAMPLE_FLOOR = 10
SUGGESTION_SAMPLE_FLOOR = 15
CONFIDENCE_BUCKET_SIZE = 10


def build_judgment_entries(
    position: Position,
    snapshot: PositionSnapshot,
    action_plan: dict[str, Any],
    chart_analysis: dict[str, Any],
    *,
    source_type: str,
    source_id: str | None = None,
    param_version: dict[str, Any] | None = None,
) -> list[JudgmentLedgerEntry]:
    entries: list[JudgmentLedgerEntry] = []
    direction = position.direction.value
    mark_price = snapshot.mark_price or action_plan.get("mark_price") or chart_analysis.get("mark_price")
    as_of = snapshot.as_of

    invalidation = action_plan.get("invalidation")
    if isinstance(invalidation, dict) and invalidation.get("price") is not None:
        price = float(invalidation["price"])
        claim = {
            "price": price,
            "condition": "break_below" if direction == "long" else "break_above",
            "implication": "thesis_invalid",
            "direction": direction,
            "basis": invalidation.get("basis"),
            "action": invalidation.get("action"),
            "distance_pct": invalidation.get("distance_pct"),
            "mark_price": mark_price,
            "level_score": _matched_level_score(
                chart_analysis,
                price,
                "support" if direction == "long" else "resistance",
            ),
        }
        entries.append(
            _ledger_entry(
                position,
                as_of,
                "invalidation",
                claim,
                None,
                source_type,
                source_id,
                param_version,
            )
        )

    for index, target in enumerate(action_plan.get("take_profit", []) or []):
        if not isinstance(target, dict) or target.get("price") is None:
            continue
        price = float(target["price"])
        claim = {
            "price": price,
            "condition": "touch_or_break_above" if direction == "long" else "touch_or_break_below",
            "implication": "profit_zone",
            "direction": direction,
            "basis": target.get("basis"),
            "action": target.get("action"),
            "distance_pct": target.get("distance_pct"),
            "mark_price": mark_price,
            "target_index": index + 1,
            "level_score": _matched_level_score(
                chart_analysis,
                price,
                "resistance" if direction == "long" else "support",
            ),
        }
        entries.append(
            _ledger_entry(
                position,
                as_of,
                "take_profit",
                claim,
                _confidence_from_basis(target.get("basis")),
                source_type,
                source_id,
                param_version,
            )
        )

    for marker in chart_analysis.get("wyckoff_markers", []) or []:
        if not isinstance(marker, dict) or marker.get("price") is None:
            continue
        expected_move = _wyckoff_expected_move(marker)
        if expected_move is None:
            continue
        confidence = _optional_int(marker.get("confidence"))
        claim = {
            "price": float(marker["price"]),
            "condition": f"event_confirms_{expected_move}",
            "implication": "directional_follow_through",
            "expected_move": expected_move,
            "event_type": marker.get("type"),
            "event_id": marker.get("id"),
            "event_time": marker.get("time"),
            "components": marker.get("components"),
            "level_score": marker.get("level_score"),
        }
        entries.append(
            _ledger_entry(
                position,
                as_of,
                "wyckoff_event",
                claim,
                confidence,
                "wyckoff",
                str(marker.get("id") or source_id or snapshot.id),
                param_version,
            )
        )

    liquidity = chart_analysis.get("liquidity")
    sweeps = liquidity.get("sweeps", []) if isinstance(liquidity, dict) else []
    for sweep in sweeps or []:
        if not isinstance(sweep, dict) or sweep.get("price") is None or not sweep.get("confirmed"):
            continue
        expected_move = sweep.get("expected_move")
        if expected_move not in {"up", "down"}:
            continue
        claim = {
            "price": float(sweep["price"]),
            "condition": f"sweep_confirms_{expected_move}",
            "implication": "liquidity_sweep_follow_through",
            "expected_move": expected_move,
            "side": sweep.get("side"),
            "sweep_id": sweep.get("id"),
            "pool_id": sweep.get("pool_id"),
            "pool_price": sweep.get("pool_price"),
            "wick_extreme": sweep.get("wick_extreme"),
            "depth_pct": sweep.get("depth_pct"),
            "depth_atr": sweep.get("depth_atr"),
            "grade": sweep.get("grade"),
            "volume_ratio": sweep.get("volume_ratio"),
            "wyckoff_equivalent": sweep.get("wyckoff_equivalent"),
            "basis": sweep.get("basis"),
            "components": sweep.get("components"),
        }
        entries.append(
            _ledger_entry(
                position,
                as_of,
                "liquidity_sweep",
                claim,
                _optional_int(sweep.get("confidence")),
                "liquidity",
                str(sweep.get("id") or source_id or snapshot.id),
                param_version,
            )
        )

    for zone in chart_analysis.get("harmonic_prz", []) or []:
        if not isinstance(zone, dict) or zone.get("low") is None or zone.get("high") is None:
            continue
        expected_move = "up" if zone.get("direction") == "bullish" else "down" if zone.get("direction") == "bearish" else None
        if expected_move is None:
            continue
        claim = {
            "low": float(zone["low"]),
            "high": float(zone["high"]),
            "mid": float(zone.get("mid") or (float(zone["low"]) + float(zone["high"])) / 2),
            "condition": "enter_prz",
            "implication": "potential_reversal",
            "expected_move": expected_move,
            "pattern": zone.get("pattern"),
            "pattern_id": zone.get("pattern_id"),
            "basis": zone.get("basis"),
            "status": zone.get("status"),
        }
        entries.append(
            _ledger_entry(
                position,
                as_of,
                "harmonic_prz",
                claim,
                _optional_int(zone.get("confidence")),
                "harmonic",
                str(zone.get("pattern_id") or source_id or snapshot.id),
                param_version,
            )
        )

    # WO-45: 판단 시점 레짐 태깅 — 주간 개선 비교의 레짐 통제(동일 레짐 전후)에 쓰인다.
    # claim에 넣어도 judgment_id는 price/condition만 쓰므로 멱등성은 유지된다.
    regime = _analysis_regime(chart_analysis)
    if regime:
        for entry in entries:
            entry.claim.setdefault("regime", regime)

    return entries


def _analysis_regime(chart_analysis: dict[str, Any]) -> str | None:
    historical = chart_analysis.get("historical_backtest") if isinstance(chart_analysis.get("historical_backtest"), dict) else {}
    current = historical.get("current_regime") if isinstance(historical.get("current_regime"), dict) else {}
    regime = current.get("regime")
    return str(regime) if regime and regime != "unknown" else None


def score_judgments(
    trade: Trade,
    judgments: list[JudgmentLedgerEntry],
    snapshots: list[PositionSnapshot],
    monitoring_logs: list[MonitoringLog],
) -> list[JudgmentScore]:
    price_path = _price_path(trade, snapshots, monitoring_logs)
    scores = []
    for judgment in judgments:
        score = _score_one_judgment(trade, judgment, price_path)
        scores.append(score)
    return scores


def score_interim_judgments(
    position: Position,
    judgments: list[JudgmentLedgerEntry],
    snapshots: list[PositionSnapshot],
    monitoring_logs: list[MonitoringLog],
    *,
    as_of: datetime | None = None,
) -> list[JudgmentScore]:
    scored_at = as_of or utc_now()
    current_price = _latest_position_price(position, snapshots, monitoring_logs)
    if current_price is None:
        return []
    synthetic_trade = Trade(
        id=uuid5(
            NAMESPACE_URL,
            f"fce:interim-trade:{position.id}:{scored_at.date().isoformat()}",
        ),
        position_id=position.id,
        symbol=position.symbol,
        direction=position.direction,
        entry_price=position.entry_price,
        exit_price=current_price,
        quantity=position.quantity,
        pnl_percent=position.pnl_percent,
        pnl_amount=position.unrealized_pl or 0,
        entry_score=position.entry_score,
        exit_score=position.current_score,
        holding_minutes=max(0, int((scored_at - _aware(position.opened_at)).total_seconds() // 60)),
        exit_reason="interim_scoring_open_position",
        review_text="",
        created_at=scored_at,
    )
    scores = score_judgments(synthetic_trade, judgments, snapshots, monitoring_logs)
    interim_scores = []
    for score in scores:
        metrics = {
            **score.metrics,
            "score_context": "interim",
            "interim_as_of": scored_at.isoformat(),
        }
        interim_scores.append(score.model_copy(update={"trade_id": None, "metrics": metrics}))
    return interim_scores


def build_review_v2(trade: Trade, judgments: list[JudgmentLedgerEntry], scores: list[JudgmentScore]) -> dict[str, Any]:
    outcome_counts = Counter(score.outcome for score in scores)
    by_type: dict[str, dict[str, Any]] = {}
    for score in scores:
        bucket = by_type.setdefault(
            score.judgment_type,
            {"total": 0, "correct": 0, "wrong": 0, "whipsaw": 0, "untested": 0},
        )
        bucket["total"] += 1
        bucket[score.outcome] += 1

    tested = len([score for score in scores if score.outcome != "untested"])
    correct = outcome_counts["correct"]
    accuracy = round(correct / tested * 100, 1) if tested else None
    scorecard = {
        "total": len(scores),
        "tested": tested,
        "correct": correct,
        "wrong": outcome_counts["wrong"],
        "whipsaw": outcome_counts["whipsaw"],
        "untested": outcome_counts["untested"],
        "accuracy_pct": accuracy,
        "by_type": by_type,
        "scores": [score.model_dump(mode="json") for score in scores],
    }
    return {
        "version": "review_v2",
        "source": "deterministic_scorecard",
        "generated_at": utc_now().isoformat(),
        "trade_id": str(trade.id),
        "position_id": str(trade.position_id),
        "summary": {
            "symbol": trade.symbol,
            "direction": trade.direction.value if isinstance(trade.direction, Direction) else str(trade.direction),
            "pnl_percent": trade.pnl_percent,
            "pnl_amount": trade.pnl_amount,
            "holding_minutes": trade.holding_minutes,
            "exit_reason": trade.exit_reason,
            "judgment_count": len(judgments),
            "tested_judgment_count": tested,
            "accuracy_pct": accuracy,
        },
        "scorecard": scorecard,
        "notes": _review_notes(trade, scores, tested, accuracy),
    }


def render_review(trade: Trade, review_v2: dict[str, Any] | None = None) -> str:
    result_label = "수익 거래" if trade.pnl_percent >= 0 else "손실 거래"
    score_delta = None
    if trade.entry_score is not None and trade.exit_score is not None:
        score_delta = trade.exit_score - trade.entry_score
    score_line = f"진입 대비 청산 점수 변화는 {score_delta:+d}점입니다." if score_delta is not None else "점수 변화 데이터가 충분하지 않습니다."

    scorecard = (review_v2 or trade.review_v2 or {}).get("scorecard", {})
    total = int(scorecard.get("total") or 0)
    tested = int(scorecard.get("tested") or 0)
    accuracy = scorecard.get("accuracy_pct")
    if total:
        judgment_line = (
            f"판단 원장:\n채점 가능한 판단 {total}개 중 {tested}개가 실제 가격 경로로 검증됐고, 적중률은 {accuracy:.1f}%입니다."
            if isinstance(accuracy, (int, float))
            else f"판단 원장:\n채점 가능한 판단 {total}개 중 {tested}개가 검증됐습니다."
        )
        issue_lines = _score_issue_lines(scorecard.get("scores", []))
    else:
        judgment_line = "판단 원장:\n보유 중 저장된 무효화/익절/구조 판단이 아직 없어 채점할 항목이 없습니다."
        issue_lines = []

    return (
        f"📋 Trade Review v2: {trade.symbol}\n\n"
        f"결과:\n{result_label}, 수익률 {trade.pnl_percent:.2f}%, 손익 {trade.pnl_amount:.2f} USDT\n\n"
        f"진입/청산 점수:\nEntry Score {trade.entry_score if trade.entry_score is not None else 'N/A'}점, "
        f"Exit Score {trade.exit_score if trade.exit_score is not None else 'N/A'}점. {score_line}\n\n"
        f"{judgment_line}\n\n"
        f"청산 사유:\n{trade.exit_reason}\n\n"
        f"다음 개선점:\n{_next_improvement_text(total, tested, accuracy, issue_lines)}"
    )


def generate_review_text(
    trade: Trade,
    review_v2: dict[str, Any],
    *,
    api_key: str = "",
    model: str = "gpt-4.1-mini",
    llm_client=None,
) -> tuple[str, str, str | None]:
    template_text = render_review(trade, review_v2)
    if not api_key.strip():
        return template_text, "template", "openai_api_key_missing"
    allowed_json = {"trade": trade.model_dump(mode="json"), "review_v2": review_v2}
    prompt = _review_prompt(json.dumps(allowed_json, ensure_ascii=False, sort_keys=True))
    try:
        output = llm_client(prompt, model) if llm_client else call_openai_insight(prompt, api_key, model)
    except Exception as exc:
        return template_text, "template", f"llm_call_failed:{type(exc).__name__}"
    if not validate_llm_numbers(output, allowed_json):
        return template_text, "fallback_template", "llm_number_validation_failed"
    if _contains_review_hedge(output):
        return template_text, "fallback_template", "llm_hedge_boilerplate_detected"
    return output.strip(), "llm", None


def build_calibration_summary(
    scores: list[JudgmentScore],
    suggestions: list[CalibrationSuggestion],
    alert_responses: list[Any] | None = None,
    *,
    self_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    invalidation = [score for score in scores if score.judgment_type == "invalidation"]
    take_profit = [score for score in scores if score.judgment_type == "take_profit"]
    wyckoff = [score for score in scores if score.judgment_type == "wyckoff_event"]
    briefing = [score for score in scores if score.judgment_type == "analyst_briefing"]
    judgment_types = _judgment_type_scorecard(scores)
    confidence_curve = _confidence_buckets(scores)
    return {
        "generated_at": utc_now(),
        "sample_floor": SAMPLE_FLOOR,
        "totals": _outcome_summary(scores),
        "invalidation": _outcome_summary(invalidation),
        "take_profit": {
            **_outcome_summary(take_profit),
            "reach_rate_pct": _tested_rate(take_profit),
        },
        "judgment_types": judgment_types,
        "confidence_curve": confidence_curve,
        "level_quality": _level_quality(scores),
        "score_contexts": _score_context_counts(scores),
        "wyckoff_confidence": _confidence_buckets(wyckoff),
        "briefing_performance": build_briefing_calibration_summary(briefing),
        "suggestion_status_counts": _suggestion_status_counts(suggestions),
        "autonomy": experiments_snapshot(suggestions),
        "weekly_report": build_weekly_calibration_report(scores, suggestions, alert_responses, self_audit=self_audit),
        "alert_response_summary": build_alert_response_summary(alert_responses or []),
        "scout_setup_summary": build_scout_calibration_summary(scores),
        "suggestions": [suggestion.model_dump(mode="json") for suggestion in suggestions],
        "sample_warning": "표본 N < 10 구간은 표본 부족으로 결론을 내리지 않습니다.",
    }


def build_weekly_calibration_report(
    scores: list[JudgmentScore],
    suggestions: list[CalibrationSuggestion],
    alert_responses: list[Any] | None = None,
    *,
    now: datetime | None = None,
    self_audit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    end_at = now or utc_now()
    start_at = end_at - timedelta(days=7)
    weekly_scores = [score for score in scores if _aware(score.created_at) >= start_at]
    weekly_alert_responses = [response for response in (alert_responses or []) if _aware(response.detected_at) >= start_at]
    pending_suggestions = [suggestion for suggestion in suggestions if suggestion.status == "pending"]
    scheduled_suggestions = [suggestion for suggestion in suggestions if suggestion.status == "scheduled"]
    experiment_suggestions = [suggestion for suggestion in suggestions if suggestion.status == "experiment"]
    totals = _outcome_summary(weekly_scores)
    return {
        "generated_at": end_at,
        "period": {
            "label": "최근 7일",
            "start_at": start_at.isoformat(),
            "end_at": end_at.isoformat(),
            "timezone": "UTC",
        },
        "totals": totals,
        "judgment_types": _judgment_type_scorecard(weekly_scores),
        "confidence_curve": _confidence_buckets(weekly_scores),
        "pending_suggestions": [suggestion.model_dump(mode="json") for suggestion in pending_suggestions[:5]],
        "pending_suggestions_count": len(pending_suggestions),
        "scheduled_suggestions": [suggestion.model_dump(mode="json") for suggestion in scheduled_suggestions[:5]],
        "scheduled_suggestions_count": len(scheduled_suggestions),
        "experiment_suggestions": [suggestion.model_dump(mode="json") for suggestion in experiment_suggestions[:5]],
        "experiment_suggestions_count": len(experiment_suggestions),
        "autonomy": experiments_snapshot(suggestions),
        "best_judgment": _representative_judgment(weekly_scores, "correct"),
        "worst_judgment": _representative_judgment(weekly_scores, "wrong"),
        "alert_response_summary": build_alert_response_summary(weekly_alert_responses),
        "scout_setup_summary": build_scout_calibration_summary(weekly_scores),
        "briefing_performance": build_briefing_calibration_summary([score for score in weekly_scores if score.judgment_type == "analyst_briefing"]),
        "highlights": _weekly_highlights(weekly_scores, totals),
        "self_audit": self_audit or {},
        "sample_warning": "최근 7일 표본 N < 10 구간은 결론을 보류합니다.",
    }


def _correct_rate_pct(bucket: list[JudgmentScore]) -> float | None:
    if not bucket:
        return None
    correct = len([score for score in bucket if score.outcome == "correct"])
    return correct / len(bucket) * 100


def _noisy_rate_pct(bucket: list[JudgmentScore]) -> float | None:
    if not bucket:
        return None
    noisy = len([score for score in bucket if score.outcome in {"wrong", "whipsaw"}])
    return noisy / len(bucket) * 100


def _oos_validation(
    bucket: list[JudgmentScore],
    metric_fn: Any,
    holds_fn: Any,
    *,
    min_slice: int = 5,
) -> dict[str, Any]:
    """WO-36 §4: 제안 근거 표본을 시간순 70/30 분할해 검증기간에서도 신호가 성립하는지 첨부.

    ``holds_fn`` 은 검증기간 지표를 받아 제안이 여전히 정당한지(문제가 지속되는지) 판단한다.
    """

    ordered = sorted(bucket, key=lambda score: _aware(score.created_at))
    split = int(len(ordered) * 0.7)
    train = ordered[:split]
    validation = ordered[split:]
    train_rate = metric_fn(train) if len(train) >= min_slice else None
    val_rate = metric_fn(validation) if len(validation) >= min_slice else None
    holds = bool(val_rate is not None and holds_fn(val_rate))
    return {
        "split_ratio": 0.7,
        "train": {
            "sample_size": len(train),
            "rate_pct": round(train_rate, 1) if train_rate is not None else None,
        },
        "validation": {
            "sample_size": len(validation),
            "rate_pct": round(val_rate, 1) if val_rate is not None else None,
        },
        "holds_in_validation": holds,
        "sample_state": "ok" if val_rate is not None else "insufficient",
    }


def generate_calibration_suggestions(
    scores: list[JudgmentScore],
) -> list[CalibrationSuggestion]:
    invalidation_bucket = [
        score
        for score in scores
        if score.judgment_type == "invalidation" and score.outcome != "untested" and 40 <= float(score.claim.get("level_score") or -1) <= 55
    ]
    suggestions: list[CalibrationSuggestion] = []
    if len(invalidation_bucket) >= SUGGESTION_SAMPLE_FLOOR:
        correct = len([score for score in invalidation_bucket if score.outcome == "correct"])
        correct_rate = correct / len(invalidation_bucket)
        if correct_rate < 0.4:
            suggestion_id = uuid5(NAMESPACE_URL, "fce:calibration:min_invalidation_level_score:55")
            suggestions.append(
                CalibrationSuggestion(
                    id=suggestion_id,
                    suggestion_type="min_invalidation_level_score",
                    title="무효화 근거 최소 레벨 점수 상향 제안",
                    rationale=(f"레벨 score 40~55 구간 무효화 판단의 적중률이 {correct_rate * 100:.1f}% (N={len(invalidation_bucket)})로 낮습니다."),
                    proposed_change={
                        "parameter": "min_invalidation_level_score",
                        "from": 40,
                        "to": 55,
                    },
                    sample_size=len(invalidation_bucket),
                    oos_validation=_oos_validation(invalidation_bucket, _correct_rate_pct, lambda rate: rate < 40.0),
                )
            )
    suggestions.extend(_confidence_calibration_suggestions(scores))
    suggestions.extend(_trigger_near_suggestions(scores))
    suggestions.extend(_harmonic_tolerance_suggestions(scores))
    return suggestions


def _ledger_entry(
    position: Position,
    as_of: datetime,
    judgment_type: str,
    claim: dict[str, Any],
    confidence: int | None,
    source_type: str,
    source_id: str | None,
    param_version: dict[str, Any] | None = None,
) -> JudgmentLedgerEntry:
    key = _judgment_key(position, as_of, judgment_type, claim, source_type, source_id)
    return JudgmentLedgerEntry(
        id=uuid5(NAMESPACE_URL, key),
        judgment_id=key,
        position_id=position.id,
        source_type=source_type,
        source_id=source_id,
        as_of=as_of,
        type=judgment_type,
        claim=claim,
        confidence=confidence,
        param_version=param_version or {},
    )


def _judgment_key(
    position: Position,
    as_of: datetime,
    judgment_type: str,
    claim: dict[str, Any],
    source_type: str,
    source_id: str | None,
) -> str:
    price_part = claim.get("price") or claim.get("mid") or f"{claim.get('low')}:{claim.get('high')}"
    condition = claim.get("condition")
    return f"fce:judgment:{position.id}:{as_of.isoformat()}:{source_type}:{source_id or 'none'}:{judgment_type}:{condition}:{price_part}"


def _score_one_judgment(trade: Trade, judgment: JudgmentLedgerEntry, price_path: list[dict[str, Any]]) -> JudgmentScore:
    path = [point for point in price_path if point["time"] > judgment.as_of]
    if judgment.type == "invalidation":
        outcome, detail, metrics = _score_invalidation(judgment, path)
    elif judgment.type == "planned_invalidation":
        outcome, detail, metrics = _score_invalidation(_judgment_with_condition(judgment, trade, "invalidation"), path)
    elif judgment.type == "take_profit":
        outcome, detail, metrics = _score_take_profit(judgment, path)
    elif judgment.type == "planned_take_profit":
        outcome, detail, metrics = _score_take_profit(_judgment_with_condition(judgment, trade, "take_profit"), path)
    elif judgment.type == "wyckoff_event":
        outcome, detail, metrics = _score_directional_event(judgment, path)
    elif judgment.type == "liquidity_sweep":
        outcome, detail, metrics = _score_directional_event(judgment, path)
    elif judgment.type == "candidate_signature":
        outcome, detail, metrics = _score_directional_event(judgment, path)
    elif judgment.type == "analyst_briefing":
        outcome, detail, metrics = _score_analyst_briefing(judgment, path)
    elif judgment.type == "harmonic_prz":
        outcome, detail, metrics = _score_harmonic_prz(judgment, path)
    elif judgment.type == "entry_checklist":
        outcome, detail, metrics = _score_entry_checklist(trade, judgment, path)
    elif judgment.type == "alert_fired":
        outcome, detail, metrics = _score_alert_fired(judgment, path)
    else:
        outcome, detail, metrics = (
            "untested",
            "지원하지 않는 판단 유형입니다.",
            {"path_points": len(path)},
        )
    score_id = uuid5(NAMESPACE_URL, f"fce:judgment-score:{trade.id}:{judgment.judgment_id}")
    metrics.update(
        {
            "judgment_as_of": judgment.as_of.isoformat(),
            "trade_created_at": trade.created_at.isoformat(),
            "path_points": len(path),
            "confidence": judgment.confidence,
            "level_score": judgment.claim.get("level_score"),
            "param_version": judgment.param_version,
        }
    )
    return JudgmentScore(
        id=score_id,
        judgment_id=judgment.judgment_id,
        position_id=judgment.position_id,
        trade_id=trade.id,
        judgment_type=judgment.type,
        claim=judgment.claim,
        confidence=judgment.confidence,
        outcome=outcome,
        detail=detail,
        metrics=metrics,
        param_version=judgment.param_version,
    )


def _score_invalidation(judgment: JudgmentLedgerEntry, path: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    price = _optional_float(judgment.claim.get("price"))
    condition = judgment.claim.get("condition")
    if price is None or not path:
        return "untested", "무효화 가격 이후 검증할 가격 경로가 부족합니다.", {}
    final = path[-1]["price"]
    breached = _first_breach(path, price, condition)
    if breached is None:
        return (
            "untested",
            "보유 종료 전 무효화 가격을 이탈/돌파하지 않았습니다.",
            {"final_price": final, "level": price},
        )
    if condition == "break_below":
        if final <= price * (1 - PRICE_TOLERANCE):
            return (
                "correct",
                "무효화 이탈 후 가격이 회복하지 못해 기준이 유효했습니다.",
                {
                    "breach_at": breached["time"].isoformat(),
                    "final_price": final,
                    "level": price,
                },
            )
        return (
            "whipsaw",
            "무효화 이탈 후 다시 회복해 기준이 민감했을 가능성이 있습니다.",
            {
                "breach_at": breached["time"].isoformat(),
                "final_price": final,
                "level": price,
            },
        )
    if final >= price * (1 + PRICE_TOLERANCE):
        return (
            "correct",
            "무효화 돌파 후 가격이 되돌리지 않아 기준이 유효했습니다.",
            {
                "breach_at": breached["time"].isoformat(),
                "final_price": final,
                "level": price,
            },
        )
    return (
        "whipsaw",
        "무효화 돌파 후 다시 하락해 기준이 민감했을 가능성이 있습니다.",
        {
            "breach_at": breached["time"].isoformat(),
            "final_price": final,
            "level": price,
        },
    )


def _score_take_profit(judgment: JudgmentLedgerEntry, path: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    price = _optional_float(judgment.claim.get("price"))
    condition = judgment.claim.get("condition")
    if price is None or not path:
        return "untested", "익절 후보 이후 검증할 가격 경로가 부족합니다.", {}
    final = path[-1]["price"]
    reached = _first_breach(path, price, condition)
    if reached is None:
        return (
            "untested",
            "보유 종료 전 익절 후보에 도달하지 않았습니다.",
            {"final_price": final, "target": price},
        )
    if condition == "touch_or_break_above":
        if final < price * (1 - PRICE_TOLERANCE):
            return (
                "correct",
                "익절 후보 도달 후 되돌림이 발생해 목표가가 유효했습니다.",
                {
                    "reached_at": reached["time"].isoformat(),
                    "final_price": final,
                    "target": price,
                },
            )
        if final > price * 1.01:
            return (
                "wrong",
                "익절 후보를 강하게 돌파해 기준이 보수적이었습니다.",
                {
                    "reached_at": reached["time"].isoformat(),
                    "final_price": final,
                    "target": price,
                },
            )
    else:
        if final > price * (1 + PRICE_TOLERANCE):
            return (
                "correct",
                "익절 후보 도달 후 반등이 발생해 목표가가 유효했습니다.",
                {
                    "reached_at": reached["time"].isoformat(),
                    "final_price": final,
                    "target": price,
                },
            )
        if final < price * 0.99:
            return (
                "wrong",
                "익절 후보를 강하게 이탈해 기준이 보수적이었습니다.",
                {
                    "reached_at": reached["time"].isoformat(),
                    "final_price": final,
                    "target": price,
                },
            )
    return (
        "correct",
        "익절 후보 도달 후 종료 가격이 목표 근처에 머물러 기준이 유효했습니다.",
        {
            "reached_at": reached["time"].isoformat(),
            "final_price": final,
            "target": price,
        },
    )


def _score_directional_event(judgment: JudgmentLedgerEntry, path: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    base = _optional_float(judgment.claim.get("price"))
    expected = judgment.claim.get("expected_move")
    if base is None or not path or expected not in {"up", "down"}:
        return "untested", "이벤트 이후 검증할 가격 경로가 부족합니다.", {}
    final = path[-1]["price"]
    if expected == "up":
        if final >= base * (1 + PRICE_TOLERANCE):
            return (
                "correct",
                "이벤트 이후 가격이 상승 방향으로 전개됐습니다.",
                {"base_price": base, "final_price": final},
            )
        if final <= base * (1 - PRICE_TOLERANCE):
            return (
                "wrong",
                "이벤트 이후 가격이 기대 방향과 반대로 전개됐습니다.",
                {"base_price": base, "final_price": final},
            )
    else:
        if final <= base * (1 - PRICE_TOLERANCE):
            return (
                "correct",
                "이벤트 이후 가격이 하락 방향으로 전개됐습니다.",
                {"base_price": base, "final_price": final},
            )
        if final >= base * (1 + PRICE_TOLERANCE):
            return (
                "wrong",
                "이벤트 이후 가격이 기대 방향과 반대로 전개됐습니다.",
                {"base_price": base, "final_price": final},
            )
    return (
        "untested",
        "이벤트 이후 가격 변화가 판정 임계값에 미달했습니다.",
        {"base_price": base, "final_price": final},
    )


def _score_harmonic_prz(judgment: JudgmentLedgerEntry, path: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    low = _optional_float(judgment.claim.get("low"))
    high = _optional_float(judgment.claim.get("high"))
    mid = _optional_float(judgment.claim.get("mid"))
    expected = judgment.claim.get("expected_move")
    if low is None or high is None or mid is None or not path or expected not in {"up", "down"}:
        return "untested", "PRZ 이후 검증할 가격 경로가 부족합니다.", {}
    touched = next(
        (
            point
            for point in path
            if low <= point["price"] <= high or (expected == "down" and point["price"] > high) or (expected == "up" and point["price"] < low)
        ),
        None,
    )
    if touched is None:
        return (
            "untested",
            "보유 종료 전 PRZ에 도달하지 않았습니다.",
            {"prz_low": low, "prz_high": high},
        )
    final = path[-1]["price"]
    if expected == "down":
        if final < mid:
            return (
                "correct",
                "PRZ 도달 후 하락 반전이 확인됐습니다.",
                {
                    "touched_at": touched["time"].isoformat(),
                    "final_price": final,
                    "prz_mid": mid,
                },
            )
        if final > high * (1 + PRICE_TOLERANCE):
            return (
                "wrong",
                "PRZ를 상향 돌파해 반전 구간이 실패했습니다.",
                {
                    "touched_at": touched["time"].isoformat(),
                    "final_price": final,
                    "prz_high": high,
                },
            )
    else:
        if final > mid:
            return (
                "correct",
                "PRZ 도달 후 상승 반전이 확인됐습니다.",
                {
                    "touched_at": touched["time"].isoformat(),
                    "final_price": final,
                    "prz_mid": mid,
                },
            )
        if final < low * (1 - PRICE_TOLERANCE):
            return (
                "wrong",
                "PRZ를 하향 이탈해 반전 구간이 실패했습니다.",
                {
                    "touched_at": touched["time"].isoformat(),
                    "final_price": final,
                    "prz_low": low,
                },
            )
    return (
        "whipsaw",
        "PRZ 도달 후 방향성이 충분히 확정되지 않았습니다.",
        {
            "touched_at": touched["time"].isoformat(),
            "final_price": final,
            "prz_mid": mid,
        },
    )


def _score_entry_checklist(trade: Trade, judgment: JudgmentLedgerEntry, path: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    checklist = judgment.claim.get("checklist") if isinstance(judgment.claim, dict) else []
    if not isinstance(checklist, list) or not checklist:
        return (
            "untested",
            "진입 체크리스트 항목이 없어 채점하지 않았습니다.",
            {"path_points": len(path)},
        )
    pass_count = len([item for item in checklist if isinstance(item, dict) and item.get("status") == "pass"])
    fail_count = len([item for item in checklist if isinstance(item, dict) and item.get("status") == "fail"])
    slippage_flag = bool(judgment.claim.get("slippage_flag"))
    pnl = _optional_float(trade.pnl_percent)
    if pnl is None:
        return (
            "untested",
            "체크리스트 판단을 PnL과 대조할 수 없습니다.",
            {"pass_count": pass_count, "fail_count": fail_count},
        )
    thesis_quality = pass_count >= fail_count and not slippage_flag
    if thesis_quality and pnl >= 0:
        return (
            "correct",
            "진입 체크리스트가 양호했고 현재/종료 성과도 플러스입니다.",
            {"pass_count": pass_count, "fail_count": fail_count, "pnl_percent": pnl},
        )
    if thesis_quality and pnl < 0:
        return (
            "wrong",
            "진입 체크리스트는 양호했지만 현재/종료 성과가 마이너스입니다.",
            {"pass_count": pass_count, "fail_count": fail_count, "pnl_percent": pnl},
        )
    if not thesis_quality and pnl < 0:
        return (
            "correct",
            "진입 체크리스트 경고가 있었고 현재/종료 성과도 마이너스입니다.",
            {"pass_count": pass_count, "fail_count": fail_count, "pnl_percent": pnl},
        )
    return (
        "whipsaw",
        "진입 체크리스트 경고가 있었지만 현재/종료 성과는 플러스입니다.",
        {"pass_count": pass_count, "fail_count": fail_count, "pnl_percent": pnl},
    )


def _score_alert_fired(judgment: JudgmentLedgerEntry, path: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    payload = judgment.claim if isinstance(judgment.claim, dict) else {}
    rule_id = str(payload.get("rule_id") or payload.get("state_key") or "")
    trigger = _optional_float(payload.get("trigger_price"))
    current = _optional_float(payload.get("current_price"))
    if not path:
        return (
            "untested",
            "알림 이후 검증할 가격 경로가 부족합니다.",
            {"rule_id": rule_id},
        )
    final = path[-1]["price"]
    if trigger is None or current is None:
        return (
            "untested",
            "알림 payload에 기준 가격이 부족해 방향성 채점을 보류했습니다.",
            {"rule_id": rule_id, "final_price": final},
        )
    if "invalidation_breach" in rule_id or "liq_proximity" in rule_id:
        adverse_continued = abs(final - trigger) >= abs(current - trigger)
        if adverse_continued:
            return (
                "correct",
                "알림 이후 위험 방향 움직임이 유지돼 알림 기준이 유효했습니다.",
                {
                    "rule_id": rule_id,
                    "trigger": trigger,
                    "current": current,
                    "final_price": final,
                },
            )
        return (
            "whipsaw",
            "알림 이후 가격이 기준 근처로 되돌아와 민감했을 가능성이 있습니다.",
            {
                "rule_id": rule_id,
                "trigger": trigger,
                "current": current,
                "final_price": final,
            },
        )
    return (
        "untested",
        "이 알림 유형은 WO-FCE-23 대응 복기에서 별도 채점합니다.",
        {"rule_id": rule_id, "final_price": final},
    )


def _score_analyst_briefing(judgment: JudgmentLedgerEntry, path: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    base = _optional_float(judgment.claim.get("price"))
    stance = str(judgment.claim.get("stance") or "")
    expected = judgment.claim.get("expected_move")
    if base is None or not path:
        return "untested", "브리핑 이후 검증할 가격 경로가 부족합니다.", {"stance": stance}
    final = path[-1]["price"]
    change_pct = ((final - base) / base) * 100 if base else 0
    if stance == "conflicted":
        if abs(change_pct) <= PRICE_TOLERANCE * 100:
            return (
                "correct",
                "브리핑이 충돌을 보고했고 이후 가격도 큰 방향성을 보이지 않았습니다.",
                {"stance": stance, "base_price": base, "final_price": final, "change_pct": round(change_pct, 3)},
            )
        return (
            "wrong",
            "브리핑은 충돌로 보류했지만 이후 가격이 한 방향으로 전개됐습니다.",
            {"stance": stance, "base_price": base, "final_price": final, "change_pct": round(change_pct, 3)},
        )
    if stance == "insufficient" or expected not in {"up", "down"}:
        return (
            "untested",
            "브리핑이 방향 판단을 보류해 방향성 채점을 하지 않았습니다.",
            {"stance": stance, "base_price": base, "final_price": final, "change_pct": round(change_pct, 3)},
        )
    return _score_directional_event(judgment, path)


def _judgment_with_condition(judgment: JudgmentLedgerEntry, trade: Trade, kind: str) -> JudgmentLedgerEntry:
    direction = trade.direction.value if isinstance(trade.direction, Direction) else str(trade.direction)
    if kind == "invalidation":
        condition = "break_below" if direction == "long" else "break_above"
    else:
        condition = "touch_or_break_above" if direction == "long" else "touch_or_break_below"
    claim = {
        **judgment.claim,
        "condition": judgment.claim.get("condition") or condition,
        "direction": direction,
    }
    return judgment.model_copy(
        update={
            "claim": claim,
            "type": "invalidation" if kind == "invalidation" else "take_profit",
        }
    )


def _price_path(
    trade: Trade,
    snapshots: list[PositionSnapshot],
    monitoring_logs: list[MonitoringLog],
) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if snapshot.mark_price is not None:
            points.append(
                {
                    "time": _aware(snapshot.as_of),
                    "price": float(snapshot.mark_price),
                    "source": "snapshot",
                }
            )
    for log in monitoring_logs:
        points.append(
            {
                "time": _aware(log.created_at),
                "price": float(log.current_price),
                "source": "monitoring_log",
            }
        )
    points.append(
        {
            "time": _aware(trade.created_at),
            "price": float(trade.exit_price),
            "source": "exit",
        }
    )
    deduped: dict[tuple[datetime, float], dict[str, Any]] = {}
    for point in points:
        deduped[(point["time"], point["price"])] = point
    return sorted(deduped.values(), key=lambda item: item["time"])


def _latest_position_price(
    position: Position,
    snapshots: list[PositionSnapshot],
    monitoring_logs: list[MonitoringLog],
) -> float | None:
    for snapshot in sorted(snapshots, key=lambda item: item.as_of, reverse=True):
        if snapshot.mark_price is not None:
            return float(snapshot.mark_price)
    for log in sorted(monitoring_logs, key=lambda item: item.created_at, reverse=True):
        return float(log.current_price)
    for value in (position.mark_price, position.current_price, position.entry_price):
        if value is not None:
            return float(value)
    return None


def _first_breach(path: list[dict[str, Any]], level: float, condition: str | None) -> dict[str, Any] | None:
    if condition in {"break_below", "touch_or_break_below"}:
        return next((point for point in path if point["price"] <= level), None)
    if condition in {"break_above", "touch_or_break_above"}:
        return next((point for point in path if point["price"] >= level), None)
    return None


def _outcome_summary(scores: list[JudgmentScore]) -> dict[str, Any]:
    counts = Counter(score.outcome for score in scores)
    tested = len([score for score in scores if score.outcome != "untested"])
    correct = counts["correct"]
    accuracy = round(correct / tested * 100, 1) if tested else None
    # WO-36 표기 표준: 적중률도 CI 병기 (전 표면).
    accuracy_ci = bootstrap_ci_from_counts(correct, tested) if tested else None
    sample_state = "ok" if tested >= SAMPLE_FLOOR else "insufficient_sample"
    return {
        "total": len(scores),
        "tested": tested,
        "correct": correct,
        "wrong": counts["wrong"],
        "whipsaw": counts["whipsaw"],
        "untested": counts["untested"],
        "accuracy_pct": accuracy,
        "accuracy_ci": list(accuracy_ci) if accuracy_ci else None,
        "correct_rate_pct": accuracy,
        "sample_state": sample_state,
        "sample_floor": SAMPLE_FLOOR,
        "conclusion": _accuracy_conclusion(accuracy, tested),
    }


def _tested_rate(scores: list[JudgmentScore]) -> float | None:
    if not scores:
        return None
    reached = len([score for score in scores if score.outcome != "untested"])
    return round(reached / len(scores) * 100, 1)


def _confidence_buckets(scores: list[JudgmentScore]) -> list[dict[str, Any]]:
    buckets: dict[str, list[JudgmentScore]] = defaultdict(list)
    for score in scores:
        if score.confidence is None:
            continue
        start = _confidence_bucket_start(score.confidence)
        end = min(100, start + CONFIDENCE_BUCKET_SIZE - 1)
        buckets[f"{start}-{end}"].append(score)
    result = []
    for label in sorted(buckets.keys()):
        summary = _outcome_summary(buckets[label])
        start = int(label.split("-", 1)[0])
        midpoint = min(100, start + CONFIDENCE_BUCKET_SIZE / 2)
        accuracy = summary.get("accuracy_pct")
        gap = round(float(accuracy) - midpoint, 1) if isinstance(accuracy, (int, float)) and summary["tested"] >= SAMPLE_FLOOR else None
        result.append(
            {
                "bucket": label,
                "confidence_midpoint_pct": midpoint,
                "calibration_gap_pct": gap,
                "calibration_state": _confidence_calibration_state(gap, summary["tested"]),
                **summary,
            }
        )
    return result


def _judgment_type_scorecard(scores: list[JudgmentScore]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[JudgmentScore]] = defaultdict(list)
    for score in scores:
        grouped[score.judgment_type].append(score)
    return {judgment_type: _outcome_summary(items) for judgment_type, items in sorted(grouped.items())}


def build_briefing_calibration_summary(scores: list[JudgmentScore]) -> dict[str, Any]:
    by_stance: dict[str, list[JudgmentScore]] = defaultdict(list)
    by_bucket: dict[str, list[JudgmentScore]] = defaultdict(list)
    conflicted = []
    for score in scores:
        stance = str(score.claim.get("stance") or "unknown")
        by_stance[stance].append(score)
        composite = _optional_float(score.claim.get("composite_score"))
        if composite is None:
            bucket = "unknown"
        elif composite < 55:
            bucket = "0-54"
        elif composite < 70:
            bucket = "55-69"
        elif composite < 85:
            bucket = "70-84"
        else:
            bucket = "85-100"
        by_bucket[bucket].append(score)
        if stance == "conflicted":
            conflicted.append(score)
    return {
        "total": len(scores),
        "summary": _outcome_summary(scores),
        "by_stance": {stance: _outcome_summary(items) for stance, items in sorted(by_stance.items())},
        "by_score_bucket": {bucket: _outcome_summary(items) for bucket, items in sorted(by_bucket.items())},
        "conflicted_honesty": _outcome_summary(conflicted),
        "sample_warning": "브리핑 스탠스는 이후 가격 경로로 채점합니다. N<10 구간은 결론을 보류합니다.",
    }


def _level_quality(scores: list[JudgmentScore]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {"invalidation": [], "take_profit": []}
    for judgment_type in result:
        typed = [score for score in scores if score.judgment_type == judgment_type and score.claim.get("level_score") is not None]
        buckets: dict[str, list[JudgmentScore]] = defaultdict(list)
        for score in typed:
            level_score = _optional_float(score.claim.get("level_score"))
            if level_score is None:
                continue
            if level_score < 40:
                label = "0-39"
            elif level_score <= 55:
                label = "40-55"
            elif level_score <= 70:
                label = "56-70"
            else:
                label = "71-100"
            buckets[label].append(score)
        result[judgment_type] = [{"bucket": label, **_outcome_summary(items)} for label, items in sorted(buckets.items())]
    return result


def _score_context_counts(scores: list[JudgmentScore]) -> dict[str, int]:
    counts = Counter(str(score.metrics.get("score_context") or "closed_trade") for score in scores)
    return {
        "closed_trade": counts["closed_trade"],
        "interim": counts["interim"],
        "total": len(scores),
    }


def _suggestion_status_counts(
    suggestions: list[CalibrationSuggestion],
) -> dict[str, int]:
    counts = Counter(suggestion.status for suggestion in suggestions)
    return {
        "pending": counts["pending"],
        "scheduled": counts["scheduled"],
        "experiment": counts["experiment"],
        "adopted": counts["adopted"],
        "approved": counts["approved"],
        "rejected": counts["rejected"],
        "vetoed": counts["vetoed"],
        "discarded": counts["discarded"],
        "dwell_blocked": counts["dwell_blocked"],
        "rolled_back": counts["rolled_back"],
        "total": len(suggestions),
    }


def _confidence_calibration_suggestions(
    scores: list[JudgmentScore],
) -> list[CalibrationSuggestion]:
    suggestions: list[CalibrationSuggestion] = []
    config_by_type = {
        "wyckoff_event": "wyckoff_event_min_confidence",
        "harmonic_prz": "harmonic_min_confidence",
    }
    for judgment_type, parameter in config_by_type.items():
        typed_scores = [score for score in scores if score.judgment_type == judgment_type and score.confidence is not None and score.outcome != "untested"]
        buckets: dict[int, list[JudgmentScore]] = defaultdict(list)
        for score in typed_scores:
            buckets[_confidence_bucket_start(int(score.confidence or 0))].append(score)
        for start, bucket_scores in sorted(buckets.items()):
            if len(bucket_scores) < SUGGESTION_SAMPLE_FLOOR:
                continue
            correct = len([score for score in bucket_scores if score.outcome == "correct"])
            accuracy = correct / len(bucket_scores) * 100
            midpoint = min(100, start + CONFIDENCE_BUCKET_SIZE / 2)
            if accuracy > midpoint - 20:
                continue
            proposed_floor = min(90, start + CONFIDENCE_BUCKET_SIZE)
            suggestion_id = uuid5(NAMESPACE_URL, f"fce:calibration:{parameter}:{start}:{proposed_floor}")
            suggestions.append(
                CalibrationSuggestion(
                    id=suggestion_id,
                    suggestion_type="confidence_floor_review",
                    title=f"{_judgment_type_label(judgment_type)} 신뢰도 하한 상향 검토",
                    rationale=(
                        f"{judgment_type} 신뢰도 {start}~{min(100, start + 9)} 구간의 실제 적중률이 "
                        f"{accuracy:.1f}% (N={len(bucket_scores)})로 표시 신뢰도보다 낮습니다."
                    ),
                    proposed_change={
                        "parameter": parameter,
                        "from": start,
                        "to": proposed_floor,
                    },
                    sample_size=len(bucket_scores),
                    oos_validation=_oos_validation(
                        bucket_scores,
                        _correct_rate_pct,
                        lambda rate, threshold=midpoint - 20: rate < threshold,
                    ),
                )
            )
    return suggestions


def _trigger_near_suggestions(
    scores: list[JudgmentScore],
) -> list[CalibrationSuggestion]:
    trigger_scores = [
        score
        for score in scores
        if score.judgment_type == "alert_fired"
        and score.outcome in {"wrong", "whipsaw", "correct"}
        and "trigger_near" in str(score.claim.get("rule_id") or score.claim.get("state_key") or "")
    ]
    if len(trigger_scores) < SUGGESTION_SAMPLE_FLOOR:
        return []
    noisy = len([score for score in trigger_scores if score.outcome in {"wrong", "whipsaw"}])
    noisy_rate = noisy / len(trigger_scores)
    if noisy_rate < 0.55:
        return []
    suggestion_id = uuid5(NAMESPACE_URL, "fce:calibration:alert_trigger_near_pct:1.0")
    return [
        CalibrationSuggestion(
            id=suggestion_id,
            suggestion_type="trigger_near_distance",
            title="트리거 근접 알림 거리 축소 검토",
            rationale=f"trigger_near 알림의 오판/휩쏘 비율이 {noisy_rate * 100:.1f}% (N={len(trigger_scores)})입니다.",
            proposed_change={
                "parameter": "alert_trigger_near_pct",
                "from": 1.5,
                "to": 1.0,
            },
            sample_size=len(trigger_scores),
            oos_validation=_oos_validation(trigger_scores, _noisy_rate_pct, lambda rate: rate >= 55.0),
        )
    ]


def _harmonic_tolerance_suggestions(
    scores: list[JudgmentScore],
) -> list[CalibrationSuggestion]:
    harmonic_scores = [score for score in scores if score.judgment_type == "harmonic_prz" and score.outcome != "untested"]
    if len(harmonic_scores) < SUGGESTION_SAMPLE_FLOOR:
        return []
    correct = len([score for score in harmonic_scores if score.outcome == "correct"])
    accuracy = correct / len(harmonic_scores)
    if accuracy >= 0.45:
        return []
    suggestion_id = uuid5(NAMESPACE_URL, "fce:calibration:harmonic_ratio_tolerance_multiplier:0.85")
    return [
        CalibrationSuggestion(
            id=suggestion_id,
            suggestion_type="harmonic_tolerance_review",
            title="하모닉 비율 허용 오차 축소 검토",
            rationale=f"하모닉 PRZ 판단의 실제 적중률이 {accuracy * 100:.1f}% (N={len(harmonic_scores)})로 낮습니다.",
            proposed_change={
                "parameter": "harmonic_ratio_tolerance_multiplier",
                "from": 1.0,
                "to": 0.85,
            },
            sample_size=len(harmonic_scores),
            oos_validation=_oos_validation(harmonic_scores, _correct_rate_pct, lambda rate: rate < 45.0),
        )
    ]


def _weekly_highlights(scores: list[JudgmentScore], totals: dict[str, Any]) -> list[str]:
    tested = int(totals.get("tested") or 0)
    if tested < SAMPLE_FLOOR:
        return [f"최근 7일 검증 표본이 N={tested}개라 성능 결론은 보류합니다."]
    accuracy = totals.get("accuracy_pct")
    highlights = [
        f"최근 7일 검증 판단 N={tested}, 전체 적중률 {accuracy:.1f}%입니다."
        if isinstance(accuracy, (int, float))
        else f"최근 7일 검증 판단 N={tested}개입니다."
    ]
    by_type = _judgment_type_scorecard(scores)
    weak_types = [
        (judgment_type, data)
        for judgment_type, data in by_type.items()
        if data.get("sample_state") == "ok" and isinstance(data.get("accuracy_pct"), (int, float)) and float(data["accuracy_pct"]) < 50
    ]
    if weak_types:
        judgment_type, data = sorted(weak_types, key=lambda item: float(item[1]["accuracy_pct"]))[0]
        highlights.append(f"{_judgment_type_label(judgment_type)} 구간 적중률이 {data['accuracy_pct']:.1f}%로 가장 약합니다.")
    return highlights


def _representative_judgment(scores: list[JudgmentScore], outcome: str) -> dict[str, Any] | None:
    candidates = [score for score in scores if score.outcome == outcome]
    if not candidates:
        return None
    selected = sorted(
        candidates,
        key=lambda score: (score.confidence or 0, score.created_at),
        reverse=True,
    )[0]
    return {
        "judgment_type": selected.judgment_type,
        "outcome": selected.outcome,
        "confidence": selected.confidence,
        "detail": selected.detail,
        "claim": selected.claim,
        "created_at": selected.created_at.isoformat(),
    }


def _accuracy_conclusion(accuracy: float | None, tested: int) -> str:
    if tested < SAMPLE_FLOOR:
        return "표본 부족"
    if accuracy is None:
        return "미검증"
    if accuracy >= 70:
        return "유효"
    if accuracy >= 50:
        return "관찰 필요"
    return "개선 필요"


def _confidence_calibration_state(gap: float | None, tested: int) -> str:
    if tested < SAMPLE_FLOOR:
        return "insufficient_sample"
    if gap is None:
        return "untested"
    if gap <= -20:
        return "overconfident"
    if gap >= 20:
        return "underconfident"
    return "aligned"


def _confidence_bucket_start(confidence: int | float) -> int:
    start = int(float(confidence) // CONFIDENCE_BUCKET_SIZE * CONFIDENCE_BUCKET_SIZE)
    return min(max(start, 0), 90)


def _judgment_type_label(judgment_type: str) -> str:
    labels = {
        "invalidation": "무효화",
        "take_profit": "익절",
        "wyckoff_event": "와이코프",
        "liquidity_sweep": "유동성 스윕",
        "harmonic_prz": "하모닉 PRZ",
        "analyst_briefing": "브리핑 스탠스",
        "alert_fired": "알림",
        "scout_setup": "진입 전 셋업",
    }
    return labels.get(judgment_type, judgment_type)


def _review_notes(trade: Trade, scores: list[JudgmentScore], tested: int, accuracy: float | None) -> list[str]:
    notes = []
    if not scores:
        notes.append("보유 중 생성된 판단 원장이 없어 종료 복기는 결과와 사용자의 청산 사유 중심으로만 생성됐습니다.")
    elif tested == 0:
        notes.append("저장된 판단은 있으나 종료 전 가격 경로에서 테스트되지 않았습니다.")
    elif accuracy is not None and accuracy < 50:
        notes.append("이번 거래에서는 엔진 판단의 적중률이 낮아 무효화/익절 기준을 보수적으로 재검토해야 합니다.")
    else:
        notes.append("이번 거래에서는 일부 엔진 판단이 실제 가격 경로와 대조되어 채점됐습니다.")
    if trade.memo:
        notes.append("사용자 메모가 있어 정량 채점과 주관적 복기를 함께 확인할 수 있습니다.")
    return notes


def _review_prompt(review_json: str) -> str:
    return f"""
너는 FOMO Control Engine의 거래 종료 복기 분석가다.

입력 JSON에는 trade 결과와 review_v2 판단 채점표만 들어 있다.
너는 JSON의 숫자를 새로 계산하지 않고, 저장된 숫자와 채점 결과를 사용해 한국어 복기 문장을 작성한다.

원칙:
1. JSON에 없는 가격, 점수, 퍼센트, 시간, 표본 수를 만들지 않는다.
2. 숫자는 입력 JSON에 있는 값만 그대로 인용한다.
3. 채점 결과가 untested면 실패로 단정하지 말고 "미검증"이라고 표현한다.
4. "단정하지 않습니다", "투자 조언이 아닙니다", "확정적으로" 같은 헤지 상용구를 쓰지 않는다.
5. 출력은 아래 섹션만 사용하고, 번호 목록을 쓰지 않는다.

입력 JSON:
{review_json}

출력 형식:

📋 Trade Review v2: {{symbol}}

결과:
{{거래 결과와 청산 사유 요약}}

판단 채점:
{{무효화/익절/와이코프/PRZ 판단 중 맞은 것, 틀린 것, 미검증}}

잘한 점:
{{데이터 기준으로 유지할 행동}}

고칠 점:
{{다음 거래에서 바꿀 기준}}
""".strip()


def _contains_review_hedge(value: str) -> bool:
    patterns = ["단정하지 않습니다", "확정적으로", "투자 조언이 아닙니다"]
    return any(pattern in value for pattern in patterns)


def _score_issue_lines(scores: list[dict[str, Any]]) -> list[str]:
    lines = []
    for score in scores:
        if score.get("outcome") in {"wrong", "whipsaw"}:
            label = "오판" if score.get("outcome") == "wrong" else "휩쏘"
            lines.append(f"{label}: {score.get('judgment_type')} - {score.get('detail')}")
    return lines[:3]


def _next_improvement_text(total: int, tested: int, accuracy: Any, issue_lines: list[str]) -> str:
    if issue_lines:
        return " / ".join(issue_lines)
    if total == 0:
        return "다음 거래부터 보유 중 인사이트 또는 액션 플랜을 저장해 무효화/익절 판단을 채점 가능한 형태로 남기세요."
    if tested == 0:
        return "판단은 저장됐지만 실제 가격 경로에서 테스트되지 않았습니다. 표본이 쌓일 때까지 결론을 보류하세요."
    if isinstance(accuracy, (int, float)) and accuracy >= 70:
        return "이번 거래에서 검증된 판단은 대체로 유효했습니다. 같은 조건의 표본을 더 쌓아 캘리브레이션하세요."
    return "무효화/익절 기준의 근거 점수와 실제 도달 후 반응을 비교해 기준을 조정하세요."


def _matched_level_score(chart_analysis: dict[str, Any], price: float, kind: str) -> int | None:
    price_levels = chart_analysis.get("price_levels", {})
    candidates = []
    for group in (price_levels.get(kind, []), price_levels.get("invalidation", [])):
        if isinstance(group, list):
            candidates.extend(item for item in group if isinstance(item, dict))
    tolerance = max(abs(price) * 0.00001, 1e-8)
    for level in candidates:
        level_price = _optional_float(level.get("price"))
        if level_price is not None and abs(level_price - price) <= tolerance:
            return _optional_int(level.get("score"))
    return None


def _confidence_from_basis(value: Any) -> int | None:
    if not isinstance(value, str) or "신뢰도" not in value:
        return None
    parts = value.split("신뢰도", 1)[1].strip().split()
    if not parts:
        return None
    return _optional_int(parts[0])


def _wyckoff_expected_move(marker: dict[str, Any]) -> str | None:
    side = marker.get("side")
    event_type = str(marker.get("type") or "").lower()
    if side == "accumulation" or event_type in {"spring", "sos", "lps", "test", "sc"}:
        return "up"
    if side == "distribution" or event_type in {"utad", "sow", "lpsy", "bc"}:
        return "down"
    return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
