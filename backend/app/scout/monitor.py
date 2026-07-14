from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any
from uuid import UUID, NAMESPACE_URL, uuid5

from app.backtest.statistics import format_stat_line
from app.core.config import Settings
from app.db.models import (
    ArmedSetup,
    Direction,
    EntryIntent,
    JudgmentLedgerEntry,
    JudgmentScore,
    ScoutSnapshot,
    utc_now,
)
from app.notify.rules import AlertCandidate
from app.review.params import engine_param_snapshot

SCOUT_SENTINEL_POSITION_ID = UUID(int=0)


def setup_candidates_from_analysis(symbol: str, timeframe: str, analysis: dict[str, Any], settings: Settings) -> list[dict[str, Any]]:
    mark = _float(analysis.get("mark_price"))
    if mark is None:
        return []
    candidates: list[dict[str, Any]] = []
    candidates.extend(_harmonic_candidates(symbol, timeframe, analysis, mark, settings))
    candidates.extend(_level_candidates(symbol, timeframe, analysis, mark, settings))
    candidates.extend(_wyckoff_candidates(symbol, timeframe, analysis, mark, settings))
    candidates.extend(_crowding_level_candidates(symbol, timeframe, analysis, mark, settings))
    return sorted(
        candidates,
        key=lambda item: (
            abs(float(item.get("distance_pct") or 999)),
            -(int(item.get("confidence") or 0)),
        ),
    )[:8]


def process_scout_scan(repo: Any, settings: Settings, payload: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in payload.get("rows", []) if isinstance(row, dict)]
    snapshots: list[ScoutSnapshot] = []
    armed: list[ArmedSetup] = []
    entry_intents: list[EntryIntent] = []
    candidates: list[AlertCandidate] = []
    warnings: list[str] = []
    if len(rows) > settings.scout_watchlist_symbol_limit:
        warnings.append(f"관심종목 {len(rows)}개가 상한 {settings.scout_watchlist_symbol_limit}개를 초과했습니다. 워커는 라운드로빈 스캔이 필요합니다.")
    for row in rows[: settings.scout_watchlist_symbol_limit]:
        if row.get("error"):
            continue
        snapshot = _snapshot_from_row(row)
        repo.add_scout_snapshot(snapshot)
        snapshots.append(snapshot)
        setups = arm_auto_setups(repo, settings, row, snapshot) if settings.scout_auto_arm_enabled else []
        armed.extend(setups)
        candidates.extend(
            evaluate_setup_alerts(
                repo,
                settings,
                setups or repo.list_armed_setups(symbol=row.get("symbol"), status="armed", limit=20),
                row,
            )
        )
        intent_result = evaluate_entry_intents(repo, settings, row, snapshot)
        entry_intents.extend(intent_result["intents"])
        candidates.extend(intent_result["candidates"])
        stance_candidate = _tracked_stance_candidate(row, settings)
        if stance_candidate is not None:
            candidates.append(stance_candidate)
        flow_candidate = _tracked_flow_candidate(repo, row, settings)
        if flow_candidate is not None:
            candidates.append(flow_candidate)
        alignment_candidate = _full_alignment_candidate(repo, row, settings)
        if alignment_candidate is not None:
            candidates.append(alignment_candidate)
    scores = score_scout_setups(repo, settings)
    intent_scores = score_entry_intents(repo, settings)
    return {
        **payload,
        "snapshots": [item.model_dump(mode="json") for item in snapshots],
        "armed_setups": [item.model_dump(mode="json") for item in armed],
        "entry_intents": [item.model_dump(mode="json") for item in repo.list_entry_intents(limit=300)],
        "alert_candidates": [candidate.payload for candidate in candidates],
        "_alert_candidate_objects": candidates,
        "rate_budget": scout_rate_budget(settings, len(rows)),
        "warnings": warnings,
        "scout_scores": scores,
        "entry_intent_scores": intent_scores,
    }


def arm_auto_setups(repo: Any, settings: Settings, row: dict[str, Any], snapshot: ScoutSnapshot) -> list[ArmedSetup]:
    candidates = [candidate for candidate in row.get("setup_candidates", []) if isinstance(candidate, dict)]
    if not candidates:
        return []
    symbol = str(row.get("symbol") or "").upper()
    timeframe = str(row.get("timeframe") or "4h")
    active = [setup for setup in repo.list_armed_setups(symbol=symbol, status="armed", limit=50) if setup.source == "auto"]
    auto_symbols = {setup.symbol.upper() for setup in repo.list_armed_setups(status="armed", limit=1000) if setup.source == "auto"}
    by_id = {setup.id: setup for setup in active}
    saved: list[ArmedSetup] = []
    for candidate in candidates:
        if len([setup for setup in repo.list_armed_setups(symbol=symbol, status="armed", limit=50)]) >= settings.scout_max_armed_setups_per_symbol:
            break
        if symbol not in auto_symbols and len(auto_symbols) >= settings.scout_auto_arm_symbol_limit:
            break
        setup_id = _setup_id(symbol, timeframe, candidate)
        existing = by_id.get(setup_id) or repo.get_armed_setup(setup_id)
        if existing and existing.status in {"triggered", "invalidated", "disarmed"}:
            continue
        preview = dict(candidate.get("preview")) if isinstance(candidate.get("preview"), dict) else {}
        one_liners = _row_one_liners(row)
        if one_liners:
            preview.setdefault("scout_overall_stance", one_liners.get("overall_stance"))
            preview.setdefault("scout_counts_summary", one_liners.get("summary"))
        now = utc_now()
        setup = ArmedSetup(
            id=setup_id,
            symbol=symbol,
            timeframe=timeframe,
            source="auto",
            setup_type=str(candidate.get("setup_type") or "setup"),
            direction=_direction(candidate.get("direction")),
            trigger_price=_float(candidate.get("trigger_price")),
            trigger_label=str(candidate.get("trigger_label") or "셋업"),
            trigger_condition=str(candidate.get("trigger_condition") or "조건 확인"),
            distance_pct=_float(candidate.get("distance_pct")),
            confidence=_int(candidate.get("confidence")),
            basis=str(candidate.get("basis") or ""),
            status="armed",
            preview=preview,
            snapshot_id=snapshot.id,
            judgment_id=f"scout_setup:{setup_id}",
            setup_near_alerted_at=existing.setup_near_alerted_at if existing else None,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            last_seen_at=now,
        )
        repo.upsert_armed_setup(setup)
        _record_setup_judgment(repo, setup, snapshot)
        saved.append(setup)
    return saved


def arm_manual_setup(
    repo: Any,
    symbol: str,
    timeframe: str,
    trigger_price: float,
    label: str,
    condition: str,
    direction: str | None = None,
) -> ArmedSetup:
    now = utc_now()
    setup = ArmedSetup(
        id=uuid5(
            NAMESPACE_URL,
            f"fce:manual-setup:{symbol.upper()}:{timeframe}:{round(trigger_price, 8)}:{label}",
        ),
        symbol=symbol.upper(),
        timeframe=timeframe,
        source="manual",
        setup_type="manual_price",
        direction=_direction(direction),
        trigger_price=trigger_price,
        trigger_label=label,
        trigger_condition=condition,
        basis="사용자 수동 무장 조건",
        status="armed",
        judgment_id=f"scout_setup:manual:{symbol.upper()}:{round(trigger_price, 8)}",
        created_at=now,
        updated_at=now,
        last_seen_at=now,
    )
    repo.upsert_armed_setup(setup)
    return setup


def disarm_setup(repo: Any, setup_id: UUID) -> ArmedSetup | None:
    setup = repo.get_armed_setup(setup_id)
    if setup is None:
        return None
    updated = setup.model_copy(update={"status": "disarmed", "updated_at": utc_now()})
    repo.upsert_armed_setup(updated)
    return updated


