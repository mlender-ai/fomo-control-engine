"""WO-FCE-DEFAULTS-01 1-2 — 검증 대상 범위.

## 폴리는 코드 판정을 막고 있지 않았다

WO 는 "트랙별 판정이 폴리 때문에 막히지 않게 한다"고 했다. 실측하니 **막고 있지 않았다:**

| 소비처 | 폴리가 막는가 |
| --- | --- |
| `live_trading_gate.live_trading_readiness_report` | 아니다 — 트랙별 행이고 AND 가 없다 |
| `sample_rate.sample_rate_report` | 아니다 — `tracks_with_shortfall` 목록에 이름만 오른다 |
| `COMPLETION_DEFINITION.md` | **막는다** — 4트랙 완료 서술에 폴리가 포함돼 있다 |
| `pending_decisions.poly_track_disposition` | **막는다** — BLOCKING 등급 |

즉 실제 차단은 **문서와 결정 항목**에 있었다. 그래서 이 모듈은 판정 계층을 고치지 않고
(그 파일들은 `VERDICT_MODULES` 로 고정돼 있다) **범위를 선언**한다. 보고 표면이 이것을
읽어 "검증 대상 제외"를 표시하고, 문서가 이것을 정본으로 삼는다.

## 왜 A(제외)인가

451 지역 차단이 안 풀리면 B(유니버스 교체)가 불가능하다 — 시장 목록 API 가 막혀 있다.
남는 것은 A 와 C 뿐이고, **C(유지)는 검증 판정을 영구 미결로 둔다.** 그래서 A 다.

**수집·원장은 유지한다.** 제외는 판정 범위에서 빼는 것이고 데이터를 버리는 것이 아니다.
451 이 풀리면 되돌린다.

## 임시값이다

`FCE_VALIDATION_EXCLUDE_POLY=false` 로 되돌아간다(C4). 화면·문서에 `임시값` 이 붙는다(C5).
"""

from __future__ import annotations

from typing import Any

TRACK_POLY = "poly"

# 제외 사유. 값이 아니라 **왜** 가 함께 다녀야 한다 — 사유 없는 제외는 은폐다.
EXCLUSION_REASONS = {
    TRACK_POLY: (
        "451 지역 차단으로 수집이 멈췄고(코드로 해결 불가) 보유 8건 전부 검증 창 밖 만기라 "
        "구조적으로 검증 표본이 0이다. 유니버스 교체(B안)는 시장 목록 API 가 차단돼 불가능하므로 "
        "선택지가 A(제외)와 C(유지)뿐이고, C 는 판정을 영구 미결로 둔다."
    )
}

PROVISIONAL_LABEL = "임시값"


def excluded_tracks(settings: Any) -> frozenset[str]:
    """검증 판정 범위에서 빼는 트랙. 설정으로 되돌린다(C4)."""
    if bool(getattr(settings, "validation_exclude_poly", False)):
        return frozenset({TRACK_POLY})
    return frozenset()


def in_validation_scope(track: str, settings: Any) -> bool:
    return track not in excluded_tracks(settings)


def scope_block(settings: Any) -> dict[str, Any]:
    """보고 표면이 읽는 범위 선언. 제외된 트랙과 사유·원복 방법을 함께 낸다."""
    excluded = sorted(excluded_tracks(settings))
    return {
        "excluded_tracks": excluded,
        "reasons": {track: EXCLUSION_REASONS.get(track, "사유 미기재") for track in excluded},
        "provisional": bool(excluded),
        "label": PROVISIONAL_LABEL if excluded else None,
        "revert": "FCE_VALIDATION_EXCLUDE_POLY=false" if TRACK_POLY in excluded else None,
        "data_kept": "수집·원장은 유지한다. 제외는 판정 범위에서 빼는 것이고 데이터를 버리는 것이 아니다.",
        "measured_note": (
            "실측 확인: 폴리는 live_trading_gate·sample_rate 를 막고 있지 않았다(둘 다 트랙별 행이고 AND 가 없다). "
            "실제 차단은 COMPLETION_DEFINITION 서술과 pending_decisions 항목에 있었다."
        ),
    }


def track_scope_status(track: str, settings: Any) -> dict[str, Any]:
    """한 트랙의 범위 상태. 화면이 `검증 대상 제외 (451 차단)` 를 이것으로 그린다."""
    excluded = track in excluded_tracks(settings)
    return {
        "track": track,
        "in_validation_scope": not excluded,
        "excluded": excluded,
        "reason": EXCLUSION_REASONS.get(track) if excluded else None,
        "label": f"검증 대상 제외 · {PROVISIONAL_LABEL}" if excluded else None,
        "revert": "FCE_VALIDATION_EXCLUDE_POLY=false" if excluded else None,
    }
