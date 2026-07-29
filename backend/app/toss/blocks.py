"""WO-FCE-TOSS-US-STALL-01 — 토스 시장별 차단 상태의 단일 정본.

## 왜 이 모듈이 생겼나 (2026-07-28 사고)

`service._authentication_blocked` 는 **자기잠금 래치(self-locking latch)** 였다.

    401 수신 → 차단 등록 → collect_market 진입 즉시 반환(API 호출 안 함)
             → 성공할 기회가 없음 → 차단이 영원히 유지

해제 경로가 "수집을 성공적으로 마쳤을 때"뿐인데, 차단 때문에 수집 시도 자체가 없었다.
실측: 2026-07-28 13:51 UTC ~ 07-29 10:39 UTC **20.8시간** 동안 KR·US 양쪽 모두
`market-calendar` 를 **단 한 번도 호출하지 않았다**. 그 사이 07-28 미국 정규장 잔여 6시간과
07-29 한국 정규장 6.5시간이 통째로 유실됐다. 잡은 10초마다 "ok" 로 완주했고, 프로세스는
22.7시간 무중단이라 재시작으로도 풀리지 않았다. 결국 사람이 진단 API 를 때려서 풀렸다.

## 고정하는 원칙 (C2 — 자기잠금 금지)

> **모든 차단에는 자동 재시도 경로가 있어야 한다.**
> 재시도하지 않기 때문에 해제될 수 없는 구조는 차단이 아니라 영구 정지다.

차단 3종(auth/edge/maintenance)은 서로 다른 정책을 갖고 있었고 그중 auth 만 영구 래치였다.
여기서 `{status, reason, blocked_until, attempt_count, last_error}` 공통 구조로 통일한다.
백오프는 재시도를 **보장하면서도** 무의미한 반복 호출을 막는다(C4 레이트 리밋).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# 차단 사유별 재시도 백오프(초). 상한에 도달해도 재시도는 **멈추지 않는다**.
_BACKOFF: dict[str, tuple[int, ...]] = {
    # 토큰 만료·일시적 발급 실패가 대부분이라 빠르게 되돌아본다.
    "authentication_failed": (60, 120, 240, 480, 900),
    # IP 미등록이면 사람이 고쳐야 한다 — 그래도 재시도는 유지하되 간격을 길게(무의미한 호출 감소).
    "edge_blocked": (60, 300, 900, 1800, 1800),
    # 토스 점검은 기존 정책(15분)을 유지한다. 이미 TTL 이 있어 자기잠금이 아니었다.
    "maintenance": (900,),
}
DEFAULT_BACKOFF: tuple[int, ...] = (60, 120, 240, 480, 900)


def _now_monotonic() -> float:
    return time.monotonic()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MarketBlock:
    """한 시장의 수집 차단 상태. `blocked_until` 이 지나면 **반드시** 실호출로 재시도한다."""

    market: str
    status: str
    reason: str
    blocked_until: float
    attempt_count: int
    first_blocked_at: str
    last_error: dict[str, Any] = field(default_factory=dict)

    def retry_in_seconds(self, *, now: float | None = None) -> int:
        return max(0, int(round(self.blocked_until - (now if now is not None else _now_monotonic()))))

    def blocked_seconds(self) -> int:
        try:
            since = datetime.fromisoformat(self.first_blocked_at)
        except ValueError:
            return 0
        return max(0, int((datetime.now(timezone.utc) - since).total_seconds()))

    def as_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "status": self.status,
            "reason": self.reason,
            "attempt_count": self.attempt_count,
            "retry_in_seconds": self.retry_in_seconds(),
            "first_blocked_at": self.first_blocked_at,
            "blocked_seconds": self.blocked_seconds(),
            "last_error": self.last_error,
        }


_blocks: dict[str, MarketBlock] = {}
# 조기 반환은 예외를 던지지 않아 잡 하트비트에 흔적이 없다(D4). 마지막 결과를 여기 남긴다.
_outcomes: dict[str, dict[str, Any]] = {}


def backoff_seconds(status: str, attempt: int) -> int:
    schedule = _BACKOFF.get(status, DEFAULT_BACKOFF)
    return int(schedule[min(max(attempt, 1) - 1, len(schedule) - 1)])


def register_block(market: str, status: str, reason: str, detail: dict[str, Any] | None = None) -> MarketBlock:
    """차단을 등록하거나 연장한다. 같은 사유가 반복되면 백오프가 늘어난다.

    사유가 **바뀌면** 시도 횟수를 초기화한다 — 다른 고장이면 다시 빠르게 확인해야 한다.
    """
    previous = _blocks.get(market)
    attempt = previous.attempt_count + 1 if previous is not None and previous.status == status else 1
    first_blocked_at = previous.first_blocked_at if previous is not None and previous.status == status else _now_iso()
    block = MarketBlock(
        market=market,
        status=status,
        reason=reason,
        blocked_until=_now_monotonic() + backoff_seconds(status, attempt),
        attempt_count=attempt,
        first_blocked_at=first_blocked_at,
        last_error=dict(detail or {}),
    )
    _blocks[market] = block
    return block


def active_block(market: str) -> MarketBlock | None:
    """지금 이 순간 호출을 막아야 하는 차단. 재시도 시각이 지났으면 **None** — 실호출로 넘어간다."""
    block = _blocks.get(market)
    if block is None:
        return None
    return block if block.blocked_until > _now_monotonic() else None


def pending_block(market: str) -> MarketBlock | None:
    """재시도 시각이 지나 해제를 시도해야 하는 차단(있으면 토큰 폐기 등 사전 조치용)."""
    block = _blocks.get(market)
    if block is None:
        return None
    return block if block.blocked_until <= _now_monotonic() else None


def clear_block(market: str) -> MarketBlock | None:
    """수집 성공 시 해제. 반환값이 있으면 '복구'이므로 호출부가 1회 알린다."""
    return _blocks.pop(market, None)


def clear_all() -> None:
    _blocks.clear()


def record_outcome(
    market: str,
    status: str,
    *,
    reason: str | None = None,
    block: MarketBlock | None = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """C3: 산출물 없는 반환은 사유와 함께 관측 가능해야 한다."""
    outcome: dict[str, Any] = {
        "market": market,
        "status": status,
        "reason": reason,
        "observed_at": _now_iso(),
        "blocked": block is not None,
    }
    if block is not None:
        outcome.update(
            {
                "retry_in_seconds": block.retry_in_seconds(),
                "attempt_count": block.attempt_count,
                "blocked_since": block.first_blocked_at,
                "blocked_seconds": block.blocked_seconds(),
            }
        )
    if detail:
        outcome["detail"] = detail
    _outcomes[market] = outcome
    return outcome


def last_outcome(market: str) -> dict[str, Any] | None:
    return _outcomes.get(market)


def outcomes_snapshot() -> dict[str, dict[str, Any]]:
    return {market: dict(outcome) for market, outcome in _outcomes.items()}


def blocks_snapshot() -> dict[str, dict[str, Any]]:
    return {market: block.as_dict() for market, block in _blocks.items()}


def stall_reasons() -> dict[str, str]:
    """생존 감시가 "왜 멈췄는지"를 함께 보고하도록 시장별 한 줄 사유를 만든다(작업 3).

    감시가 정지를 알렸는데 이유를 몰라 사람이 코드를 뒤져야 했던 게 이번 사고의 절반이다.
    """
    reasons: dict[str, str] = {}
    for market, block in _blocks.items():
        minutes = block.blocked_seconds() // 60
        reasons[market] = f"{block.status} {minutes}분 지속 (재시도 {block.retry_in_seconds()}초 후 · {block.attempt_count}회차)"
    for market, outcome in _outcomes.items():
        if market in reasons:
            continue
        status = str(outcome.get("status") or "")
        if status in {"observed", "open"}:
            continue
        reasons[market] = f"{status}" + (f" · {outcome['reason']}" if outcome.get("reason") else "")
    return reasons