def active_tracking_symbols(repo: Any) -> set[str]:
    symbols = {item.symbol.upper() for item in repo.list_entry_intents(status="active", limit=1000)}
    symbols.update(item.symbol.upper() for item in repo.list_armed_setups(status="armed", limit=1000))
    return symbols


def _tracked_stance_candidate(row: dict[str, Any], settings: Settings) -> AlertCandidate | None:
    if not row.get("tracked") or "stance_flipped" not in settings.alert_enabled_rule_set:
        return None
    confluence = row.get("confluence") if isinstance(row.get("confluence"), dict) else {}
    state = confluence.get("stance_state") if isinstance(confluence.get("stance_state"), dict) else {}
    if not state.get("flipped"):
        return None
    previous = str(state.get("previous_stance") or "")
    current = str(state.get("stance") or confluence.get("stance") or "")
    if previous == current or current not in {"long_leaning", "short_leaning"}:
        return None
    symbol = str(row.get("symbol") or "-").upper()
    label = {"long_leaning": "상방", "short_leaning": "하방", "conflicted": "충돌", "insufficient": "판단 유보"}
    top_key = "long_evidence" if current == "long_leaning" else "short_evidence"
    top = confluence.get(top_key) if isinstance(confluence.get(top_key), list) else []
    reason = str(top[0].get("claim") or "확정 캔들 근거 갱신") if top and isinstance(top[0], dict) else "확정 캔들 근거 갱신"
    return AlertCandidate(
        rule_id="stance_flipped",
        severity="warn",
        position_id=None,
        symbol=symbol,
        identity=f"tracked:{symbol}:{state.get('last_bar_at')}:{current}",
        title="추적 스탠스 반전",
        message=(
            f"🟡 추적 변화 · <b>{escape(symbol)}</b> {label.get(previous, previous)} → {label.get(current, current)}\n"
            f"사유: {escape(reason)}\n추적 중인 진입 전 심볼의 확정 캔들 판정 변화입니다."
        ),
        payload={"kind": "tracked_scout", "from": previous, "to": current, "bar_at": state.get("last_bar_at")},
    )


def _tracked_flow_candidate(repo: Any, row: dict[str, Any], settings: Settings) -> AlertCandidate | None:
    if not row.get("tracked") or "flow_divergence" not in settings.alert_enabled_rule_set:
        return None
    analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else row
    derivatives = analysis.get("derivatives") if isinstance(analysis.get("derivatives"), dict) else {}
    signals = derivatives.get("signals") if isinstance(derivatives.get("signals"), dict) else {}
    flow = signals.get("money_flow") if isinstance(signals.get("money_flow"), dict) else {}
    if flow.get("state") != "futures_led" or flow.get("provisional"):
        return None
    symbol = str(row.get("symbol") or "-").upper()
    snapshots = repo.list_scout_snapshots(symbol=symbol, limit=2)
    previous = snapshots[1] if len(snapshots) > 1 else None
    previous_analysis = previous.analysis if previous and isinstance(previous.analysis, dict) else {}
    previous_derivatives = previous_analysis.get("derivatives") if isinstance(previous_analysis.get("derivatives"), dict) else {}
    previous_signals = previous_derivatives.get("signals") if isinstance(previous_derivatives.get("signals"), dict) else {}
    previous_flow = previous_signals.get("money_flow") if isinstance(previous_signals.get("money_flow"), dict) else {}
    if previous_flow.get("state") == "futures_led":
        return None
    episode = str(flow.get("as_of") or row.get("as_of") or utc_now().isoformat())
    return AlertCandidate(
        rule_id="flow_divergence",
        severity="warn",
        position_id=None,
        symbol=symbol,
        identity=f"tracked:{symbol}:futures_led:{episode}",
        title="선물 단독 견인 경계",
        message=(
            f"🟠 추적 변화 · <b>{escape(symbol)}</b> 선물 단독 견인 경계\n"
            "가격 상승에 선물 매수와 OI 증가가 동반됐지만 현물 유입은 확인되지 않았습니다.\n"
            f"출처: {escape(str(flow.get('source_label') or 'Bitget 단일 거래소 프록시'))}"
        ),
        payload={"kind": "tracked_scout", "money_flow": flow},
    )


def _full_alignment_candidate(repo: Any, row: dict[str, Any], settings: Settings) -> AlertCandidate | None:
    if "full_alignment" not in settings.alert_enabled_rule_set:
        return None
    alignment = row.get("full_alignment") if isinstance(row.get("full_alignment"), dict) else {}
    if not alignment.get("unanimous") or not alignment.get("bar_at"):
        return None
    now = utc_now()
    today = now.date()
    fired_today = 0
    symbol = str(row.get("symbol") or "-").upper()
    cooldown_cutoff = now - timedelta(hours=max(1, int(settings.universe_symbol_cooldown_hours)))
    for alert in repo.list_alerts(limit=500):
        if alert.rule_id in {"full_alignment", "universe_discovery"} and alert.fired_at.date() == today:
            fired_today += 1
        if alert.rule_id in {"full_alignment", "universe_discovery"} and alert.symbol.upper() == symbol and alert.fired_at >= cooldown_cutoff:
            return None
    if fired_today >= max(0, int(settings.universe_daily_alert_limit)):
        return None
    direction = str(alignment.get("direction") or "")
    direction_label = "상방" if direction == "long" else "하방"
    agreeing = int(alignment.get("agreeing") or 0)
    total = agreeing + int(alignment.get("dissenting") or 0)
    sample = str(alignment.get("sample_label") or "표본 축적 중")
    return AlertCandidate(
        rule_id="full_alignment",
        severity="info",
        position_id=None,
        symbol=symbol,
        identity=f"full_alignment:{symbol}:{alignment.get('bar_at')}:{direction}",
        title="만장일치 정렬 발굴",
        message=(
            f"🎯 정렬 · <b>{escape(symbol)}</b> {direction_label} 만장일치 ({agreeing}/{total} · HTF 정렬)\n"
            f"{escape(sample)}\n→ 정렬 상태 발견 통보입니다. 진입 판단은 사용자 몫입니다."
        ),
        payload={
            "kind": "universe_discovery",
            "direction": direction,
            "agreeing": agreeing,
            "dissenting": int(alignment.get("dissenting") or 0),
            "score": alignment.get("score"),
            "bar_at": alignment.get("bar_at"),
            "sample_size": alignment.get("sample_size"),
        },
    )


