"""포지션 관측 공백 — **"없다"와 "못 봤다"를 구분한다.**

## 사건 (2026-09-09)

일일 요약이 열린 포지션 **위에서** "열린 포지션이 없습니다."를 찍었다. 그 시각 사용자
계좌에는 ZECUSDT 숏이 열려 있었다(미실현 -86.75 USDT).

근거는 이미 페이로드에 실려 있었다. 아무도 읽지 않았을 뿐이다:

| 근거 | 만드는 곳 | 읽던 곳 |
| --- | --- | --- |
| `sync_failed` | `WorkerManager._sync_positions` | **없음** |
| `sync_stale` · `sync_stale_note` | `WorkerManager._alert_payload` | **없음** |
| `positions_unavailable` | `sync_live_positions` | 정기 펄스만 |
| `open_count` | `sync_live_positions` | **없음** |
| 거래소 `status`(`error`·`permission_error`·`not_configured`) | `_sync_bitget_positions` | **없음** |

렌더러는 `positions` 가 비었다는 사실 **하나만** 보고 "없습니다"라고 단정했다.

빈 목록은 최소 네 가지를 뜻할 수 있다:

1. 정말 없다 — 동기화 성공 + 신선 + 원장 0건
2. 동기화 주기가 죽었다(`sync_failed`) — 직전 성공 결과가 통째로 지워졌다
3. 결과가 낡았다(`sync_stale`) — 그 시점 기준이지 지금이 아니다
4. 원장엔 있는데 분석이 실패해 빠졌다(`positions_unavailable` · `open_count` > 렌더 수)

2·3·4를 1로 표시하면 **침묵이 정상으로 위장한다.** 이 저장소가 이미 두 번 적은 원칙이고
(`ENGINE-LIVENESS-01` D1, `ALERT-SILENCE-01` 3-1), 정기 펄스는 그중 4번만 고쳐 뒀다.
그래서 판정을 여기 한 곳에서 만들고 일일 요약·`/positions`·정기 펄스가 같은 문장을 쓴다.

**게이트가 아니라 문장이다.** 이 모듈은 알림을 막지 않는다 — 막으면 그것이 곧 침묵이다.
"""

from __future__ import annotations

from html import escape
from typing import Any, Mapping

# 거래소 동기화 상태 라벨. `ok` 와 `demo` 만 "관측이 성립했다"로 본다.
PROVIDER_STATUS_LABELS = {
    "error": "거래소 API 오류",
    "permission_error": "거래소 API 권한 오류",
    "not_configured": "거래소 API 키 미설정",
    "not_active": "거래소 어댑터 비활성",
}
HEALTHY_PROVIDER_STATUSES = frozenset({"ok", "demo"})
_UNAVAILABLE_ROWS_SHOWN = 5


def observation_gap_lines(payload: Mapping[str, Any], *, rendered: int | None = None) -> list[str]:
    """빈 목록을 "없음"으로 읽으면 **안 되는** 이유들. 없으면 빈 리스트.

    `rendered` 는 실제로 화면에 나간 포지션 수다. `open_count`(원장 기준)보다 적으면
    그 차이가 곧 관측 공백이다 — 사유가 남지 않은 공백도 공백이라고 적는다.
    """
    lines: list[str] = []
    if payload.get("sync_failed"):
        note = str(payload.get("sync_failed_note") or "직전 동기화 주기가 실패했다").strip()
        lines.append(f"⚠ 포지션 동기화 실패 — {escape(note)}")
    if payload.get("sync_stale"):
        note = str(payload.get("sync_stale_note") or "동기화 결과가 낡았다").strip()
        lines.append(f"⚠ 동기화 낡음 — {escape(note)}")
    status = str(payload.get("status") or "").strip().lower()
    if status and status not in HEALTHY_PROVIDER_STATUSES:
        label = PROVIDER_STATUS_LABELS.get(status, f"거래소 동기화 상태 {status}")
        error = str(payload.get("error") or "").strip()
        lines.append(f"⚠ {escape(label)}{f' — {escape(error[:120])}' if error else ''}")
    unavailable = [row for row in (payload.get("positions_unavailable") or []) if isinstance(row, dict)]
    if unavailable:
        lines.append(f"⚠ 관측 불가 {len(unavailable)}건 — 포지션은 열려 있다(분석 조회 실패)")
        for row in unavailable[:_UNAVAILABLE_ROWS_SHOWN]:
            symbol = escape(str(row.get("symbol") or "-"))
            reason = escape(str(row.get("reason") or "사유 미상")[:80])
            lines.append(f"  · <b>{symbol}</b> — {reason}")
    open_count = _int_or_none(payload.get("open_count"))
    if open_count is not None and rendered is not None:
        # 사유가 붙은 공백(`positions_unavailable`)은 이미 위에서 셌다. 남는 차이는
        # **설명되지 않은** 공백이다 — 그것이 가장 위험하므로 따로 적는다.
        unexplained = open_count - rendered - len(unavailable)
        if unexplained > 0:
            lines.append(f"⚠ 원장 열린 포지션 {open_count}건 중 {rendered}건만 표시됐다 — 설명되지 않은 공백 {unexplained}건")
    return lines


