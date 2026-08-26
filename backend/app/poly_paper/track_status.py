"""WO-FCE-POLY-STATUS-01 — 폴리 트랙 상태를 화면이 말하게 한다.

## 왜 이 모듈이 생겼나

화면이 **원시 예외 문자열**을 그대로 뱉었다:

```
HTTPStatusError: Client error '451 Unavailable For Legal Reasons' for url '...'
```

사용자가 "되는 건가 안 되는 건가"를 판단할 수 없다. 그리고 `451` 은 네트워크 오류가
아니라 **정책 차단**이다 — 재시도·백오프로 풀리지 않는다.

동시에 `FULL-AUDIT-01` 이 이 트랙을 `STRUCTURALLY_BLOCKED` 로 판정했는데(만기 2027-01-01,
검증 창 내 정산 0건) **화면이 그 판정을 표시하지 않고 정상 트랙처럼 렌더했다.**

이 모듈은 **판정을 만들지 않는다.** 이미 있는 판정(`sample_viability`)과 이미 있는 사실
(`expiry.sample_possible`, 수집 오류)을 사람이 읽을 수 있는 상태로 바꾼다(C3).

## 차단을 우회하지 않는다

`451` 은 법적 차단이다. 프록시·VPN·우회 경로를 만들지 않는다(C1). 이 모듈이 하는 일은
차단됐다는 사실을 **정확히 쓰는 것**이고, 그 이상은 이 저장소가 할 일이 아니다.
"""

from __future__ import annotations

import re
from typing import Any

# 수집 상태 분류. 원시 예외를 사용자에게 던지지 않는다.
STATUS_OK = "ok"
STATUS_GEO_BLOCKED = "geo_blocked"
STATUS_TRANSIENT = "transient_error"
STATUS_UNKNOWN = "unknown"

# 재시도로 풀리는가. `451` 은 아니다 — 그것이 이 분류의 요점이다.
RETRYABLE = {STATUS_TRANSIENT: True, STATUS_GEO_BLOCKED: False, STATUS_OK: False, STATUS_UNKNOWN: True}

_GEO_BLOCK_PATTERN = re.compile(r"\b451\b|Unavailable For Legal Reasons", re.IGNORECASE)


def classify_collection(status: Any, error: Any) -> dict[str, Any]:
    """수집 오류를 **판정된 상태**로 바꾼다 (2-1 항목 1).

    `451` 을 일반 오류와 같은 칸에 넣으면 "재시도하면 되겠지"로 읽힌다. 재시도로 풀리지
    않는다는 것이 이 상태의 핵심 정보다.
    """
    raw = str(error or "")
    state = str(status or "").lower()
    if state == "ok" and not raw:
        return {
            "status": STATUS_OK,
            "label": "수집 정상",
            "retryable": False,
            "detail": None,
            "advice": None,
        }
    if _GEO_BLOCK_PATTERN.search(raw):
        return {
            "status": STATUS_GEO_BLOCKED,
            "label": "수집 차단 (451) · 지역 제한",
            "retryable": False,
            "detail": raw[:400],
            # C1 — 우회를 제안하지 않는다. 사실만 쓴다.
            "advice": "폴리마켓이 이 지역에서 API 접근을 차단했다. 재시도·백오프로 풀리지 않으며 우회하지 않는다(법적 차단). 접근 가능한 지역·경로 확보는 코드 밖의 결정이다.",
        }
    if raw:
        return {
            "status": STATUS_TRANSIENT,
            "label": "수집 실패 (일시적일 수 있음)",
            "retryable": True,
            "detail": raw[:400],
            "advice": "다음 수집 주기에 재시도된다. 반복되면 원인을 확정한다.",
        }
    return {"status": STATUS_UNKNOWN, "label": "수집 상태 미상", "retryable": True, "detail": None, "advice": None}


