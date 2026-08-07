from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AlertRuleState:
    status: str = "armed"
    last_fired_at: datetime | None = None
    cooldown_until: datetime | None = None
    last_payload: dict[str, Any] = field(default_factory=dict)
    # WO-FCE-BREACH-ALERT-FIX-01: 마지막으로 알린 무효화 이탈 폭(%). 이탈은 상태이므로
    # 같은 깊이를 반복해 알리지 않고, 유의미하게 악화됐을 때만 다시 알린다.
    last_breach_pct: float | None = None


@dataclass
class NotificationState:
    muted_until: datetime | None = None
    sent_alert_keys: dict[str, datetime] = field(default_factory=dict)
    last_summary_date: str | None = None
    last_weekly_calibration_date: str | None = None
    # WO-FCE-PERFORMANCE-REPORT-01: 주간 성과 리포트 발송일. 캘리브레이션과 별도 주기다.
    last_weekly_performance_date: str | None = None
    alert_rule_states: dict[str, AlertRuleState] = field(default_factory=dict)
    suppressed_alerts: list[dict[str, Any]] = field(default_factory=list)
    # WO-44: 포지션별 라이프사이클 트래커 (verdict/stance/insufficient) — 영속 대상.
    lifecycle_positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_pulse_at: datetime | None = None
    # WO-44 Part C: 발송 실패분 — 다음 pulse에 병합.
    pending_redelivery: list[dict[str, Any]] = field(default_factory=list)
    # 같은 고래의 짧은 시간 내 진입 체결을 Telegram 한 건으로 묶기 위한 대기열.
    # 원시 체결 원장은 DB에 그대로 남고, 이 상태는 알림 표현만 지연한다.
    whale_alert_events: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # WO-FCE-STRUCTURE-CONTEXT-01: 심볼별 직전 구조 컨텍스트. 전이 감지에 쓰며,
    # 이것이 없으면 매 틱 같은 구조 상태를 재발송하게 된다(C6 스팸 금지).
    structure_contexts: dict[str, dict[str, Any]] = field(default_factory=dict)
    # WO-FCE-PAPER-OBSERVABILITY-01 (D3): 페이퍼 트랙 skipped 이벤트의 스팸 억제 상태.
    # key = "track:kind:reason" → {last_sent_date, suppressed, first_seen}.
    # 연속 동일 사유는 억제하고, 상태 전이(최초) + 일 1회 리마인더만 발송한다.
    paper_skip_state: dict[str, dict[str, Any]] = field(default_factory=dict)
    # WO-FCE-VALIDATION-VERDICT-01 Phase 1-3: 트랙별 직전 완주 판정. 전이가 있을 때만
    # 알리기 위한 상태이며, 이것이 없으면 매 주기 같은 판정을 재발송한다(스팸 금지).
    sample_verdicts: dict[str, str] = field(default_factory=dict)

    def is_muted(self) -> bool:
        return self.muted_until is not None and datetime.now(timezone.utc) < self.muted_until

    def mute_for(self, seconds: int) -> datetime:
        self.muted_until = datetime.now(timezone.utc) + timedelta(seconds=max(1, seconds))
        return self.muted_until

    def unmute(self) -> None:
        self.muted_until = None

    # ── 페이퍼 skipped 스팸 억제 (WO-FCE-PAPER-OBSERVABILITY-01, D3) ──
    def register_paper_skip(self, key: str, *, now: datetime | None = None) -> bool:
        """Return True only when this skip should be sent (transition or daily reminder).

        같은 사유가 연속되면 억제하고 억제 횟수를 누적한다. 사유가 처음 등장하거나
        (상태 전이) 날짜가 바뀌면(일 1회 리마인더) True를 반환한다.
        """
        moment = now or datetime.now(timezone.utc)
        today = moment.date().isoformat()
        entry = self.paper_skip_state.get(key)
        if entry is None:
            self.paper_skip_state[key] = {"last_sent_date": today, "suppressed": 0, "first_seen": moment.isoformat()}
            return True
        if entry.get("last_sent_date") != today:
            entry["last_sent_date"] = today
            entry["suppressed"] = 0
            return True
        entry["suppressed"] = int(entry.get("suppressed", 0) or 0) + 1
        return False

    def clear_paper_skips_for_track(self, track: str) -> None:
        """엔진이 다시 정상 평가하면 그 트랙의 skip 상태를 지워 다음 스킵을 새 전이로 만든다.

        ⚠️ rejected_summary 상태는 지우지 않는다(WO-FCE-PAPER-ENTRY-REALITY-01).
        거부 집계는 **평가가 정상일 때 매 틱 발생**하므로, effective_run 마다 상태를 지우면
        "최다 거부 게이트 전이 시에만 발송" 정책이 무력화되어 60초마다 스팸이 재발한다.
        거부 상태는 게이트가 실제로 바뀌거나 날짜가 바뀔 때만 다시 발송된다.
        """
        prefix = f"{track}:"
        for key in [key for key in self.paper_skip_state if key.startswith(prefix) and ":rejected_summary:" not in key]:
            del self.paper_skip_state[key]

    # ── 영속화 (WO-44 Part C) ──────────────────────────────────────
    # 콰이어트 아워 억제분·아침 요약 상태·라이프사이클 트래커가 인메모리라
    # 재시작 시 유실되던 결함의 수리. 실패해도 알림 경로는 계속 동작한다.

    def save(self, path: str) -> None:
        try:
            payload = {
                "muted_until": _iso(self.muted_until),
                "last_summary_date": self.last_summary_date,
                "last_weekly_calibration_date": self.last_weekly_calibration_date,
                "last_weekly_performance_date": self.last_weekly_performance_date,
                "suppressed_alerts": self.suppressed_alerts[-100:],
                "lifecycle_positions": self.lifecycle_positions,
                "last_pulse_at": _iso(self.last_pulse_at),
                "pending_redelivery": self.pending_redelivery[-50:],
                "whale_alert_events": {address: events[-200:] for address, events in self.whale_alert_events.items() if events},
                "paper_skip_state": self.paper_skip_state,
                "structure_contexts": self.structure_contexts,
                "sample_verdicts": self.sample_verdicts,
                "alert_rule_states": _retained_rule_states(self.alert_rule_states),
            }
            directory = os.path.dirname(os.path.abspath(path)) or "."
            fd, tmp = tempfile.mkstemp(dir=directory, prefix=".notif_state_")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, default=str)
            os.replace(tmp, path)
        except Exception as exc:
            logger.warning("notification state save failed: %s", exc)

    def load(self, path: str) -> None:
        try:
            if not os.path.exists(path):
                return
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            logger.warning("notification state load failed: %s", exc)
            return
        self.muted_until = _parse_dt(payload.get("muted_until"))
        self.last_summary_date = payload.get("last_summary_date")
        self.last_weekly_calibration_date = payload.get("last_weekly_calibration_date")
        self.last_weekly_performance_date = payload.get("last_weekly_performance_date")
        self.sample_verdicts = {str(key): str(value) for key, value in (payload.get("sample_verdicts") or {}).items() if value}
        self.suppressed_alerts = [item for item in payload.get("suppressed_alerts", []) if isinstance(item, dict)]
        self.lifecycle_positions = {str(key): value for key, value in (payload.get("lifecycle_positions") or {}).items() if isinstance(value, dict)}
        self.last_pulse_at = _parse_dt(payload.get("last_pulse_at"))
        self.pending_redelivery = [item for item in payload.get("pending_redelivery", []) if isinstance(item, dict)]
        self.whale_alert_events = {
            str(address).lower(): [item for item in events if isinstance(item, dict)][-200:]
            for address, events in (payload.get("whale_alert_events") or {}).items()
            if isinstance(events, list)
        }
        self.paper_skip_state = {str(key): value for key, value in (payload.get("paper_skip_state") or {}).items() if isinstance(value, dict)}
        self.structure_contexts = {str(key): value for key, value in (payload.get("structure_contexts") or {}).items() if isinstance(value, dict)}
        for key, raw in (payload.get("alert_rule_states") or {}).items():
            if not isinstance(raw, dict):
                continue
            self.alert_rule_states[str(key)] = AlertRuleState(
                status=str(raw.get("status") or "armed"),
                last_fired_at=_parse_dt(raw.get("last_fired_at")),
                cooldown_until=_parse_dt(raw.get("cooldown_until")),
                last_breach_pct=_optional_float(raw.get("last_breach_pct")),
            )


