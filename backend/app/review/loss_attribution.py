"""손실 패인 분류 (WO-FCE-ENTRY-THROUGHPUT-01 작업 3).

judgment ledger 는 T+1/T+5/T+20 결과를 채점하지만 **왜 졌는지**는 분류하지 않았다.
"졌다"는 알아도 패인을 모르면 개선 입력이 되지 않는다.

> **패인 분포는 관측이지 자동 개선 입력이 아니다.**
> 이 분포를 근거로 파라미터를 바꾸는 것은 별도 WO에서 한 번에 하나씩 검증한다.
> 이 모듈은 분류만 하고 어떤 임계값도 건드리지 않는다.

신규 집계 테이블을 만들지 않는다 — 태그는 기존 원장(`paper_trades.loss_tags`)에 싣는다.
"""

from __future__ import annotations

from typing import Any, Literal

LossReason = Literal[
    "invalidation_correct",
    "invalidation_too_tight",
    "entry_too_late",
    "structure_misread",
    "gap_loss",
    "time_stop",
]

# 사용자가 읽을 라벨 — 주간 자체평가 리포트에 그대로 쓴다.
LOSS_REASON_LABELS: dict[str, str] = {
    "invalidation_correct": "무효화선이 옳았음 (불가피)",
    "invalidation_too_tight": "손절이 너무 좁음 (직후 복귀)",
    "entry_too_late": "진입이 늦음 (즉시 역행)",
    "structure_misread": "구조 오독 (국면 재해석)",
    "gap_loss": "갭 손실 (무효화선보다 나쁘게 체결)",
    "time_stop": "시간 손절 (방향은 유지, 진행 없음)",
}

# 손절 직후 원래 방향으로 복귀했다고 볼 되돌림 비율(진입~무효화 거리 대비).
# 관측용 판정 기준이며 진입 게이트가 아니다 — 이 값은 어떤 게이트에도 쓰이지 않는다.
_RECOVERY_R_THRESHOLD = 0.5
# "즉시" 역행으로 볼 보유 봉 수.
_IMMEDIATE_ADVERSE_BARS = 1


def classify_loss(trade: dict[str, Any], *, post_exit_extreme: float | None = None) -> str | None:
    """청산 1건의 패인을 분류한다. 손실이 아니면 None.

    ``post_exit_extreme`` 은 청산 이후 관측된 원래 방향의 최대 도달가다(롱이면 최고가).
    없으면 복귀 여부를 판정하지 않는다 — 모르는 것을 단정하지 않기 위해서다.
    """
    net_pnl = _float(trade.get("net_pnl_usdt"))
    if net_pnl is None or net_pnl >= 0:
        return None

    exit_reason = str(trade.get("exit_reason") or "")
    # 갭 체결은 다른 무엇보다 먼저다 — 체결 자체가 무효화선보다 나빴다는 사실이 패인이다.
    if exit_reason == "gap_loss" or bool(trade.get("gapped")):
        return "gap_loss"
    if exit_reason in {"time_stop", "time_decay"}:
        return "time_stop"

    entry = _float(trade.get("entry_price"))
    invalidation = _float(trade.get("invalidation_price"))
    direction = str(trade.get("direction") or "long")
    holding_bars = int(trade.get("holding_bars") or 0)

    # 진입 직후 즉시 역행이면 구조가 아니라 타이밍 문제로 본다.
    if holding_bars <= _IMMEDIATE_ADVERSE_BARS:
        return "entry_too_late"

    if entry is not None and invalidation is not None and post_exit_extreme is not None:
        risk = abs(entry - invalidation)
        if risk > 0:
            recovery = (post_exit_extreme - entry) / risk if direction == "long" else (entry - post_exit_extreme) / risk
            # 손절 뒤 원래 방향으로 충분히 되돌아왔다면 손절폭이 좁았던 것이다.
            if recovery >= _RECOVERY_R_THRESHOLD:
                return "invalidation_too_tight"

    # 진입 시 판정한 국면이 사후에 뒤집혔다면 구조 오독.
    if _stance_flipped(trade):
        return "structure_misread"
    return "invalidation_correct"


def _stance_flipped(trade: dict[str, Any]) -> bool:
    snapshot = trade.get("stance_snapshot")
    if not isinstance(snapshot, dict):
        return False
    entry_state = str(snapshot.get("state") or snapshot.get("stance_state") or "")
    exit_state = str(snapshot.get("exit_state") or snapshot.get("final_state") or "")
    if not entry_state or not exit_state:
        return False
    return entry_state != exit_state


def attribution_summary(tags: list[str | None]) -> dict[str, Any]:
    """패인 분포 집계. 표본 0이면 숫자를 만들지 않는다(선행 WO 의 표본 규칙과 동일)."""
    present = [tag for tag in tags if tag]
    total = len(present)
    if total == 0:
        return {"total": 0, "reasons": [], "top_reason": None}
    counts: dict[str, int] = {}
    for tag in present:
        counts[tag] = counts.get(tag, 0) + 1
    reasons = [
        {
            "reason": reason,
            "label": LOSS_REASON_LABELS.get(reason, reason),
            "count": count,
            "share_pct": round(count / total * 100.0, 1),
        }
        for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {"total": total, "reasons": reasons, "top_reason": reasons[0]["reason"]}


def format_attribution_lines(summary: dict[str, Any]) -> list[str]:
    """주간 자체평가 리포트 라인.

    개선 제안을 **후보**로만 적는다 — 자동 적용하지 않는다는 원칙을 문장으로도 남긴다.
    """
    total = int(summary.get("total") or 0)
    if total == 0:
        return ["<b>🔎 손실 자체평가</b>", "· 분류 대상 손실 없음"]
    lines = ["<b>🔎 손실 자체평가</b>", f"· 손실 {total}건 분류"]
    for row in summary.get("reasons") or []:
        lines.append(f"· {row['label']} {row['count']}건 ({row['share_pct']:.0f}%)")
    top = summary.get("top_reason")
    if top == "invalidation_too_tight":
        lines.append("→ 손절폭 재검토 <b>후보</b> (자동 적용 아님 — 별도 WO에서 단건 검증)")
    elif top == "entry_too_late":
        lines.append("→ 진입 타이밍 재검토 <b>후보</b> (자동 적용 아님)")
    elif top == "gap_loss":
        lines.append("→ 갭 리스크 재검토 <b>후보</b> (자동 적용 아님)")
    lines.append("<i>패인 분포는 관측이며 파라미터 자동 변경 입력이 아닙니다.</i>")
    return lines


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
