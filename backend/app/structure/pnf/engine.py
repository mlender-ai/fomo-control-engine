"""PNF 열 구성과 수평 카운트 측정 목표.

용어
----
- **박스(box)**: PNF 격자의 최소 가격 단위. 고정값이 아니라 ATR·가격 비례로 산출해
  종목별 변동성 차이를 흡수한다(§박스 크기 산출 규칙).
- **리버설(reversal)**: 열을 바꾸는 데 필요한 역방향 박스 수. 기본 3(전통적 3-box reversal).
- **수평 카운트(horizontal count)**: 축적 구간에서 형성된 열의 개수 × 박스 × 리버설.
  와이코프의 Cause(원인 축적 폭)를 Effect(목표 이동 폭)로 환산하는 고전적 방법이다.

박스 크기 산출 규칙 (문서 고정)
--------------------------------
``box = clamp(ATR × BOX_ATR_FRACTION, price × MIN_BOX_PCT, price × MAX_BOX_PCT)``

ATR이 없거나 0이면 ``price × DEFAULT_BOX_PCT``로 폴백한다. ATR 비례를 1차 기준으로 쓰는
이유는 같은 3% 박스라도 저변동 종목에서는 과도하게 촘촘하고 고변동 종목에서는 과도하게
성기기 때문이다. 상·하한 클램프는 극단적 ATR에서 열이 1개로 뭉치거나 수백 개로 흩어지는
것을 막는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Sequence

# 박스 크기 산출 상수 — 변경 시 docs/PNFTarget.md의 규칙표를 함께 갱신한다.
BOX_ATR_FRACTION = 0.5
MIN_BOX_PCT = 0.005
MAX_BOX_PCT = 0.03
DEFAULT_BOX_PCT = 0.01

PNF_REVERSAL_DEFAULT = 3
# 열이 너무 적으면 "원인"이 축적됐다고 볼 수 없다. 미만이면 목표를 만들지 않는다(C5).
PNF_MIN_COLUMNS = 4


@dataclass(frozen=True)
class PnfColumn:
    """PNF 격자의 한 열. ``direction`` 은 "X"(상승) 또는 "O"(하락)."""

    direction: str
    top_box: int
    bottom_box: int

    @property
    def box_count(self) -> int:
        return self.top_box - self.bottom_box + 1


@dataclass(frozen=True)
class MeasuredObjective:
    """수평 카운트 측정 목표. 산출 불가 시 이 객체 자체를 만들지 않는다(None 반환)."""

    target_price: float
    count_columns: int
    box_size: float
    reversal: int
    count_line: float
    tr_start: Any
    tr_end: Any
    confidence: int
    direction: str = "long"
    basis: str = "PNF 수평 카운트(와이코프 Cause & Effect)"
    meta: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {
            "target_price": self.target_price,
            "count_columns": self.count_columns,
            "box_size": self.box_size,
            "reversal": self.reversal,
            "count_line": self.count_line,
            "tr_start": self.tr_start,
            "tr_end": self.tr_end,
            "confidence": self.confidence,
            "direction": self.direction,
            "basis": self.basis,
            **({"meta": self.meta} if self.meta else {}),
        }


def resolve_box_size(price: float, atr: float | None) -> float | None:
    """박스 크기를 ATR·가격 비례로 산출한다. 산출 불가면 None."""
    if price is None or not math.isfinite(price) or price <= 0:
        return None
    floor = price * MIN_BOX_PCT
    ceiling = price * MAX_BOX_PCT
    if atr is None or not math.isfinite(atr) or atr <= 0:
        candidate = price * DEFAULT_BOX_PCT
    else:
        candidate = atr * BOX_ATR_FRACTION
    box = min(max(candidate, floor), ceiling)
    return box if box > 0 else None


def build_pnf_columns(
    candles: Sequence[Any],
    box_size: float,
    reversal: int = PNF_REVERSAL_DEFAULT,
) -> list[PnfColumn]:
    """확정 캔들의 고가/저가로 PNF 열을 구성한다.

    호출자가 **확정 캔들만** 넘겨야 한다(C4). 이 함수는 넘어온 순서를 그대로 신뢰하며
    미래 봉을 참조하지 않는다 — 각 캔들은 자신보다 앞선 상태에만 영향을 준다.
    """
    if box_size <= 0 or reversal < 1 or not candles:
        return []
    columns: list[PnfColumn] = []
    direction: str | None = None
    top = bottom = 0

    for candle in candles:
        high = _num(getattr(candle, "high", None))
        low = _num(getattr(candle, "low", None))
        if high is None or low is None or high <= 0 or low <= 0:
            continue
        high_box = int(math.floor(high / box_size))
        low_box = int(math.ceil(low / box_size))
        if direction is None:
            direction, top, bottom = "X", high_box, low_box
            if top < bottom:
                top = bottom
            continue
        if direction == "X":
            if high_box > top:
                top = high_box
            elif low_box <= top - reversal:
                columns.append(PnfColumn("X", top, bottom))
                direction, top, bottom = "O", top - 1, low_box
        else:
            if low_box < bottom:
                bottom = low_box
            elif high_box >= bottom + reversal:
                columns.append(PnfColumn("O", top, bottom))
                direction, top, bottom = "X", high_box, bottom + 1

    if direction is not None:
        columns.append(PnfColumn(direction, top, bottom))
    return columns


def measured_objective(
    candles: Sequence[Any],
    *,
    trading_range: dict[str, Any] | None,
    reversal: int = PNF_REVERSAL_DEFAULT,
    direction: str = "long",
    min_columns: int = PNF_MIN_COLUMNS,
) -> MeasuredObjective | None:
    """축적 구간의 수평 카운트로 측정 목표를 산출한다.

    반환 None 조건(C5 — 억지 목표 생성 금지):
      - 축적 구간(TR)이 없거나 지지/저항 가격이 불완전
      - 박스 크기를 산출할 수 없음
      - TR 대역 안의 열 수가 ``min_columns`` 미만 (원인이 충분히 축적되지 않음)
      - 산출된 목표가 카운트 라인보다 유리하지 않음

    롱만 지원한다. 주식 페이퍼가 long_only이며 숏 목표는 검증 표본이 없기 때문이다.
    """
    if direction != "long" or not trading_range or not candles:
        return None
    support = _level_price(trading_range.get("support"))
    resistance = _level_price(trading_range.get("resistance"))
    if support is None or resistance is None or resistance <= support:
        return None

    last_close = _num(getattr(candles[-1], "close", None))
    reference_price = last_close if last_close and last_close > 0 else resistance
    box_size = resolve_box_size(reference_price, _num(trading_range.get("atr")))
    if box_size is None:
        return None

    columns = build_pnf_columns(candles, box_size, reversal)
    if not columns:
        return None

    # TR 대역과 겹치는 열만 "원인"으로 센다 — 구간 밖 추세 구간의 열은 축적이 아니다.
    low_box = support / box_size
    high_box = resistance / box_size
    congestion = [column for column in columns if column.top_box >= low_box and column.bottom_box <= high_box]
    count_columns = len(congestion)
    if count_columns < min_columns:
        return None

    # 고전적 카운트 라인: 축적 구간의 하단(지지). 목표 = 카운트라인 + 열수 × 박스 × 리버설.
    count_line = support
    target_price = count_line + count_columns * box_size * reversal
    if target_price <= max(count_line, reference_price):
        return None

    return MeasuredObjective(
        target_price=round(target_price, 8),
        count_columns=count_columns,
        box_size=round(box_size, 8),
        reversal=reversal,
        count_line=round(count_line, 8),
        tr_start=trading_range.get("start_time"),
        tr_end=trading_range.get("end_time"),
        confidence=_confidence(count_columns, trading_range),
        direction=direction,
        meta={
            "total_columns": len(columns),
            "candles_inside": trading_range.get("candles_inside"),
            "reference_price": round(reference_price, 8),
        },
    )


def _confidence(count_columns: int, trading_range: dict[str, Any]) -> int:
    """열 수와 구간 체류 캔들 수로 신뢰도를 매긴다. 상한 85 — 추정임을 잊지 않기 위함."""
    base = 45 + min(25, (count_columns - PNF_MIN_COLUMNS) * 5)
    inside = _num(trading_range.get("candles_inside")) or 0
    if inside >= 30:
        base += 10
    elif inside >= 20:
        base += 5
    return int(min(85, max(0, base)))


def _level_price(level: Any) -> float | None:
    if isinstance(level, dict):
        return _num(level.get("price"))
    return _num(level)


def _num(value: Any) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
