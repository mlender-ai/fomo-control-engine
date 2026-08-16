"""검증 창 술어 — **엔진과 사용자가 같은 함수를 쓴다** (WO-FCE-METRIC-TRUTH-01 결정 2).

## 왜 이 모듈이 생겼나

술어가 두 곳에 각각 구현돼 있었고, 서로 달랐다.

```
엔진   paper/service.py:682        entry_at >= 앵커
사용자 db/repository/paper.py:51   exit_at  >= 앵커
```

같은 앵커를 넣어도 다른 집합이 나온다. 실측: 사용자를 `entry_at` 으로 재계산하면
N=54 · +36.18% · PF 1.87, `exit_at` 으로 하면 N=56 · +17.57% · PF 0.55 — **완전히 다른 그림**이다.

## 결정 2 — 둘 중 하나를 고르는 문제가 아니다

- `entry_at` 만: 결과가 창 밖에서 난 거래가 들어온다
- `exit_at` 만: **앵커 이전 로직으로 진입한 거래**가 들어온다

둘 다 오염이다. 양쪽을 걸어 **"창 안에서 결정하고 창 안에서 결판난"** 것만 남긴다.

```
entry_at >= 앵커  AND  exit_at IS NOT NULL  AND  exit_at <= 종료
```

> **두 곳에 각각 구현하면 다시 갈라진다.** 그래서 함수 하나로 두고 양 계열이 이것을 호출한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def _stamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def in_window(entry_at: Any, exit_at: Any, *, start: datetime | None, end: datetime | None = None) -> bool:
    """창 안에서 **결정하고 결판난** 거래인가.

    청산되지 않은 거래는 결판이 나지 않았으므로 성과 모집단에 들어가지 않는다.
    """
    entered = _stamp(entry_at)
    exited = _stamp(exit_at)
    if exited is None:
        return False
    if start is not None:
        if entered is None or entered < start:
            return False
    if end is not None and exited > end:
        return False
    return True


def filter_trades(trades: list[Any], *, start: datetime | None, end: datetime | None = None) -> list[Any]:
    """양 계열 공용 필터. `entry_at`·`exit_at` 속성을 가진 객체면 종류를 가리지 않는다."""
    return [t for t in trades if in_window(getattr(t, "entry_at", None), getattr(t, "exit_at", None), start=start, end=end)]
