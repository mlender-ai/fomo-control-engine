"""WO-FCE-REPLAY-DEPTH-01 4-4 작업 4 — 발표값 자동 대조 레지스트리.

## 왜 이것이 필요한가

`RISK-SIZING-01` Phase 1~4 의 반사실은 전부 **커밋되지 않은 임시 스크립트**로 산출됐다.
Phase 4 착수 시점에 Phase 3-5 가 발표한 합계를 **재현할 수 없었고**, 원인은 확정할 수 없다 —
읽을 스크립트가 없기 때문이다. 모집단과 차단 9건은 정확히 재현되는데 합계만 일정한
오프셋만큼 어긋난다(gross −2.205R · 비용 −0.303R · net −1.902R, 두 행에서 **동일**).

> **문서에 숫자가 실리면 그 숫자를 만든 경로도 코드로 남아야 하고, 다음 실행이 같은 값을
> 얻는지 매번 대조돼야 한다.** 그것이 이 레지스트리다.

## 재현 안 되는 값을 지우지 않는다

`reproduces=False` 인 항목을 레지스트리에서 빼면 사고가 기록에서 사라진다. 대신 **알려진
오프셋을 함께 등록**하고, 그 오프셋이 **변하면 실패**시킨다.

- 오프셋이 그대로다 → 알려진 미해결 항목. 새 드리프트는 없다
- 오프셋이 달라졌다 → **조용한 드리프트다.** 지금 잡아야 한다

## 두 계층

| 계층 | 대상 | 어디서 강제되나 |
|---|---|---|
| 픽스처 기준선 | 커밋된 캔들 위의 재판정 | **CI** (`tests/test_paper_replay.py`) |
| 원장 기준선 | 라이브 `paper_trades` 위의 반사실 | 호스트 (`scripts/paper_replay_report.py`) |

CI 에는 DB 가 없다. 그래서 원장 기준선은 `requires_database=True` 로 표시하고 CI 는
**등록 위생만** 강제한다 — 등록되지 않은 발표값이 생기는 것을 막는 것이 그 계층의 일이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


# 비교 허용 오차. 문서 표기 자릿수(소수 3~4자리)보다 작게 잡는다.
DEFAULT_TOLERANCE = 5e-4


@dataclass(frozen=True)
class PublishedValue:
    """문서에 발표된 숫자 한 묶음과 그 재현 상태."""

    key: str
    source_doc: str
    values: Mapping[str, float]
    reproduces: bool
    requires_database: bool
    note: str
    # `reproduces=False` 일 때만 의미가 있다. 실측 − 발표 의 알려진 차이.
    known_offset: Mapping[str, float] = field(default_factory=dict)
    tolerance: float = DEFAULT_TOLERANCE


PUBLISHED_VALUES: dict[str, PublishedValue] = {
    "risk_sizing.phase3_5.current": PublishedValue(
        key="risk_sizing.phase3_5.current",
        source_doc="docs/validation/POSITION_SIZING.md §Phase 3-5 (현행 · 잠금 없음)",
        values={"n": 33, "gross_r": 1.037, "cost_r": 4.072, "net_r": -3.035, "profit_factor": 0.8204, "mdd_usdt": 23.94},
        reproduces=False,
        requires_database=True,
        known_offset={"gross_r": -2.205, "cost_r": -0.303, "net_r": -1.902},
        note="산출 스크립트 미커밋으로 원인 미확정. 모집단(N)과 차단 9건은 정확히 재현된다 — 합계만 일정 오프셋만큼 어긋난다.",
    ),
    "risk_sizing.phase3_5.locked": PublishedValue(
        key="risk_sizing.phase3_5.locked",
        source_doc="docs/validation/POSITION_SIZING.md §Phase 3-5 (same_bar 잠금 적용)",
        values={"n": 24, "gross_r": 1.950, "cost_r": 2.945, "net_r": -0.995, "profit_factor": 0.9109, "mdd_usdt": 13.95},
        reproduces=False,
        requires_database=True,
        known_offset={"gross_r": -2.205, "cost_r": -0.303, "net_r": -1.902},
        note="현행 행과 **동일한** 오프셋이다. 두 행에서 같다는 사실이 모집단 차이가 아니라 계산 경로 차이를 가리킨다.",
    ),
    # CI 가 실제로 강제하는 계층. DB 없이 커밋된 캔들만으로 재현된다 — 재판정 경로가
    # 조용히 바뀌면 여기서 즉시 실패한다. **합성 캔들이며 성적이 아니다**(C9).
    "replay_fixture.close": PublishedValue(
        key="replay_fixture.close",
        source_doc="docs/validation/REPLAY_HARNESS.md §재판정 CI 기준선 (tests/fixtures/replay_candles_4h.json · stop_fill=close)",
        values={
            "judgment_points": 81,
            "trades_closed": 11,
            "gross_r": 10.3398,
            "cost_r": 1.6118,
            "net_r": 8.728,
            "profit_factor": 3.5152,
            "mdd_usdt": 8.6754,
        },
        reproduces=True,
        requires_database=False,
        note="합성 캔들 위의 재판정이다. 엔진 성적이 아니라 **경로 재현성**의 기준선이다.",
    ),
    "replay_fixture.intrabar": PublishedValue(
        key="replay_fixture.intrabar",
        source_doc="docs/validation/REPLAY_HARNESS.md §재판정 CI 기준선 (stop_fill=intrabar)",
        values={
            "trades_closed": 11,
            "gross_r": 10.5,
            "cost_r": 1.6049,
            "net_r": 8.8951,
            "profit_factor": 4.849,
            "mdd_usdt": 5.7775,
        },
        reproduces=True,
        requires_database=False,
        note="봉 중간 터치 손절 반사실의 기준선. `close` 행과의 차이가 곧 체결 규칙의 효과다.",
    ),
    "stop_execution.phase2": PublishedValue(
        key="stop_execution.phase2",
        source_doc="docs/validation/STOP_EXECUTION.md §1 (invalidation_breach N=8)",
        values={"n": 8, "mean_gross_r": -1.559, "mean_cost_r": 0.122, "mean_net_r": -1.681},
        reproduces=True,
        requires_database=True,
        note="건별 발표값이 있는 유일한 집합. 봉 중간 터치 반사실의 기준선이다.",
    ),
}


def compare(key: str, actual: Mapping[str, Any]) -> dict[str, Any]:
    """발표값과 실측을 대조한다. **어긋나면 그 사실을 페이로드에 남긴다.**

    `reproduces=False` 항목은 값이 아니라 **오프셋**을 대조한다 — 알려진 미해결 차이가
    그대로인지 보는 것이 목적이고, 값 일치를 요구하면 매 실행이 실패해 신호가 죽는다.
    """
    entry = PUBLISHED_VALUES.get(key)
    if entry is None:
        return {"key": key, "status": "unregistered", "failures": [f"발표값이 레지스트리에 없다: {key}"]}

    failures: list[str] = []
    rows: list[dict[str, Any]] = []
    for name, published in entry.values.items():
        measured = actual.get(name)
        if measured is None:
            failures.append(f"{name}: 실측값 없음")
            rows.append({"metric": name, "published": published, "measured": None, "status": "missing"})
            continue
        delta = float(measured) - float(published)
        if entry.reproduces:
            ok = abs(delta) <= entry.tolerance
            if not ok:
                failures.append(f"{name}: 발표 {published} vs 실측 {measured} (차이 {delta:+.4f})")
        else:
            expected_offset = float(entry.known_offset.get(name, 0.0))
            ok = abs(delta - expected_offset) <= entry.tolerance
            if not ok:
                failures.append(f"{name}: 알려진 오프셋 {expected_offset:+.4f} 가 {delta:+.4f} 로 변했다 — 새 드리프트")
        rows.append(
            {
                "metric": name,
                "published": published,
                "measured": measured,
                "delta": round(delta, 6),
                "expected_offset": entry.known_offset.get(name, 0.0) if not entry.reproduces else 0.0,
                "status": "ok" if ok else "mismatch",
            }
        )
    return {
        "key": key,
        "source_doc": entry.source_doc,
        "reproduces": entry.reproduces,
        "requires_database": entry.requires_database,
        "note": entry.note,
        "rows": rows,
        "failures": failures,
        "status": "ok" if not failures else "mismatch",
    }


def compare_all(measured: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """여러 발표값을 한 번에 대조한다. 실측이 없는 항목은 `skipped` 로 남긴다.

    **조용히 건너뛰지 않는다.** 건너뛴 항목이 목록에 남아야 "대조했다"와 "대조할 수
    없었다"가 구분된다.
    """
    results = []
    skipped = []
    for key, entry in sorted(PUBLISHED_VALUES.items()):
        actual = measured.get(key)
        if actual is None:
            skipped.append({"key": key, "reason": "database_required" if entry.requires_database else "not_measured"})
            continue
        results.append(compare(key, actual))
    failures = [failure for result in results for failure in result["failures"]]
    return {
        "compared": results,
        "skipped": skipped,
        "failures": failures,
        "status": "ok" if not failures else "mismatch",
    }
