"""WO-FCE-WHALE-FOLLOW-02 7-1 — 추종 자격. **규칙 하나, 조건 셋.**

## 규칙

> 추적된 고래 중 **승률 좋은 애들**을 골라서 따라간다.

```
N >= 30           표본. 승률이 의미를 가지는 최소선
승률 >= 55%       **점추정**이다. CI 하한이 아니다
MM 추정 아님       마켓메이커 체결은 재고 관리다 — 정의상 잡음
```

끝이다. 셋 다 만족하면 따라가고, 하나라도 못 채우면 안 따라간다.

## Phase 6 의 2축 자격을 걷어냈다 (7-1 항목 2)

Phase 6 은 `observation`/`promotion` 두 축에 CI 하한·유형·신뢰도·휴면일까지 얹었다.
페이퍼 트레이딩에 그 장치가 필요 없었다 — 그리고 복잡한 만큼 **틀린 것을 통과시켰다**:

```
0x1ee7…edf5 · unclassified (신뢰 0.0) · N=39 · CI 하한 35.9%   ← 통과했다
```

신뢰 0.0 은 "근거가 아예 없다"는 뜻이고 CI 하한 35.9% 는 점추정이 51% 남짓이라는 뜻이다.
**동전 던지기를 따라가고 있었다.** 새 규칙은 이 지갑을 승률 조건에서 떨어뜨린다.

## 왜 CI 하한이 아니라 점추정인가

CI 하한 55% 는 사실상 승률 62.5% 를 요구한다. 그것은 **통과 선언의 문턱**이지 관찰
착수의 문턱이 아니다. 페이퍼는 증명이 아니라 관찰이므로 점추정을 쓴다.

CI 하한과 신뢰도는 **표시에는 남는다**(C10). 자격 판정에서 뺄 뿐이다 — 지우면 나중에
"왜 이 지갑을 따라갔나"를 되짚을 수 없다.

## `unclassified` 를 허용하는 이유

`0x10f1d8…202f` 가 `unclassified`(양방향 고빈도)이고 승률 1위(64.9%)다. **모르는 것을
배제하면 영원히 모른다.** 허용하되 플래그하고, N·승률 조건이 실제 거름망 역할을 한다.

## 승격 기준은 한 글자도 바뀌지 않는다 (C3)

| 축 | 기준 | 이 모듈 |
| --- | --- | --- |
| 추종(페이퍼 진입) | N>=30 · 승률 점추정>=55% · MM 아님 | **여기** |
| 승격(검증 통과 선언) | 28일 · N>=30 · CI 하한 55% | **무변경 · 참조하지 않음** |

`onchain/service.py` 의 승격 판정은 이 모듈을 import 하지 않고, 이 모듈도 그것을 읽지 않는다. **추종 자격과 승격은 별개다** — 승격 여부는 표시용으로만 싣는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.onchain import participant_type

# ── 추종 자격 (§2 · 결과 확인 전 고정) ──────────────────────────────────

# 승률이 의미를 가지는 최소 표본. Phase 6 의 20 에서 올렸다 — 20 은 우연을 통과시킨다.
FOLLOW_MIN_SAMPLE = 30
# **점추정**이다. CI 하한이 아니다. 55% 미만은 수수료를 이길 근거가 없다.
FOLLOW_MIN_WIN_PCT = 55.0
# 배제 유형. **둘 다 방향 베팅이 아니다** — 신호가 아닌 것을 신호로 삼게 된다.
#
# - `market_maker`: 체결이 재고 관리다. maker 98.8% 지갑의 체결은 방향 의견이 아니다
# - `basis_carry`: **델타 중립**이다. 현물 롱 + 퍼프 숏에서 퍼프 다리만 따라가는 것은
#   헤지를 방향 베팅으로 오독하는 것이다 (WO-FCE-WHALE-EXIT-REPLAY-01 2-7)
#
# `WHALE-FOLLOW-02` 문안이 MM 만 지목한 것은 누락이었고 2-7 이 그것을 정정했다.
# **자격 임계(N>=30 · 55%)는 건드리지 않는다 — 유형 필터만 넓힌다.**
FOLLOW_EXCLUDED_TYPES = frozenset({participant_type.TYPE_MARKET_MAKER, participant_type.TYPE_BASIS_CARRY})

# 추종 자격은 **하나**다. Phase 6 의 observation/promotion 2축을 대체한다(7-1 항목 2).
# 유형별 배제 사유. 하나로 뭉뚱그리면 "왜 이 지갑이 빠졌나"를 화면에서 답할 수 없다(2-7 항목 4).
EXCLUSION_REASONS = {
    participant_type.TYPE_MARKET_MAKER: "체결이 재고 관리다 — 방향 베팅이 아니다",
    participant_type.TYPE_BASIS_CARRY: "델타 중립이다 — 퍼프 다리만 따라가면 헤지를 방향 베팅으로 오독한다",
}

QUALIFICATION_FOLLOW = "follow"


@dataclass(frozen=True)
class FollowStatus:
    """추종 자격 판정. 탈락도 사유를 남긴다(C10)."""

    address: str
    eligible: bool
    reason: str
    sample_size: int
    win_pct: float | None
    # 아래 셋은 **표시 전용**이다. 자격 판정에 쓰이지 않는다(§2).
    ci_low: float | None
    participant_type: str
    participant_confidence: float | None
    unclassified_flag: bool
    excluded_sample: int = 0

    def as_payload(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "eligible": self.eligible,
            "reason": self.reason,
            "sample_size": self.sample_size,
            "win_pct": self.win_pct,
            "ci_low": self.ci_low,
            "participant_type": self.participant_type,
            "participant_confidence": self.participant_confidence,
            "unclassified_flag": self.unclassified_flag,
            "excluded_sample": self.excluded_sample,
            "criteria": criteria(),
            # 표시용 값이 자격 근거로 오독되지 않게 못 박는다.
            "display_only": ["ci_low", "participant_confidence"],
            "label": "미검증 추종 자격",
            "not_promotion": "추종 자격은 승격(28일·N>=30·CI 하한 55%)이 아니다. 이 트랙 성과를 승격 근거로 쓰지 않는다.",
        }


def criteria() -> dict[str, Any]:
    return {
        "min_sample": FOLLOW_MIN_SAMPLE,
        "min_win_pct": FOLLOW_MIN_WIN_PCT,
        "excluded_types": sorted(FOLLOW_EXCLUDED_TYPES),
        "rule": "N>=30 · 승률 점추정>=55% · MM 추정 아님",
    }


def follow_status(
    *,
    address: str,
    sample_size: int,
    wins: int,
    ci_low: float | None = None,
    estimate: dict[str, Any] | None = None,
    excluded_sample: int = 0,
) -> FollowStatus:
    """§2 의 세 조건을 그대로 판정한다.

    `excluded_sample` 은 오염으로 계수에서 뺀 표본 수다. `sample_size` 는 이미 제외된 뒤의
    값이 들어와야 한다 — 이 함수가 다시 빼지 않는다.
    """
    kind = str((estimate or {}).get("participant_type") or participant_type.TYPE_UNCLASSIFIED)
    raw_confidence = (estimate or {}).get("confidence")
    confidence = float(raw_confidence) if isinstance(raw_confidence, (int, float)) else None
    unclassified = kind == participant_type.TYPE_UNCLASSIFIED
    win_pct = round(wins / sample_size * 100, 1) if sample_size else None

    def _status(eligible: bool, reason: str) -> FollowStatus:
        return FollowStatus(
            address=address,
            eligible=eligible,
            reason=reason,
            sample_size=sample_size,
            win_pct=win_pct,
            ci_low=ci_low,
            participant_type=kind,
            participant_confidence=confidence,
            unclassified_flag=unclassified,
            excluded_sample=excluded_sample,
        )

    if kind in FOLLOW_EXCLUDED_TYPES:
        return _status(False, f"{kind} 추정 — {EXCLUSION_REASONS[kind]}")
    if sample_size < FOLLOW_MIN_SAMPLE:
        detail = f"표본 {sample_size}/{FOLLOW_MIN_SAMPLE}"
        if excluded_sample:
            detail += f" (오염 {excluded_sample}건 제외 후)"
        return _status(False, f"{detail} 미달")
    if win_pct is None or win_pct < FOLLOW_MIN_WIN_PCT:
        return _status(False, f"승률 점추정 {win_pct}% — {FOLLOW_MIN_WIN_PCT}% 이상 요구")
    flag = " · 유형 미분류(플래그)" if unclassified else ""
    return _status(True, f"표본 {sample_size} · 승률 {win_pct}% · {kind}{flag} — 추종 자격 통과(승격 아님)")


def eligible_addresses(statuses: dict[str, FollowStatus]) -> set[str]:
    return {address for address, status in statuses.items() if status.eligible}


# 탈락 사유 분류. 사유 문자열을 세면 표현이 바뀔 때마다 집계가 깨진다 — 조건 순서로 센다.
REASON_EXCLUDED_TYPE = "excluded_type"
REASON_SAMPLE = "sample_below_min"
REASON_WIN_RATE = "win_rate_below_min"
REASON_PASS = "eligible"


def rejection_reason(status: FollowStatus) -> str:
    """왜 떨어졌는가. `follow_status` 와 **같은 순서**로 판정한다 — 두 곳이 갈리면 분해가 거짓이 된다."""
    if status.eligible:
        return REASON_PASS
    if status.participant_type in FOLLOW_EXCLUDED_TYPES:
        return REASON_EXCLUDED_TYPE
    if status.sample_size < FOLLOW_MIN_SAMPLE:
        return REASON_SAMPLE
    return REASON_WIN_RATE


def funnel(statuses: dict[str, FollowStatus]) -> dict[str, Any]:
    """자격 깔때기 — **몇 개가 어디서 떨어졌는가** (WO-FCE-REPORT-DEFECTS-01 7-3 항목 3).

    "N개 중 3개 통과"만 보이면 그 감소가 기준 탓인지 표본 탓인지 알 수 없다. 단계별로 센다.

    > **모집단은 우리 판정 원장이다** — `whale_sample_sizes`(우리 엔진이 채점한 `whale_entry`
    > 판정 건수)이며, `win_rate.observed_win_rates`(고래 자신의 체결 손익)와 **다른 것을
    > 센다.** 두 수를 나란히 두고 "감소"라고 부르면 안 된다. 그것이 D3 이었다.
    """
    counts = {REASON_PASS: 0, REASON_EXCLUDED_TYPE: 0, REASON_SAMPLE: 0, REASON_WIN_RATE: 0}
    for status in statuses.values():
        counts[rejection_reason(status)] += 1
    total = len(statuses)
    return {
        "population": total,
        "population_note": "우리 판정 원장에서 채점된 지갑 수 (whale_sample_sizes) — 고래 자신의 체결 승률 모집단과 다르다",
        "eligible": counts[REASON_PASS],
        "rejected": {
            REASON_EXCLUDED_TYPE: counts[REASON_EXCLUDED_TYPE],
            REASON_SAMPLE: counts[REASON_SAMPLE],
            REASON_WIN_RATE: counts[REASON_WIN_RATE],
        },
        # 2-7 항목 3·4 — 어느 유형이 몇 개인지. "배제 5개"만 보이면 `basis_carry` 추가의
        # 영향이 얼마인지 알 수 없다.
        "excluded_by_type": {
            kind: sum(1 for status in statuses.values() if status.participant_type == kind and not status.eligible) for kind in sorted(FOLLOW_EXCLUDED_TYPES)
        },
        "exclusion_reasons": EXCLUSION_REASONS,
        "criteria": criteria(),
        "label": f"{total}개 중 {counts[REASON_PASS]}개 통과 · MM {counts[REASON_EXCLUDED_TYPE]} · 표본 미달 {counts[REASON_SAMPLE]} · 승률 미달 {counts[REASON_WIN_RATE]}",
    }


def summary(statuses: dict[str, FollowStatus]) -> dict[str, Any]:
    """통과자와 **탈락자 사유**를 함께 낸다.

    통과자가 0명이면 그 사실을 명시한다 — 기준을 낮추지 않는다(7-1 항목 4).
    """
    eligible = [status for status in statuses.values() if status.eligible]
    passers = [
        {
            "address": status.address,
            "sample_size": status.sample_size,
            "win_pct": status.win_pct,
            "participant_type": status.participant_type,
            "participant_confidence": status.participant_confidence,
            "ci_low": status.ci_low,
        }
        for status in sorted(eligible, key=lambda item: item.win_pct or 0.0, reverse=True)
    ]
    return {
        "wallets": len(statuses),
        "eligible": len(eligible),
        "eligible_addresses": sorted(status.address for status in eligible),
        "passers": passers,
        "unclassified_eligible": sum(1 for status in eligible if status.unclassified_flag),
        "rejected": sorted(
            ({"address": status.address, "reason": status.reason} for status in statuses.values() if not status.eligible),
            key=lambda item: item["address"],
        ),
        "criteria": criteria(),
        "zero_passers_note": (
            "통과자 0명이다. 기준을 낮추지 않는다 — 표본이 쌓이거나 승률이 오를 때까지 진입하지 않는 것이 설계다(7-1 항목 4)." if not eligible else None
        ),
        "promotion_criteria_untouched": "28일 · N>=30 · CI 하한 55% (C3 · 이 모듈이 참조하지 않는다)",
        "label": "미검증 추종 자격",
    }
