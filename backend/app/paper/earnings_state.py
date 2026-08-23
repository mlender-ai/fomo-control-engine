"""WO-FCE-EARNINGS-SUPPLY-01 4-3 — 실적 게이트 3상태 (KR 트랙 선례 이식).

## 무엇을 고치는가

크립토 페이퍼 경로는 실적 게이트를 **불리언 하나**로 뭉갠다:

```python
paper/service.py:2262   _earnings_clear(analysis) -> bool
  crypto  → True
  stock   → bool({}) == False      # 데이터가 없어서 False
  index   → bool({}) == False      # 실적 구간이라서 False — **와 구분되지 않는다**
```

`analysis["earnings"]` 를 채우는 코드가 저장소에 없으므로 stock·index 는 **항상** False 다.
그리고 그 False 가 "실적 구간이라 막았다"인지 "데이터가 없어 못 봤다"인지 밖에서 알 수 없다.

> **모르는 것과 아니라고 판정한 것을 같은 값으로 적으면, 공급 결함이 게이트 성과로 위장된다.**

## KR 트랙은 이미 이 문제를 풀었다 (불변 규칙 2 — 새로 만들지 않는다)

```python
stock_paper/policy.py:66
results["earnings_gate"] = {
    "status": "not_evaluable",     # ← 통과도 차단도 아닌 제3상태
    "threshold": "source_backlog",
    "required": False,             # ← 판정에서 제외
}
stock_paper/parameters.py:56       # 불변식이 재방문을 강제한다
    raise ValueError("earnings must remain explicitly not_evaluable until a source is connected")
```

이 모듈은 그 어휘(`not_evaluable`)와 구조(`required` 플래그)를 크립토 경로로 **옮긴다.**
설계를 새로 하지 않는다.

## 판정은 재구현하지 않는다 (C1)

`days_to_event not in {-1, 0, 1}` 같은 임계는 **여기 없다.** 이 모듈은 `_earnings_clear` 를
**호출**하고, 그 앞에 "페이로드가 비었는가" 하나만 더 본다. 그것이 이 WO 가 추가하는
유일한 술어이며, 정확히 4-3 이 요구한 구분이다.

## `required` 는 왜 기본값이 False 인가 (개정 §2)

공급원이 배선되기 전에 차단으로 바꾸면 **아무것도 얻지 못하고 관측만 잃는다.**
262종은 현재 crypto 로 오분류돼 어차피 이 게이트를 받지 않고, 올바르게 stock 인 소수만
추가로 죽는다 — 그 심볼들은 이미 다른 게이트에서 표본 0이다.

공급원 배선 후 `required=True` 로 전환하면, 그때 남은 `not_evaluable` 은 진짜 신호다
(상장 폐지 · 미지원 · 데이터 결함).
"""

from __future__ import annotations

from typing import Any, Callable, Iterable


# KR 트랙과 같은 어휘를 쓴다. 크립토 전용 상태명을 새로 만들면 두 트랙의 퍼널을 나란히
# 놓을 수 없다.
CLEAR = "clear"
EARNINGS_WINDOW = "earnings_window"
NOT_EVALUABLE = "not_evaluable"

EARNINGS_STATES = (CLEAR, EARNINGS_WINDOW, NOT_EVALUABLE)

# 실적 게이트가 걸리는 자산군. **값을 바꾸지 않는다**(C1) — `_earnings_clear` 와 같은 집합이다.
GATED_ASSET_CLASSES = frozenset({"stock", "index"})

# `stock_paper/policy.py` 가 쓰는 값과 같은 문자열. 공급원이 붙기 전까지의 사유다.
SOURCE_BACKLOG = "source_backlog"


def earnings_state(analysis: dict[str, Any], *, earnings_clear: Callable[[dict[str, Any]], bool]) -> str:
    """실적 게이트의 3상태.

    `earnings_clear` 를 주입받아 **호출**한다 — 임계도 창 판정도 여기서 다시 쓰지 않는다.
    이 함수가 추가하는 판단은 "실적 페이로드가 비었는가" 하나뿐이다.
    """
    asset_class = str(analysis.get("asset_class") or "")
    if asset_class not in GATED_ASSET_CLASSES:
        # 크립토는 이 게이트의 대상이 아니다. `_earnings_clear` 도 True 를 준다.
        return CLEAR
    payload = analysis.get("earnings") or analysis.get("earnings_risk")
    if not isinstance(payload, dict) or not payload:
        # 공급원이 없어 **재보지 못한** 상태. "아니다"가 아니다.
        return NOT_EVALUABLE
    return CLEAR if earnings_clear(analysis) else EARNINGS_WINDOW


def earnings_gate_passes(state: str, *, required: bool) -> bool:
    """게이트 통과 여부. `paper/policy.py` 는 이 불리언만 받는다(C3 — 정책 파일 diff 0줄).

    `required=False`(공급원 배선 전): `not_evaluable` 은 통과시킨다 — KR 트랙과 같다.
    `required=True`(배선 후): `not_evaluable` 도 차단한다 — 공급원이 있는데 데이터가 없으면
    그것은 진짜 신호다.

    **`earnings_window` 는 두 모드 모두에서 차단이다.** 그 판정은 데이터가 있을 때만 나오며
    이 WO 는 그것을 완화하지 않는다.
    """
    if state == EARNINGS_WINDOW:
        return False
    if state == NOT_EVALUABLE:
        return not required
    return True


def earnings_observation(state: str, *, required: bool) -> dict[str, Any]:
    """원장·퍼널에 남길 관측치. `stock_paper/policy.py::earnings_gate` 와 같은 모양이다."""
    return {
        "status": state,
        "measured_value": state,
        "threshold": SOURCE_BACKLOG if state == NOT_EVALUABLE else "days_to_event",
        "required": bool(required),
        "passed": earnings_gate_passes(state, required=required),
    }


def coverage_summary(states: Iterable[str]) -> dict[str, Any]:
    """무데이터 비율 — 공급 품질 지표 (4-3 작업 3 · C9).

    **이 값이 높으면 그것 자체가 결함이다.** 게이트가 잘 걸러서 진입이 없는 것과, 데이터가
    없어서 판정을 못 한 것은 다른 문제이고 처방도 다르다.
    """
    counts = {state: 0 for state in EARNINGS_STATES}
    total = 0
    for state in states:
        total += 1
        if state in counts:
            counts[state] += 1
    evaluable = counts[CLEAR] + counts[EARNINGS_WINDOW]
    return {
        "total": total,
        "counts": counts,
        "not_evaluable_pct": round(counts[NOT_EVALUABLE] / total * 100, 2) if total else None,
        "coverage_pct": round(evaluable / total * 100, 2) if total else None,
        "note": "`not_evaluable` 은 불통과가 아니다 — 공급원이 없어 재보지 못한 건이다.",
    }
