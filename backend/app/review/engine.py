from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
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
from app.positions.insight import call_openai_insight, validate_llm_numbers

PRICE_TOLERANCE = 0.005


def build_judgment_entries(
    position: Position,
    snapshot: PositionSnapshot,
    action_plan: dict[str, Any],
    chart_analysis: dict[str, Any],
    *,
    source_type: str,
    source_id: str | None = None,
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
            "level_score": _matched_level_score(chart_analysis, price, "support" if direction == "long" else "resistance"),
        }
        entries.append(_ledger_entry(position, as_of, "invalidation", claim, None, source_type, source_id))

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
            "level_score": _matched_level_score(chart_analysis, price, "resistance" if direction == "long" else "support"),
        }
        entries.append(_ledger_entry(position, as_of, "take_profit", claim, _confidence_from_basis(target.get("basis")), source_type, source_id))

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
        entries.append(_ledger_entry(position, as_of, "wyckoff_event", claim, confidence, "wyckoff", str(marker.get("id") or source_id or snapshot.id)))

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
        entries.append(_ledger_entry(position, as_of, "harmonic_prz", claim, _optional_int(zone.get("confidence")), "harmonic", str(zone.get("pattern_id") or source_id or snapshot.id)))

    return entries


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


def build_review_v2(trade: Trade, judgments: list[JudgmentLedgerEntry], scores: list[JudgmentScore]) -> dict[str, Any]:
    outcome_counts = Counter(score.outcome for score in scores)
    by_type: dict[str, dict[str, Any]] = {}
    for score in scores:
        bucket = by_type.setdefault(score.judgment_type, {"total": 0, "correct": 0, "wrong": 0, "whipsaw": 0, "untested": 0})
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
            f"판단 원장:\n채점 가능한 판단 {total}개 중 {tested}개가 실제 가격 경로로 검증됐고, "
            f"적중률은 {accuracy:.1f}%입니다." if isinstance(accuracy, (int, float)) else f"판단 원장:\n채점 가능한 판단 {total}개 중 {tested}개가 검증됐습니다."
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


def build_calibration_summary(scores: list[JudgmentScore], suggestions: list[CalibrationSuggestion]) -> dict[str, Any]:
    invalidation = [score for score in scores if score.judgment_type == "invalidation"]
    take_profit = [score for score in scores if score.judgment_type == "take_profit"]
    wyckoff = [score for score in scores if score.judgment_type == "wyckoff_event"]
    return {
        "generated_at": utc_now(),
        "totals": _outcome_summary(scores),
        "invalidation": _outcome_summary(invalidation),
        "take_profit": {
            **_outcome_summary(take_profit),
            "reach_rate_pct": _tested_rate(take_profit),
        },
        "wyckoff_confidence": _confidence_buckets(wyckoff),
        "suggestions": [suggestion.model_dump(mode="json") for suggestion in suggestions],
        "sample_warning": "표본 N < 10 구간은 표본 부족으로 결론을 내리지 않습니다.",
    }


def generate_calibration_suggestions(scores: list[JudgmentScore]) -> list[CalibrationSuggestion]:
    invalidation_bucket = [
        score
        for score in scores
        if score.judgment_type == "invalidation"
        and score.outcome != "untested"
        and 40 <= float(score.claim.get("level_score") or -1) <= 55
    ]
    suggestions: list[CalibrationSuggestion] = []
    if len(invalidation_bucket) >= 10:
        correct = len([score for score in invalidation_bucket if score.outcome == "correct"])
        correct_rate = correct / len(invalidation_bucket)
        if correct_rate < 0.4:
            suggestion_id = uuid5(NAMESPACE_URL, "fce:calibration:min_invalidation_level_score:55")
            suggestions.append(
                CalibrationSuggestion(
                    id=suggestion_id,
                    suggestion_type="min_invalidation_level_score",
                    title="무효화 근거 최소 레벨 점수 상향 제안",
                    rationale=(
                        f"레벨 score 40~55 구간 무효화 판단의 적중률이 {correct_rate * 100:.1f}%"
                        f" (N={len(invalidation_bucket)})로 낮습니다."
                    ),
                    proposed_change={"parameter": "min_invalidation_level_score", "from": 40, "to": 55},
                    sample_size=len(invalidation_bucket),
                )
            )
    return suggestions


def _ledger_entry(
    position: Position,
    as_of: datetime,
    judgment_type: str,
    claim: dict[str, Any],
    confidence: int | None,
    source_type: str,
    source_id: str | None,
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
    )


