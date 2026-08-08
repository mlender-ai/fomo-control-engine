"""WO-FCE-SAMPLE-RATE-01 Phase 5 — 사용자 결정 대기 항목.

## 왜 코드에 두는가

코드로 풀 수 없는 결정이 문서 안에만 있으면 잊힌다. 잊힌 결정은 없는 결정과 같고,
그 사이 관측은 계속 깎인다(호스트 절전이 정확히 그 예다).

그래서 미결 항목을 **대시보드와 주간 리포트에 상시 노출**한다. 자동으로 결정하지 않는다 —
비대칭 자율 원칙상 완화·해제·승인 방향은 사람만 할 수 있다. 이 모듈은 **잊히지 않게**만 한다.

## 항목이 닫히는 조건

각 항목은 `resolved_when` 에 닫히는 조건을 명시한다. 조건이 코드로 확인 가능한 것은
자동으로 닫히고(예: 서명 플래그), 그렇지 않은 것은 사람이 문서를 고칠 때 닫힌다.
**"아마 했을 것"으로 닫지 않는다.**
"""

from __future__ import annotations

from typing import Any

BLOCKING = "blocking"  # 이것이 없으면 검증·전환이 진행되지 않는다
IMPACTING = "impacting"  # 진행은 되지만 표본·품질이 계속 깎인다


def pending_decisions(*, gate_approved: bool, sleep_guard: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """지금 사람을 기다리는 결정들.

    `gate_approved` 는 코드로 확인 가능하므로 자동으로 닫힌다. 나머지는 문서 상태를
    코드가 알 수 없어 상시 노출하며, 사람이 문서를 고칠 때 이 목록에서 뺀다.
    """
    guard = sleep_guard or {}
    items: list[dict[str, Any]] = []

    if not gate_approved:
        items.append(
            {
                "id": "live_trading_gate_signature",
                "severity": BLOCKING,
                "title": "자동매매 게이트 6축 임계 확정 + 서명",
                "detail": "미승인 상태에서는 측정 축을 전부 통과해도 해제되지 않는다(축 6).",
                "document": "docs/validation/LIVE_TRADING_GATE.md §5",
                "resolved_when": "서명란을 채우고 live_trading_gate.GATE_APPROVED 를 True 로 바꾼다",
            }
        )

    # 절전은 코드가 상태를 읽을 수 있다 — 보호 중이면 굳이 결정 대기로 올리지 않는다.
    if guard.get("available") is not True or guard.get("guarded") is not True:
        items.append(
            {
                "id": "host_persistence_choice",
                "severity": IMPACTING,
                "title": "호스트 지속성 안 선택 (A/B/C/D)",
                "detail": "맥 절전이 US 정규장 후반을 계속 삭제한다. 절전 제거 시 US 검증이 13~27 거래일 단축된다.",
                "document": "docs/validation/HOST_PERSISTENCE.md §3",
                "resolved_when": "안을 선택해 적용하고 scripts/local/check-sleep-guard.sh 가 보호 중을 반환한다",
            }
        )

    items.append(
        {
            "id": "statistical_method_adoption",
            "severity": BLOCKING,
            "title": "검증 통계 방법 채택 여부",
            "detail": (
                "현재 방법(두 95% CI 비겹침)은 15%p 우위를 80% 검정력으로 잡는 데 팔당 283건이 필요하다. "
                "목표 30건으로는 40%p 우위만 탐지된다 — 30건과 현재 방법은 서로 맞지 않는다."
            ),
            "document": "docs/validation/REQUIRED_SAMPLE.md",
            "resolved_when": "방법을 택하고 COMPLETION_DEFINITION.md 의 표본 기준을 그 방법 기준으로 갱신한다",
        }
    )

    # WO-FCE-DIRECTIONAL-INTEGRITY-01 §0 — Phase 0 외 어떤 작업도 이 3건 전에 착수하지 않는다.
    # 코드가 문서의 확정 여부를 알 수 없으므로 상시 노출하고, 사람이 문서를 고칠 때 뺀다.
    items.append(
        {
            "id": "validation_window_contamination",
            "severity": BLOCKING,
            "title": "검증 창 오염 처리 (A/B/C)",
            "detail": (
                "방향 판정 로직을 바꾸면 검증 창 전반부(구 로직)와 후반부(신 로직) 표본이 섞인다. "
                "혼합 표본으로는 어떤 판정도 나오지 않는다. C안(계측·표시만 먼저)이 기본 권고다."
            ),
            "document": "docs/validation/DIRECTIONAL_COVERAGE.md §4",
            "resolved_when": "안을 택해 COMPLETION_DEFINITION.md 에 검증 창 처리 결과를 기록한다",
        }
    )
    items.append(
        {
            "id": "wyckoff_role",
            "severity": BLOCKING,
            "title": "와이코프의 지위 — 투표자 유지 vs 진입 규칙 승격",
            "detail": "이 결정이 진입 지점(Spring·LPS) 도입 여부의 전제다. 미정이면 정합화는 투표자 전제에서 멈춘다.",
            "document": "docs/WYCKOFF-AUDIT-01.md §5",
            "resolved_when": "지위를 확정해 docs/DirectionalEngine.md 에 기록한다",
        }
    )
    items.append(
        {
            "id": "directional_coverage_thresholds",
            "severity": BLOCKING,
            "title": "커버리지 임계 3종 확정",
            "detail": (
                "최소 판정 가능 엔진 수 · 최대 허용 침묵 가중 비율 · 엔진 수 기준 최소 근거. "
                "현재 커버리지는 판정에 전혀 반영되지 않는다 — 7개가 살아있을 때와 4개만 살아있을 때가 구분되지 않는다."
            ),
            "document": "docs/validation/DIRECTIONAL_COVERAGE.md §2",
            "resolved_when": "재판정 실측 분포를 본 뒤 임계 3종을 문서에 확정 기록한다",
        }
    )
    return items


def pending_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    blocking = [item for item in items if item["severity"] == BLOCKING]
    return {
        "count": len(items),
        "blocking_count": len(blocking),
        "items": items,
        "line": (f"결정 대기 {len(items)}건 (진행 차단 {len(blocking)}건)" if items else "결정 대기 없음"),
    }


def format_pending_lines(items: list[dict[str, Any]]) -> list[str]:
    """주간 리포트 블록. 건수만 세지 않고 **무엇을 기다리는지** 적는다."""
    if not items:
        return ["", "<b>🗳 결정 대기</b>", "· 없음"]
    lines = ["", f"<b>🗳 결정 대기 {len(items)}건</b>"]
    for item in items:
        marker = "⛔" if item["severity"] == BLOCKING else "⚠️"
        lines.append(f"{marker} {item['title']}")
        lines.append(f"  └ {item['document']}")
    lines.append("<i>이 항목들은 코드로 풀 수 없습니다. 결정 전까지 매주 다시 보고합니다.</i>")
    return lines


__all__ = ["BLOCKING", "IMPACTING", "format_pending_lines", "pending_decisions", "pending_summary"]
