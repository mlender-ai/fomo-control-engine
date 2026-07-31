"""Point & Figure 수평 카운트 — 와이코프 Law of Cause and Effect (WO-FCE-PNF-TARGET-01).

**이것은 진입 시그널이 아니라 목표 계산기다.** 새 감지기를 추가하는 것이 아니라, 이미
`structure/wyckoff/engine.py`가 식별한 축적/분산 구간(Trading Range)에 대해 "누적된 원인의
크기"를 목표 가격으로 환산한다. 진입 판단 로직에는 어떤 새 시그널도 추가하지 않는다(C2).

배경: 기존 목표는 인접 구조 레벨("다음 저항까지")이라 누적 원인의 크기와 무관하다. 그래서
구조 레벨이 촘촘한 종목은 첫 목표가 손절폭보다 가까워 RR<1이 되고, RR 게이트가 종목을
거르는 게 아니라 목표 산출 방식이 스스로를 거르는 기아 상태가 된다.

정직성(C5): 축적 구간이 불명확하거나 열 수가 임계 미만이면 ``None``을 반환한다. 억지로
목표를 만들지 않는다. 룩어헤드 금지(C4): 확정 캔들만 입력으로 받는다.
"""

from .engine import (
    PNF_MIN_COLUMNS,
    PNF_REVERSAL_DEFAULT,
    MeasuredObjective,
    PnfColumn,
    build_pnf_columns,
    measured_objective,
    resolve_box_size,
)

__all__ = [
    "PNF_MIN_COLUMNS",
    "PNF_REVERSAL_DEFAULT",
    "MeasuredObjective",
    "PnfColumn",
    "build_pnf_columns",
    "measured_objective",
    "resolve_box_size",
]
