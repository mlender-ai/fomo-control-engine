"""WO-FCE-DEFAULTS-01 — 적용된 임시값 목록.

## 왜 한곳에 모으는가

임시값이 코드 곳곳에 흩어져 있으면 **어느 값이 임시인지 알 수 없게 된다.** 그러면 임시값이
확정값처럼 굳고, 그것이 C5 가 막으려는 상태다.

이 모듈은 판정하지 않는다. 지금 적용 중인 임시값과 **각각의 원복 방법**을 한 화면에 낸다.
사용자가 확정하면 여기서 뺀다 — 뺀다는 행위가 곧 확정 기록이다.

## 넣은 것과 안 넣은 것

| 넣는다 | 안 넣는다 |
| --- | --- |
| 화면·파이프라인을 돌리는 값 | 측정을 거짓말하게 만드는 값 |
| 자본 선언 · 처리 방침 · 상한 | invariant 완화 · 유실일 분모 제외 · 임계 하향 |

두 번째 열은 임시값이 아니라 조작이다. 이 모듈에 그런 항목은 없어야 하고,
`test_no_measurement_altering_default_is_listed` 가 그것을 고정한다.
"""

from __future__ import annotations

from typing import Any

# 임시값 한 건의 형식. `revert` 가 없으면 목록에 올릴 수 없다 — 원복 불가는 임시값이 아니다.
_REQUIRED_KEYS = ("id", "label", "value", "basis", "revert")


def applied_defaults(settings: Any) -> list[dict[str, Any]]:
    """지금 적용 중인 임시값. 설정을 읽어 실제 상태를 낸다 — 문서를 믿지 않는다."""
    items: list[dict[str, Any]] = []

    capital = float(getattr(settings, "whale_follow_starting_capital_usdt", 0.0) or 0.0)
    slots = int(getattr(settings, "whale_follow_max_open_positions", 0) or 0)
    if capital > 0:
        items.append(
            {
                "id": "whale_follow_capital",
                "label": "고래 추종 시작 자본 · 동시 보유 상한",
                "value": f"{capital:g} USDT · {slots}건",
                "basis": "크립토 트랙과 동일(margin 100 × 5). 두 트랙을 같은 자본에서 비교할 수 있어야 한다.",
                "revert": "FCE_WHALE_FOLLOW_STARTING_CAPITAL_USDT=0 · FCE_WHALE_FOLLOW_MAX_OPEN_POSITIONS=0",
                "affects": "TRACK_CAPITAL 자본 대비 수익률 · 추종 진입 상한",
            }
        )

    if bool(getattr(settings, "validation_exclude_poly", False)):
        items.append(
            {
                "id": "poly_validation_exclusion",
                "label": "폴리마켓 검증 대상 제외",
                "value": "A안 (제외) · 수집·원장 유지",
                "basis": "451 지역 차단으로 B(유니버스 교체)가 불가능하고 C(유지)는 판정을 영구 미결로 둔다.",
                "revert": "FCE_VALIDATION_EXCLUDE_POLY=false",
                "affects": "COMPLETION_DEFINITION 완료 대상 트랙",
            }
        )

    latency = int(getattr(settings, "whale_follow_max_latency_minutes", 0) or 0)
    drift = float(getattr(settings, "whale_follow_max_drift_pct_of_stop", 0.0) or 0.0)
    if latency > 0 or drift > 0:
        items.append(
            {
                "id": "whale_follow_caps",
                "label": "추종 진입 지연·이탈 상한",
                "value": f"지연 {latency}분 · 이탈 {drift:g}% (무효화 거리 대비)",
                "basis": "WHALE-FOLLOW-02 7-2 시작값. 24시간 관측 후 조정하되 관측을 기다리지 않는다 — 상한 없이 도는 것이 더 나쁘다.",
                "revert": "FCE_WHALE_FOLLOW_MAX_LATENCY_MINUTES · FCE_WHALE_FOLLOW_MAX_DRIFT_PCT_OF_STOP",
                "affects": "추종 진입 거부율",
            }
        )

    if bool(getattr(settings, "stock_paper_hold_queued_orders", False)):
        items.append(
            {
                "id": "stock_queue_hold",
                "label": "KR 주식 큐 주문 보류",
                "value": "세션 개장 시 대기 주문 일괄 체결 보류",
                "basis": "체결가는 세션 시가로 만들고 invariant 는 현재 분봉으로 검사한다 — 봉 불일치로 US 가 이미 정지했고 KR 큐 13,836건이 같은 실패를 대기 중이다.",
                "revert": "FCE_STOCK_PAPER_HOLD_QUEUED_ORDERS=false",
                "affects": "KR 주식 체결 발생 · invariant 정지 예방",
            }
        )

    for item in items:
        missing = [key for key in _REQUIRED_KEYS if not item.get(key)]
        if missing:
            raise ValueError(f"임시값 항목에 {missing} 가 없다 — 원복 방법 없는 임시값은 확정값이다(C4)")
    return items


def summary(settings: Any) -> dict[str, Any]:
    items = applied_defaults(settings)
    return {
        "count": len(items),
        "items": items,
        "label": "임시값",
        "principle": "화면·파이프라인을 돌리는 임시값은 넣는다. 측정을 거짓말하게 만드는 임시값은 넣지 않는다.",
        "not_applied": [
            "체결 invariant 완화",
            "유실일을 유효일 분모에서 제외",
            "표본 미달을 충분으로 표기",
            "게이트 임계 하향",
        ],
        "not_applied_reason": "이 넷은 임시값이 아니라 조작이다. 하면 두 달의 계측이 전부 무의미해진다.",
        "document": "docs/validation/PROVISIONAL_DEFAULTS.md",
    }
