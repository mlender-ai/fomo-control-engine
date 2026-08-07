"""WO-FCE-VALIDATION-VERDICT-01 Phase 1-3 — 판정 발행과 전이 감시.

## 왜 이 모듈이 생겼나

측정 장치는 완성됐는데(`sample_viability.py`) **그 장치가 산출한 숫자가 사용자에게
발행되지 않았다.** 대시보드를 열어야만 보이는 판정은 안 보이는 판정이다.

두 가지를 한다:

1. **주간 발행** — 트랙별 판정과 D+28 예상 표본을 주간 리포트에 상시 포함한다.
2. **전이 알림** — 판정이 바뀌면(예: `VIABLE` → `SLOW`) 즉시 1건 알린다.

전이는 **나빠질 때만이 아니라 좋아질 때도** 알린다. 좋아진 것을 조용히 넘기면
"언제부터 좋아졌는가"를 사후에 알 수 없다.

## 스팸 금지

같은 판정이 유지되는 동안에는 아무것도 보내지 않는다. 전이가 있을 때만 1건이다.
주간 리포트가 상시 보고를 담당하므로 이 알림은 **변화**만 담당한다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from app.validation import sample_viability

# 판정을 사람이 읽는 말로. 영문 상수를 그대로 알림에 내보내면 무슨 뜻인지 매번 찾아봐야 한다.
VERDICT_LABELS: dict[str, str] = {
    sample_viability.VERDICT_VIABLE: "완주 가능",
    sample_viability.VERDICT_SLOW: "창 밖 도달",
    sample_viability.VERDICT_STRUCTURALLY_BLOCKED: "구조적 차단",
    sample_viability.VERDICT_INSUFFICIENT_DATA: "판정 불가",
}

# 판정별 대응 규칙 — **결과를 보기 전에 고정한다**(WO §1-2).
# 판정이 나온 뒤에 규칙을 만들면 결과에 맞춰 규칙을 고르게 된다.
VERDICT_ACTIONS: dict[str, str] = {
    sample_viability.VERDICT_VIABLE: "그대로 진행 · 개입 없음",
    sample_viability.VERDICT_SLOW: "관측 유지 + 주간 재판정 · 기준 완화 금지",
    sample_viability.VERDICT_STRUCTURALLY_BLOCKED: "선정 기준 개별 처리 (기회 표면 확대 / 창 연장 / 트랙 유보)",
    sample_viability.VERDICT_INSUFFICIENT_DATA: "유효일 확보가 선행 · 다른 조치 금지",
}

# 나빠지는 방향의 전이. 이 방향은 알림 문구에 경고를 붙인다.
_SEVERITY_ORDER = [
    sample_viability.VERDICT_VIABLE,
    sample_viability.VERDICT_SLOW,
    sample_viability.VERDICT_INSUFFICIENT_DATA,
    sample_viability.VERDICT_STRUCTURALLY_BLOCKED,
]


def current_verdicts(connection: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """트랙별 판정 + D+28 예상 표본. 발행과 전이 감지가 같은 값을 본다."""
    report = sample_viability.sample_viability_report(connection, now=now)
    return {
        track: {
            "label": row["label"],
            "verdict": row["verdict"],
            "verdict_reason": row["verdict_reason"],
            "effective_days": row["effective_days"],
            "effective_days_target": row["effective_days_target"],
            "scored_samples": row["scored_samples"],
            "scored_samples_target": row["scored_samples_target"],
            "projected_samples_at_target": row["projected_samples_at_target"],
            "statement": row["statement"],
        }
        for track, row in report["tracks"].items()
    }


def _worsened(previous: str, current: str) -> bool:
    try:
        return _SEVERITY_ORDER.index(current) > _SEVERITY_ORDER.index(previous)
    except ValueError:
        return False


def detect_transitions(previous: dict[str, str], current: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """판정이 바뀐 트랙만 돌려준다.

    **첫 관측은 전이가 아니다.** 이전 값이 없는 상태를 전이로 치면 배선 직후 4트랙이
    한꺼번에 알림으로 나간다 — 그건 변화가 아니라 초기화다.
    """
    transitions: list[dict[str, Any]] = []
    for track, row in current.items():
        before = previous.get(track)
        after = str(row["verdict"])
        if before is None or before == after:
            continue
        transitions.append(
            {
                "track": track,
                "label": row["label"],
                "from": before,
                "to": after,
                "worsened": _worsened(before, after),
                "reason": row["verdict_reason"],
                "projected_samples_at_target": row["projected_samples_at_target"],
            }
        )
    return transitions


def format_transition_message(transitions: list[dict[str, Any]]) -> str:
    """전이 알림 1건. 전이가 없으면 호출부가 부르지 않는다."""
    lines = ["<b>🔄 검증 완주 판정 변경</b>"]
    for item in transitions:
        arrow = "⚠️" if item["worsened"] else "✅"
        before = VERDICT_LABELS.get(str(item["from"]), str(item["from"]))
        after = VERDICT_LABELS.get(str(item["to"]), str(item["to"]))
        lines.append("")
        lines.append(f"{arrow} <b>{item['label']}</b> {before} → <b>{after}</b>")
        lines.append(f"· {item['reason']}")
        lines.append(f"· 대응: {VERDICT_ACTIONS.get(str(item['to']), '—')}")
    lines.append("")
    lines.append("<i>판정별 대응 규칙은 결과를 보기 전에 고정됐습니다. 기준을 결과에 맞춰 바꾸지 않습니다.</i>")
    return "\n".join(lines)


def format_weekly_verdict_lines(verdicts: dict[str, dict[str, Any]], *, target_samples: int = sample_viability.TARGET_SAMPLES) -> list[str]:
    """주간 리포트에 붙는 판정 블록 (§1-3).

    캘린더 일수로 진행을 말하지 않는다 — 유효 관측일과 채점 가능 표본으로만 말한다.
    """
    if not verdicts:
        return []
    lines = ["", "<b>🎯 완주 가능성 판정</b>"]
    for row in verdicts.values():
        label = VERDICT_LABELS.get(str(row["verdict"]), str(row["verdict"]))
        marker = "⚠️" if row["verdict"] in {sample_viability.VERDICT_STRUCTURALLY_BLOCKED, sample_viability.VERDICT_SLOW} else "·"
        lines.append(
            f"{marker} <b>{row['label']}</b> {label} — 유효일 {row['effective_days']}/{row['effective_days_target']}"
            f" · 표본 {row['scored_samples']}/{row['scored_samples_target']}"
            f" · D+{row['effective_days_target']} 예상 {row['projected_samples_at_target']}건"
        )
        if row["verdict"] != sample_viability.VERDICT_VIABLE:
            lines.append(f"  └ 대응: {VERDICT_ACTIONS.get(str(row['verdict']), '—')}")
    lines.append(f"<i>N≥{target_samples} 미만은 표본 부족입니다. 승률·수익률을 계산하지 않습니다.</i>")
    return lines


__all__ = [
    "VERDICT_ACTIONS",
    "VERDICT_LABELS",
    "current_verdicts",
    "detect_transitions",
    "format_transition_message",
    "format_weekly_verdict_lines",
]
