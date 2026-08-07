"""WO-FCE-WHALE-ALERT-DEMOTE-01 Phase 1 — 텔레그램 발송 통합 관문.

## 왜 이 모듈이 생겼나

화이트리스트가 **경로마다 하나씩** 있었다. `paper_events.TELEGRAM_SENDABLE_KINDS` 는
페이퍼 트랙 이벤트(`kind` 를 가진 dict)에만 적용되고, `notify/alerts.py::_fire_if_allowed`
경로(`AlertCandidate`)는 그 화이트리스트를 **아예 호출하지 않는다.**

그래서 고래 다중체결 알림(`whale_entry`)이 원칙을 우회한 채 발송됐다. 이건 한 건의 버그가
아니라 구조의 문제다 — 새 알림이 다른 경로로 들어오면 원칙이 적용되지 않는다.

> **관문은 하나여야 한다. 두 개면 그중 하나는 반드시 잊힌다.**

## 두 이름 공간을 구분한다

| 공간 | 식별자 | 예 |
| --- | --- | --- |
| 페이퍼 트랙 이벤트 | `kind` | `opened` · `closed` · `rejected_summary` |
| 포지션·시스템 알림 | `rule_id` | `invalidation_breach` · `whale_entry` |

**둘은 다른 공간이다.** 페이퍼 이벤트의 허용 목록(`opened`/`closed`)을 `rule_id` 공간에
그대로 적용하면 무효화 이탈·청산 접근 같은 critical 포지션 경보가 전부 끊긴다. 그건 이 WO 가
고치려는 문제가 아니라 새로 만드는 사고다.

## 기본 차단 (default-deny)

레지스트리에 없는 `rule_id` 는 **차단**된다. "새 알림이 다른 경로로 들어오면 원칙이 적용되지
않는다"가 결함의 본질이었고, 기본 차단이 그 구멍을 막는다. 새 알림을 푸시하려면 여기 명시적으로
등록해야 하며 **그 등록 행위가 곧 검토 지점**이다.

## 강등은 발송 경로만

강등된 신호도 수집·저장·조회는 그대로다(C2). 이 모듈은 텔레그램 도달 여부만 정하고,
차단 사유를 함께 반환해 조용히 사라지지 않게 한다(C3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.notify.paper_events import TELEGRAM_SENDABLE_KINDS

# 강등된 신호 — 관측은 계속하되 푸시하지 않는다.
#
# `whale_entry`: 미검증 관측이고 사후 채점 표본이 부족하다(실측 2026-08-08: 승률 20.0%,
# N=5, 누적 -3.00R). 게다가 배치 identity 에 체결 ID·건수가 들어가 state_key 가 매번 새로
# 생성되므로 쿨다운이 구조적으로 무력화돼 상한이 없었다 — 같은 지갑에서 6건이 같은 분에
# 도착했다. 빈도를 조이는 접근은 선행 WO 에서 이미 실패했으므로 발송 경로에서 뺀다(C1).
DEMOTED_RULES: frozenset[str] = frozenset({"whale_entry"})

DEMOTION_REASON = "미검증 신호는 푸시 대상이 아님 (WO-FCE-WHALE-ALERT-DEMOTE-01)"
UNREGISTERED_REASON = "발송 레지스트리 미등록 rule_id — 기본 차단 (등록 전까지 푸시 불가)"

# 푸시가 허용된 `rule_id`.
#
# ⚠️ 이 목록은 **현행 동작을 보존한다.** WO 문안은 C7 의 페이퍼 이벤트 허용 목록
# (opened/closed/생존·사망/일요약)을 그대로 쓰라고 했으나, 그것을 `rule_id` 공간에 적용하면
# 기본 활성 36종 중 약 30종 — `invalidation_breach`(무효화 이탈), `liq_proximity`(청산 접근),
# `mdd_limit_critical` 등 critical 포지션 경보 포함 — 이 전부 차단된다. 이 WO 의 목표는
# 고래 스팸 차단이지 알림 정책 전면 개편이 아니므로 고래만 강등하고 나머지는 유지한다.
# 범위 확대는 사용자 결정 사항이며 별도 WO 다.
PUSH_ALLOWED_RULES: frozenset[str] = frozenset(
    {
        # 포지션 위험·상태
        "trigger_near",
        "invalidation_breach",
        "take_profit_hit",
        "status_worsened",
        "health_drop",
        "liq_proximity",
        "liq_unknown_high_lev",
        "position_opened",
        "position_closed",
        "position_structure_event",
        "verdict_changed",
        "stance_flipped",
        "evidence_insufficient",
        "periodic_pulse",
        # 구조·시장 관측
        "wyckoff_event",
        "full_alignment",
        "flow_divergence",
        "funding_extreme",
        "oi_divergence",
        "liq_cluster_near",
        "universe_discovery",
        # 셋업·진입 의도
        "setup_near",
        "setup_triggered",
        "setup_invalidated",
        "intent_approaching",
        "intent_zone_entered",
        "intent_zone_entered_partial",
        "intent_invalidated",
        # 계좌 한도
        "mdd_limit_warn",
        "mdd_limit_critical",
        # 생존·사망·복구 — C7 이 명시적으로 허용하며 뮤트도 관통한다(C5: 이 경로 무영향).
        "data_stall",
        "engine_liveness",
        "job_backoff_stuck",
        "infra_capacity",
        "process_restarted",
    }
)


@dataclass(frozen=True)
class PushDecision:
    """발송 허용 여부와 **사유**. 사유 없는 차단은 침묵이다(C3)."""

    allowed: bool
    reason: str
    rule_id: str

    @property
    def demoted(self) -> bool:
        return self.reason == DEMOTION_REASON


def evaluate_rule(rule_id: str) -> PushDecision:
    """`AlertCandidate` 경로의 관문. 모든 발송이 여기를 지나야 한다."""
    if rule_id in DEMOTED_RULES:
        return PushDecision(allowed=False, reason=DEMOTION_REASON, rule_id=rule_id)
    if rule_id in PUSH_ALLOWED_RULES:
        return PushDecision(allowed=True, reason="", rule_id=rule_id)
    return PushDecision(allowed=False, reason=UNREGISTERED_REASON, rule_id=rule_id)


def evaluate_event(event: dict[str, Any]) -> PushDecision:
    """페이퍼 트랙 이벤트(`kind`) 경로의 관문. 같은 모듈이 판정해 정책이 갈라지지 않게 한다."""
    kind = str(event.get("kind") or "")
    if kind in TELEGRAM_SENDABLE_KINDS:
        return PushDecision(allowed=True, reason="", rule_id=kind)
    return PushDecision(allowed=False, reason=f"페이퍼 이벤트 화이트리스트 밖 kind={kind or '없음'}", rule_id=kind)


def registry_snapshot() -> dict[str, Any]:
    """관문 상태 — "무엇이 왜 안 오는가"를 진단 표면에서 조회 가능하게 한다(C3)."""
    return {
        "principle": "관문은 하나다. 레지스트리에 없는 rule_id 는 기본 차단된다.",
        "allowed_rule_count": len(PUSH_ALLOWED_RULES),
        "allowed_rules": sorted(PUSH_ALLOWED_RULES),
        "demoted_rules": sorted(DEMOTED_RULES),
        "demotion_reason": DEMOTION_REASON,
        "paper_event_kinds": sorted(TELEGRAM_SENDABLE_KINDS),
        "note": "강등은 발송 경로만이다. 수집·저장·조회는 그대로 유지된다(C2).",
    }


__all__ = [
    "DEMOTED_RULES",
    "DEMOTION_REASON",
    "PUSH_ALLOWED_RULES",
    "UNREGISTERED_REASON",
    "PushDecision",
    "evaluate_event",
    "evaluate_rule",
    "registry_snapshot",
]