def evaluate_setup_alerts(repo: Any, settings: Settings, setups: list[ArmedSetup], row: dict[str, Any]) -> list[AlertCandidate]:
    enabled = settings.alert_enabled_rule_set
    current = _float(row.get("mark_price"))
    if current is None:
        return []
    candidates: list[AlertCandidate] = []
    for setup in setups:
        if setup.status != "armed" or setup.trigger_price is None:
            continue
        distance = _distance_pct(current, setup.trigger_price)
        rearmed_setup = setup
        if setup.setup_near_alerted_at is not None and abs(distance) >= settings.scout_setup_rearm_pct:
            rearmed_setup = setup.model_copy(
                update={
                    "setup_near_alerted_at": None,
                    "updated_at": utc_now(),
                    "distance_pct": round(distance, 2),
                }
            )
            repo.upsert_armed_setup(rearmed_setup)
        if "setup_invalidated" in enabled and _setup_invalidated(rearmed_setup, current):
            invalidated = rearmed_setup.model_copy(
                update={
                    "status": "invalidated",
                    "invalidated_at": utc_now(),
                    "updated_at": utc_now(),
                    "distance_pct": round(distance, 2),
                }
            )
            repo.upsert_armed_setup(invalidated)
            candidates.append(_setup_candidate("setup_invalidated", "info", invalidated, current, distance))
            continue
        if "setup_triggered" in enabled and _setup_triggered(rearmed_setup, current):
            triggered = rearmed_setup.model_copy(
                update={
                    "status": "triggered",
                    "triggered_at": utc_now(),
                    "updated_at": utc_now(),
                    "distance_pct": round(distance, 2),
                }
            )
            repo.upsert_armed_setup(triggered)
            candidate = _gated_setup_candidate(settings, "setup_triggered", "action", triggered, current, distance)
            if candidate is not None:
                candidates.append(candidate)
            continue
        if "setup_near" in enabled and rearmed_setup.setup_near_alerted_at is None and abs(distance) <= settings.scout_setup_near_pct:
            updated = rearmed_setup.model_copy(
                update={
                    "setup_near_alerted_at": utc_now(),
                    "updated_at": utc_now(),
                    "distance_pct": round(distance, 2),
                }
            )
            repo.upsert_armed_setup(updated)
            candidate = _gated_setup_candidate(settings, "setup_near", "info", updated, current, distance)
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def evaluate_entry_intents(repo: Any, settings: Settings, row: dict[str, Any], snapshot: ScoutSnapshot) -> dict[str, Any]:
    symbol = str(row.get("symbol") or "").upper()
    current = _float(row.get("mark_price"))
    if not symbol or current is None:
        return {"intents": [], "candidates": []}
    backtest_summary = _row_backtest_summary(row)
    now = utc_now()
    candidates: list[AlertCandidate] = []
    updated_intents: list[EntryIntent] = []
    for intent in repo.list_entry_intents(symbol=symbol, status="active", limit=50):
        if intent.expires_at <= now:
            expired = intent.model_copy(update={"status": "expired", "updated_at": now, "last_seen_at": now})
            repo.upsert_entry_intent(expired)
            updated_intents.append(expired)
            distance = _zone_distance_pct(current, expired) if expired.kind == "zone" else 0.0
            candidates.append(
                _intent_candidate(
                    "intent_invalidated", "info", expired, current, distance, "추적 만료" if expired.kind == "watch" else "의도 만료", backtest_summary
                )
            )
            continue

        if intent.kind == "watch":
            refreshed = intent.model_copy(update={"updated_at": now, "last_seen_at": now})
            repo.upsert_entry_intent(refreshed)
            updated_intents.append(refreshed)
            continue

        condition_state = _intent_condition_state(intent, row)
        distance = _zone_distance_pct(current, intent)
        in_zone = bool(condition_state.get("price_in_zone", {}).get("met"))
        all_met = all(bool(condition_state.get(condition, {}).get("met")) for condition in intent.conditions)
        rearmed = intent
        if intent.approaching_alerted_at is not None and distance > max(settings.entry_intent_rearm_pct, intent.tolerance_pct * 2):
            rearmed = intent.model_copy(update={"approaching_alerted_at": None, "updated_at": now})
            repo.upsert_entry_intent(rearmed)

        if _intent_invalidated(rearmed, current):
            invalidated = rearmed.model_copy(
                update={
                    "status": "invalidated",
                    "invalidated_at": now,
                    "updated_at": now,
                    "last_seen_at": now,
                    "condition_state": condition_state,
                }
            )
            repo.upsert_entry_intent(invalidated)
            updated_intents.append(invalidated)
            candidates.append(_intent_candidate("intent_invalidated", "info", invalidated, current, distance, "존 반대편 이탈", backtest_summary))
            continue

        if in_zone and all_met:
            triggered = rearmed.model_copy(
                update={
                    "status": "triggered",
                    "triggered_at": now,
                    "zone_entered_alerted_at": now,
                    "updated_at": now,
                    "last_seen_at": now,
                    "condition_state": condition_state,
                }
            )
            repo.upsert_entry_intent(triggered)
            _record_intent_judgment(repo, triggered, snapshot, condition_state)
            updated_intents.append(triggered)
            candidates.append(_intent_candidate("intent_zone_entered", "action", triggered, current, distance, "등록 조건 충족", backtest_summary))
            continue

        if in_zone:
            should_alert = rearmed.partial_alerted_at is None
            partial = rearmed.model_copy(
                update={
                    "status": "active",
                    "partial_alerted_at": now if should_alert else rearmed.partial_alerted_at,
                    "updated_at": now,
                    "last_seen_at": now,
                    "condition_state": condition_state,
                }
            )
            repo.upsert_entry_intent(partial)
            updated_intents.append(partial)
            if should_alert:
                candidates.append(_intent_candidate("intent_zone_entered_partial", "info", partial, current, distance, "조건 일부 미충족", backtest_summary))
            continue

        if distance <= rearmed.tolerance_pct and rearmed.approaching_alerted_at is None:
            approaching = rearmed.model_copy(
                update={
                    "approaching_alerted_at": now,
                    "updated_at": now,
                    "last_seen_at": now,
                    "condition_state": condition_state,
                }
            )
            repo.upsert_entry_intent(approaching)
            updated_intents.append(approaching)
            candidates.append(_intent_candidate("intent_approaching", "info", approaching, current, distance, "존 접근", backtest_summary))
            continue

        refreshed = rearmed.model_copy(update={"updated_at": now, "last_seen_at": now, "condition_state": condition_state})
        repo.upsert_entry_intent(refreshed)
        updated_intents.append(refreshed)
    return {"intents": updated_intents, "candidates": candidates}


def score_scout_setups(repo: Any, settings: Settings) -> dict[str, Any]:
    scored = 0
    skipped: list[dict[str, str]] = []
    cutoff = utc_now() - timedelta(hours=settings.scout_setup_score_after_hours)
    for setup in repo.list_armed_setups(limit=1000):
        if setup.status not in {"triggered", "invalidated"}:
            skipped.append({"setup_id": str(setup.id), "reason": "not_resolved"})
            continue
        judgment_id = setup.judgment_id or f"scout_setup:{setup.id}"
        existing = [score for score in repo.list_judgment_scores(position_id=SCOUT_SENTINEL_POSITION_ID, limit=1000) if score.judgment_id == judgment_id]
        if existing:
            skipped.append({"setup_id": str(setup.id), "reason": "already_scored"})
            continue
        resolved_at = setup.triggered_at or setup.invalidated_at or setup.updated_at
        if resolved_at > cutoff:
            skipped.append({"setup_id": str(setup.id), "reason": "score_window_open"})
            continue
        snapshots = repo.list_scout_snapshots(symbol=setup.symbol, limit=500)
        outcome, detail, metrics = _score_setup_path(setup, snapshots, settings)
        repo.add_judgment_score(
            JudgmentScore(
                judgment_id=judgment_id,
                position_id=SCOUT_SENTINEL_POSITION_ID,
                trade_id=None,
                judgment_type="scout_setup",
                claim=_setup_claim(setup),
                confidence=setup.confidence,
                outcome=outcome,
                detail=detail,
                metrics=metrics,
                param_version=engine_param_snapshot(repo),
            )
        )
        scored += 1
    return {"scores": scored, "skipped": skipped}


def score_entry_intents(repo: Any, settings: Settings) -> dict[str, Any]:
    scored = 0
    skipped: list[dict[str, str]] = []
    cutoff = utc_now() - timedelta(hours=settings.entry_intent_score_after_hours)
    for intent in repo.list_entry_intents(limit=1000):
        if intent.status not in {"triggered", "invalidated", "expired"}:
            skipped.append({"intent_id": str(intent.id), "reason": "not_resolved"})
            continue
        judgment_id = intent.judgment_id or f"entry_intent:{intent.id}"
        existing = [score for score in repo.list_judgment_scores(position_id=SCOUT_SENTINEL_POSITION_ID, limit=1000) if score.judgment_id == judgment_id]
        if existing:
            skipped.append({"intent_id": str(intent.id), "reason": "already_scored"})
            continue
        resolved_at = intent.triggered_at or intent.invalidated_at or intent.updated_at
        if resolved_at > cutoff:
            skipped.append({"intent_id": str(intent.id), "reason": "score_window_open"})
            continue
        snapshots = repo.list_scout_snapshots(symbol=intent.symbol, limit=500)
        outcome, detail, metrics = _score_intent_path(intent, snapshots)
        repo.add_judgment_score(
            JudgmentScore(
                judgment_id=judgment_id,
                position_id=SCOUT_SENTINEL_POSITION_ID,
                trade_id=None,
                judgment_type="entry_intent",
                claim=_intent_claim(intent),
                confidence=None,
                outcome=outcome,
                detail=detail,
                metrics=metrics,
                param_version=engine_param_snapshot(repo),
            )
        )
        scored += 1
    return {"scores": scored, "skipped": skipped}


