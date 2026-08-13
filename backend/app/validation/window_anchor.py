"""WO-FCE-WINDOW-ANCHOR-01 — 검증 창 앵커.

## 왜 필요한가

검증 완료 판정에 **창 개념이 없었다.**

```
sample_viability.py:145   SELECT * FROM observation_coverage WHERE track=?    ← 시간 조건 없음
sample_viability.py:79-101  표본 SQL 8종 전부 WHERE 절에 시각 조건 없음
```

`effective_days` 는 트랙의 **역사상 모든 유효일 개수**였고, `scored_samples` 는 생애 누적이었다.
그래서 `start_paper_benchmark(reset=True)` 를 실행해도 스코어보드만 0으로 돌아가고 검증
카운터는 어제 값 그대로 남는다 — 재시작한 다음 날 대시보드에 "유효일 28/28"이 그대로 뜨는
분열 상태가 된다. **재시작이 검증 경로에 도달하지 않는다.**

## 창은 필터지 삭제가 아니다 (C1·C2)

앵커 이전 행은 하나도 지우지 않는다. 계수에서 빠질 뿐 조회는 그대로 가능하고, 제외된 건수도
함께 노출한다. **창 밖으로 나가는 것이지 사라지는 것이 아니다.**

## 앵커 미설정 = 현행 동작 (C7)

앵커가 `None` 이면 어떤 조건도 걸지 않는다. 이 WO 자체는 기존 판정을 **한 숫자도** 바꾸지
않아야 하며, 그 사실을 테스트가 강제한다
(`test_window_anchor.py::test_absent_anchor_reproduces_current_numbers`).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# 창 상태 (D4). 지금까지는 "완료 / 미달" 둘뿐이었고, **목표일을 넘겼는데 표본이 모자란 상태**를
# 표현할 이름이 없었다. 그 상태가 무기한 지속되는데도 화면은 계속 "진행 중"으로 보였다.
WINDOW_RUNNING = "running"  # 목표일 이내, 조건 미충족 — 정상 진행
WINDOW_COMPLETE = "complete"  # 3조건 전부 충족
WINDOW_OVERRUN = "overrun"  # 목표 유효일 경과, 그러나 표본·국면 미달

# 검증 창을 갖는 트랙. `sample_viability.TRACK_SAMPLE_SPECS` 와 같은 집합이며, 여기 둔 이유는
# `app/db/repository/` 가 상위 검증 모듈을 임포트하면 순환이 생기기 때문이다.
VALIDATION_TRACKS: tuple[str, ...] = ("crypto", "stock_kr", "stock_us", "poly")

WINDOW_STATE_LABELS: dict[str, str] = {
    WINDOW_RUNNING: "진행 중",
    WINDOW_COMPLETE: "조건 충족(완료)",
    WINDOW_OVERRUN: "목표일 경과·조건 미달",
}


@dataclass(frozen=True)
class WindowAnchor:
    """한 트랙의 현재 검증 창.

    `anchored_at` 은 표본(체결·진입 시각) 필터 하한, `anchor_day` 는 관측일 필터 하한이다.
    두 축의 해상도가 다르므로 같은 값을 두 형태로 보관한다 — 계산으로 유도하면 시간대
    경계에서 하루가 어긋난다.
    """

    track: str
    window_seq: int
    anchored_at: datetime
    anchor_day: str
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "track": self.track,
            "window_seq": self.window_seq,
            "anchored_at": self.anchored_at.isoformat(),
            "anchor_day": self.anchor_day,
            "reason": self.reason,
            "label": f"창 {self.window_seq}회차 · 앵커 {self.anchor_day}",
        }


def current_anchor(connection: sqlite3.Connection, track: str) -> WindowAnchor | None:
    """현재 창 = `window_seq` 최대값. 테이블이 없거나 행이 없으면 앵커 없음(현행 동작)."""
    try:
        row = connection.execute(
            "SELECT track, window_seq, anchored_at, anchor_day, reason FROM validation_windows WHERE track=? ORDER BY window_seq DESC LIMIT 1",
            (track,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return _anchor(row)


def anchor_history(connection: sqlite3.Connection, track: str) -> list[WindowAnchor]:
    """과거 창까지 전부 (C2). 창을 옮긴 이력이 남아야 "그때 무엇을 어디부터 셌는가"를 안다."""
    try:
        rows = connection.execute(
            "SELECT track, window_seq, anchored_at, anchor_day, reason FROM validation_windows WHERE track=? ORDER BY window_seq",
            (track,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [anchor for anchor in (_anchor(row) for row in rows) if anchor is not None]


def open_window(
    connection: sqlite3.Connection,
    track: str,
    *,
    anchored_at: datetime,
    reason: str | None = None,
    now: datetime | None = None,
) -> WindowAnchor:
    """새 창을 연다 — **append-only**. 기존 행을 고치지 않으므로 이력이 보존된다(C2)."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    anchored = anchored_at.astimezone(timezone.utc) if anchored_at.tzinfo else anchored_at.replace(tzinfo=timezone.utc)
    previous = current_anchor(connection, track)
    window_seq = (previous.window_seq + 1) if previous else 1
    anchor = WindowAnchor(
        track=track,
        window_seq=window_seq,
        anchored_at=anchored,
        anchor_day=anchored.date().isoformat(),
        reason=reason,
    )
    connection.execute(
        """INSERT INTO validation_windows (track, window_seq, anchored_at, anchor_day, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(track, window_seq) DO UPDATE SET
            anchored_at=excluded.anchored_at, anchor_day=excluded.anchor_day, reason=excluded.reason""",
        (track, window_seq, anchor.anchored_at.isoformat(), anchor.anchor_day, reason, moment.isoformat()),
    )
    return anchor


