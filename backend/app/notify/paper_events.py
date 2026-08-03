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

# ── 텔레그램 발송 화이트리스트 (WO-FCE-ALERT-WHITELIST-02 작업 1) ──────────────
#
# **여기 없는 kind 는 텔레그램에 도달하지 않는다.** 억제(빈도 제한)가 아니라 원천 제외다.
#
# 왜 억제가 아니라 화이트리스트인가:
# 선행 WO들이 "집계 1건" → "전이 시에만"으로 빈도를 계속 좁혀 왔지만, 유니버스가 오염되면
# 최다 거부 게이트가 계속 뒤바뀌어(unsupported_crypto_question → 마켓 한도 →
# resolution_time_invalid) **전이 자체가 반복 발생**해 사실상 스팸이 됐다(2026-08-01 실측:
# 09:01 거부 10 · 09:07 거부 73 · 10:09 거부 39). 빈도를 조이는 접근은 오염된 입력 앞에서
# 반복 실패한다. 그래서 "무엇이 안 일어났는가"는 **발송 경로에 아예 넣지 않는다**.
#
# 거부·미발생은 조회 대상이다: /api/system/paper/diagnosis 와 일 1회 성과 요약.
TELEGRAM_SENDABLE_KINDS = frozenset({"opened", "closed"})

# 억제 정책이 남아 있는 kind — 화이트리스트를 통과한 뒤 추가로 빈도를 제한할 때 쓴다.
# 현재 화이트리스트가 opened/closed 뿐이라 실질 대상이 없지만, 계약은 유지한다.
SUPPRESSIBLE_KINDS: frozenset[str] = frozenset()

VALID_KINDS = frozenset({"opened", "closed", "rejected_summary", "skipped", "error"})


def is_telegram_sendable(event: dict[str, Any]) -> bool:
    """이 이벤트를 텔레그램으로 보낼 수 있는가.

    거부(`rejected_summary`)·미발생(`skipped`)·오류(`error`)는 **어떤 형태로도** False다.
    집계든 전이든 예외 없다(C1). 생존·사망·복구 알림은 이 경로가 아니라 `notify/rules.py`
    계열이 담당하므로 영향받지 않는다(C4).
    """
    return str(event.get("kind") or "") in TELEGRAM_SENDABLE_KINDS


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