# 영속 상한. 초과분을 삽입 순서로 자르면 **활성 쿨다운이 축출**된다 — 재로드 시 그 키는
# 사라지고 `setdefault` 가 status="armed" 로 되살려 같은 알림이 다시 나간다.
# 2026-08-04 실측: 저장 키가 정확히 500 포화, 그중 whale_entry 가 288개(58%)를 점유했고
# DRAMUSDT 무효화 이탈이 01:46/02:22/03:22/04:22 로 반복됐다. 쿨다운 만료가 아니라 축출이었다.
_ALERT_RULE_STATE_LIMIT = 2000


def _retained_rule_states(states: dict[str, "AlertRuleState"], *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """영속할 규칙 상태를 고른다 — **살아있는 쿨다운을 절대 버리지 않는다.**

    우선순위: ① 쿨다운이 아직 유효한 키(버리면 알림이 부활한다) ② 최근 발사 순.
    상한을 넘으면 오래 전에 만료된 것부터 버린다. 삽입 순서로 자르지 않는다.
    """
    current = now or datetime.now(timezone.utc)

    def sort_key(item: tuple[str, "AlertRuleState"]) -> tuple[int, float]:
        _, rule = item
        active = 1 if rule.cooldown_until is not None and rule.cooldown_until > current else 0
        fired = rule.last_fired_at.timestamp() if rule.last_fired_at else 0.0
        return (active, fired)

    ordered = sorted(states.items(), key=sort_key, reverse=True)
    return {
        key: {
            "status": rule.status,
            "last_fired_at": _iso(rule.last_fired_at),
            "cooldown_until": _iso(rule.cooldown_until),
            "last_breach_pct": rule.last_breach_pct,
        }
        for key, rule in ordered[:_ALERT_RULE_STATE_LIMIT]
    }


def _optional_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


notification_state = NotificationState()
