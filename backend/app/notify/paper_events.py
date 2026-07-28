"""Shared paper-track event contract (WO-FCE-PAPER-OBSERVABILITY-01).

침묵 금지 원칙: 3개 페이퍼 트랙(crypto/stock/poly)의 모든 미발생은 사유와 함께
관측 가능해야 한다. 이 모듈은 stock/poly 트랙이 crypto 트랙과 같은 알림 경로
(`_send_paper_events` → `format_paper_event`)를 태우기 위한 최소 공통 계약을 정의한다.

공통 이벤트 계약: ``{track, kind, symbol, ts, detail}``
  - track: "crypto" | "stock" | "poly"
  - kind:  "opened" | "closed" | "rejected_summary" | "skipped" | "error"
  - symbol: 종목/마켓 식별자 (집계 이벤트는 "*" 등 요약 심볼)
  - ts:    ISO8601 UTC
  - detail: 트랙별 부가 정보 (포맷터가 사람이 읽는 문장으로 렌더)

crypto 트랙은 기존 ``{kind, reason, trade}`` 계약을 유지한다(회귀 금지, C3). 포맷터는
``track`` 키 유무로 경로를 분기하므로 crypto 이벤트는 이 모듈을 거치지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# 스팸 방지를 위해 억제 대상이 되는 "미발생" kind — 상태 전이 시 1회 + 일 1회 리마인더만.
#
# WO-FCE-PAPER-ENTRY-REALITY-01 (D2): rejected_summary 를 여기 포함한다.
# 선행 WO는 "개별 발송 대신 집계 1건"만 규정하고 **발송 빈도**를 명시하지 않았다.
# 그 결과 폴리는 60초 폴링마다 집계 1건 = 일 1,440건을 발송했다(2026-07-28 실측).
# 거부는 "무엇이 일어났는가"가 아니라 "무엇이 안 일어났는가"이므로 알림 대상이 아니라
# 조회 대상이다(C3). 최다 거부 게이트가 **바뀔 때만** 1건 발송하고, 나머지는 일 1회
# 요약과 /api/system/paper/diagnosis 로 본다.
SUPPRESSIBLE_KINDS = frozenset({"skipped", "rejected_summary"})

VALID_KINDS = frozenset({"opened", "closed", "rejected_summary", "skipped", "error"})


def track_event(
    track: str,
    kind: str,
    symbol: str,
    *,
    detail: dict[str, Any] | None = None,
    ts: datetime | None = None,
) -> dict[str, Any]:
    """Build a track-tagged paper event conforming to the common contract."""
    moment = ts or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return {
        "track": track,
        "kind": kind,
        "symbol": symbol,
        "ts": moment.isoformat(),
        "detail": dict(detail or {}),
    }


def suppression_key(event: dict[str, Any]) -> str:
    """Stable key identifying a suppressible event so repeats collapse to one line.

    같은 트랙·같은 사유의 연속 이벤트는 하나의 상태로 취급한다. 사유가 바뀌면
    새 키가 되어 **상태 전이**로 다시 1회 발송된다.

    - skipped: detail.reason 이 상태다.
    - rejected_summary: **detail.top_reject_gate** 가 상태다(WO-FCE-PAPER-ENTRY-REALITY-01).
      거부 건수(40→41→42)는 매 틱 흔들리므로 상태로 쓰면 안 된다 — 건수를 키에 넣으면
      매번 새 상태가 되어 스팸이 그대로 유지된다. 최다 거부 게이트가 바뀔 때만 알린다.
    """
    track = str(event.get("track") or "")
    kind = str(event.get("kind") or "")
    detail = event.get("detail") or {}
    if kind == "rejected_summary":
        state = str(detail.get("top_reject_gate") or "none")
    else:
        state = str(detail.get("reason") or "")
    return f"{track}:{kind}:{state}"