def build_scout_calibration_summary(scores: list[JudgmentScore]) -> dict[str, Any]:
    scout_scores = [score for score in scores if score.judgment_type == "scout_setup"]
    intent_scores = [score for score in scores if score.judgment_type == "entry_intent"]
    discovery_scores = [score for score in scores if score.judgment_type == "universe_discovery"]
    total = len(scout_scores)
    correct = len([score for score in scout_scores if score.outcome == "correct"])
    wrong = len([score for score in scout_scores if score.outcome == "wrong"])
    whipsaw = len([score for score in scout_scores if score.outcome == "whipsaw"])
    untested = len([score for score in scout_scores if score.outcome == "untested"])
    by_type: dict[str, dict[str, Any]] = {}
    for score in scout_scores:
        setup_type = str(score.claim.get("setup_type") or "setup")
        bucket = by_type.setdefault(
            setup_type,
            {"total": 0, "correct": 0, "wrong": 0, "whipsaw": 0, "untested": 0},
        )
        bucket["total"] += 1
        bucket[score.outcome] += 1
    for bucket in by_type.values():
        bucket_tested = int(bucket["correct"] + bucket["wrong"] + bucket["whipsaw"])
        bucket["tested"] = bucket_tested
        bucket["accuracy_pct"] = round((bucket["correct"] / bucket_tested) * 100, 1) if bucket_tested else None
    tested = correct + wrong + whipsaw
    return {
        "total": total,
        "tested": tested,
        "correct": correct,
        "wrong": wrong,
        "whipsaw": whipsaw,
        "untested": untested,
        "accuracy_pct": round((correct / tested) * 100, 1) if tested else None,
        "by_type": by_type,
        "quality": _setup_quality_summary(scout_scores),
        "entry_intents": _intent_calibration_summary(intent_scores),
        "discoveries": _generic_score_summary(discovery_scores),
        "sample_warning": "진입하지 않은 셋업도 트리거 이후 가격 경로로 결과론적 채점합니다. N<10 구간은 결론을 보류합니다.",
    }


def _generic_score_summary(scores: list[JudgmentScore]) -> dict[str, Any]:
    total = len(scores)
    correct = len([score for score in scores if score.outcome == "correct"])
    wrong = len([score for score in scores if score.outcome == "wrong"])
    whipsaw = len([score for score in scores if score.outcome == "whipsaw"])
    untested = len([score for score in scores if score.outcome == "untested"])
    tested = correct + wrong + whipsaw
    return {
        "total": total,
        "tested": tested,
        "correct": correct,
        "wrong": wrong,
        "whipsaw": whipsaw,
        "untested": untested,
        "accuracy_pct": round((correct / tested) * 100, 1) if tested else None,
        "sample_warning": "발견 채널은 기존 관심종목 셋업과 분리 집계합니다. N<10 구간은 결론을 보류합니다.",
    }


def _setup_quality_summary(scores: list[JudgmentScore]) -> dict[str, Any]:
    total = len(scores)
    invalidation_too_close = sum(1 for score in scores if score.metrics.get("invalidation_too_close") is True)
    rr_outliers = sum(1 for score in scores if score.metrics.get("rr_above_display_cap") is True)
    briefing_htf_countertrend = sum(1 for score in scores if score.metrics.get("briefing_htf_countertrend") is True)
    return {
        "sample_size": total,
        "invalidation_too_close": invalidation_too_close,
        "rr_above_display_cap": rr_outliers,
        "briefing_htf_countertrend": briefing_htf_countertrend,
        "briefing_htf_countertrend_rate_pct": round((briefing_htf_countertrend / total) * 100, 1) if total else None,
    }


def scout_rate_budget(settings: Settings, watchlist_count: int) -> dict[str, Any]:
    per_symbol = 3
    interval_minutes = max(1, settings.worker_scout_scan_interval_seconds / 60)
    max_symbols = settings.scout_watchlist_symbol_limit
    return {
        "bitget_requests_per_symbol": per_symbol,
        "interval_minutes": interval_minutes,
        "watchlist_count": watchlist_count,
        "max_symbols_per_tick": max_symbols,
        "requests_per_tick_at_limit": max_symbols * per_symbol,
        "formula": "symbols × (candles 1 + ticker 1 + derivatives 1) / interval",
        "round_robin_required": watchlist_count > max_symbols,
    }