def sample_labels(*, resolution_count: int, our_positions: int, settling_within_validation: int) -> dict[str, Any]:
    """숫자 셋의 정체를 밝힌다 (2-2).

    화면이 `정산 표본 N=12774` 를 우리 표본처럼 보여줬다. 보유가 8건인데 표본이 12,774 일
    수 없다 — 그것은 **시장 전체의 확률 추정 채점(Brier) 관측**이고 우리 거래 표본이 아니다.

    실측 2026-08-26 이 그 불일치를 그대로 보여준다:

    | 값 | 수 | 무엇인가 |
    | --- | --- | --- |
    | `poly_resolutions` | 12,774 | 시장 전체 정산 관측 — 확률 추정 채점용 |
    | `poly_positions` | 9 | **우리 포지션** |
    | 검증 창 내 정산 예정 | **0** | **우리 검증 표본** |

    그리고 `sample_viability` 의 `exit_completion_rate` 가 **1419.333**(141,933%)로 나온다 —
    분자(12,774)와 분모(9)가 다른 것을 세는 증거다. 그 계산은 판정 모듈에 있고 이 WO 는
    그것을 고치지 않는다(C3). **화면에서 두 수를 갈라 놓는 것까지가 이 WO 의 범위다.**
    """
    return {
        "calibration_samples": int(resolution_count),
        "calibration_label": "시장 전체 확률 추정 채점(Brier) 관측 — 우리 거래 표본이 아니다",
        "our_positions": int(our_positions),
        "our_validation_samples": int(settling_within_validation),
        "our_validation_label": "검증 창 안에 정산되는 우리 포지션 — 이것이 트랙의 검증 표본이다",
        "mismatch_note": (
            "두 수는 다른 것을 센다. 판정 모듈의 청산 완료율이 141,933% 로 나오는 것이 그 증거다 — "
            "분자는 시장 전체 관측이고 분모는 우리 포지션이다. 판정 로직 수정은 이 작업의 범위가 아니다(C3)."
        ),
    }


def clock_breakdown(coverage_rows: list[dict[str, Any]], *, window_start: str | None) -> dict[str, Any]:
    """검증 시계 0 의 사유 분해 (2-2 항목 3).

    `0/28일 (유실 12일 제외)` 만으로는 왜 0인지 알 수 없다. 유실이 절전 때문인지 차단
    때문인지에 따라 조치가 완전히 다르다 — 하나는 호스트 설정이고 하나는 코드 밖이다.

    실측 2026-08-26 (창 2026-08-13 개시):

    | 사유 | 일수 |
    | --- | --- |
    | 커버리지 미달 (관측이 있었으나 부족) | 7 |
    | 수집 정지 (451) | 7 |
    | **유효** | **0** |
    """
    stalled = 0
    thin = 0
    valid = 0
    other = 0
    for row in coverage_rows:
        day = str(row.get("day") or "")
        if window_start and day < window_start:
            continue
        if int(row.get("valid") or 0):
            valid += 1
            continue
        reason = str(row.get("reason") or "")
        if "관측 0건" in reason:
            stalled += 1
        elif "커버리지" in reason:
            thin += 1
        else:
            other += 1
    total = stalled + thin + valid + other
    return {
        "window_start": window_start,
        "days_counted": total,
        "valid_days": valid,
        "stalled_days": stalled,
        "thin_coverage_days": thin,
        "other_days": other,
        "label": f"유효 {valid}일 · 수집 정지 {stalled}일 · 커버리지 미달 {thin}일" + (f" · 기타 {other}일" if other else ""),
        "note": (
            "수집 정지와 커버리지 미달은 조치가 다르다 — 정지는 451 지역 차단(코드 밖)이고, "
            "커버리지 미달은 관측 간격 문제다. 둘을 '유실'로 합치면 무엇을 고쳐야 하는지 사라진다."
        ),
    }


def track_status(
    *,
    collection: dict[str, Any],
    viability: dict[str, Any] | None,
    expiry: dict[str, Any] | None,
) -> dict[str, Any]:
    """트랙 상태 배지. 판정을 만들지 않고 **이미 있는 판정을 표시한다**(C3)."""
    verdict = str((viability or {}).get("verdict") or "")
    reason = str((viability or {}).get("verdict_reason") or "")
    sample_possible = (expiry or {}).get("sample_possible")
    structurally_blocked = verdict == "STRUCTURALLY_BLOCKED" or sample_possible is False
    blocked_collection = collection.get("status") == STATUS_GEO_BLOCKED

    if structurally_blocked and blocked_collection:
        headline = "구조적 검증 불가 + 수집 차단"
    elif structurally_blocked:
        headline = "구조적 검증 불가"
    elif blocked_collection:
        headline = "수집 차단 (451) · 지역 제한"
    else:
        headline = "관측 진행"
    return {
        "headline": headline,
        "structurally_blocked": structurally_blocked,
        "verdict": verdict or None,
        "verdict_reason": reason or (expiry or {}).get("label"),
        "collection": collection,
        # C4 — 정상처럼 보이게 하지 않는다. 재시작으로 풀리지 않는다는 것을 문장으로 적는다.
        "restart_resolves": False if (structurally_blocked or blocked_collection) else None,
        "restart_note": (
            "재시작해도 해소되지 않는다. 구조적 불가는 만기가 검증 창 밖이기 때문이고, 수집 차단은 지역 제한이기 때문이다 — 둘 다 프로세스 재기동과 무관하다."
            if (structurally_blocked and blocked_collection)
            else "재시작해도 해소되지 않는다 — 만기가 검증 창 밖이다."
            if structurally_blocked
            else "재시작해도 해소되지 않는다 — 지역 차단이다."
            if blocked_collection
            else None
        ),
    }