def window_state(*, effective_days: int, target_days: int, complete: bool) -> str:
    """창 상태 3종 (D4).

    목표 유효일을 넘겼는데 조건이 미충족이면 `overrun` 이다. 이 상태를 이름 없이 두면 화면은
    계속 "진행 중"으로 보이고, 유효일은 무기한 증가한다. **D+28에 아무 일도 일어나지 않는다**는
    사실이 표시로 드러나야 한다.
    """
    if complete:
        return WINDOW_COMPLETE
    return WINDOW_OVERRUN if effective_days >= target_days else WINDOW_RUNNING


def effective_days_label(effective_days: int, target_days: int) -> str:
    """`31/28 (목표일 경과)` — 목표값에서 상한 처리해 경과를 가리지 않는다(C6)."""
    base = f"{effective_days}/{target_days}"
    return f"{base} (목표일 경과)" if effective_days > target_days else base


def since_clause(anchor: WindowAnchor | None, *, inner_sql: str, column: str = "t") -> tuple[str, tuple[Any, ...]]:
    """표본 쿼리에 창 하한을 씌운다.

    원본 SQL 8종을 고치지 않고 감싼다 — 각 쿼리에 `WHERE` 를 손으로 끼워 넣으면 이미 `WHERE`
    가 있는 것과 없는 것이 섞여 실수가 난다. 앵커가 없으면 **원본을 그대로 돌려준다**(C7).
    """
    if anchor is None:
        return inner_sql, ()
    return f"SELECT {column} FROM ({inner_sql}) WHERE {column} >= ?", (anchor.anchored_at.isoformat(),)


def _anchor(row: Any) -> WindowAnchor | None:
    if row is None:
        return None
    data = (
        dict(row)
        if isinstance(row, sqlite3.Row)
        else {
            "track": row[0],
            "window_seq": row[1],
            "anchored_at": row[2],
            "anchor_day": row[3],
            "reason": row[4],
        }
    )
    anchored = _parse(data.get("anchored_at"))
    if anchored is None:
        return None
    return WindowAnchor(
        track=str(data.get("track") or ""),
        window_seq=int(data.get("window_seq") or 1),
        anchored_at=anchored,
        anchor_day=str(data.get("anchor_day") or anchored.date().isoformat()),
        reason=(str(data["reason"]) if data.get("reason") else None),
    )


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


__all__ = [
    "VALIDATION_TRACKS",
    "WINDOW_COMPLETE",
    "WINDOW_OVERRUN",
    "WINDOW_RUNNING",
    "WINDOW_STATE_LABELS",
    "WindowAnchor",
    "anchor_history",
    "current_anchor",
    "effective_days_label",
    "open_window",
    "since_clause",
    "window_state",
]