def can_assert_empty(payload: Mapping[str, Any], *, rendered: int | None = None) -> bool:
    """ "열린 포지션이 없습니다"를 **단정해도 되는가.**

    근거가 하나라도 어긋나면 단정하지 않는다. 이 함수가 `False` 인데 "없음"을 찍는 경로가
    생기면 그것이 이 모듈이 고친 결함의 재발이다.
    """
    return not observation_gap_lines(payload, rendered=rendered)


def empty_evidence_line(payload: Mapping[str, Any]) -> str:
    """ "없음"의 **출처**. 근거를 대지 못하면 그 "없음"은 여전히 주장일 뿐이다.

    2026-09-09 2차: 사유가 하나도 없는데도(동기화 성공·신선·원장 0건) 계좌에는 포지션이
    열려 있는 경우가 남는다 — 거래소가 그 포지션을 **응답에 담지 않은** 경우다. 이때
    `can_assert_empty` 는 참이고 화면은 "감시 정상"을 찍는다. 우리 쪽 판정은 옳지만
    사용자가 보는 화면과는 어긋난다.

    그 어긋남을 **한 줄로 국소화한다.** 거래소가 몇 건을 줬는지(어떤 productType 으로)와
    원장이 몇 건인지를 함께 적으면, 거래소 앱에 포지션이 보이는데 여기가 0건이면 문제는
    표시가 아니라 **거래소 조회**라는 것이 즉시 드러난다(계정 유형·productType 불일치).
    """
    parts: list[str] = []
    synced = _int_or_none(payload.get("synced"))
    if synced is not None:
        product = str(payload.get("product_type") or "").strip()
        parts.append(f"거래소 {escape(product)} {synced}건" if product else f"거래소 {synced}건")
    open_count = _int_or_none(payload.get("open_count"))
    if open_count is not None:
        parts.append(f"원장 {open_count}건")
    age = _age_phrase(payload.get("sync_age_seconds"))
    if age:
        parts.append(age)
    return " · ".join(parts)


def _age_phrase(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ""
    if seconds < 90:
        return "방금 동기화"
    return f"동기화 {seconds / 60:.0f}분 전"


def ledger_fallback_lines(rows: list[Mapping[str, Any]]) -> list[str]:
    """원장 행만으로 만든 대체 목록. **네트워크를 타지 않는 값만 쓴다.**

    동기화가 죽었을 때 "아무것도 없다"고 말하는 대신 **가진 것을 말한다.** 관측(현재가·
    판정)은 없으므로 진입 시점 사실만 적고, 그 출처를 명시한다.
    """
    if not rows:
        return ["원장에도 열린 포지션이 없다 — 다만 위 사유로 거래소 상태는 확인되지 않았다."]
    lines = [f"<b>원장 기준 열린 포지션 {len(rows)}건</b> (관측 실패 · 진입 시점 값)"]
    for row in rows[:10]:
        symbol = escape(str(row.get("symbol") or "-"))
        leverage = row.get("leverage")
        lines.append(
            f"• <b>{symbol}</b> {_direction_kr(row.get('direction'))}{f' {escape(str(leverage))}x' if leverage else ''} @ {_price(row.get('entry_price'))}"
        )
    if len(rows) > 10:
        lines.append(f"… 외 {len(rows) - 10}건")
    return lines


def _direction_kr(value: Any) -> str:
    if hasattr(value, "value"):
        value = value.value
    return "롱" if value == "long" else "숏" if value == "short" else "-"


def _price(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if abs(number) >= 100:
        return f"{number:.2f}"
    if abs(number) >= 1:
        return f"{number:.4f}"
    return f"{number:.6f}"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