def _judgment_key(position: Position, as_of: datetime, judgment_type: str, claim: dict[str, Any], source_type: str, source_id: str | None) -> str:
    price_part = claim.get("price") or claim.get("mid") or f"{claim.get('low')}:{claim.get('high')}"
    condition = claim.get("condition")
    return f"fce:judgment:{position.id}:{as_of.isoformat()}:{source_type}:{source_id or 'none'}:{judgment_type}:{condition}:{price_part}"


def _score_one_judgment(trade: Trade, judgment: JudgmentLedgerEntry, price_path: list[dict[str, Any]]) -> JudgmentScore:
    path = [point for point in price_path if point["time"] > judgment.as_of]
    if judgment.type == "invalidation":
        outcome, detail, metrics = _score_invalidation(judgment, path)
    elif judgment.type == "take_profit":
        outcome, detail, metrics = _score_take_profit(judgment, path)
    elif judgment.type == "wyckoff_event":
        outcome, detail, metrics = _score_directional_event(judgment, path)
    elif judgment.type == "harmonic_prz":
        outcome, detail, metrics = _score_harmonic_prz(judgment, path)
    else:
        outcome, detail, metrics = "untested", "지원하지 않는 판단 유형입니다.", {"path_points": len(path)}
    score_id = uuid5(NAMESPACE_URL, f"fce:judgment-score:{trade.id}:{judgment.judgment_id}")
    metrics.update(
        {
            "judgment_as_of": judgment.as_of.isoformat(),
            "trade_created_at": trade.created_at.isoformat(),
            "path_points": len(path),
            "confidence": judgment.confidence,
            "level_score": judgment.claim.get("level_score"),
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
    )


def _score_invalidation(judgment: JudgmentLedgerEntry, path: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    price = _optional_float(judgment.claim.get("price"))
    condition = judgment.claim.get("condition")
    if price is None or not path:
        return "untested", "무효화 가격 이후 검증할 가격 경로가 부족합니다.", {}
    final = path[-1]["price"]
    breached = _first_breach(path, price, condition)
    if breached is None:
        return "untested", "보유 종료 전 무효화 가격을 이탈/돌파하지 않았습니다.", {"final_price": final, "level": price}
    if condition == "break_below":
        if final <= price * (1 - PRICE_TOLERANCE):
            return "correct", "무효화 이탈 후 가격이 회복하지 못해 기준이 유효했습니다.", {"breach_at": breached["time"].isoformat(), "final_price": final, "level": price}
        return "whipsaw", "무효화 이탈 후 다시 회복해 기준이 민감했을 가능성이 있습니다.", {"breach_at": breached["time"].isoformat(), "final_price": final, "level": price}
    if final >= price * (1 + PRICE_TOLERANCE):
        return "correct", "무효화 돌파 후 가격이 되돌리지 않아 기준이 유효했습니다.", {"breach_at": breached["time"].isoformat(), "final_price": final, "level": price}
    return "whipsaw", "무효화 돌파 후 다시 하락해 기준이 민감했을 가능성이 있습니다.", {"breach_at": breached["time"].isoformat(), "final_price": final, "level": price}


def _score_take_profit(judgment: JudgmentLedgerEntry, path: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    price = _optional_float(judgment.claim.get("price"))
    condition = judgment.claim.get("condition")
    if price is None or not path:
        return "untested", "익절 후보 이후 검증할 가격 경로가 부족합니다.", {}
    final = path[-1]["price"]
    reached = _first_breach(path, price, condition)
    if reached is None:
        return "untested", "보유 종료 전 익절 후보에 도달하지 않았습니다.", {"final_price": final, "target": price}
    if condition == "touch_or_break_above":
        if final < price * (1 - PRICE_TOLERANCE):
            return "correct", "익절 후보 도달 후 되돌림이 발생해 목표가가 유효했습니다.", {"reached_at": reached["time"].isoformat(), "final_price": final, "target": price}
        if final > price * 1.01:
            return "wrong", "익절 후보를 강하게 돌파해 기준이 보수적이었습니다.", {"reached_at": reached["time"].isoformat(), "final_price": final, "target": price}
    else:
        if final > price * (1 + PRICE_TOLERANCE):
            return "correct", "익절 후보 도달 후 반등이 발생해 목표가가 유효했습니다.", {"reached_at": reached["time"].isoformat(), "final_price": final, "target": price}
        if final < price * 0.99:
            return "wrong", "익절 후보를 강하게 이탈해 기준이 보수적이었습니다.", {"reached_at": reached["time"].isoformat(), "final_price": final, "target": price}
    return "correct", "익절 후보 도달 후 종료 가격이 목표 근처에 머물러 기준이 유효했습니다.", {"reached_at": reached["time"].isoformat(), "final_price": final, "target": price}


def _score_directional_event(judgment: JudgmentLedgerEntry, path: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    base = _optional_float(judgment.claim.get("price"))
    expected = judgment.claim.get("expected_move")
    if base is None or not path or expected not in {"up", "down"}:
        return "untested", "이벤트 이후 검증할 가격 경로가 부족합니다.", {}
    final = path[-1]["price"]
    if expected == "up":
        if final >= base * (1 + PRICE_TOLERANCE):
            return "correct", "이벤트 이후 가격이 상승 방향으로 전개됐습니다.", {"base_price": base, "final_price": final}
        if final <= base * (1 - PRICE_TOLERANCE):
            return "wrong", "이벤트 이후 가격이 기대 방향과 반대로 전개됐습니다.", {"base_price": base, "final_price": final}
    else:
        if final <= base * (1 - PRICE_TOLERANCE):
            return "correct", "이벤트 이후 가격이 하락 방향으로 전개됐습니다.", {"base_price": base, "final_price": final}
        if final >= base * (1 + PRICE_TOLERANCE):
            return "wrong", "이벤트 이후 가격이 기대 방향과 반대로 전개됐습니다.", {"base_price": base, "final_price": final}
    return "untested", "이벤트 이후 가격 변화가 판정 임계값에 미달했습니다.", {"base_price": base, "final_price": final}


def _score_harmonic_prz(judgment: JudgmentLedgerEntry, path: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    low = _optional_float(judgment.claim.get("low"))
    high = _optional_float(judgment.claim.get("high"))
    mid = _optional_float(judgment.claim.get("mid"))
    expected = judgment.claim.get("expected_move")
    if low is None or high is None or mid is None or not path or expected not in {"up", "down"}:
        return "untested", "PRZ 이후 검증할 가격 경로가 부족합니다.", {}
    touched = next((point for point in path if low <= point["price"] <= high or (expected == "down" and point["price"] > high) or (expected == "up" and point["price"] < low)), None)
    if touched is None:
        return "untested", "보유 종료 전 PRZ에 도달하지 않았습니다.", {"prz_low": low, "prz_high": high}
    final = path[-1]["price"]
    if expected == "down":
        if final < mid:
            return "correct", "PRZ 도달 후 하락 반전이 확인됐습니다.", {"touched_at": touched["time"].isoformat(), "final_price": final, "prz_mid": mid}
        if final > high * (1 + PRICE_TOLERANCE):
            return "wrong", "PRZ를 상향 돌파해 반전 구간이 실패했습니다.", {"touched_at": touched["time"].isoformat(), "final_price": final, "prz_high": high}
    else:
        if final > mid:
            return "correct", "PRZ 도달 후 상승 반전이 확인됐습니다.", {"touched_at": touched["time"].isoformat(), "final_price": final, "prz_mid": mid}
        if final < low * (1 - PRICE_TOLERANCE):
            return "wrong", "PRZ를 하향 이탈해 반전 구간이 실패했습니다.", {"touched_at": touched["time"].isoformat(), "final_price": final, "prz_low": low}
    return "whipsaw", "PRZ 도달 후 방향성이 충분히 확정되지 않았습니다.", {"touched_at": touched["time"].isoformat(), "final_price": final, "prz_mid": mid}


def _price_path(trade: Trade, snapshots: list[PositionSnapshot], monitoring_logs: list[MonitoringLog]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for snapshot in snapshots:
        if snapshot.mark_price is not None:
            points.append({"time": _aware(snapshot.as_of), "price": float(snapshot.mark_price), "source": "snapshot"})
    for log in monitoring_logs:
        points.append({"time": _aware(log.created_at), "price": float(log.current_price), "source": "monitoring_log"})
    points.append({"time": _aware(trade.created_at), "price": float(trade.exit_price), "source": "exit"})
    deduped: dict[tuple[datetime, float], dict[str, Any]] = {}
    for point in points:
        deduped[(point["time"], point["price"])] = point
    return sorted(deduped.values(), key=lambda item: item["time"])


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
    return {
        "total": len(scores),
        "tested": tested,
        "correct": correct,
        "wrong": counts["wrong"],
        "whipsaw": counts["whipsaw"],
        "untested": counts["untested"],
        "accuracy_pct": round(correct / tested * 100, 1) if tested else None,
        "sample_state": "ok" if len(scores) >= 10 else "insufficient_sample",
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
        start = int(score.confidence // 10 * 10)
        start = min(max(start, 0), 90)
        buckets[f"{start}-{start + 10}"].append(score)
    result = []
    for label in sorted(buckets.keys()):
        summary = _outcome_summary(buckets[label])
        result.append({"bucket": label, **summary})
    return result


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
