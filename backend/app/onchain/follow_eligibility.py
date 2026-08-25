"""WO-FCE-WHALE-FOLLOW-01 Phase 6-1 — 관찰 자격(승격과 분리).

## 왜 승격 기준을 페이퍼의 전제로 쓸 수 없나

Phase 5 는 "승격 통과자 0명 → 추종 트랙 미배선"으로 끝났다. 그 설계가 순환이었다.

```
승격하려면 승률이 증명돼야 한다
증명하려면 표본이 있어야 한다
그런데 "이 고래를 따라가면 버는가"의 표본은 추종 트랙에서만 나온다
추종 트랙은 승격을 요구한다        ← 순환
```

이 저장소는 같은 순환을 두 번 끊었다 — `paper/policy.py` 의 시그니처 `record_only`,
그리고 `DISCOVERY-UNBLOCK-01` 의 `backtest_sample` 순환 해제. 세 번째가 여기다.

**페이퍼 트레이딩은 돈이 들지 않는 관찰 장치다.** 증명을 요구할 대상이 아니라 증명을
만드는 수단이다.

## 승격 기준은 한 글자도 바뀌지 않는다

| 단계 | 기준 | 이 모듈 |
| --- | --- | --- |
| 관찰(페이퍼 진입) | N≥20 · 승률 점추정>50% · MM·캐리 아님 · 활동 중 | **신설** |
| 승격(검증 통과 선언) | 28일 · N≥30 · CI 하한 55% | **무변경** |
| 실주문 | 봉인 | **무변경** |

`onchain/service.py` 의 승격 판정은 이 모듈을 import 하지 않는다. 두 축은 서로를
참조하지 않으며, `test_promotion_thresholds_are_unchanged` 가 그것을 고정한다(C1).

## 그리고 두 축은 다른 것을 잰다

승격 심사가 재는 것은 **고래 자신의 승률**이다. 추종 트랙이 재는 것은
**"이 고래를 신호로 삼고 우리 사이징·출구로 거래하면 버는가"** 다.

고래 승률이 55% 가 아니어도 우리 손익비가 붙으면 벌 수 있고, 승률 70% 고래를 따라가도
지연·비용 때문에 잃을 수 있다. 후자는 추종 트랙 없이는 영원히 측정되지 않는다.

그래서 관찰 트랙 성과는 **승격 근거로 쓰지 않는다**(C11). 다른 질문의 답이다.

## `unclassified` 를 왜 허용하는가

`0x10f1d8…202f` 가 `unclassified`(양방향 고빈도)이고 표본 2위(N=37)이며 승률 1위(64.9%)다.
**모르는 것을 배제하면 영원히 모른다.** 허용하되 `unclassified_flag` 로 표시하고, 유형
분류 근거가 쌓이면 재판정한다.

**단 MM 추정은 배제한다**(C4). 보수성이 아니라 정의상 잡음이다 — maker 98.8% 지갑의
체결은 방향 베팅이 아니라 재고 관리이므로, 그것을 신호로 삼는 것은 신호가 아닌 것을
신호로 삼는 것이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.onchain import participant_type

# ── 관찰 자격 기준 (결과 확인 전 고정 · 6-1 항목 1) ──────────────────────

# 승격은 30 이다. 관찰은 그보다 낮다 — 관찰은 선언이 아니라 측정의 시작이다.
OBSERVATION_MIN_SAMPLE = 20
# CI 하한이 아니라 **점추정**이다. CI 하한 55% 는 사실상 승률 62.5% 를 요구하는데,
# 그것은 통과 선언의 문턱이지 관찰 착수의 문턱이 아니다.
OBSERVATION_MIN_WIN_PCT = 50.0
# 추종 신호가 나오려면 지갑이 지금도 거래하고 있어야 한다. 리더보드 주간 창과 같은 수다.
OBSERVATION_MAX_IDLE_DAYS = 7
# 추종 대상에서 배제하는 유형. `unclassified` 는 여기 없다 — 허용하되 플래그한다.
OBSERVATION_EXCLUDED_TYPES = frozenset({participant_type.TYPE_MARKET_MAKER, participant_type.TYPE_BASIS_CARRY})

QUALIFICATION_OBSERVATION = "observation"
QUALIFICATION_PROMOTION = "promotion"


@dataclass(frozen=True)
class ObservationStatus:
    """관찰 자격 판정. 승격 판정과 **별도 축**이다(6-1 항목 2)."""

    address: str
    eligible: bool
    reason: str
    sample_size: int
    win_pct: float | None
    ci_low: float | None
    participant_type: str
    participant_confidence: float | None
    unclassified_flag: bool
    idle_days: int | None
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
            "idle_days": self.idle_days,
            "excluded_sample": self.excluded_sample,
            "criteria": {
                "min_sample": OBSERVATION_MIN_SAMPLE,
                "min_win_pct": OBSERVATION_MIN_WIN_PCT,
                "max_idle_days": OBSERVATION_MAX_IDLE_DAYS,
                "excluded_types": sorted(OBSERVATION_EXCLUDED_TYPES),
            },
            # C8·C11 — 이 자격은 검증 통과가 아니다. 문구가 데이터에 붙어 다녀야 한다.
            "label": "미검증 관찰 자격",
            "not_promotion": "관찰 자격은 승격(28일·N≥30·CI 하한 55%)이 아니다. 이 트랙 성과를 승격 근거로 쓰지 않는다.",
        }


def observation_status(
    *,
    address: str,
    sample_size: int,
    wins: int,
    ci_low: float | None,
    estimate: dict[str, Any] | None,
    last_fill_at: datetime | None,
    now: datetime,
    excluded_sample: int = 0,
) -> ObservationStatus:
    """관찰 자격을 판정한다. 탈락도 사유를 남긴다(C10).

    `excluded_sample` 은 6-4 가 계수에서 뺀 오염 표본 수다. `sample_size` 는 이미 제외된
    뒤의 값이 들어와야 한다 — 이 함수가 다시 빼지 않는다.
    """
    kind = str((estimate or {}).get("participant_type") or participant_type.TYPE_UNCLASSIFIED)
    raw_confidence = (estimate or {}).get("confidence")
    confidence = float(raw_confidence) if isinstance(raw_confidence, (int, float)) else None
    unclassified = kind == participant_type.TYPE_UNCLASSIFIED
    win_pct = round(wins / sample_size * 100, 1) if sample_size else None
    idle_days = None if last_fill_at is None else max(0, int((now - last_fill_at).total_seconds() // 86400))

    def _status(eligible: bool, reason: str) -> ObservationStatus:
        return ObservationStatus(
            address=address,
            eligible=eligible,
            reason=reason,
            sample_size=sample_size,
            win_pct=win_pct,
            ci_low=ci_low,
            participant_type=kind,
            participant_confidence=confidence,
            unclassified_flag=unclassified,
            idle_days=idle_days,
            excluded_sample=excluded_sample,
        )

    if kind in OBSERVATION_EXCLUDED_TYPES:
        return _status(False, f"{kind} 추정 — 방향 베팅이 아니므로 추종 대상이 아니다(C4)")
    if sample_size < OBSERVATION_MIN_SAMPLE:
        detail = f"표본 {sample_size}/{OBSERVATION_MIN_SAMPLE}"
        if excluded_sample:
            detail += f" (오염 {excluded_sample}건 제외 후)"
        return _status(False, f"{detail} 미달")
    if win_pct is None or win_pct <= OBSERVATION_MIN_WIN_PCT:
        return _status(False, f"승률 점추정 {win_pct}% — {OBSERVATION_MIN_WIN_PCT}% 초과 요구")
    if idle_days is None:
        return _status(False, "체결 기록이 없다 — 활동 여부를 확인할 수 없다")
    if idle_days > OBSERVATION_MAX_IDLE_DAYS:
        return _status(False, f"{idle_days}일간 체결 없음 — 활동 중이 아니다")
    flag = " · 유형 미분류(플래그)" if unclassified else ""
    return _status(True, f"표본 {sample_size} · 승률 {win_pct}% · {kind}{flag} — 관찰 자격 통과(승격 아님)")


def eligible_addresses(statuses: dict[str, ObservationStatus]) -> set[str]:
    return {address for address, status in statuses.items() if status.eligible}


def qualification_for(address: str, *, promotion_trusted: set[str], observation_eligible: set[str]) -> str | None:
    """진입 근거가 될 자격 종류. 승격이 있으면 승격을 우선한다(C3·C8 플래그용)."""
    key = address.lower()
    if key in {item.lower() for item in promotion_trusted}:
        return QUALIFICATION_PROMOTION
    if key in {item.lower() for item in observation_eligible}:
        return QUALIFICATION_OBSERVATION
    return None


def summary(statuses: dict[str, ObservationStatus]) -> dict[str, Any]:
    eligible = [status for status in statuses.values() if status.eligible]
    return {
        "wallets": len(statuses),
        "eligible": len(eligible),
        "eligible_addresses": sorted(status.address for status in eligible),
        "unclassified_eligible": sum(1 for status in eligible if status.unclassified_flag),
        "criteria": {
            "min_sample": OBSERVATION_MIN_SAMPLE,
            "min_win_pct": OBSERVATION_MIN_WIN_PCT,
            "max_idle_days": OBSERVATION_MAX_IDLE_DAYS,
            "excluded_types": sorted(OBSERVATION_EXCLUDED_TYPES),
        },
        "promotion_criteria_untouched": "28일 · N>=30 · CI 하한 55% (C1 · 이 모듈이 참조하지 않는다)",
        "label": "미검증 관찰 자격",
    }