def _harmonic_candidates(
    symbol: str,
    timeframe: str,
    analysis: dict[str, Any],
    mark: float,
    settings: Settings,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for pattern in analysis.get("harmonic_prz", []) if isinstance(analysis.get("harmonic_prz"), list) else []:
        if not isinstance(pattern, dict):
            continue
        confidence = _int(pattern.get("confidence"))
        mid = _float(pattern.get("mid"))
        if confidence is None or confidence < settings.scout_harmonic_auto_arm_confidence or mid is None:
            continue
        distance = _distance_pct(mark, mid)
        if abs(distance) > settings.scout_harmonic_auto_arm_distance_pct:
            continue
        direction = "long" if pattern.get("direction") == "bullish" else "short" if pattern.get("direction") == "bearish" else None
        items.append(
            {
                "symbol": symbol.upper(),
                "timeframe": timeframe,
                "setup_type": "harmonic_prz",
                "direction": direction,
                "trigger_price": mid,
                "trigger_label": f"{pattern.get('pattern') or '하모닉'} PRZ",
                "trigger_condition": "반전 후보 구간 도달 시 반응 확인",
                "distance_pct": round(distance, 2),
                "confidence": confidence,
                "basis": str(pattern.get("basis") or "하모닉 PRZ"),
            }
        )
    return items


def _level_candidates(
    symbol: str,
    timeframe: str,
    analysis: dict[str, Any],
    mark: float,
    settings: Settings,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    levels = analysis.get("price_levels") if isinstance(analysis.get("price_levels"), dict) else {}
    for side in ("support", "resistance"):
        for level in levels.get(side, []) if isinstance(levels.get(side), list) else []:
            if not isinstance(level, dict):
                continue
            price = _float(level.get("price"))
            score = _int(level.get("score"))
            if price is None or score is None or score < settings.scout_level_auto_arm_score:
                continue
            distance = _distance_pct(mark, price)
            if abs(distance) > settings.scout_level_auto_arm_distance_pct:
                continue
            direction = "long" if side == "support" else "short"
            items.append(
                {
                    "symbol": symbol.upper(),
                    "timeframe": timeframe,
                    "setup_type": "structure_level",
                    "direction": direction,
                    "trigger_price": price,
                    "trigger_label": "구조 지지" if side == "support" else "구조 저항",
                    "trigger_condition": "레벨 근접 시 반응 확인",
                    "distance_pct": round(distance, 2),
                    "confidence": score,
                    "basis": f"{'지지' if side == 'support' else '저항'} · 터치 {level.get('touches', '-')} · 점수 {score}",
                }
            )
    return items


def _wyckoff_candidates(
    symbol: str,
    timeframe: str,
    analysis: dict[str, Any],
    mark: float,
    settings: Settings,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for marker in analysis.get("wyckoff_markers", []) if isinstance(analysis.get("wyckoff_markers"), list) else []:
        if not isinstance(marker, dict):
            continue
        confidence = _int(marker.get("confidence"))
        price = _float(marker.get("price")) or mark
        if confidence is None or confidence < settings.scout_wyckoff_auto_arm_confidence:
            continue
        label = str(marker.get("label") or marker.get("type") or "와이코프 이벤트")
        lower = label.lower()
        direction = "long" if "spring" in lower or "sos" in lower else "short" if "utad" in lower or "sow" in lower else None
        items.append(
            {
                "symbol": symbol.upper(),
                "timeframe": timeframe,
                "setup_type": "wyckoff_event",
                "direction": direction,
                "trigger_price": price,
                "trigger_label": label,
                "trigger_condition": "와이코프 이벤트 확정 후 후속 반응 확인",
                "distance_pct": round(_distance_pct(mark, price), 2),
                "confidence": confidence,
                "basis": f"와이코프 이벤트 · 신뢰도 {confidence}",
            }
        )
    return items


def _crowding_level_candidates(
    symbol: str,
    timeframe: str,
    analysis: dict[str, Any],
    mark: float,
    settings: Settings,
) -> list[dict[str, Any]]:
    derivatives = analysis.get("derivatives") if isinstance(analysis.get("derivatives"), dict) else {}
    signals = derivatives.get("signals") if isinstance(derivatives.get("signals"), dict) else {}
    crowding = signals.get("crowding_score") if isinstance(signals.get("crowding_score"), dict) else None
    score = _int((crowding or {}).get("score"))
    if score is None or score < 80:
        return []
    level_candidates = _level_candidates(symbol, timeframe, analysis, mark, settings)
    for item in level_candidates:
        item["setup_type"] = "crowding_level"
        item["basis"] = f"{item.get('basis')} + 수급 쏠림 {score}"
        item["confidence"] = min(100, int(item.get("confidence") or 0) + 10)
    return level_candidates


def _gated_setup_candidate(
    settings: Settings,
    rule_id: str,
    severity: str,
    setup: ArmedSetup,
    current: float,
    distance: float,
) -> AlertCandidate | None:
    state = _setup_direction_state(setup)
    if state["conflict"] and not settings.scout_conflict_setup_alerts_enabled:
        return None
    effective_severity = severity
    if state["conflict"] and rule_id == "setup_triggered" and not state["htf_aligned"]:
        effective_severity = "info"
    if _invalidation_too_close(setup):
        effective_severity = "info"
    return _setup_candidate(rule_id, effective_severity, setup, current, distance)


def _setup_candidate(rule_id: str, severity: str, setup: ArmedSetup, current: float, distance: float) -> AlertCandidate:
    title_map = {
        "setup_near": "셋업 접근",
        "setup_triggered": "셋업 트리거",
        "setup_invalidated": "셋업 무효화",
    }
    state = _setup_direction_state(setup)
    too_close = _invalidation_too_close(setup)
    emoji = "🎯" if rule_id == "setup_near" else "🟢" if rule_id == "setup_triggered" else "🟡"
    preview = _preview_line(setup)
    context = _setup_context_line(setup)
    briefing = _briefing_preview_line(setup)
    backtest = _backtest_preview_line(setup)
    title = title_map.get(rule_id, "셋업")
    if rule_id != "setup_invalidated":
        if state["conflict"]:
            title = "충돌 셋업 · 양방향 근거"
            emoji = "⚖️"
        elif state["overall_direction"] is None:
            title = "셋업 감지 · 종합 판단 유보"
        elif too_close:
            title = "셋업 품질 경고 · 무효화 과근접"
            emoji = "⚠️"
        else:
            title = f"{title} · 종합 정합"
    lines = [
        f"{emoji} <b>{escape(setup.symbol)}</b> — {escape(title)}",
        f"{escape(setup.trigger_label)} {_price(setup.trigger_price)} (거리 {_signed_pct(distance)}) · 신뢰도 {setup.confidence or '-'}",
        context,
        preview,
        backtest,
        briefing,
    ]
    if state["conflict"]:
        lines.append(
            f"셋업({escape(_setup_kind_label(setup))})은 {state['setup_label']}, 종합은 {state['overall_label']}. "
            "소수 지표가 맞을 수도 있음 — 상위 추세 직접 확인 권고."
        )
        if state["htf_aligned"]:
            lines.append("⚠️ 셋업이 상위 추세와 정렬 — 종합 판정이 추세 역행 가능")
        lines.append("→ 양측 근거 정보입니다. 자동 진입 신호가 아닙니다.")
    elif state["overall_direction"] is None and rule_id != "setup_invalidated":
        lines.append("종합 판단 유보 중 · 셋업만 감지")
        lines.append("→ 개별 이벤트 관찰 정보이며 방향 결론이 아닙니다.")
    elif rule_id != "setup_invalidated":
        lines.append("→ 개별 셋업과 종합 방향이 정합합니다. 무효화와 R:R을 함께 확인하세요.")
    if too_close and rule_id != "setup_invalidated":
        lines.append("⚠️ 무효화 과근접 — 노이즈 의심. R:R 산출 불가 · 자동 진입 제외")
    return AlertCandidate(
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        position_id=None,
        symbol=setup.symbol,
        identity=str(setup.id),
        title=title,
        message="\n".join([line for line in lines if line]),
        payload={
            "rule_id": rule_id,
            "kind": "scout_setup",
            "setup_id": str(setup.id),
            "symbol": setup.symbol,
            "timeframe": setup.timeframe,
            "setup_type": setup.setup_type,
            "direction": setup.direction,
            "trigger_price": setup.trigger_price,
            "current_price": current,
            "distance_pct": round(distance, 2),
            "confidence": setup.confidence,
            "basis": setup.basis,
            "preview": setup.preview,
            "direction_conflict": state["conflict"],
            "overall_direction": state["overall_direction"],
            "htf_direction": state["htf_direction"],
            "htf_aligned": state["htf_aligned"],
            "briefing_htf_countertrend": state["briefing_htf_countertrend"],
            "invalidation_too_close": too_close,
            "number_sources": [
                {
                    "label": "trigger_price",
                    "value": setup.trigger_price,
                    "source": "armed_setups.trigger_price",
                },
                {
                    "label": "current_price",
                    "value": current,
                    "source": "scout_snapshot.mark_price",
                },
                {
                    "label": "distance_pct",
                    "value": round(distance, 2),
                    "source": "computed_from_scout_snapshot",
                },
            ],
        },
    )


def _record_setup_judgment(repo: Any, setup: ArmedSetup, snapshot: ScoutSnapshot) -> None:
    repo.add_judgment(
        JudgmentLedgerEntry(
            judgment_id=setup.judgment_id or f"scout_setup:{setup.id}",
            position_id=SCOUT_SENTINEL_POSITION_ID,
            source_type="scout_setup",
            source_id=str(setup.id),
            as_of=snapshot.as_of,
            type="scout_setup",
            claim=_setup_claim(setup),
            confidence=setup.confidence,
            param_version=engine_param_snapshot(repo),
        )
    )


def _setup_claim(setup: ArmedSetup) -> dict[str, Any]:
    preview = setup.preview if isinstance(setup.preview, dict) else {}
    direction_state = _setup_direction_state(setup)
    return {
        "setup_id": str(setup.id),
        "symbol": setup.symbol,
        "timeframe": setup.timeframe,
        "setup_type": setup.setup_type,
        "direction": setup.direction,
        "price": setup.trigger_price,
        "condition": setup.trigger_condition,
        "basis": setup.basis,
        "source": setup.source,
        "quality_metrics": {
            "invalidation_distance_pct": preview.get("invalidation_distance_pct"),
            "invalidation_too_close": _invalidation_too_close(setup),
            "rr_ratio_raw": preview.get("rr_ratio_raw", preview.get("rr_ratio")),
            "rr_above_display_cap": bool(_quality_anomalies(setup).get("rr_above_display_cap")),
            "briefing_htf_countertrend": direction_state["briefing_htf_countertrend"],
        },
    }


def _score_setup_path(setup: ArmedSetup, snapshots: list[ScoutSnapshot], settings: Settings) -> tuple[str, str, dict[str, Any]]:
    resolved_at = setup.triggered_at or setup.invalidated_at or setup.updated_at
    path = [snapshot for snapshot in snapshots if snapshot.as_of > resolved_at]
    if not path:
        return "untested", "트리거 이후 가격 경로 표본이 없습니다.", {"samples": 0, **_setup_quality_metrics(setup)}
    prices = [_float(snapshot.mark_price) for snapshot in path]
    prices = [price for price in prices if price is not None]
    if not prices or setup.trigger_price is None:
        return "untested", "트리거 또는 이후 가격이 부족합니다.", {"samples": len(path), **_setup_quality_metrics(setup)}
    max_price, min_price = max(prices), min(prices)
    direction = setup.direction or "long"
    favorable = (
        ((max_price - setup.trigger_price) / setup.trigger_price) * 100
        if direction == "long"
        else ((setup.trigger_price - min_price) / setup.trigger_price) * 100
    )
    adverse = (
        ((setup.trigger_price - min_price) / setup.trigger_price) * 100
        if direction == "long"
        else ((max_price - setup.trigger_price) / setup.trigger_price) * 100
    )
    metrics = {
        "samples": len(path),
        "favorable_pct": round(favorable, 2),
        "adverse_pct": round(adverse, 2),
        **_setup_quality_metrics(setup),
    }
    if setup.status == "invalidated":
        return "wrong", "셋업 전제가 붕괴되어 무효 처리됐습니다.", metrics
    if favorable >= max(1.0, adverse * 1.2):
        return "correct", "트리거 이후 기대 방향 반응이 우세했습니다.", metrics
    if adverse >= max(1.0, favorable * 1.2):
        return "wrong", "트리거 이후 기대 방향과 반대 경로가 우세했습니다.", metrics
    return "whipsaw", "트리거 이후 양방향 변동이 섞여 결론을 보류합니다.", metrics


def _intent_candidate(
    rule_id: str,
    severity: str,
    intent: EntryIntent,
    current: float,
    distance: float,
    reason: str,
    backtest_summary: str | None = None,
) -> AlertCandidate:
    title_map = {
        "intent_approaching": "진입 의도 접근",
        "intent_zone_entered": "진입 의도 조건 충족",
        "intent_zone_entered_partial": "진입 의도 존 진입",
        "intent_invalidated": "진입 의도 무효화",
    }
    emoji = "📍" if rule_id != "intent_zone_entered" else "🟢"
    unmet = _unmet_conditions(intent)
    lines = [
        f"{emoji} <b>{escape(intent.symbol)}</b> {_direction_label(intent.direction)} · {escape(title_map.get(rule_id, '진입 의도'))}",
        f"존 {_price(intent.zone_lower)}–{_price(intent.zone_upper)} · 현재 {_price(current)} · 거리 {_signed_pct(distance)}",
        f"조건: {_conditions_text(intent.condition_state)}",
    ]
    if unmet:
        lines.append(f"미충족: {escape(', '.join(unmet))}")
    preview = _intent_preview_line(intent)
    if preview:
        lines.append(preview)
    if backtest_summary:
        lines.append(escape(backtest_summary))
    if intent.note:
        lines.append(f"메모: {escape(intent.note)}")
    lines.append(f"→ 등록한 조건 상태 통보입니다. 진입 판단은 사용자 몫입니다. ({escape(reason)})")
    return AlertCandidate(
        rule_id=rule_id,
        severity=severity,  # type: ignore[arg-type]
        position_id=None,
        symbol=intent.symbol,
        identity=str(intent.id),
        title=title_map.get(rule_id, "진입 의도"),
        message="\n".join(lines),
        payload={
            "rule_id": rule_id,
            "kind": "entry_intent",
            "intent_id": str(intent.id),
            "symbol": intent.symbol,
            "timeframe": intent.timeframe,
            "direction": intent.direction,
            "zone_lower": intent.zone_lower,
            "zone_upper": intent.zone_upper,
            "current_price": current,
            "distance_pct": round(distance, 2),
            "conditions": intent.conditions,
            "condition_state": intent.condition_state,
            "preview": intent.preview,
            "backtest_summary": backtest_summary,
            "number_sources": [
                {"label": "zone_lower", "value": intent.zone_lower, "source": "entry_intents.zone_lower"},
                {"label": "zone_upper", "value": intent.zone_upper, "source": "entry_intents.zone_upper"},
                {"label": "current_price", "value": current, "source": "scout_snapshot.mark_price"},
                {"label": "distance_pct", "value": round(distance, 2), "source": "computed_from_scout_snapshot"},
            ],
        },
    )


def _intent_condition_state(intent: EntryIntent, row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    mark = _float(row.get("mark_price"))
    in_zone = bool(mark is not None and intent.zone_lower <= mark <= intent.zone_upper)
    state: dict[str, dict[str, Any]] = {
        "price_in_zone": {"met": in_zone, "label": "가격 존 진입" if in_zone else "가격 존 밖"},
    }
    analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
    summary_event = row.get("top_event") if isinstance(row.get("top_event"), dict) else {}
    state["sweep_confirmed"] = _intent_sweep_state(intent, analysis)
    state["wyckoff_event"] = {
        "met": bool(summary_event.get("confidence") and int(summary_event.get("confidence") or 0) >= 70),
        "label": str(summary_event.get("label") or "와이코프 이벤트 미확인"),
    }
    volume_xray = analysis.get("volume_xray") if isinstance(analysis.get("volume_xray"), dict) else {}
    rvol = _float(volume_xray.get("relative_volume"))
    spike = bool(volume_xray.get("spike_detected") or (rvol is not None and rvol >= 1.5))
    state["volume_spike"] = {"met": spike, "label": f"상대거래량 {rvol:.2f}배" if rvol is not None else "거래량 급증 미확인"}
    briefing = row.get("analyst_briefing") if isinstance(row.get("analyst_briefing"), dict) else {}
    confluence = briefing.get("confluence") if isinstance(briefing.get("confluence"), dict) else {}
    stance = str(confluence.get("stance") or "")
    aligned = (intent.direction == "long" and stance == "long_leaning") or (intent.direction == "short" and stance == "short_leaning")
    state["briefing_aligned"] = {"met": aligned, "label": str(confluence.get("stance_label") or "브리핑 미생성")}
    return state


def _intent_sweep_state(intent: EntryIntent, analysis: dict[str, Any]) -> dict[str, Any]:
    liquidity = analysis.get("liquidity") if isinstance(analysis, dict) else {}
    sweeps = liquidity.get("sweeps") if isinstance(liquidity, dict) and isinstance(liquidity.get("sweeps"), list) else []
    for sweep in sweeps:
        if not isinstance(sweep, dict) or not sweep.get("confirmed"):
            continue
        grade = str(sweep.get("grade") or "")
        price = _float(sweep.get("price") or sweep.get("pool_price"))
        if grade not in {"Mid", "Strong"} or price is None:
            continue
        if intent.zone_lower * 0.995 <= price <= intent.zone_upper * 1.005:
            return {"met": True, "label": f"{grade} 스윕 확인"}
    return {"met": False, "label": "Mid/Strong 스윕 미확인"}


def _zone_distance_pct(current: float, intent: EntryIntent) -> float:
    if current <= 0:
        return 0.0
    if intent.zone_lower <= current <= intent.zone_upper:
        return 0.0
    target = intent.zone_lower if current < intent.zone_lower else intent.zone_upper
    return abs(((target - current) / current) * 100)


def _intent_invalidated(intent: EntryIntent, current: float) -> bool:
    if intent.zone_entered_alerted_at is None and intent.partial_alerted_at is None:
        return False
    buffer_pct = max(0.5, intent.tolerance_pct)
    if intent.direction == "long":
        return current < intent.zone_lower * (1 - buffer_pct / 100)
    return current > intent.zone_upper * (1 + buffer_pct / 100)


def _record_intent_judgment(repo: Any, intent: EntryIntent, snapshot: ScoutSnapshot, condition_state: dict[str, Any]) -> None:
    repo.add_judgment(
        JudgmentLedgerEntry(
            judgment_id=intent.judgment_id or f"entry_intent:{intent.id}",
            position_id=SCOUT_SENTINEL_POSITION_ID,
            source_type="entry_intent",
            source_id=str(intent.id),
            as_of=snapshot.as_of,
            type="entry_intent",
            claim={**_intent_claim(intent), "condition_state": condition_state},
            confidence=None,
            param_version=engine_param_snapshot(repo),
        )
    )


def _intent_claim(intent: EntryIntent) -> dict[str, Any]:
    return {
        "intent_id": str(intent.id),
        "symbol": intent.symbol,
        "timeframe": intent.timeframe,
        "direction": intent.direction,
        "zone_lower": intent.zone_lower,
        "zone_upper": intent.zone_upper,
        "conditions": list(intent.conditions),
        "tolerance": intent.tolerance,
        "source": "user_entry_intent",
    }


def _score_intent_path(intent: EntryIntent, snapshots: list[ScoutSnapshot]) -> tuple[str, str, dict[str, Any]]:
    resolved_at = intent.triggered_at or intent.invalidated_at or intent.updated_at
    path = [snapshot for snapshot in snapshots if snapshot.as_of > resolved_at]
    if not path:
        return "untested", "의도 조건 이후 가격 경로 표본이 없습니다.", {"samples": 0}
    prices = [_float(snapshot.mark_price) for snapshot in path]
    prices = [price for price in prices if price is not None]
    if not prices:
        return "untested", "조건 이후 가격 표본이 부족합니다.", {"samples": len(path)}
    entry_ref = (intent.zone_lower + intent.zone_upper) / 2
    max_price, min_price = max(prices), min(prices)
    favorable = ((max_price - entry_ref) / entry_ref) * 100 if intent.direction == "long" else ((entry_ref - min_price) / entry_ref) * 100
    adverse = ((entry_ref - min_price) / entry_ref) * 100 if intent.direction == "long" else ((max_price - entry_ref) / entry_ref) * 100
    metrics = {"samples": len(path), "favorable_pct": round(favorable, 2), "adverse_pct": round(adverse, 2)}
    if intent.status in {"invalidated", "expired"}:
        return "wrong", "의도 존이 반응 전 무효화 또는 만료됐습니다.", metrics
    if favorable >= max(1.0, adverse * 1.2):
        return "correct", "조건 충족 이후 의도 방향 반응이 우세했습니다.", metrics
    if adverse >= max(1.0, favorable * 1.2):
        return "wrong", "조건 충족 이후 의도와 반대 경로가 우세했습니다.", metrics
    return "whipsaw", "조건 충족 이후 양방향 변동이 섞여 결론을 보류합니다.", metrics


def _intent_calibration_summary(scores: list[JudgmentScore]) -> dict[str, Any]:
    total = len(scores)
    correct = len([score for score in scores if score.outcome == "correct"])
    wrong = len([score for score in scores if score.outcome == "wrong"])
    whipsaw = len([score for score in scores if score.outcome == "whipsaw"])
    untested = len([score for score in scores if score.outcome == "untested"])
    tested = correct + wrong + whipsaw
    return {
        "total": total,
        "tested": tested,
        "correct": correct,
        "wrong": wrong,
        "whipsaw": whipsaw,
        "untested": untested,
        "accuracy_pct": round((correct / tested) * 100, 1) if tested else None,
        "sample_warning": "진입 의도 존은 실제 진입 여부와 별개로 조건 이후 가격 경로로 채점합니다.",
    }


def _intent_preview_line(intent: EntryIntent) -> str:
    preview = intent.preview if isinstance(intent.preview, dict) else {}
    rr = preview.get("rr_ratio")
    checks = ""
    if preview.get("checklist_passed") is not None and preview.get("checklist_total") is not None:
        checks = f" · 체크 {preview.get('checklist_passed')}/{preview.get('checklist_total')}"
    if rr is None and not checks and not preview.get("invalidation_too_close"):
        return ""
    return f"프리뷰({_direction_label(intent.direction)} 10x 가정): R:R {_rr_preview_value(preview)}{checks}"


def _conditions_text(condition_state: dict[str, Any]) -> str:
    labels: list[str] = []
    for key, state in condition_state.items():
        if not isinstance(state, dict):
            continue
        labels.append(f"{_condition_label(key)}={'충족' if state.get('met') else '대기'}")
    return escape(" · ".join(labels)) if labels else "-"


def _unmet_conditions(intent: EntryIntent) -> list[str]:
    unmet: list[str] = []
    for condition in intent.conditions:
        state = intent.condition_state.get(condition) if isinstance(intent.condition_state, dict) else None
        if not isinstance(state, dict) or not state.get("met"):
            unmet.append(_condition_label(condition))
    return unmet


def _direction_label(direction: str) -> str:
    return "숏" if direction == "short" else "롱"


def _condition_label(condition: str) -> str:
    labels = {
        "price_in_zone": "가격 존",
        "sweep_confirmed": "스윕",
        "wyckoff_event": "와이코프",
        "volume_spike": "거래량",
        "briefing_aligned": "브리핑",
    }
    return labels.get(condition, condition)


def _snapshot_from_row(row: dict[str, Any]) -> ScoutSnapshot:
    as_of = _parse_dt(row.get("as_of")) or utc_now()
    return ScoutSnapshot(
        symbol=str(row.get("symbol") or "").upper(),
        timeframe=str(row.get("timeframe") or "4h"),
        as_of=as_of,
        mark_price=_float(row.get("mark_price")),
        setup_proximity_pct=_float(row.get("setup_proximity_pct")),
        summary=row,
        analysis=row.get("analysis") if isinstance(row.get("analysis"), dict) else {},
    )


def _setup_triggered(setup: ArmedSetup, current: float) -> bool:
    if setup.trigger_price is None:
        return False
    if setup.setup_type == "wyckoff_event":
        return True
    direction = setup.direction or "long"
    if direction == "long":
        return current <= setup.trigger_price if setup.setup_type in {"harmonic_prz", "structure_level", "crowding_level"} else current >= setup.trigger_price
    return current >= setup.trigger_price if setup.setup_type in {"harmonic_prz", "structure_level", "crowding_level"} else current <= setup.trigger_price


def _setup_invalidated(setup: ArmedSetup, current: float) -> bool:
    if setup.trigger_price is None:
        return False
    buffer_pct = 2.5
    direction = setup.direction or "long"
    if direction == "long":
        return current < setup.trigger_price * (1 - buffer_pct / 100)
    return current > setup.trigger_price * (1 + buffer_pct / 100)


def _setup_id(symbol: str, timeframe: str, candidate: dict[str, Any]) -> UUID:
    price = _float(candidate.get("trigger_price"))
    rounded = round(price or 0.0, 8)
    return uuid5(
        NAMESPACE_URL,
        f"fce:auto-setup:{symbol.upper()}:{timeframe}:{candidate.get('setup_type')}:{candidate.get('trigger_label')}:{rounded}",
    )


def _setup_direction_state(setup: ArmedSetup) -> dict[str, Any]:
    preview = setup.preview if isinstance(setup.preview, dict) else {}
    setup_direction = setup.direction if setup.direction in {"long", "short"} else None
    overall_value = preview.get("briefing_stance_state") or preview.get("scout_overall_stance") or preview.get("briefing_stance")
    overall_direction = _direction_from_label(overall_value)
    htf_direction = _direction_from_label(preview.get("htf_trend"))
    conflict = bool(setup_direction and overall_direction and setup_direction != overall_direction)
    return {
        "setup_direction": setup_direction,
        "setup_label": _direction_label(setup_direction) if setup_direction else "방향 미지정",
        "overall_direction": overall_direction,
        "overall_label": f"{_direction_label(overall_direction)} 우세" if overall_direction else "판단 유보",
        "htf_direction": htf_direction,
        "htf_aligned": bool(setup_direction and htf_direction and setup_direction == htf_direction),
        "conflict": conflict,
        "briefing_htf_countertrend": bool(overall_direction and htf_direction and overall_direction != htf_direction),
    }


def _direction_from_label(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"long", "long_leaning", "bullish", "up", "uptrend", "상방", "롱", "상승"}:
        return "long"
    if normalized in {"short", "short_leaning", "bearish", "down", "downtrend", "하방", "숏", "하락"}:
        return "short"
    if "bullish" in normalized or "long" in normalized or "롱" in normalized or "상방" in normalized or "상승" in normalized:
        return "long"
    if "bearish" in normalized or "short" in normalized or "숏" in normalized or "하방" in normalized or "하락" in normalized:
        return "short"
    return None


def _quality_anomalies(setup: ArmedSetup) -> dict[str, Any]:
    preview = setup.preview if isinstance(setup.preview, dict) else {}
    return preview.get("quality_anomalies") if isinstance(preview.get("quality_anomalies"), dict) else {}


def _invalidation_too_close(setup: ArmedSetup) -> bool:
    preview = setup.preview if isinstance(setup.preview, dict) else {}
    return bool(preview.get("invalidation_too_close") or _quality_anomalies(setup).get("invalidation_too_close"))


def _setup_quality_metrics(setup: ArmedSetup) -> dict[str, Any]:
    preview = setup.preview if isinstance(setup.preview, dict) else {}
    state = _setup_direction_state(setup)
    return {
        "invalidation_distance_pct": preview.get("invalidation_distance_pct"),
        "invalidation_too_close": _invalidation_too_close(setup),
        "rr_ratio_raw": preview.get("rr_ratio_raw", preview.get("rr_ratio")),
        "rr_above_display_cap": bool(_quality_anomalies(setup).get("rr_above_display_cap")),
        "briefing_htf_countertrend": state["briefing_htf_countertrend"],
    }


def _setup_kind_label(setup: ArmedSetup) -> str:
    if setup.trigger_label:
        return setup.trigger_label
    labels = {
        "harmonic_prz": "하모닉 PRZ",
        "structure_level": "구조 레벨",
        "crowding_level": "수급 구조 레벨",
        "wyckoff_event": "와이코프 이벤트",
    }
    return labels.get(setup.setup_type, "개별 이벤트")


def _rr_preview_value(preview: dict[str, Any]) -> str:
    if preview.get("invalidation_too_close"):
        return "산출 불가 · 무효화 너무 가까움"
    display = preview.get("rr_ratio_display")
    if display is not None:
        return str(display)
    rr = _float(preview.get("rr_ratio"))
    cap = _float(preview.get("rr_display_cap")) or 10.0
    if rr is None:
        return "-"
    return f"{cap:g}+" if rr > cap else f"{rr:g}"


def _preview_line(setup: ArmedSetup) -> str:
    preview = setup.preview if isinstance(setup.preview, dict) else {}
    rr = preview.get("rr_ratio")
    inv = preview.get("invalidation_distance_pct")
    checks = ""
    if preview.get("checklist_passed") is not None and preview.get("checklist_total") is not None:
        checks = f" · 체크 {preview.get('checklist_passed')}/{preview.get('checklist_total')}"
    if rr is None and inv is None and not checks and not preview.get("invalidation_too_close"):
        return ""
    direction = _direction_label(setup.direction) if setup.direction else "-"
    return f"프리뷰({direction} 10x 가정): R:R {_rr_preview_value(preview)} · 무효화 {_signed_pct(inv)}{checks}"


def _backtest_preview_line(setup: ArmedSetup) -> str:
    preview = setup.preview if isinstance(setup.preview, dict) else {}
    summary = preview.get("backtest_summary")
    return escape(summary.strip()) if isinstance(summary, str) and summary.strip() else ""


def _briefing_preview_line(setup: ArmedSetup) -> str:
    preview = setup.preview if isinstance(setup.preview, dict) else {}
    summary = preview.get("briefing_summary")
    if isinstance(summary, str) and summary.strip():
        return f"종합 브리핑: {escape(_strip_briefing_prefix(summary))}"
    stance = preview.get("briefing_stance")
    if isinstance(stance, str) and stance.strip():
        return f"종합 브리핑: {escape(_strip_briefing_prefix(stance))}"
    return ""


def _setup_context_line(setup: ArmedSetup) -> str:
    state = _setup_direction_state(setup)
    direction = state["setup_label"]
    if state["overall_direction"] is None:
        return f"셋업 방향: {direction} · 개별 이벤트 기준"
    stance_label = "상방 근거 우세" if state["overall_direction"] == "long" else "하방 근거 우세"
    conflict = " · 충돌" if state["conflict"] else ""
    return f"셋업 방향: {direction} · 현재 종합: {stance_label}{conflict}"


def _strip_briefing_prefix(value: str) -> str:
    text = value.strip()
    for prefix in ("브리핑:", "브리핑："):
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text


def _row_one_liners(row: dict[str, Any]) -> dict[str, Any]:
    analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
    one_liners = analysis.get("one_liners")
    if not isinstance(one_liners, dict):
        chart_analysis = row.get("chart_analysis") if isinstance(row.get("chart_analysis"), dict) else {}
        one_liners = chart_analysis.get("one_liners")
    return one_liners if isinstance(one_liners, dict) else {}


def _row_backtest_summary(row: dict[str, Any]) -> str | None:
    if isinstance(row.get("backtest_summary"), str):
        return row["backtest_summary"]
    analysis = row.get("analysis") if isinstance(row.get("analysis"), dict) else {}
    context = analysis.get("historical_backtest") if isinstance(analysis.get("historical_backtest"), dict) else None
    stats = context.get("stats") if isinstance(context, dict) and isinstance(context.get("stats"), list) else []
    if not stats:
        return None
    stat = stats[0]
    n = int(stat.get("sample_size") or 0)
    if n <= 0:
        return None
    current = context.get("current_regime") if isinstance(context.get("current_regime"), dict) else {}
    # WO-36 §7: 승률 발행은 표기 표준(CI·N·레짐) 경유.
    return "백테스트: " + format_stat_line(
        stat,
        sample_floor=int(context.get("sample_floor") or 10),
        current_regime=str(current.get("regime")) if current.get("regime") else None,
    )


def _distance_pct(base: float, target: float) -> float:
    return ((target - base) / base) * 100 if base else 0.0


def _direction(value: Any) -> str | None:
    if value == Direction.long or value == "long" or value == "bullish":
        return "long"
    if value == Direction.short or value == "short" or value == "bearish":
        return "short"
    return None


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _price(value: Any) -> str:
    number = _float(value)
    if number is None:
        return "-"
    if abs(number) >= 100:
        return f"{number:.2f}"
    if abs(number) >= 1:
        return f"{number:.4f}"
    return f"{number:.6f}"


def _signed_pct(value: Any) -> str:
    number = _float(value)
    if number is None:
        return "-"
    return f"{number:+.2f}%"
